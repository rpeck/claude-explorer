"""Bug B: Three-class error classification in the SSE pipeline.

The /api/fetch/refresh stream must distinguish:
  - AUTH      -> trigger capture-and-retry-fetch
  - TRANSIENT -> emit `error` event with kind=TRANSIENT, retryable=true
                 (e.g. retries exhausted on a TLS / 5xx blip)
  - TERMINAL  -> emit `error` event with kind=TERMINAL, retryable=false

The frontend uses `kind` and `retryable` to decide whether to show a
Retry button vs. a sticky "open Details" toast.

Build-9's predicate `_is_session_expired_error` covered AUTH only;
anything else fell through to a generic event with no kind. This
module pins the new contract.
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


def test_classify_error_auth() -> None:
    from backend.routers.fetch import _classify_error
    from fetcher.bulk_fetch import FetchAuthError

    assert _classify_error(FetchAuthError("401 Unauthorized")) == "AUTH"


def test_classify_error_transient() -> None:
    from backend.routers.fetch import _classify_error
    from fetcher.bulk_fetch import FetchTransientError

    assert _classify_error(FetchTransientError("HTTP 503")) == "TRANSIENT"


def test_classify_error_terminal() -> None:
    from backend.routers.fetch import _classify_error
    from fetcher.bulk_fetch import FetchTerminalError

    assert _classify_error(FetchTerminalError("schema mismatch")) == "TERMINAL"
    # Plain Exception (not a FetchError subclass) defaults to TERMINAL.
    assert _classify_error(RuntimeError("disk full")) == "TERMINAL"


def test_classify_error_legacy_string_auth() -> None:
    """Legacy string-based 401/403/cf-mitigated still classifies as AUTH."""
    from backend.routers.fetch import _classify_error

    assert _classify_error(RuntimeError("401 Client Error: Unauthorized")) == "AUTH"
    assert _classify_error(RuntimeError("403 Forbidden cf-mitigated")) == "AUTH"


def test_transient_error_does_not_trigger_capture(
    client: TestClient,
    isolated_creds: Path,
    isolated_output: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network blip after retry exhaustion must NOT pop the capture browser."""
    from fetcher.bulk_fetch import FetchTransientError

    capture_calls = {"n": 0}

    async def fake_capture(timeout: int = 300, headless: bool = False) -> dict:
        capture_calls["n"] += 1
        return {"session_key": "sk", "org_id": "org"}

    monkeypatch.setattr(
        "backend.routers.fetch.capture_credentials", fake_capture, raising=False
    )

    def fail_transient(self):
        raise FetchTransientError("HTTP 503 after 3 attempts")

    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation_list",
        fail_transient,
        raising=False,
    )

    response = client.get("/api/fetch/refresh?incremental=true")
    assert response.status_code == 200
    events = _parse_sse_events(response.text)

    assert capture_calls["n"] == 0, "capture must NOT run for transient errors"

    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events, f"expected an error event, got {events}"
    err = error_events[-1]
    assert err.get("kind") == "TRANSIENT", err
    assert err.get("retryable") is True, err


def test_auth_error_classified_with_kind_after_capture_retry_fails(
    client: TestClient,
    isolated_creds: Path,
    isolated_output: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If capture runs and the post-capture fetch is STILL auth-blocked,
    the final error event must carry kind=AUTH so the toast is sticky."""
    from fetcher.bulk_fetch import FetchAuthError

    async def fake_capture(timeout: int = 300, headless: bool = False, **kwargs) -> dict:
        # CredentialsV2 shape (post-cowork-multi-org C2).
        org_id = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
        return {
            "schema_version": 2,
            "session_key": "sk2",
            "cf_bm": None,
            "cf_clearance": None,
            "captured_at": "2026-05-01T00:00:00+00:00",
            "orgs": [{"uuid": org_id, "name": None, "capabilities": [], "seen_in_response": False}],
            "primary_org_id": org_id,
            "legacy_migration_target": org_id,
            "org_id": org_id,
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
    assert final.get("kind") == "AUTH"
    assert final.get("retryable") is False


def test_terminal_error_classified_with_kind(
    client: TestClient,
    isolated_creds: Path,
    isolated_output: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-AUTH non-TRANSIENT failure carries kind=TERMINAL."""
    from fetcher.bulk_fetch import FetchTerminalError

    def fail_terminal(self):
        raise FetchTerminalError("JSON decode error: unexpected end of input")

    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation_list",
        fail_terminal,
        raising=False,
    )

    response = client.get("/api/fetch/refresh?incremental=true")
    assert response.status_code == 200
    events = _parse_sse_events(response.text)

    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events
    final = error_events[-1]
    assert final.get("kind") == "TERMINAL"
    assert final.get("retryable") is False


def test_retry_progress_event_emitted(
    client: TestClient,
    isolated_creds: Path,
    isolated_output: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry events recorded by the fetcher are surfaced as `progress` SSE events."""

    def fake_list(self):
        # Simulate that the fetcher had to retry once before succeeding.
        self.retry_events.append(
            {
                "attempt": 1,
                "max_attempts": 3,
                "error": "ssl handshake failed",
                "message": "Network hiccup; retrying (1 of 2)...",
            }
        )
        return []

    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation_list",
        fake_list,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.save_index",
        lambda self, conversations: None,
        raising=False,
    )

    response = client.get("/api/fetch/refresh?incremental=true")
    assert response.status_code == 200
    events = _parse_sse_events(response.text)

    hiccup_events = [
        e for e in events
        if e.get("type") == "progress" and "hiccup" in (e.get("message") or "").lower()
    ]
    assert hiccup_events, f"expected hiccup progress event, got {events}"
