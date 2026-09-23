"""Security-adjacent tests for ``GET /api/attachments/{conv}/{file}/{variant}``.

Targets ``backend/routers/files.py:278-317``. The route serves cached
attachment bytes from ``<data_dir>.parent / "files" / <conv> / <file>``;
the security contract is "serve only files inside that tree, never
escape it via traversal segments or absolute-path injection".

Spec-driven discipline (TESTING.md §1):
    Allowlist of files consulted while authoring this test:
      * ``PLANS/2026.05.07-frontend-api-contract.md`` (ATCH clauses)
      * ``PLANS/2026.05.08 BACKEND TEST PLAN.md`` (P2.6)
      * ``backend/routers/files.py:278-317`` (under test)
      * ``backend/tests/conftest.py`` (``isolated_data_dir``)
      * ``backend/tests/_security_helpers.py`` (this PR)

Why we call the handler directly for traversal cases
----------------------------------------------------

httpx (used by ``TestClient``) normalizes ``../`` out of URL paths
BEFORE sending. Starlette routing also requires exactly three segments
between ``/api/attachments/`` and the variant tail. A request to
``/api/attachments/../../etc/file/preview`` either gets normalized to
``/api/etc/file/preview`` by the client OR fails to match the route
on the server — neither path reaches the handler.

The contract under test is the HANDLER's logic ("does it refuse
``..`` segments before hitting the filesystem?"), not URL parsing. To
exercise that contract we call ``get_attachment(...)`` directly with
the malicious segments. For the variant-allowlist test (3 plain
segments, no ``..``) we still use ``TestClient`` since the URL passes
client + routing untouched.

Pre-fix RED behavior (this commit, ``files.py`` not yet patched)
----------------------------------------------------------------

For ``conv_uuid=".."``, ``file_uuid=".."``, the impl computes
``file_dir = _attachments_root() / ".." / ".."`` — which IS a directory
(it resolves to a parent of the data root). ``is_dir()`` returns True;
the glob proceeds; if a file matching ``<variant>.*`` exists at that
location, the handler returns 200 with the file's bytes. We exploit
this by planting a magic-byte file in the parent, so the RED commit's
test failure shows a real bytes-leak, not just a status-code mismatch.

Post-fix GREEN behavior (next commit)
-------------------------------------

The handler will check
``file_dir.resolve().relative_to(_attachments_root().resolve())``
immediately after building ``file_dir``; the ``ValueError`` raised by
``relative_to`` becomes a 400 with ``detail="invalid path"``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.tests._security_helpers import (
    LEAK_MAGIC,
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
    """Plant a ``document.<ext>`` file containing :data:`LEAK_MAGIC` at
    a target directory.

    Used by traversal tests to materialize a "secret" outside the
    attachments root that the handler's glob would find pre-fix. The
    magic bytes are unique enough that
    :func:`assert_no_leak_bytes` can't be spuriously satisfied by
    error-message echo of the rejected path.
    """

    target_dir.mkdir(parents=True, exist_ok=True)
    secret = target_dir / name
    secret.write_bytes(LEAK_MAGIC + b"\nsecret-content")
    return secret


# ---------------------------------------------------------------------------
# Traversal tests (handler-direct; httpx + Starlette would normalize URLs)
# ---------------------------------------------------------------------------


def test__get_attachment__conv_uuid_dotdot__returns_400_no_leak(
    attach_env: tuple[TestClient, Path],
) -> None:
    """ATCH-400-CONV-TRAVERSAL + ATCH-NEG-FS.

    The contract: ``conv_uuid=".."`` (a path-segment traversal) MUST be
    rejected with 400 and MUST NOT cause the handler to read or return
    a file outside the attachments root.

    Pre-fix this test FAILS because:
      * ``_attachments_root() = <data_dir>/files``
      * ``file_dir = <data_dir>/files/.. /..`` resolves to
        ``<data_dir>.parent`` (i.e. ``<tmp_path>``), which IS a
        directory; ``is_dir()`` accepts it.
      * The glob ``document.*`` then finds our planted secret at the
        traversal target → handler returns ``FileResponse`` with the
        leaked bytes (REAL READ VULNERABILITY).

    Post-fix this test PASSES because the new
    ``file_dir.resolve().relative_to(_attachments_root().resolve())``
    check catches the escape before ``is_dir()`` runs and raises 400.
    """

    _client, root = attach_env
    # ``conv_uuid=".."`` + ``file_uuid=".."`` lands ``file_dir`` two
    # levels up from the attachments root — i.e. at
    # ``<tmp_path>/data/files/.. /..`` which is ``<tmp_path>``.
    leak_target = root.parent.parent  # == <tmp_path>
    _plant_secret_at(leak_target, "document.txt")

    from backend.routers.files import get_attachment

    # Direct handler call: bypasses httpx and Starlette URL normalization.
    try:
        resp = get_attachment(conv_uuid="..", file_uuid="..", variant="document")
    except HTTPException as exc:
        # Post-fix path: handler raises HTTPException(400). Confirm the
        # contract: 400 status, ``detail`` populated, no leaked bytes
        # were ever materialized into a response object.
        assert exc.status_code == 400, (
            f"expected 400 on traversal; got {exc.status_code}: {exc.detail!r}"
        )
        assert exc.detail, "expected non-empty detail on 400"
        return

    # Pre-fix path: handler returned a FileResponse without raising.
    # That file MUST NOT be the planted secret — but pre-fix it IS, so
    # the assertion fires with a clear message documenting the leak.
    leaked = Path(resp.path).read_bytes()
    assert LEAK_MAGIC not in leaked, (
        "TRAVERSAL LEAK: handler returned a file outside the attachments "
        f"root containing magic={LEAK_MAGIC!r}; path={resp.path}"
    )
    pytest.fail(
        f"expected HTTPException(400); got FileResponse(path={resp.path!r})"
    )


def test__get_attachment__file_uuid_dotdot__returns_400_no_leak(
    attach_env: tuple[TestClient, Path],
) -> None:
    """ATCH-400-FILE-TRAVERSAL + ATCH-NEG-FS.

    Same traversal contract for ``file_uuid``. With ``conv_uuid="conv1"``
    + ``file_uuid="../.."``:
      * ``file_dir = <data_dir>/files/conv1/../..`` resolves to
        ``<data_dir>`` (one level UP from the attachments root).
      * For ``is_dir()`` to walk through ``conv1`` correctly, we MUST
        materialize ``<files>/conv1/`` first; otherwise ``is_dir()``
        returns False and the route 404s for the wrong reason
        (would mask the contract failure with a non-vulnerability
        false-pass).

    Pre-fix: 200 with leaked bytes from ``<data_dir>/document.txt``.
    Post-fix: 400.
    """

    _client, root = attach_env
    # Materialize <files>/conv1/ so ``conv1/../..`` walks correctly.
    (root / "conv1").mkdir(parents=True, exist_ok=True)
    # Plant secret at <files>/conv1/../.. == <data_dir>.
    leak_target = root.parent  # == <data_dir>
    _plant_secret_at(leak_target, "document.txt")

    from backend.routers.files import get_attachment

    try:
        resp = get_attachment(
            conv_uuid="conv1", file_uuid="../..", variant="document"
        )
    except HTTPException as exc:
        assert exc.status_code == 400, (
            f"expected 400 on file_uuid traversal; got {exc.status_code}: "
            f"{exc.detail!r}"
        )
        assert exc.detail, "expected non-empty detail on 400"
        return

    leaked = Path(resp.path).read_bytes()
    assert LEAK_MAGIC not in leaked, (
        "FILE-UUID TRAVERSAL LEAK: handler returned outside-root file "
        f"with magic={LEAK_MAGIC!r}; path={resp.path}"
    )
    pytest.fail(
        f"expected HTTPException(400); got FileResponse(path={resp.path!r})"
    )


def test__get_attachment__absolute_path_injection__returns_400(
    attach_env: tuple[TestClient, Path], tmp_path: Path
) -> None:
    """ATCH-400-CONV-TRAVERSAL (absolute-path variant).

    Python's ``Path()`` operator discards the left side when the right
    is absolute: ``Path("/a") / "/b" == Path("/b")``. So
    ``conv_uuid=str(some_absolute_dir)`` makes ``file_dir`` resolve
    OUTSIDE the attachments root entirely. Pre-fix the handler reads
    that directory; post-fix the ``relative_to`` check rejects it.
    """

    _client, root = attach_env

    # Plant the secret somewhere predictable that ISN'T under root.
    # ``tmp_path`` is the test's isolated tmp dir; perfect.
    leak_dir = tmp_path / "external"
    leak_dir.mkdir()
    secret = leak_dir / "document.bin"
    secret.write_bytes(LEAK_MAGIC + b"\nabsolute-injection-leak")

    # ``conv_uuid`` is an ABSOLUTE path string. Path semantics:
    #   _attachments_root() / "/.../external" / "x"  ==  Path("/.../external/x")
    # which discards the attachments root entirely.
    abs_conv = str(leak_dir)
    # Make the file_uuid so file_dir == leak_dir (i.e. ``conv_uuid="/abs"``
    # + ``file_uuid="."`` resolves to ``leak_dir``).
    from backend.routers.files import get_attachment

    try:
        resp = get_attachment(conv_uuid=abs_conv, file_uuid=".", variant="document")
    except HTTPException as exc:
        assert exc.status_code == 400, (
            f"expected 400 on absolute-path injection; got {exc.status_code}: "
            f"{exc.detail!r}"
        )
        assert exc.detail, "expected non-empty detail on 400"
        return

    leaked = Path(resp.path).read_bytes()
    assert LEAK_MAGIC not in leaked, (
        "ABSOLUTE-PATH LEAK: handler returned a file outside the "
        f"attachments root; path={resp.path}"
    )
    pytest.fail(
        f"expected HTTPException(400); got FileResponse(path={resp.path!r})"
    )


# ---------------------------------------------------------------------------
# Variant-allowlist tests (TestClient OK; route accepts 3 plain segments)
# ---------------------------------------------------------------------------


def test__get_attachment__unknown_variant__returns_400(
    attach_env: tuple[TestClient, Path],
) -> None:
    """ATCH-400-UNKNOWN-VARIANT.

    Variant must be one of ``thumbnail|preview|original|document``. An
    unknown variant must return 400 (not 404). This pins the existing
    allowlist behavior MORE STRICTLY than
    ``test_desktop_attachments_full.py:280-281`` (which accepts 400 OR
    404) — per the api-contract Ambiguities table, the contractually
    correct answer is 400 ("the request is malformed, not a missing
    resource").
    """

    client, _root = attach_env
    resp = client.get("/api/attachments/c/f/evil")

    assert resp.status_code == 400, (
        f"expected 400 for unknown variant; got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "detail" in body
    # The detail should reference the variant for diagnostics.
    assert "variant" in body["detail"].lower() or "evil" in body["detail"]


def test__get_attachment__variant_exact_membership__pins_allowlist(
    attach_env: tuple[TestClient, Path],
) -> None:
    """ATCH-400-UNKNOWN-VARIANT (exact-membership lockdown).

    Names that are *near-misses* of the allowed set (``thumbnails`` plural,
    ``Thumbnail`` capitalized, leading-space variants) must all 400. This
    ensures the allowlist is exact-string membership, not a substring or
    case-insensitive match.
    """

    client, _root = attach_env

    for variant in ("thumbnails", "Thumbnail", "THUMBNAIL", " thumbnail"):
        # ``" thumbnail"`` will URL-encode to ``%20thumbnail`` which the
        # route receives as the variant segment — still not in the
        # allowlist, so 400.
        resp = client.get(f"/api/attachments/c/f/{variant}")
        assert resp.status_code == 400, (
            f"expected 400 for near-miss variant {variant!r}; "
            f"got {resp.status_code}: {resp.text}"
        )

    # Sanity: the four ALLOWED variants pass the allowlist (they 404
    # because no file is cached, but they do NOT 400). This is the
    # negative-space side of the assertion: confirm we didn't tighten
    # the allowlist by accident.
    for variant in ("thumbnail", "preview", "original", "document"):
        resp = client.get(f"/api/attachments/c/f/{variant}")
        assert resp.status_code != 400, (
            f"unexpectedly 400 for allowlisted variant {variant!r}: "
            f"{resp.text}"
        )
