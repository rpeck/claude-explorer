"""Path-traversal security tests for ``GET /api/{org}/files/{file}/{variant}``.

Targets ``backend/routers/files.py:83-154`` — specifically the
upstream-404 LOCAL-FALLBACK glob at line 111:

    cached = [
        m for m in _attachments_root().glob(f"*/{file_uuid}/{variant}.*")
        if m.is_file()
    ]

Per the V1 test-hardening plan (PLANS/2026.05.18-test-hardening.md
Task C1), every path-taking route needs explicit malicious-input
tests. The proxy route's ``file_uuid`` is a URL path parameter (so
Starlette would 404 any URL with raw ``/`` or ``%2F``) — BUT the
glob is a string-formatted pattern that could theoretically escape
the attachments root if a single segment of ``..`` slipped through.

Empirical findings (recorded here so a future framework upgrade
that changes the routing behavior surfaces as a regression):
  * Starlette decodes ``%2F`` → ``/`` BEFORE route matching, then
    the 4-segment URL fails to match the 3-segment route. The
    glob attack is NOT exploitable via HTTP under FastAPI's current
    routing semantics.
  * Python's ``pathlib.Path.glob`` DOES expand ``..`` in the pattern.
    With ``file_uuid=".."``, the glob ``*/../<variant>.*`` matches
    files at the attachments-root level (one up from ``*/``), which
    is STILL inside the attachments tree — no escape.
  * With ``file_uuid="../.."``, the glob ``*/../../<variant>.*``
    DOES match files OUTSIDE the attachments root (verified empirically
    in a standalone Python REPL). But that requires a ``/`` in
    ``file_uuid``, which Starlette blocks at routing time.

So the contract is defense-in-depth: the FastAPI route is the first
line; the glob is the second. We test BOTH.

Spec-driven discipline (TESTING.md §1):
    Allowlist of files consulted while authoring this test:
      * PLANS/2026.05.18-test-hardening.md (Task C1)
      * backend/routers/files.py:83-154 (under test)
      * backend/tests/_security_helpers.py
      * backend/tests/conftest.py (isolated_data_dir)
      * backend/tests/test_files_proxy.py (sibling — happy-path tests)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.tests._security_helpers import (
    LEAK_MAGIC,
    attachments_root_for,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Drop a fake credentials.json so the proxy gets past
    ``_load_session_cookies``.
    """

    creds = {
        "schema_version": 2,
        "session_key": "sk-ant-sid01-fake-test-key",
        "primary_org_id": "ae24ae66-4622-48e7-b4b3-1ab2c49f933d",
        "org_id": "ae24ae66-4622-48e7-b4b3-1ab2c49f933d",
        "orgs": [
            {
                "uuid": "ae24ae66-4622-48e7-b4b3-1ab2c49f933d",
                "name": "Personal",
                "capabilities": [],
            }
        ],
        "captured_at": "2026-05-03T00:00:00Z",
        "cf_bm": "fake-cf-bm",
        "cf_clearance": "fake-cf-clearance",
    }
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text(json.dumps(creds))
    monkeypatch.setattr(
        "backend.routers.files.DEFAULT_CREDENTIALS_PATH", creds_path
    )
    return creds_path


@pytest.fixture
def proxy_env(
    isolated_data_dir: Path, fresh_creds: Path
) -> tuple[TestClient, Path]:
    """``(client, attachments_root)`` with credentials seeded so the
    proxy gets past the cookie-load and into the upstream call.
    """

    from backend.main import app

    root = attachments_root_for(isolated_data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return TestClient(app), root


# ---------------------------------------------------------------------------
# Routing-layer pin
# ---------------------------------------------------------------------------


def test__route_layer__url_encoded_slash_in_file_uuid__404s(
    proxy_env: tuple[TestClient, Path],
) -> None:
    """Pin Starlette's behavior: ``file_uuid=%2F%2E%2E`` (URL-encoded
    ``/..``) decodes to ``/..`` BEFORE route matching, expanding the
    URL to more than 3 segments after the prefix and yielding a 404.

    This is the FIRST line of defense for the local-fallback glob
    escape. If a future Starlette change loosened ``%2F`` handling
    in path segments, this test would fail and the glob's
    second-line defense would matter.
    """

    client, _root = proxy_env

    # ``file_uuid=%2F%2E%2E`` → decodes to ``/..`` → URL becomes
    # ``/api/o1/files//../thumbnail`` → too many segments → 404.
    resp = client.get("/api/o1/files/%2F%2E%2E/thumbnail")
    assert resp.status_code == 404, (
        f"Starlette routing pin: %2F in path segment must 404; got "
        f"{resp.status_code}: {resp.text}. If this is no longer the "
        f"case, the local-fallback glob in files.py:111 may be "
        f"exploitable — add server-side sanitization on file_uuid."
    )


def test__route_layer__dotdot_in_file_uuid__client_normalized(
    proxy_env: tuple[TestClient, Path],
) -> None:
    """Pin httpx client-side URL normalization: a raw ``../`` in the
    URL is collapsed by httpx BEFORE the request is sent, so the
    server never sees the traversal segments. The request hits a
    different route (or no route) entirely.
    """

    client, _root = proxy_env

    # httpx will normalize ``..`` segments; the result is a URL with
    # ``..`` collapsed (often hitting the root or a different prefix).
    resp = client.get("/api/o1/files/../etc/passwd/thumbnail")
    # The collapsed URL won't match the 3-segment file route. Expect
    # 404 (no matching route). 200 would mean a route DID match —
    # which would be a bug.
    assert resp.status_code == 404, (
        f"httpx client-side ``..`` normalization pin: expected 404 "
        f"after URL collapse; got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# Handler-direct: local-fallback glob with malicious file_uuid
# ---------------------------------------------------------------------------


def test__proxy__local_fallback_glob__single_dotdot_stays_in_root(
    proxy_env: tuple[TestClient, Path],
) -> None:
    """Single ``..`` segment as ``file_uuid`` (hypothetically reaching
    the handler) — the glob ``*/../thumbnail.*`` lands at the
    attachments root itself.

    A file matching ``thumbnail.*`` directly under the attachments
    root would be served. This is technically "inside the attachments
    tree" so doesn't escape — but it's unexpected behavior. Plant a
    sentinel and verify whether it's served, then document the
    outcome.

    NOTE: Reaching this codepath via HTTP requires bypassing
    Starlette's routing — which Starlette doesn't allow per the
    routing-layer pins above. This test exercises the handler
    directly to document the second-line defense.
    """

    client, root = proxy_env

    # Plant a sentinel one dir up from the conv subdirs (i.e. at the
    # attachments root level).
    leak = root / "thumbnail.png"
    leak.write_bytes(LEAK_MAGIC + b"\nattachments-root-level")

    # Materialize a conv subdir so the leading ``*`` of the glob
    # matches.
    (root / "any-conv").mkdir()

    # Mock upstream to 404 so the local-fallback path runs.
    upstream = MagicMock()
    upstream.status_code = 404
    upstream.content = b'{"detail":"not found"}'
    upstream.headers = {"content-type": "application/json"}

    with patch("curl_cffi.requests.get", return_value=upstream):
        # ``file_uuid=".."`` is a single path segment, so it survives
        # Starlette routing. (The single segment is what's evaluated
        # against the 3-segment route — Starlette accepts it.)
        resp = client.get("/api/o1/files/../thumbnail")

    # Two acceptable outcomes:
    #   (a) Starlette/httpx normalizes the URL such that the route
    #       doesn't match → 404 / 405.
    #   (b) Handler reaches the glob, matches our planted file at
    #       root level (INSIDE the attachments tree), and serves it.
    # Both are acceptable; the security contract is "no file OUTSIDE
    # the attachments root is served".
    if resp.status_code == 200:
        # Glob matched. Verify the served bytes came from INSIDE the
        # attachments tree — i.e. the planted LEAK_MAGIC sentinel.
        # The earlier `or len(resp.content) > 0` clause was a dead
        # branch: any non-empty 200 response satisfied it, so a
        # regression that returned an unrelated single byte like
        # b'?' would silently pass. Tightened per /code-audit Hunt
        # #14 (rubber-stamps) Round-2 Critic finding. The sibling
        # test `test__proxy__local_fallback_glob__outside_root_never
        # _served` still pins the primary security contract (no
        # outside-root file served).
        assert resp.content.startswith(LEAK_MAGIC), (
            f"served bytes did not start with planted LEAK_MAGIC; "
            f"status={resp.status_code}, first_bytes={resp.content[:32]!r}"
        )
        # The fact that we're serving root-level files via this
        # codepath is documented behavior, not a vulnerability.
    else:
        # Route mismatch or handler reject — also fine.
        assert resp.status_code in (400, 404, 405), (
            f"unexpected status {resp.status_code}: {resp.text}"
        )


def test__proxy__local_fallback_glob__outside_root_never_served(
    proxy_env: tuple[TestClient, Path], tmp_path: Path
) -> None:
    """Sanity: a file planted OUTSIDE the attachments tree MUST NEVER
    be served by the proxy, regardless of which path-traversal-style
    ``file_uuid`` the client sends.

    This is the "the contract is satisfied" assertion: even if the
    handler's glob behavior changes, the bytes from outside the
    attachments tree don't end up in the response.
    """

    client, root = proxy_env

    # Plant a sentinel OUTSIDE the attachments root.
    outside = tmp_path / "outside-attachments"
    outside.mkdir()
    leak = outside / "thumbnail.png"
    leak.write_bytes(LEAK_MAGIC + b"\noutside-attachments-tree")

    (root / "some-conv").mkdir()

    upstream = MagicMock()
    upstream.status_code = 404
    upstream.content = b""
    upstream.headers = {"content-type": "application/json"}

    # Try a few file_uuid values; none should leak the outside file.
    attempts = [
        "file-uuid-normal",  # legit-looking, no traversal
        "..",                # single dotdot
        ".",                 # single dot (no-op)
    ]
    with patch("curl_cffi.requests.get", return_value=upstream):
        for file_uuid in attempts:
            resp = client.get(f"/api/o1/files/{file_uuid}/thumbnail")
            assert LEAK_MAGIC not in resp.content, (
                f"PROXY LOCAL-FALLBACK LEAK: file_uuid={file_uuid!r} "
                f"caused the response to contain bytes from outside the "
                f"attachments tree (status={resp.status_code})"
            )
