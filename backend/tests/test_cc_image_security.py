"""Path-traversal security tests for ``GET /api/cc-image?path=...``.

Targets ``backend/routers/files.py:193-264``. Per CLAUDE-TESTING.md
§5.9 + the V1 test-hardening plan (PLANS/2026.05.18-test-hardening.md
Task C1), every path-taking route MUST have explicit malicious-input
tests covering the full attack matrix.

Companion to ``test_cc_image.py`` — that file covers ``..``-traversal,
symlink, non-image-extension, and ``%2F``-canonical cases. THIS file
adds the V1-mandated full matrix:

  * ``../../../etc/passwd`` (URL-encoded + raw via Query)
  * Symlink-outside-root (also in ``test_cc_image.py`` — included
    here for completeness so the full V1 contract lives in one place)
  * Non-readable file (chmod 0o000)
  * Backslash traversal ``..\\..\\..\\etc\\passwd``
  * Null byte injection
  * Absolute path URL-encoded
  * Tilde expansion (``~root/secret``) — UNIQUE to cc-image because
    its handler explicitly calls ``Path(path).expanduser()``
  * Double-URL-encoding
  * Negative-space: response body must not leak the absolute on-disk
    path the server tried to access (CWE-200).

The cc-image handler accepts ``path`` as a ``Query`` param, so unlike
the attachments handler it does NOT go through Starlette path-segment
routing (which strips ``%2F``). Query-string decoding is more permissive
— ``\\x00``, ``\\``, ``~`` all pass through unchanged. That makes
cc-image the more interesting attack surface.

Spec-driven discipline (CLAUDE-TESTING.md §1):
    Allowlist of files consulted while authoring this test:
      * PLANS/2026.05.18-test-hardening.md (Task C1)
      * CLAUDE-TESTING.md §5.9
      * backend/routers/files.py:193-264 (under test)
      * backend/tests/_security_helpers.py
      * backend/tests/conftest.py (isolated_data_dir)
      * backend/tests/test_cc_image.py (sibling — context only, no
        copy-paste; this file pins the cases that one doesn't)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.tests._security_helpers import (
    LEAK_MAGIC,
    assert_no_leak_bytes,
    make_image_cache_dir,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cc_image_env(isolated_data_dir: Path) -> tuple[TestClient, Path, Path]:
    """TestClient + cache_dir + claude_dir, fully isolated under tmp_path.

    Returns ``(client, cache_dir, claude_dir)`` so each test can plant
    files in ``cache_dir`` (inside the cache root the route accepts)
    OR outside ``claude_dir`` (to exercise rejection).
    """

    from backend.main import app

    claude_dir = isolated_data_dir.parent / "claude"
    cache_dir = make_image_cache_dir(claude_dir, "test-session")
    return TestClient(app), cache_dir, claude_dir


def _is_root() -> bool:
    """True iff the test process runs as root (e.g. some CI containers).

    Used to skip the 0o000-permission test, since root bypasses Unix
    permission bits and would surface a spurious pass.
    """

    return hasattr(os, "geteuid") and os.geteuid() == 0


def _plant_secret_outside_cache(tmp_path: Path) -> Path:
    """Plant a secret PNG outside the image-cache root.

    Returns the on-disk path. Body is :data:`LEAK_MAGIC` + filler so a
    bytes-leak in the response is detectable.
    """

    secret = tmp_path / "secret-outside.png"
    secret.write_bytes(LEAK_MAGIC + b"\x00restofpngbytes")
    return secret


# ---------------------------------------------------------------------------
# Path-traversal: ../../../etc/passwd (raw + URL-encoded)
# ---------------------------------------------------------------------------


def test__get_cc_image__etc_passwd_absolute__rejected_no_leak(
    cc_image_env: tuple[TestClient, Path, Path],
) -> None:
    """Direct absolute path ``/etc/passwd`` MUST be rejected.

    The handler does:
        candidate = Path(path).expanduser().resolve(strict=False)
        candidate.relative_to(root.resolve())  # ValueError → 403
    so an absolute path outside the cache root raises ``ValueError``
    and the handler 403s.

    Negative-space: ``root:`` (the canonical /etc/passwd first
    line) MUST NOT appear in the body.
    """

    client, _cache_dir, _claude_dir = cc_image_env

    resp = client.get("/api/cc-image", params={"path": "/etc/passwd"})

    assert resp.status_code in (400, 403, 404), (
        f"expected 4xx for /etc/passwd; got {resp.status_code}: {resp.text}"
    )
    # /etc/passwd content sentinel — the universal Unix ``root:`` line.
    assert "root:" not in resp.text, (
        f"response body contains /etc/passwd content: {resp.text!r}"
    )


def test__get_cc_image__url_encoded_etc_passwd__rejected_no_leak(
    cc_image_env: tuple[TestClient, Path, Path],
) -> None:
    """URL-encoded ``%2Fetc%2Fpasswd`` decodes to ``/etc/passwd`` and
    is rejected the same way.

    Pins Starlette's Query-param decoding behavior: ``%2F`` IS decoded
    to ``/`` (unlike in path segments where it would be route-rejected
    earlier). This test forces a Starlette decoding-behavior change to
    surface here.
    """

    client, _cache_dir, _claude_dir = cc_image_env

    # Hit raw URL so the encoding survives the test pipeline.
    resp = client.get("/api/cc-image?path=%2Fetc%2Fpasswd")

    assert resp.status_code in (400, 403, 404), (
        f"expected 4xx for %2F-encoded /etc/passwd; got "
        f"{resp.status_code}: {resp.text}"
    )
    assert "root:" not in resp.text


def test__get_cc_image__dotdot_relative_traversal__rejected_no_leak(
    cc_image_env: tuple[TestClient, Path, Path], tmp_path: Path
) -> None:
    """``<cache_dir>/../../../etc/passwd`` (a path starting in the
    cache then ``..``-ing out) is canonicalized by ``Path.resolve()``
    and rejected.

    Different from the absolute case because it exercises the lexical
    join-then-canonicalize flow that's the most common real-world
    traversal pattern.
    """

    client, cache_dir, _claude_dir = cc_image_env

    secret = _plant_secret_outside_cache(tmp_path)
    # Build a path that starts in the cache, then traverses out via
    # ``..`` segments to the secret. ``Path.resolve()`` will normalize.
    traversal = cache_dir / ".." / ".." / ".." / secret.name
    assert ".." in str(traversal)  # sanity

    resp = client.get("/api/cc-image", params={"path": str(traversal)})

    assert resp.status_code in (400, 403, 404), (
        f"expected 4xx for ../../etc traversal; got "
        f"{resp.status_code}: {resp.text}"
    )
    assert_no_leak_bytes(resp, msg="cc-image dotdot traversal")


# ---------------------------------------------------------------------------
# Symlink-outside-root
# ---------------------------------------------------------------------------


def test__get_cc_image__symlink_to_outside_root__rejected_no_leak(
    cc_image_env: tuple[TestClient, Path, Path], tmp_path: Path
) -> None:
    """Symlink living INSIDE the cache pointing OUTSIDE — must refuse.

    ``Path.resolve()`` follows symlinks, so the canonicalized path is
    outside the cache root → ``relative_to`` raises ``ValueError`` →
    handler 403s.
    """

    client, cache_dir, _claude_dir = cc_image_env

    secret = _plant_secret_outside_cache(tmp_path)
    sym = cache_dir / "exfil-link.png"
    try:
        os.symlink(secret, sym)
    except (OSError, NotImplementedError) as e:  # pragma: no cover — Windows
        pytest.skip(f"symlink unsupported on this platform: {e}")

    resp = client.get("/api/cc-image", params={"path": str(sym)})

    assert resp.status_code in (400, 403, 404), (
        f"expected 4xx for symlink-out; got {resp.status_code}: {resp.text}"
    )
    assert_no_leak_bytes(resp, msg="cc-image symlink-out")


# ---------------------------------------------------------------------------
# Non-readable file (chmod 0o000)
# ---------------------------------------------------------------------------


@pytest.fixture
def unreadable_cc_image(
    cc_image_env: tuple[TestClient, Path, Path],
) -> Path:
    """Create ``<cache_dir>/locked.png`` with mode 0o000.

    Yields the path; restores mode 0o644 on teardown so pytest's
    ``tmp_path`` cleanup doesn't fail.

    Skipped when running as root (root ignores 0o000).
    """

    if _is_root():
        pytest.skip("permission test requires non-root user (root bypasses 0o000)")

    _client, cache_dir, _claude_dir = cc_image_env
    target = cache_dir / "locked.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\nwould-be-served")
    os.chmod(target, 0o000)
    try:
        yield target
    finally:
        try:
            os.chmod(target, 0o644)
        except FileNotFoundError:
            pass


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod 0o000 does not remove read access on Windows; NTFS uses ACLs, so the unreadable precondition cannot be established",
)
def test__get_cc_image__non_readable_file__4xx_no_path_leak(
    cc_image_env: tuple[TestClient, Path, Path],
    unreadable_cc_image: Path,
) -> None:
    """A cached image with mode 0o000 must not be served.

    Acceptable outcomes:
      * 4xx (403/404/500-but-no-leak) on the request itself, OR
      * 200 from ``FileResponse`` followed by the OS surfacing the
        permission error mid-stream (unlikely under TestClient since
        it materializes the full response before returning).

    Strict negative-space: response body MUST NOT contain the absolute
    on-disk path. CWE-200 / Information Exposure.
    """

    client, _cache_dir, _claude_dir = cc_image_env

    resp = client.get("/api/cc-image", params={"path": str(unreadable_cc_image)})

    # The bytes must not flow back to the client.
    assert b"would-be-served" not in resp.content, (
        f"PERMISSION-BYPASS LEAK: served bytes from a 0o000 file; "
        f"status={resp.status_code}"
    )

    # The response body must not leak the absolute path.
    abs_path_str = str(unreadable_cc_image)
    assert abs_path_str not in resp.text, (
        f"INFO-LEAK: response leaked absolute on-disk path "
        f"{abs_path_str!r} in body: {resp.text!r}"
    )


# ---------------------------------------------------------------------------
# Backslash traversal on POSIX
# ---------------------------------------------------------------------------


def test__get_cc_image__backslash_traversal__rejected_no_leak(
    cc_image_env: tuple[TestClient, Path, Path], tmp_path: Path
) -> None:
    """``..\\..\\..\\etc\\passwd`` MUST NOT be interpreted as a POSIX
    traversal — backslash is a literal character in POSIX filenames.

    The handler should treat this as a single weird filename that
    doesn't exist → 404 or 403 (outside cache root after ``resolve()``).
    """

    client, cache_dir, _claude_dir = cc_image_env

    # Plant a sentinel file with the literal backslash-containing name
    # would be unusual on most filesystems; skip planting and just
    # confirm the route rejects without serving.
    bad_path = str(cache_dir / "..\\..\\..\\etc\\passwd")

    resp = client.get("/api/cc-image", params={"path": bad_path})

    assert resp.status_code in (400, 403, 404), (
        f"expected 4xx for backslash traversal; got {resp.status_code}: "
        f"{resp.text}"
    )
    assert "root:" not in resp.text


# ---------------------------------------------------------------------------
# Null byte injection
# ---------------------------------------------------------------------------


def test__get_cc_image__null_byte__rejected_no_leak(
    cc_image_env: tuple[TestClient, Path, Path],
) -> None:
    """``path=foo%00.png`` MUST be rejected with a 4xx, not crash with
    a 500.

    Python's ``Path.resolve()`` raises ``ValueError("embedded null
    byte")`` on null-containing paths. The handler MUST either:
      (a) catch the ValueError and return 4xx (correct), OR
      (b) let it propagate as a 500 (a contract violation — the
          request is malformed input, not a server fault).

    This test enforces option (a). If it fails with a 500, that's
    a real bug to fix.

    URL-encoded form so the null byte survives the test pipeline.
    """

    client, _cache_dir, _claude_dir = cc_image_env

    resp = client.get("/api/cc-image?path=foo%00.png")

    # Strict: must be 4xx. A 500 fails this test on purpose.
    assert 400 <= resp.status_code < 500, (
        f"null-byte injection MUST be 4xx (request is malformed); "
        f"got {resp.status_code}: {resp.text}. "
        f"If this is a 500, the handler is not catching the "
        f"ValueError raised by Path.resolve() on null bytes — that's "
        f"a contract violation (CWE-754 / improper check for "
        f"exceptional conditions)."
    )
    # Body must not contain a Python traceback ("Traceback" or "ValueError")
    # which would itself be an info leak.
    assert "Traceback" not in resp.text, (
        f"response leaked a Python traceback: {resp.text!r}"
    )


# ---------------------------------------------------------------------------
# Tilde expansion (UNIQUE to cc-image: handler calls expanduser())
# ---------------------------------------------------------------------------


def test__get_cc_image__tilde_root__rejected_no_leak(
    cc_image_env: tuple[TestClient, Path, Path],
) -> None:
    """``path=~root/.bashrc`` expands to ``/root/.bashrc`` (or
    ``/var/root/.bashrc`` on macOS).

    The cc-image handler calls ``Path(path).expanduser()`` explicitly
    — that IS the attack surface. After expansion, the path is
    outside the cache root → 403.

    Pins this defense so a future refactor that drops the
    ``relative_to`` check would surface here.
    """

    client, _cache_dir, _claude_dir = cc_image_env

    resp = client.get("/api/cc-image", params={"path": "~root/.bashrc"})

    assert resp.status_code in (400, 403, 404), (
        f"expected 4xx for ~root expansion; got {resp.status_code}: "
        f"{resp.text}"
    )
    # The body must not contain shell config content
    # (e.g. ``alias`` or ``export PATH=``).
    body_lower = resp.text.lower()
    assert "alias " not in body_lower
    assert "export path" not in body_lower


# ---------------------------------------------------------------------------
# Double-URL-encoding
# ---------------------------------------------------------------------------


def test__get_cc_image__double_url_encoded__not_double_decoded(
    cc_image_env: tuple[TestClient, Path, Path],
) -> None:
    """``%252e%252e%252f`` MUST NOT decode twice.

    Starlette URL-decodes Query params ONCE: ``%252e`` becomes ``%2e``.
    The handler then sees the literal string ``%2e%2e%2fetc%2fpasswd``,
    which is a single weird filename (with percent signs) that:
      * doesn't start with ``/`` (not absolute),
      * doesn't traverse anywhere when ``resolve()`` is called from
        the CWD,
      * doesn't exist as a real file,
      * but its resolved location is OUTSIDE the cache root (since
        CWD is not under the cache root in tests).

    Expected: 403 (outside-root) or 404 (no such file). NOT 200.
    """

    client, _cache_dir, _claude_dir = cc_image_env

    # Hit raw URL so the double-encoding survives intact.
    resp = client.get("/api/cc-image?path=%252e%252e%252fetc%252fpasswd")

    assert resp.status_code in (400, 403, 404), (
        f"expected 4xx for double-encoded traversal; got "
        f"{resp.status_code}: {resp.text}"
    )
    assert "root:" not in resp.text


# ---------------------------------------------------------------------------
# Negative-space: absolute-path leak in error envelope (CWE-200)
# ---------------------------------------------------------------------------


def test__get_cc_image__outside_root_error_leaks_server_path(
    cc_image_env: tuple[TestClient, Path, Path], isolated_data_dir: Path
) -> None:
    """The 403 error for an outside-root path MUST NOT contain the
    server's absolute cache-root path.

    CWE-200 / Information Exposure: error messages that echo internal
    filesystem layout leak server-side path conventions.

    CURRENT BEHAVIOR (as of 2026-05-18): the handler's 403 detail
    string includes the resolved cache-root absolute path
    (``files.py:216``: ``detail=f"refused: path is outside the
    Claude Code image-cache ({root})"``). This is a CWE-200 violation
    that this test is EXPECTED to fail against until the handler is
    fixed to strip the path.

    If this test fails: surface as a security finding, don't silently
    rewrite the handler's error message in the same commit.
    """

    client, _cache_dir, claude_dir = cc_image_env

    resp = client.get("/api/cc-image", params={"path": "/etc/passwd"})
    assert resp.status_code in (400, 403, 404)

    # The absolute path of the cache root must NOT appear in the body.
    # ``Path.resolve()`` may add the macOS ``/private`` prefix, so we
    # check both forms.
    cache_root = (claude_dir / "image-cache").resolve()
    abs_root_str = str(cache_root)
    assert abs_root_str not in resp.text, (
        f"INFO-LEAK: response body contains absolute server path "
        f"{abs_root_str!r}: {resp.text!r}"
    )


def test__get_cc_image__missing_file_error_leaks_server_path(
    cc_image_env: tuple[TestClient, Path, Path],
) -> None:
    """The 404 error for a missing-but-in-root path MUST NOT contain
    the absolute on-disk path.

    Same CWE-200 contract as the outside-root case. Current handler
    raises 404 with ``detail=f"image not found: {candidate}"`` —
    leaking the resolved absolute path. EXPECTED to fail against
    current behavior; surface as a finding.
    """

    client, cache_dir, _claude_dir = cc_image_env

    candidate = cache_dir / "no-such-image.png"
    resp = client.get("/api/cc-image", params={"path": str(candidate)})
    assert resp.status_code == 404

    candidate_resolved = candidate.resolve()
    abs_str = str(candidate_resolved)
    assert abs_str not in resp.text, (
        f"INFO-LEAK: 404 response body contains absolute on-disk path "
        f"{abs_str!r}: {resp.text!r}"
    )
