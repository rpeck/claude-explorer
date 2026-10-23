"""A single-conversation re-fetch follows the one-fetch-at-a-time rule (2026-10-23).

Refresh and /fetch/start share ``_refresh_in_progress``, so a second fetch
gets 409 (PR #11). ``POST /fetch/conversation/<uuid>`` did not take the
flag: it could run during a Refresh, and two writers could save the same
conversation file at once.
"""

from __future__ import annotations

import pytest

V2 = {
    "schema_version": 2,
    "session_key": "sk-test",
    "cf_bm": None,
    "cf_clearance": None,
    "captured_at": "2026-10-23T00:00:00Z",
    "orgs": [{"uuid": "org-A", "name": "A", "capabilities": ["chat"], "seen_in_response": True}],
    "primary_org_id": "org-A",
    "legacy_migration_target": None,
}


@pytest.fixture
def fetch_mod(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from backend.routers import fetch as mod

    monkeypatch.setattr(mod, "_refresh_in_progress", False)
    monkeypatch.setattr(mod, "load_credentials", lambda _p: dict(V2))
    monkeypatch.setattr(mod, "fetch_output_dir", lambda: tmp_path / "conversations")
    monkeypatch.setattr(mod, "fetch_files_dir", lambda: tmp_path / "files")

    async def _no_reindex():
        return None

    monkeypatch.setattr(mod, "_reindex_search_after_fetch", _no_reindex)
    return mod


def test_refused_with_409_while_another_fetch_runs(fetch_mod, client, monkeypatch) -> None:
    built: list[dict] = []

    class _Stub:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(fetch_mod, "ClaudeFetcher", _Stub)
    fetch_mod._refresh_in_progress = True

    r = client.post("/api/fetch/conversation/some-uuid")
    assert r.status_code == 409, r.text
    assert "Another fetch is already running" in r.json()["detail"]
    assert built == [], "the re-fetch must not start while another fetch runs"
    assert fetch_mod._refresh_in_progress is True, "the other fetch still owns the flag"


def test_holds_the_flag_while_it_runs_and_releases_it(fetch_mod, client, monkeypatch) -> None:
    seen: list[bool] = []

    class _Stub:
        def __init__(self, **_kwargs):
            pass

        def fetch_conversation(self, uuid):
            seen.append(fetch_mod._refresh_in_progress)
            return {"uuid": uuid, "name": "Refetched", "chat_messages": []}

        def save_conversation(self, _conv):
            pass

    monkeypatch.setattr(fetch_mod, "ClaudeFetcher", _Stub)
    r = client.post("/api/fetch/conversation/some-uuid")
    assert r.status_code == 200, r.text
    assert seen == [True], "a Refresh started now must get 409"
    assert fetch_mod._refresh_in_progress is False


def test_releases_the_flag_when_the_re_fetch_fails(fetch_mod, client, monkeypatch) -> None:
    class _Stub:
        def __init__(self, **_kwargs):
            pass

        def fetch_conversation(self, _uuid):
            raise RuntimeError("HTTP 401")

    monkeypatch.setattr(fetch_mod, "ClaudeFetcher", _Stub)
    r = client.post("/api/fetch/conversation/some-uuid")
    assert r.status_code == 401, r.text
    assert fetch_mod._refresh_in_progress is False
