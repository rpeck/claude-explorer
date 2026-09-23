"""Path-traversal security tests for ``GET /api/attachments/{conv}/{file}/{variant}``.

Targets ``backend/routers/files.py:304-357``. Per TESTING.md
§5.9, every route that takes a path / URL / pattern / external input
MUST have explicit malicious-input tests. The contract is "the route
*refuses* the input (4xx with no leakage), not serves something".

Companion to ``test_attachments.py`` — that file pins the in-tree
contract (``..``-segment lexical traversal, absolute-path injection,
variant allowlist). THIS file pins the full V1 attack-vector matrix:

  * ``../../../etc/passwd`` (URL-encoded + raw)
  * Symlink-outside-root
  * Non-readable file (chmod 0o000)
  * Backslash traversal on POSIX
  * Null byte injection
  * Absolute path URL-encoded
  * Tilde expansion
  * Double-URL-encoding
  * Negative-space: response body must not leak the absolute on-disk
    path the server tried to access (CWE-200 / Information Exposure).

Spec-driven discipline (TESTING.md §1):
    Allowlist of files consulted while authoring this test:
      * PLANS/2026.05.18-test-hardening.md (Task C1)
      * TESTING.md §5.9
      * backend/routers/files.py:304-357 (under test)
      * backend/tests/_security_helpers.py (LEAK_MAGIC, helpers)
      * backend/tests/conftest.py (isolated_data_dir)

Why we call the handler directly for ``..``-laden segments
----------------------------------------------------------

httpx (used by ``TestClient``) URL-normalizes ``../`` out of paths
client-side BEFORE the request leaves the test process; Starlette
also requires exactly three path segments between ``/api/attachments/``
and the variant tail. URL-encoded slashes (``%2F``) inside path
segments are rejected by Starlette routing with a 404 before the
handler runs.

The CONTRACT under test is the handler's path-validation logic
("does it refuse traversal attempts before hitting disk?"). To
exercise that contract, the ``..``-traversal cases call
``get_attachment(...)`` directly with literal ``..`` strings as
segments. The Starlette/httpx boundary itself is pinned in
:func:`test__route_layer__url_encoded_slash_in_path_segment__404s`
below — that test documents the framework-level defense so a future
Starlette upgrade that loosens this behavior would surface here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.tests._security_helpers import (
    LEAK_MAGIC,
    assert_no_leak_bytes,
    attachments_root_for,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def attach_env(isolated_data_dir: Path) -> tuple[TestClient, Path]:
    """TestClient + attachments-root, fully isolated under ``tmp_path``.

    Returns ``(client, attachments_root)``.
    """

    from backend.main import app

    root = attachments_root_for(isolated_data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return TestClient(app), root


def _plant_secret_at(target_dir: Path, name: str = "document.txt") -> Path:
    """Plant a file containing :data:`LEAK_MAGIC` at ``target_dir/name``.

    Used by traversal tests so the negative-space ``assert_no_leak_bytes``
    check has a unique sentinel to look for in the response body.
    """

    target_dir.mkdir(parents=True, exist_ok=True)
    secret = target_dir / name
    secret.write_bytes(LEAK_MAGIC + b"\nsecret-content")
    return secret


def _is_root() -> bool:
    """True iff the test process runs as root (e.g. some CI containers).

    The 0o000-permission test requires the OS to deny reads. Root
    bypasses Unix permission bits, so we skip the test when we are
    root rather than emit a spurious pass.
    """

    return hasattr(os, "geteuid") and os.geteuid() == 0


# ---------------------------------------------------------------------------
# Path-traversal: ../../../etc/passwd (raw + URL-encoded)
# ---------------------------------------------------------------------------


def test__get_attachment__etc_passwd_via_handler__rejected_no_leak(
    attach_env: tuple[TestClient, Path],
) -> None:
    """Direct-handler call with ``conv_uuid="../../../../etc"`` +
    ``file_uuid="passwd"`` MUST be refused.

    Rationale: ``Path("<root>/files") / "../../../../etc" / "passwd"`` resolves
    OUTSIDE the attachments root. The handler's
    ``file_dir.resolve().relative_to(_attachments_root().resolve())``
    check catches this and raises 400.

    Negative-space: even on rejection, the response detail MUST NOT
    contain ``/etc/passwd``'s actual content (specifically the literal
    ``root:`` username line every Unix box has — used here as a
    second-line defense in case our isolated tmp_path coincidentally
    shares a substring with the rejected path).
    """

    _client, _root = attach_env

    from backend.routers.files import get_attachment

    try:
        resp = get_attachment(
            conv_uuid="../../../../etc",
            file_uuid="passwd",
            variant="document",
        )
    except HTTPException as exc:
        assert 400 <= exc.status_code < 500, (
            f"expected 4xx on ../../../etc/passwd; got {exc.status_code}: "
            f"{exc.detail!r}"
        )
        # Detail must not contain real /etc/passwd content (whitespace-
        # tolerant: 'root:' is the canonical first line).
        detail_str = str(exc.detail or "")
        assert "root:" not in detail_str, (
            f"response detail leaked /etc/passwd content: {detail_str!r}"
        )
        return

    # If we got here the handler returned a FileResponse — that's a leak.
    leaked = Path(resp.path).read_bytes()
    assert LEAK_MAGIC not in leaked, (
        f"TRAVERSAL LEAK: served file at {resp.path!r}"
    )
    # Even if no LEAK_MAGIC, an unexpected non-4xx outcome is a contract bug.
    pytest.fail(
        f"expected HTTPException(4xx) for /etc/passwd traversal; "
        f"got FileResponse(path={resp.path!r})"
    )


def test__get_attachment__url_encoded_traversal_via_testclient__404s(
    attach_env: tuple[TestClient, Path],
) -> None:
    """URL-encoded ``..%2F..%2F..%2Fetc%2Fpasswd`` via TestClient.

    Starlette decodes ``%2F`` to ``/`` BEFORE route matching. The
    decoded URL has more than 3 segments between ``/attachments/`` and
    the tail, so the route fails to match → 404 ``Not Found`` from
    Starlette. Documents the routing-layer defense: even if a future
    handler regression existed, Starlette would never route the
    malformed URL to it.
    """

    client, _root = attach_env

    # Raw, unencoded ``..`` is normalized by httpx client-side, so use
    # the URL-encoded form to keep the bytes intact through the test
    # pipeline. We hit the URL directly (not via ``params=``) so the
    # encoding survives.
    url = "/api/attachments/..%2F..%2F..%2Fetc/passwd/document"
    resp = client.get(url)

    # Acceptable rejection codes: 404 (route mismatch) or 400 (handler
    # rejected). We DON'T accept 200.
    assert resp.status_code in (400, 404), (
        f"expected 400/404 for URL-encoded traversal; got {resp.status_code}: "
        f"{resp.text}"
    )
    # /etc/passwd contents must not appear in the body.
    assert "root:" not in resp.text, (
        f"response body leaked /etc/passwd content: {resp.text!r}"
    )


# ---------------------------------------------------------------------------
# Symlink-outside-root
# ---------------------------------------------------------------------------


def test__get_attachment__symlink_outside_root__rejected_no_leak(
    attach_env: tuple[TestClient, Path], tmp_path: Path
) -> None:
    """A symlink INSIDE the attachments tree pointing OUTSIDE must be refused.

    ``Path.resolve()`` follows symlinks: ``chosen.resolve()`` returns the
    real outside-root path. The handler's
    ``chosen.resolve().relative_to(file_dir.resolve())`` check catches
    this and raises 403 (per files.py:351).

    Body must not contain the symlink target's bytes.
    """

    client, root = attach_env

    if not hasattr(os, "symlink"):  # pragma: no cover — defensive
        pytest.skip("symlink not supported on this platform")

    # Plant the secret outside the attachments root.
    outside = tmp_path / "outside-secret"
    outside.mkdir()
    target = outside / "preview.png"
    target.write_bytes(LEAK_MAGIC + b"\nsymlink-target-bytes")

    # Create the attachment dir, then drop a symlink named ``preview.*``
    # that points to the outside file.
    file_dir = root / "conv-sym" / "file-sym"
    file_dir.mkdir(parents=True)
    sym = file_dir / "preview.png"
    try:
        os.symlink(target, sym)
    except (OSError, NotImplementedError) as e:  # pragma: no cover — Windows
        pytest.skip(f"symlink unsupported on this platform: {e}")

    resp = client.get("/api/attachments/conv-sym/file-sym/preview")

    # The handler may either (a) refuse the resolved-outside path with
    # 403, or (b) serve via FileResponse and the OS would surface the
    # link target. The security contract is "refuse OR no body leak".
    # We allow the rejection codes that match the handler's
    # ``relative_to`` ValueError → 403 path.
    assert resp.status_code in (403, 404), (
        f"expected 403/404 for symlink-out; got {resp.status_code}: {resp.text}"
    )
    assert_no_leak_bytes(resp, msg="attachments symlink-out")


# ---------------------------------------------------------------------------
# Non-readable file (chmod 0o000)
# ---------------------------------------------------------------------------


@pytest.fixture
def unreadable_attachment(attach_env: tuple[TestClient, Path]) -> Path:
    """Create ``<root>/conv-perm/file-perm/preview.png`` with mode 0o000.

    Uses ``yield`` so the chmod restore on teardown is guaranteed even
    if the test crashes — a bare ``try/finally`` in the test body would
    skip the restore on assertion failure, leaving the file unreadable
    and breaking ``tmp_path`` cleanup.

    Skipped when running as root (root ignores 0o000).
    """

    if _is_root():
        pytest.skip("permission test requires non-root user (root bypasses 0o000)")

    _client, root = attach_env
    file_dir = root / "conv-perm" / "file-perm"
    file_dir.mkdir(parents=True)
    target = file_dir / "preview.png"
    target.write_bytes(b"would-be-served-bytes")
    os.chmod(target, 0o000)
    try:
        yield target
    finally:
        # Restore so pytest's tmp_path cleanup can rm -rf the tree.
        try:
            os.chmod(target, 0o644)
        except FileNotFoundError:
            pass


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod 0o000 does not remove read access on Windows; NTFS uses ACLs, so the unreadable precondition cannot be established",
)
def test__get_attachment__non_readable_file__4xx_no_path_leak(
    attach_env: tuple[TestClient, Path], unreadable_attachment: Path
) -> None:
    """A cached attachment with mode 0o000 must yield a 4xx (or 500 with
    NO body leak), and the response MUST NOT contain the absolute
    on-disk path the server tried to read.

    Per the task contract: "Must return 4xx (likely 403 or 500-but-no-leak).
    Restore permissions in test teardown so cleanup doesn't fail."
    """

    client, _root = attach_env

    resp = client.get("/api/attachments/conv-perm/file-perm/preview")

    # 4xx preferred; 500 acceptable IFF body doesn't leak.
    # ``FileResponse`` does an ``os.stat`` before opening, which on a
    # 0o000 file may surface differently across Starlette versions.
    assert resp.status_code != 200, (
        f"unreadable file must NOT be served; got {resp.status_code}: {resp.text}"
    )

    # Negative-space: response body must not contain the absolute path
    # we just chmod'd. CWE-200 — information exposure via error message.
    # This is the strict assertion the task contract requires.
    abs_path_str = str(unreadable_attachment)
    assert abs_path_str not in resp.text, (
        f"INFO-LEAK: response leaked absolute on-disk path {abs_path_str!r} "
        f"in body: {resp.text!r}"
    )


# ---------------------------------------------------------------------------
# Backslash traversal on POSIX
# ---------------------------------------------------------------------------


def test__get_attachment__backslash_traversal__rejected_no_leak(
    attach_env: tuple[TestClient, Path], tmp_path: Path
) -> None:
    """``..\\..\\..\\etc\\passwd`` as path components.

    On POSIX, backslash is a LITERAL character in filenames — not a
    separator. So a ``conv_uuid="..\\..\\etc"`` is a single weird
    filename, not three segments. The handler resolves
    ``<root>/files/..\\..\\etc/<file>/<variant>`` which is a single
    nonsense directory that doesn't exist → 404 (the "attachment not
    cached" branch).

    The point of this test is to pin behavior so a hypothetical
    cross-platform path normalization (which would split on backslash
    on Windows but not POSIX) doesn't accidentally promote backslash
    to a separator on POSIX too.
    """

    _client, root = attach_env

    # Plant a secret one level up, then try to escape via backslash-
    # traversal. On POSIX this should fail because backslash is not a
    # separator; the handler's relative_to check guards if it ever
    # were.
    _plant_secret_at(root.parent, "preview.txt")

    from backend.routers.files import get_attachment

    try:
        resp = get_attachment(
            conv_uuid="..\\..\\etc",
            file_uuid="passwd",
            variant="preview",
        )
    except HTTPException as exc:
        assert 400 <= exc.status_code < 500, (
            f"expected 4xx for backslash traversal; got {exc.status_code}: "
            f"{exc.detail!r}"
        )
        return

    # If we got here the handler returned a FileResponse — that's a leak.
    leaked = Path(resp.path).read_bytes()
    assert LEAK_MAGIC not in leaked, (
        f"BACKSLASH-TRAVERSAL LEAK: served file at {resp.path!r}"
    )
    pytest.fail(
        f"expected HTTPException(4xx) for backslash traversal; "
        f"got FileResponse(path={resp.path!r})"
    )


# ---------------------------------------------------------------------------
# Null byte injection
# ---------------------------------------------------------------------------


def test__get_attachment__null_byte_in_segment__rejected_no_leak(
    attach_env: tuple[TestClient, Path],
) -> None:
    """``conv_uuid="conv\\x00evil"`` must be rejected, not silently
    truncated.

    Python's ``Path.resolve()`` raises ``ValueError("embedded null
    byte")`` on null-containing paths. The handler must EITHER catch
    this and return 4xx, OR the framework must have rejected the URL
    earlier. A 500 with the null byte propagating up is a contract
    violation (the request is malformed input, not a server fault).

    This test runs against the handler directly because httpx + Starlette
    may sanitize null bytes from URLs in their own way; we want the
    handler's behavior pinned.
    """

    _client, _root = attach_env

    from backend.routers.files import get_attachment

    try:
        resp = get_attachment(
            conv_uuid="conv\x00evil",
            file_uuid="file",
            variant="preview",
        )
    except HTTPException as exc:
        assert 400 <= exc.status_code < 500, (
            f"expected 4xx for null-byte segment; got {exc.status_code}: "
            f"{exc.detail!r}"
        )
        return
    except ValueError as exc:
        # ValueError from Path.resolve() propagating uncaught is a bug.
        # The test FAILS so the user can see and decide whether to fix
        # the handler. Don't silently swallow it.
        pytest.fail(
            f"null-byte injection caused uncaught ValueError: {exc!r}. "
            f"Handler should catch this and return 4xx (request is "
            f"malformed input, not a server fault)."
        )

    leaked = Path(resp.path).read_bytes()
    assert LEAK_MAGIC not in leaked
    pytest.fail(
        f"expected HTTPException(4xx) for null-byte segment; "
        f"got FileResponse(path={resp.path!r})"
    )


# ---------------------------------------------------------------------------
# Absolute path URL-encoded
# ---------------------------------------------------------------------------


def test__get_attachment__absolute_path_url_encoded__rejected(
    attach_env: tuple[TestClient, Path],
) -> None:
    """``conv_uuid="%2Fetc"`` decodes to ``"/etc"`` which is an absolute
    path; ``Path("a") / "/etc"`` discards ``"a"`` per pathlib's
    absolute-path semantics. The defense:
    ``file_dir.resolve().relative_to(_attachments_root().resolve())``.

    On the wire, Starlette decodes ``%2F`` to ``/`` BEFORE route
    matching, so most of these URLs fail to match the 3-segment route
    and 404 at the routing layer. That's a defense-in-depth pin: even
    if the handler logic regressed, the framework wouldn't route to it.
    """

    client, _root = attach_env

    # ``%2Fetc%2Fpasswd`` as the conv_uuid segment. After decoding:
    # ``/api/attachments//etc/passwd/file/preview`` — the double slash
    # may collapse or fail to match.
    url = "/api/attachments/%2Fetc%2Fpasswd/file/preview"
    resp = client.get(url)

    assert resp.status_code in (400, 404), (
        f"expected 400/404 for URL-encoded absolute path; got "
        f"{resp.status_code}: {resp.text}"
    )
    assert "root:" not in resp.text


# ---------------------------------------------------------------------------
# Routing-layer pin
# ---------------------------------------------------------------------------


def test__route_layer__url_encoded_slash_in_path_segment__404s(
    attach_env: tuple[TestClient, Path],
) -> None:
    """Pin Starlette's behavior: ``%2F`` in a path segment is decoded
    BEFORE route matching, so a 3-segment route can't match a URL with
    encoded slashes inside one of its segments.

    Documents the framework-level defense so a future Starlette upgrade
    (or ASGI server swap) that loosens this behavior surfaces here.
    """

    client, _root = attach_env

    # ``file_uuid=foo%2Fbar`` decodes to ``foo/bar`` → 4 segments after
    # the prefix → route mismatch → 404.
    resp = client.get("/api/attachments/conv1/foo%2Fbar/preview")

    assert resp.status_code == 404, (
        f"Starlette pins URL-decoded %2F as a route-matching boundary; "
        f"expected 404 from route mismatch, got {resp.status_code}: "
        f"{resp.text}. If this changed, audit handler path validation."
    )


# ---------------------------------------------------------------------------
# Negative-space: absolute-path leak in error envelope (CWE-200)
# ---------------------------------------------------------------------------


def test__get_attachment__error_body_does_not_leak_absolute_root(
    attach_env: tuple[TestClient, Path], isolated_data_dir: Path
) -> None:
    """The error response from a missing attachment MUST NOT contain
    the server's absolute on-disk path.

    Information-exposure (CWE-200): error messages that echo internal
    filesystem layout leak server-side path conventions to the client.
    The current handler raises 404 with ``detail="attachment not
    cached"`` — no path included. This test pins that contract so a
    future helpful-error-message refactor that surfaced the resolved
    path would break here.

    Probes via a valid request to a non-existent attachment.
    """

    client, _root = attach_env

    resp = client.get("/api/attachments/no-such-conv/no-such-file/preview")

    # Expect 4xx — either 400 (validation) or 404 (not cached).
    assert resp.status_code in (400, 404), (
        f"expected 4xx for missing attachment; got {resp.status_code}: {resp.text}"
    )

    # The body must not contain the absolute tmp_path root (which would
    # reveal the server's filesystem layout).
    abs_root_str = str(isolated_data_dir)
    assert abs_root_str not in resp.text, (
        f"INFO-LEAK: response body contains absolute server path "
        f"{abs_root_str!r}: {resp.text!r}"
    )


# ---------------------------------------------------------------------------
# Tilde expansion / home-dir attack
# ---------------------------------------------------------------------------


def test__get_attachment__tilde_segment__not_expanded(
    attach_env: tuple[TestClient, Path], tmp_path: Path
) -> None:
    """``conv_uuid="~root"`` must be treated as a literal segment, NOT
    expanded to ``/root``.

    The attachments handler does NOT call ``Path(...).expanduser()``,
    so this should be a 404 (no such directory exists). The cc-image
    handler DOES call expanduser — this test pins the divergence.
    """

    _client, _root = attach_env

    from backend.routers.files import get_attachment

    # Use a tilde-prefixed conv_uuid; expect 404 not_cached, not a
    # successful lookup against ``~root/...``.
    try:
        resp = get_attachment(
            conv_uuid="~root", file_uuid="file", variant="preview"
        )
    except HTTPException as exc:
        # 4xx is the contract.
        assert 400 <= exc.status_code < 500, (
            f"expected 4xx for tilde segment; got {exc.status_code}: "
            f"{exc.detail!r}"
        )
        return

    # If we somehow got a FileResponse, ensure it's not from /root.
    home = Path("/root") if sys.platform != "darwin" else Path("/var/root")
    resp_path = Path(resp.path).resolve()
    assert not str(resp_path).startswith(str(home)), (
        f"TILDE EXPANSION LEAK: handler served from {resp_path!r}"
    )
    pytest.fail(
        f"expected HTTPException(4xx) for tilde segment; "
        f"got FileResponse(path={resp.path!r})"
    )


# ---------------------------------------------------------------------------
# Double-URL-encoding
# ---------------------------------------------------------------------------


def test__get_attachment__double_url_encoded_traversal__rejected(
    attach_env: tuple[TestClient, Path],
) -> None:
    """``%252e%252e%252f`` (double-encoded ``../``) MUST NOT decode
    twice.

    Starlette URL-decodes ONCE: ``%252e`` → ``%2e``, ``%252f`` →
    ``%2f``. The handler then sees the segment literally as
    ``%2e%2e%2f``, which is a single weird filename, not a traversal.
    This test pins Starlette's one-pass decoding so a future double-
    decoding regression would surface.
    """

    client, _root = attach_env

    # Double-encoded ``../../etc/passwd`` as the conv_uuid segment.
    url = "/api/attachments/%252e%252e%252fetc%252fpasswd/file/preview"
    resp = client.get(url)

    # Expect 4xx — either route mismatch (if Starlette did re-decode
    # %2F → /), or handler-level 404 (single-decoded literal). Both
    # acceptable; 200 is not.
    assert resp.status_code in (400, 404), (
        f"expected 4xx for double-encoded traversal; got "
        f"{resp.status_code}: {resp.text}"
    )
    assert "root:" not in resp.text
