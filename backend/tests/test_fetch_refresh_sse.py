"""SSE shape/order/payload tests for ``GET /api/fetch/refresh``.

Most of the refresh pipeline contract is already exercised by
``test_refresh_pipeline.py`` (capture-flow, post-capture retry, 409
concurrency, 0o600 perms). This file fills the strong-assertion gaps
called out in P2.2 of the backend test enhancement plan:

* Per-type payload SHAPE assertions (negative-space: extra envelope
  fields are absent).
* Wire-format reality (per TESTING.md 5.6): the server emits
  ``data: {json}\\n\\n`` only — no ``event:`` headers; ``: ping``
  keep-alives are SSE comments and must be skipped by the parser.
* Envelope asymmetry: ``/api/fetch/refresh`` capture-error events use
  the LEGACY ``{type, message}`` envelope (fetch.py:992-1000), NOT the
  ``{kind, retryable, message}`` envelope used by the fetch-phase auth
  error path. Pin both shapes so a future "let's unify" refactor
  surfaces here, not in client code that breaks silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient



_TEST_ORG_ID = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"


def _v2_creds():
    return {
        "schema_version": 2,
        "session_key": "sk-test",
        "cf_bm": None,
        "cf_clearance": None,
        "captured_at": "2026-05-01T00:00:00+00:00",
        "orgs": [{
            "uuid": _TEST_ORG_ID,
            "name": "Personal",
            "capabilities": ["chat"],
            "seen_in_response": True,
        }],
        "primary_org_id": _TEST_ORG_ID,
        "legacy_migration_target": _TEST_ORG_ID,
        "org_id": _TEST_ORG_ID,
    }


def _parse_data_frames(body: str) -> list[dict]:
    """Parse ``data: {json}\\n\\n`` frames; skip ``:`` SSE-comment keep-alives."""
    out: list[dict] = []
    for chunk in body.split("\n\n"):
        line = chunk.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            try:
                out.append(json.loads(line[len("data:"):].strip()))
            except json.JSONDecodeError:
                pass
    return out


@pytest.fixture
def _refresh_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_v2_creds()))
    out = tmp_path / "conversations"
    out.mkdir()
    files = tmp_path / "files"
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds, raising=True
    )
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_OUTPUT_DIR", out, raising=True
    )
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_FILES_DIR", files, raising=True
    )
    yield {"creds": creds, "out": out}


def test__refresh__creds_present__event_order_and_payload_shapes(
    client: TestClient, _refresh_env, monkeypatch
):
    """RFR-SSE-ORDER (P2.2). Creds present → event order: start, progress*, complete.

    Negative space: NO capture_* events, NO error events. Payload shapes pinned
    per type (start has total; progress has current/total; complete has
    current==total). Patches methods on ClaudeFetcher (per the existing
    test_refresh_pipeline.py pattern) rather than replacing the class.
    """
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.existing_pairs",
        lambda self: set(),
        raising=False,
    )
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation_list",
        lambda self: [],
        raising=False,
    )
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.save_index",
        lambda self, conversations, **_kw: None,
        raising=False,
    )

    r = client.get("/api/fetch/refresh?incremental=true")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers.get("cache-control", "").lower() == "no-cache"

    events = _parse_data_frames(r.text)
    types = [e["type"] for e in events]

    # Order: start, progress* (>=1), complete. No capture_* or error.
    assert types[0] == "start", f"first event must be 'start'; got {types!r}"
    assert types[-1] == "complete", f"last event must be 'complete'; got {types!r}"
    assert "error" not in types, f"happy path must emit no error; got {types!r}"
    assert not any(t.startswith("capture_") for t in types), (
        f"creds-present path must NOT emit capture_* events; got {types!r}"
    )
    assert types.count("progress") >= 1, f"need >=1 progress; got {types!r}"

    by_type: dict[str, list[dict]] = {}
    for e in events:
        by_type.setdefault(e["type"], []).append(e)
    start = by_type["start"][0]
    assert "total" in start, f"start must carry 'total'; got {start!r}"
    last_progress = by_type["progress"][-1]
    assert {"current", "total"} <= set(last_progress.keys()), (
        f"progress must carry current+total; got {last_progress!r}"
    )
    complete = by_type["complete"][-1]
    assert complete.get("current") == complete.get("total"), (
        f"complete.current must equal complete.total; got {complete!r}"
    )


def test__refresh__capture_failure__legacy_error_envelope(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """RFR-CAPTURE-ERR-ENV (P2.2). Capture failure uses LEGACY {type, message}
    envelope at fetch.py:992-1000 — NOT the {kind, retryable, message} envelope
    the fetch-phase auth error path uses.

    Pin both presence (type, message) AND absence (kind, retryable) so a
    future envelope unification surfaces here loudly.
    """

    creds = tmp_path / "credentials.json"  # does NOT exist
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds, raising=True
    )

    async def _failing_capture(*_args, **_kwargs):
        raise RuntimeError("browser closed mid-login")

    # Patch the value-imported binding, NOT the source module — fetch.py
    # imports `from fetcher.playwright_capture import capture_credentials`
    # at module load (line 27), so the local copy is what _run_capture_with_keepalive
    # actually awaits.
    monkeypatch.setattr(
        "backend.routers.fetch.capture_credentials",
        _failing_capture,
        raising=False,
    )

    r = client.get("/api/fetch/refresh?incremental=true")
    assert r.status_code == 200
    events = _parse_data_frames(r.text)

    err = next((e for e in events if e["type"] == "error"), None)
    assert err is not None, f"capture failure must emit error event; got {events!r}"
    assert "Capture failed" in err.get("message", ""), (
        f"capture-error message must lead with 'Capture failed:'; got {err!r}"
    )
    assert "kind" not in err, (
        f"capture errors use LEGACY envelope; 'kind' must be absent. got {err!r}"
    )
    assert "retryable" not in err, (
        f"capture errors use LEGACY envelope; 'retryable' must be absent. got {err!r}"
    )


def test__refresh__creds_present__refresh_flag_clears_after_stream(
    client: TestClient, _refresh_env, monkeypatch
):
    """RFR-FLAG-CLEAR (P2.2). After a successful refresh stream completes,
    _refresh_in_progress is False; a subsequent request returns 200, not 409.

    Defends against a finally-block regression that could leak the flag
    set across requests (the conftest reset_refresh_flag autouse fixture
    masks this in tests; we explicitly assert flag state here)."""
    from backend.routers import fetch as fetch_mod

    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.existing_pairs",
        lambda self: set(),
        raising=False,
    )
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation_list",
        lambda self: [],
        raising=False,
    )
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.save_index",
        lambda self, conversations, **_kw: None,
        raising=False,
    )

    r1 = client.get("/api/fetch/refresh?incremental=true")
    assert r1.status_code == 200
    # Flag must be clear after stream consumption (we read full body via TestClient).
    assert fetch_mod._refresh_in_progress is False, (
        "refresh flag must be False after stream completes"
    )
    # Sequential second request returns 200 (not 409).
    r2 = client.get("/api/fetch/refresh?incremental=true")
    assert r2.status_code == 200
