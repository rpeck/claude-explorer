"""A1 hunt: stable error-vocabulary persistence in _index.json.

Bug: `routers/fetch.py:683` calls `_classify_error(exc)` (returns clean
`Literal["AUTH","TRANSIENT","TERMINAL"]`), then DISCARDS that result and
re-derives `"HTTP_401"/"HTTP_403"/"HTTP_404"` from `str(exc)` six lines
later. Those HTTP_*** strings get persisted to disk via `save_index()`
AND used by the rollup at lines 918-936 to pick an SSE bucket.

Fix: persist `(error_kind, http_status)` — a stable domain vocabulary —
instead of the ad-hoc `HTTP_***` strings. The rollup switches on
`error_kind`, not string-matching. The frontend already keys off SSE
`kind` (not on-disk `error_code`), so no UI-side migration is needed.

Legacy on-disk records: only the in-memory `org_results` list within a
single request flows through the rollup, so a defensive read-time
tolerance for legacy `error_code` keeps the rollup robust if any
older-shape record sneaks through.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _parse_sse_events(text: str) -> list[dict]:
    events: list[dict] = []
    for raw in text.split("\n\n"):
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                pass
    return events


@pytest.fixture
def isolated_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"session_key": "sk", "org_id": "org"}))
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds, raising=True
    )
    return creds


@pytest.fixture
def isolated_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    out = tmp_path / "conversations"
    out.mkdir()
    monkeypatch.setattr("backend.routers.fetch.fetch_output_dir", lambda _v=out: _v)
    monkeypatch.setattr("backend.routers.fetch.fetch_files_dir", lambda _v=tmp_path / "files": _v)
    return out


# ---------------------------------------------------------------------------
# Domain vocabulary surface
# ---------------------------------------------------------------------------


def test_persisted_error_kind_vocab_is_canonical() -> None:
    """The canonical persisted vocabulary lives in `fetcher.bulk_fetch`.

    Must include all five members: AUTH_EXPIRED, ORG_FORBIDDEN,
    ORG_NOT_FOUND, TRANSIENT, TERMINAL.
    """
    from fetcher.bulk_fetch import PERSISTED_ERROR_KINDS

    assert PERSISTED_ERROR_KINDS == frozenset({
        "AUTH_EXPIRED",
        "ORG_FORBIDDEN",
        "ORG_NOT_FOUND",
        "TRANSIENT",
        "TERMINAL",
    })


def test_kind_from_http_status_mapping() -> None:
    """Mapping HTTP status -> persisted error kind is stable + total."""
    from fetcher.bulk_fetch import kind_from_http_status

    assert kind_from_http_status(401) == "AUTH_EXPIRED"
    assert kind_from_http_status(403) == "ORG_FORBIDDEN"
    assert kind_from_http_status(404) == "ORG_NOT_FOUND"
    # 5xx and unknown -> caller decides; helper returns None to signal
    # "this isn't an HTTP-status-derived kind".
    assert kind_from_http_status(500) is None
    assert kind_from_http_status(None) is None


def test_legacy_error_code_migration_read_time() -> None:
    """`migrate_legacy_error_code` maps old HTTP_*** strings -> (kind, status)."""
    from fetcher.bulk_fetch import migrate_legacy_error_code

    assert migrate_legacy_error_code("HTTP_401") == ("AUTH_EXPIRED", 401)
    assert migrate_legacy_error_code("HTTP_403") == ("ORG_FORBIDDEN", 403)
    assert migrate_legacy_error_code("HTTP_404") == ("ORG_NOT_FOUND", 404)
    assert migrate_legacy_error_code("TRANSIENT") == ("TRANSIENT", None)
    # Unknown legacy value -> TERMINAL fallback.
    assert migrate_legacy_error_code("RuntimeError") == ("TERMINAL", None)
    # None / empty -> None tuple (no info).
    assert migrate_legacy_error_code(None) == (None, None)
    assert migrate_legacy_error_code("") == (None, None)


# ---------------------------------------------------------------------------
# Persisted shape: save_index writes the new vocabulary, NOT HTTP_***.
# ---------------------------------------------------------------------------


PERSONAL_UUID = "11111111-1111-1111-1111-111111111111"


def _make_fetcher(tmp_path: Path):
    """Minimal ClaudeFetcher with a single org for save_index tests."""
    from fetcher.bulk_fetch import ClaudeFetcher

    return ClaudeFetcher(
        session_key="sk",
        orgs=[{
            "uuid": PERSONAL_UUID,
            "name": "Personal",
            "capabilities": ["chat"],
            "seen_in_response": True,
        }],
        primary_org_id=PERSONAL_UUID,
        output_dir=tmp_path / "conversations",
        files_dir=tmp_path / "files",
        download_files=False,
        delay=0.0,
    )


def test_save_index_persists_error_kind_and_http_status_for_403(tmp_path: Path) -> None:
    """A 403 on the org list must persist (ORG_FORBIDDEN, 403), not HTTP_403."""
    fetcher = _make_fetcher(tmp_path)
    with fetcher._scoped_org(fetcher.orgs[0]):
        fetcher.save_index(
            [],
            status="skipped",
            error_kind="ORG_FORBIDDEN",
            http_status=403,
            error_message="403 Forbidden",
        )

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    org = idx["orgs"][0]
    assert org["error_kind"] == "ORG_FORBIDDEN"
    assert org["http_status"] == 403
    # Bidirectional: the legacy ad-hoc string MUST be absent.
    assert "error_code" not in org or org["error_code"] is None
    # And in particular the literal HTTP_*** string MUST NOT appear.
    assert org.get("error_kind") != "HTTP_403"


def test_save_index_persists_error_kind_for_transient(tmp_path: Path) -> None:
    """A 5xx blip persists (TRANSIENT, None) — http_status optional."""
    fetcher = _make_fetcher(tmp_path)
    with fetcher._scoped_org(fetcher.orgs[0]):
        fetcher.save_index(
            [],
            status="failed",
            error_kind="TRANSIENT",
            http_status=None,
            error_message="HTTP 503 after 3 attempts",
        )

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    org = idx["orgs"][0]
    assert org["error_kind"] == "TRANSIENT"
    assert org["http_status"] is None


def test_save_index_persists_kind_terminal_for_unexpected(tmp_path: Path) -> None:
    """An unexpected exception persists (TERMINAL, None)."""
    fetcher = _make_fetcher(tmp_path)
    with fetcher._scoped_org(fetcher.orgs[0]):
        fetcher.save_index(
            [],
            status="failed",
            error_kind="TERMINAL",
            http_status=None,
            error_message="JSON decode error",
        )

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    org = idx["orgs"][0]
    assert org["error_kind"] == "TERMINAL"
    assert org["http_status"] is None


def test_save_index_ok_status_clears_error_fields(tmp_path: Path) -> None:
    """A successful run must NOT carry any error_kind / http_status."""
    fetcher = _make_fetcher(tmp_path)
    with fetcher._scoped_org(fetcher.orgs[0]):
        fetcher.save_index([], status="ok")

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    org = idx["orgs"][0]
    assert org["status"] == "ok"
    assert org["error_kind"] is None
    assert org["http_status"] is None


# ---------------------------------------------------------------------------
# Rollup: switches on error_kind (new path) AND tolerates legacy error_code.
# ---------------------------------------------------------------------------


def test_rollup_buckets_auth_expired_to_auth(
    client: TestClient,
    isolated_creds: Path,
    isolated_output: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An AUTH_EXPIRED finding on the only org -> SSE bucket = "auth"."""
    from fetcher.bulk_fetch import FetchAuthError

    # Disable capture so the test doesn't try to launch playwright.
    async def fake_capture(timeout: int = 300, headless: bool = False, **kwargs) -> dict:
        # CredentialsV2 shape, same org id so retry hits the same 401.
        return {
            "schema_version": 2,
            "session_key": "sk2",
            "cf_bm": None,
            "cf_clearance": None,
            "captured_at": "2026-05-01T00:00:00+00:00",
            "orgs": [{"uuid": "org", "name": None, "capabilities": [], "seen_in_response": False}],
            "primary_org_id": "org",
            "legacy_migration_target": "org",
            "org_id": "org",
        }

    monkeypatch.setattr(
        "backend.routers.fetch.capture_credentials", fake_capture, raising=False
    )

    def always_401(self):
        raise FetchAuthError("401 Client Error: Unauthorized")

    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation_list",
        always_401,
        raising=False,
    )

    response = client.get("/api/fetch/refresh?incremental=true")
    assert response.status_code == 200
    events = _parse_sse_events(response.text)

    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events
    final = error_events[-1]
    # Stream-level bucket maps AUTH_EXPIRED to AUTH semantic kind.
    assert final.get("kind") == "AUTH"


def test_rollup_buckets_transient_to_transient(
    client: TestClient,
    isolated_creds: Path,
    isolated_output: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TRANSIENT finding on the only org -> SSE bucket = "transient"."""
    from fetcher.bulk_fetch import FetchTransientError

    def always_503(self):
        raise FetchTransientError("HTTP 503 after 3 attempts")

    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation_list",
        always_503,
        raising=False,
    )

    response = client.get("/api/fetch/refresh?incremental=true")
    assert response.status_code == 200
    events = _parse_sse_events(response.text)

    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events
    final = error_events[-1]
    assert final.get("kind") == "TRANSIENT"


def test_rollup_tolerates_legacy_error_code_records() -> None:
    """The rollup helper must accept a legacy {error_code: "HTTP_401"} record
    without an `error_kind` field (defensive read-time migration).
    """
    from backend.routers.fetch import _rollup_bucket_for

    legacy_record = {
        "org_id": "org",
        "status": "skipped",
        "error_code": "HTTP_401",
        "error_message": "401 Unauthorized",
    }
    bucket, msg = _rollup_bucket_for(legacy_record)
    assert bucket == "auth"
    assert "401" in (msg or "")


def test_rollup_tolerates_legacy_transient_error_code() -> None:
    """Legacy {error_code: "TRANSIENT"} -> "transient" bucket."""
    from backend.routers.fetch import _rollup_bucket_for

    legacy_record = {
        "org_id": "org",
        "status": "failed",
        "error_code": "TRANSIENT",
        "error_message": "blip",
    }
    bucket, _msg = _rollup_bucket_for(legacy_record)
    assert bucket == "transient"


def test_rollup_legacy_http_403_buckets_to_fatal_or_auth() -> None:
    """Legacy HTTP_403 -> "auth" bucket (was "fatal" pre-fix; the cleaner
    classification is that 403 from Anthropic/CF is an auth failure).
    """
    from backend.routers.fetch import _rollup_bucket_for

    legacy_record = {
        "org_id": "org",
        "status": "skipped",
        "error_code": "HTTP_403",
        "error_message": "403 cf-mitigated",
    }
    bucket, _ = _rollup_bucket_for(legacy_record)
    # 403 from Anthropic is an auth-class failure (per
    # `classify_fetch_error` at routers/fetch.py:148).
    assert bucket == "auth"


def test_rollup_prefers_new_error_kind_over_legacy_field() -> None:
    """When both are present (post-migration in-flight), the new field wins."""
    from backend.routers.fetch import _rollup_bucket_for

    mixed_record = {
        "org_id": "org",
        "status": "skipped",
        "error_kind": "TRANSIENT",
        "http_status": None,
        "error_code": "HTTP_401",  # legacy, conflicting
        "error_message": "x",
    }
    bucket, _ = _rollup_bucket_for(mixed_record)
    assert bucket == "transient"


def test_rollup_unknown_kind_defaults_to_fatal() -> None:
    """An unknown error_kind (forward-compat) defaults to "fatal"."""
    from backend.routers.fetch import _rollup_bucket_for

    record = {
        "org_id": "org",
        "status": "failed",
        "error_kind": "SOMETHING_NEW",
        "error_message": "x",
    }
    bucket, _ = _rollup_bucket_for(record)
    assert bucket == "fatal"
