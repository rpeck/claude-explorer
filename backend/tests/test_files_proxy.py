"""Tests for the image-attachment proxy.

The proxy fetches /api/<org>/files/<uuid>/{thumbnail,preview} from
claude.ai using the captured sessionKey cookie, and returns the bytes
to the same-origin browser. These tests cover the request shape +
error handling without hitting the live claude.ai (which we mock).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fresh_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Drop a fake credentials.json that load_credentials will accept."""
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
def client_no_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "backend.routers.files.DEFAULT_CREDENTIALS_PATH",
        tmp_path / "missing.json",
    )
    from backend.main import app
    return TestClient(app)


def test_proxy_503_when_no_credentials(client_no_creds: TestClient) -> None:
    resp = client_no_creds.get("/api/test-org/files/test-uuid/thumbnail")
    assert resp.status_code == 503
    assert "claude-explorer capture" in resp.json()["detail"]


def test_proxy_streams_upstream_bytes(fresh_creds: Path) -> None:
    from backend.main import app
    client = TestClient(app)

    upstream = MagicMock()
    upstream.status_code = 200
    upstream.content = b"\xff\xd8\xff\xe0FAKEJPEGBYTES"
    upstream.headers = {"content-type": "image/webp"}

    with patch("curl_cffi.requests.get", return_value=upstream) as mock_get:
        resp = client.get("/api/org-1/files/file-1/thumbnail")

    assert resp.status_code == 200
    assert resp.content == b"\xff\xd8\xff\xe0FAKEJPEGBYTES"
    assert resp.headers["content-type"] == "image/webp"
    assert "max-age" in resp.headers["cache-control"]

    args, kwargs = mock_get.call_args
    assert args[0] == "https://claude.ai/api/org-1/files/file-1/thumbnail"
    assert kwargs["cookies"]["sessionKey"] == "sk-ant-sid01-fake-test-key"
    assert kwargs["cookies"]["__cf_bm"] == "fake-cf-bm"
    assert kwargs["impersonate"] == "chrome"


def test_proxy_404_propagates(fresh_creds: Path) -> None:
    """Upstream 404 with NO local cache → 404 with the new descriptive
    detail (must include 'no local cache' so observability can
    distinguish from generic upstream 404). See PROXY-LOCAL-FALLBACK-404.
    """
    from backend.main import app
    client = TestClient(app)
    upstream = MagicMock()
    upstream.status_code = 404
    upstream.content = b'{"detail":"not found"}'
    upstream.headers = {"content-type": "application/json"}
    with patch("curl_cffi.requests.get", return_value=upstream):
        resp = client.get("/api/org-1/files/missing/preview")
    assert resp.status_code == 404
    assert "no local cache" in resp.json()["detail"], (
        f"detail must distinguish 'gone everywhere' from 'gone upstream only'; "
        f"got {resp.json()!r}"
    )


def test_proxy_401_surfaces_capture_hint(fresh_creds: Path) -> None:
    from backend.main import app
    client = TestClient(app)
    upstream = MagicMock()
    upstream.status_code = 401
    upstream.content = b"unauthorized"
    upstream.headers = {"content-type": "text/plain"}
    with patch("curl_cffi.requests.get", return_value=upstream):
        resp = client.get("/api/org-1/files/file-1/thumbnail")
    assert resp.status_code == 401
    assert "session expired" in resp.json()["detail"].lower()


def test_proxy_preview_endpoint(fresh_creds: Path) -> None:
    """Preview variant is a separate route from thumbnail."""
    from backend.main import app
    client = TestClient(app)
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.content = b"PNG-BYTES"
    upstream.headers = {"content-type": "image/png"}
    with patch("curl_cffi.requests.get", return_value=upstream) as mock_get:
        resp = client.get("/api/o/files/f/preview")
    assert resp.status_code == 200
    args, _ = mock_get.call_args
    assert args[0].endswith("/files/f/preview")


# ----------------------------------------------------------------------
# Claude Code image-cache route tests
# ----------------------------------------------------------------------

def test_cc_image_serves_file_under_image_cache(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    cache_root = tmp_path / "image-cache" / "test-session"
    cache_root.mkdir(parents=True)
    img_path = cache_root / "1.png"
    img_path.write_bytes(
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfc\xff'
        b'\xff?\x03\x00\x05\xfe\x02\xfe\xa6\x14\xa6\x9d\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
    from backend import config
    config.get_settings.cache_clear()
    from backend.main import app
    client = TestClient(app)
    resp = client.get("/api/cc-image", params={"path": str(img_path)})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content.startswith(b"\x89PNG")


def test_cc_image_refuses_path_outside_image_cache(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    (tmp_path / "image-cache").mkdir()
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"\x89PNG-secret-bytes")
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
    from backend import config
    config.get_settings.cache_clear()
    from backend.main import app
    client = TestClient(app)
    resp = client.get("/api/cc-image", params={"path": str(secret)})
    assert resp.status_code == 403
    assert "outside" in resp.json()["detail"].lower()


def test_cc_image_refuses_non_image_extension(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    cache = tmp_path / "image-cache" / "session"
    cache.mkdir(parents=True)
    txt = cache / "leaked.txt"
    txt.write_bytes(b"sensitive text content")
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
    from backend import config
    config.get_settings.cache_clear()
    from backend.main import app
    client = TestClient(app)
    resp = client.get("/api/cc-image", params={"path": str(txt)})
    assert resp.status_code == 400
    assert "extension" in resp.json()["detail"].lower()


def test_cc_image_404_for_missing_path(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    (tmp_path / "image-cache" / "session").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
    from backend import config
    config.get_settings.cache_clear()
    from backend.main import app
    client = TestClient(app)
    resp = client.get(
        "/api/cc-image",
        params={"path": str(tmp_path / "image-cache" / "session" / "nope.png")},
    )
    assert resp.status_code == 404


# ----------------------------------------------------------------------
# Local-cache fallback for upstream 404 (Phase A — V1 polish)
#
# claude.ai garbage-collects file storage. When upstream 404s for a
# file we have cached locally at <attachments_root>/<conv>/<file>/<variant>.<ext>,
# serve the local copy. Bonus: this also fixes Markdown/PDF exports
# (same proxy URL).
#
# Spec-driven discipline (TESTING.md §1):
#   Allowlist of files consulted while authoring this section:
#     * PLANS/2026.05.09-v1-readiness-sweep.md (Phase A)
#     * backend/routers/files.py:79-121 (the function under fix)
#     * backend/routers/files.py:222-237 (the cc-image fallback pattern
#       this work mirrors)
#     * backend/tests/conftest.py (isolated_data_dir)
#     * backend/tests/_security_helpers.py (attachments_root_for)
# ----------------------------------------------------------------------

# E402 silenced for the two imports below: this file is organized into
# section-banner blocks, and the imports here are scoped to the proxy
# 404-fallback section that follows. Moving them to the top would split
# their definition from the tests that consume them.
import logging  # noqa: E402

from backend.tests._security_helpers import attachments_root_for  # noqa: E402

_FALLBACK_PNG = b"\x89PNG\r\n\x1a\nLOCAL-CACHE-FIXTURE-BYTES-XYZ"
_TEST_ORG = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
_TEST_FILE = "ce11f00d-aaaa-bbbb-cccc-1234567890ab"
_TEST_CONV = "4503ce75-1111-2222-3333-deadbeef1234"


@pytest.fixture
def proxy_404_fallback_env(isolated_data_dir: Path, fresh_creds: Path):
    """Returns (client, attachments_root). Caller plants files under
    ``<attachments_root>/<conv>/<file>/<variant>.<ext>`` as needed.

    Reuses ``fresh_creds`` so the proxy gets past ``_load_session_cookies``
    and ``isolated_data_dir`` so the attachments root is under tmp_path
    (no pollution of the dev's real ``~/.claude-explorer/files/``).
    """
    root = attachments_root_for(isolated_data_dir)
    root.mkdir(parents=True, exist_ok=True)
    from backend.main import app
    return TestClient(app), root


def test__proxy__upstream_404_with_local_cache__returns_local_bytes(
    proxy_404_fallback_env: tuple[TestClient, Path],
) -> None:
    """PROXY-LOCAL-FALLBACK-200: upstream 404 + local cache present →
    serve local bytes with HTTP 200.

    Pre-fix this returns 404 (bug — the upstream 404 propagates
    even though we have the file on disk).
    """
    client, root = proxy_404_fallback_env

    cache_dir = root / _TEST_CONV / _TEST_FILE
    cache_dir.mkdir(parents=True)
    (cache_dir / "preview.png").write_bytes(_FALLBACK_PNG)

    upstream = MagicMock()
    upstream.status_code = 404
    upstream.content = b'{"detail":"not found"}'
    upstream.headers = {"content-type": "application/json"}

    with patch("curl_cffi.requests.get", return_value=upstream):
        resp = client.get(f"/api/{_TEST_ORG}/files/{_TEST_FILE}/preview")

    assert resp.status_code == 200, (
        f"upstream-404 + local-cache must serve local bytes (200); "
        f"got {resp.status_code}: {resp.text!r}"
    )
    assert resp.content == _FALLBACK_PNG, "served bytes must equal the cached fixture"
    assert resp.headers["content-type"].startswith("image/"), (
        f"expected image/* content-type from local cache; got {resp.headers['content-type']!r}"
    )


def test__proxy__upstream_404_local_fallback__emits_observability_log(
    proxy_404_fallback_env: tuple[TestClient, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PROXY-LOCAL-FALLBACK-OBS: a successful local fallback emits a
    structured log line so dashboards can count "served local cache"
    independently from "served upstream 200" — without this we can't
    measure claude.ai's garbage-collection rate post-V1.
    """
    client, root = proxy_404_fallback_env
    cache_dir = root / _TEST_CONV / _TEST_FILE
    cache_dir.mkdir(parents=True)
    (cache_dir / "preview.png").write_bytes(_FALLBACK_PNG)

    upstream = MagicMock()
    upstream.status_code = 404
    upstream.content = b''
    upstream.headers = {"content-type": "application/json"}

    with caplog.at_level(logging.INFO, logger="backend.routers.files"):
        with patch("curl_cffi.requests.get", return_value=upstream):
            resp = client.get(f"/api/{_TEST_ORG}/files/{_TEST_FILE}/preview")
    assert resp.status_code == 200, (
        "fallback must succeed for the log assertion to be meaningful"
    )

    fallback_records = [
        r for r in caplog.records if "proxy_local_fallback" in r.getMessage()
    ]
    assert fallback_records, (
        f"expected a log record mentioning 'proxy_local_fallback'; "
        f"got: {[r.getMessage() for r in caplog.records]!r}"
    )


# ----------------------------------------------------------------------
# Offline fallback (Phase B — 2026-05-29).
#
# When the upstream `curl_cffi.requests.get` raises (network unreachable,
# DNS timeout, claude.ai down) we previously returned 502 unconditionally
# even if the bulk fetcher had already cached the attachment bytes on
# disk. This breaks the "images keep working when offline" claim Part 2
# makes for Desktop attachments. The fix: try the SAME local cache that
# the 404 branch already uses before raising 502.
#
# Spec-driven: tests pin the three branches of the new behavior:
#   1. upstream-unreachable + cached → serve local (200)
#   2. upstream-unreachable + NOT cached → still 502 (unchanged)
#   3. metachar `file_uuid` (path-traversal attempt) + cached →
#      still 502 (security guard holds; cache lookup skipped)
# ----------------------------------------------------------------------


def test__proxy__upstream_unreachable_with_local_cache__returns_local_bytes(
    proxy_404_fallback_env: tuple[TestClient, Path],
) -> None:
    """PROXY-OFFLINE-FALLBACK-200: upstream raises (offline) + local cache
    present → serve local bytes with HTTP 200.

    Pre-fix this returns 502 (the network-failure except branch raises
    immediately without consulting the cache).
    """
    client, root = proxy_404_fallback_env

    cache_dir = root / _TEST_CONV / _TEST_FILE
    cache_dir.mkdir(parents=True)
    (cache_dir / "thumbnail.png").write_bytes(_FALLBACK_PNG)

    with patch(
        "curl_cffi.requests.get",
        side_effect=OSError("Network is unreachable"),
    ):
        resp = client.get(f"/api/{_TEST_ORG}/files/{_TEST_FILE}/thumbnail")

    assert resp.status_code == 200, (
        f"upstream-unreachable + local-cache must serve local bytes (200); "
        f"got {resp.status_code}: {resp.text!r}"
    )
    assert resp.content == _FALLBACK_PNG, "served bytes must equal the cached fixture"
    assert resp.headers["content-type"].startswith("image/"), (
        f"expected image/* content-type from local cache; got {resp.headers['content-type']!r}"
    )


def test__proxy__upstream_unreachable_local_fallback__emits_observability_log(
    proxy_404_fallback_env: tuple[TestClient, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PROXY-OFFLINE-FALLBACK-OBS: the offline fallback emits a distinct
    structured log line (reason=upstream_unreachable) so dashboards can
    separate "served local because claude.ai 404'd" from "served local
    because we were offline."
    """
    client, root = proxy_404_fallback_env
    cache_dir = root / _TEST_CONV / _TEST_FILE
    cache_dir.mkdir(parents=True)
    (cache_dir / "thumbnail.png").write_bytes(_FALLBACK_PNG)

    with caplog.at_level(logging.INFO, logger="backend.routers.files"):
        with patch(
            "curl_cffi.requests.get",
            side_effect=OSError("Network is unreachable"),
        ):
            resp = client.get(f"/api/{_TEST_ORG}/files/{_TEST_FILE}/thumbnail")
    assert resp.status_code == 200, (
        "fallback must succeed for the log assertion to be meaningful"
    )

    fallback_records = [
        r for r in caplog.records if "proxy_local_fallback" in r.getMessage()
    ]
    assert fallback_records, (
        f"expected a log record mentioning 'proxy_local_fallback'; "
        f"got: {[r.getMessage() for r in caplog.records]!r}"
    )
    # The reason field separates this code path from the 404 fallback.
    reason_records = [
        r for r in fallback_records
        if getattr(r, "reason", None) == "upstream_unreachable"
    ]
    assert reason_records, (
        f"expected a fallback log carrying reason='upstream_unreachable'; "
        f"got reasons: {[getattr(r, 'reason', None) for r in fallback_records]!r}"
    )


def test__proxy__upstream_unreachable_no_local_cache__still_502(
    proxy_404_fallback_env: tuple[TestClient, Path],
) -> None:
    """PROXY-OFFLINE-FALLBACK-NEG: upstream raises (offline) + nothing
    cached on disk → 502 (unchanged from pre-fix behavior).

    Bidirectional pair to the positive test above: proves the cache
    lookup gates the 200, not just the unconditional return path.
    """
    client, _root = proxy_404_fallback_env

    with patch(
        "curl_cffi.requests.get",
        side_effect=OSError("Network is unreachable"),
    ):
        resp = client.get(f"/api/{_TEST_ORG}/files/{_TEST_FILE}/thumbnail")

    assert resp.status_code == 502, (
        f"upstream-unreachable + no cache must still 502; "
        f"got {resp.status_code}: {resp.text!r}"
    )


def test__proxy__upstream_unreachable_metachar_file_uuid__still_502(
    proxy_404_fallback_env: tuple[TestClient, Path],
) -> None:
    """PROXY-OFFLINE-FALLBACK-SEC: a `file_uuid` containing glob
    metacharacters offline must NOT walk the attachments tree via the
    cache lookup — same Hunt #4 guard as the 404 branch. Plant a real
    cached file under a sibling directory; the request for `**` must
    NOT return its bytes.
    """
    client, root = proxy_404_fallback_env

    # Plant a legitimate cached file for some other file_uuid so the
    # glob `*/<file_uuid>/<variant>.*` would otherwise have a match.
    legit_dir = root / _TEST_CONV / _TEST_FILE
    legit_dir.mkdir(parents=True)
    (legit_dir / "thumbnail.png").write_bytes(_FALLBACK_PNG)

    with patch(
        "curl_cffi.requests.get",
        side_effect=OSError("Network is unreachable"),
    ):
        resp = client.get(f"/api/{_TEST_ORG}/files/**/thumbnail")

    # The proxy MUST refuse the traversal — neither 200 nor leaked bytes.
    # 502 is the correct fall-through when the cache lookup is skipped.
    assert resp.status_code == 502, (
        f"metachar file_uuid must not match cache via glob; "
        f"got {resp.status_code}: {resp.text!r}"
    )
    assert _FALLBACK_PNG not in resp.content, (
        "metachar file_uuid leaked sibling-cache bytes; security guard failed"
    )
