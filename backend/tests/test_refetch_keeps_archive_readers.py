"""The list, the viewer, and search still show content that a re-fetch kept (#51).

``fetcher/tests/test_refetch_keeps_archive.py`` pins the merged files. These
tests pin what the user sees: after the server stops returning old messages
(Desktop) or old events (remote Cowork task), the archive's copy of them
still counts in the Conversation List, opens in the Conversation Pane, and
is found by search, on the fast FTS5 path and on the linear path.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fetcher.tests.ccr_fake import (
    FakeSession,
    FakeSessionsApi,
    assistant_event,
    iso,
    user_event,
)

pytestmark = pytest.mark.xfail(strict=True, reason="B51 not implemented yet")

ORG = "11111111-1111-4111-8111-111111111111"
CONV = "c0ffee00-0000-4000-8000-000000000052"
SID = "cse_01KeepArchiveReaders"

# One word that only the messages and events the server drops contain.
DESKTOP_WORD = "quokkavolume"
TASK_WORD = "wombatledger"


def _ts(minute: int) -> str:
    return f"2026-10-01T10:{minute:02d}:00Z"


def _msg(uuid: str, minute: int, text: str, sender: str, parent: str | None) -> dict:
    return {
        "uuid": uuid,
        "sender": sender,
        "text": text,
        "content": [{"type": "text", "text": text}],
        "created_at": _ts(minute),
        "updated_at": _ts(minute),
        "parent_message_uuid": parent,
        "attachments": [],
        "files": [],
        "files_v2": [],
        "truncated": False,
    }


D1 = _msg("d1", 1, f"how do I mount the {DESKTOP_WORD} share", "human", None)
D2 = _msg("d2", 2, "use the NFS export", "assistant", "d1")
D3 = _msg("d3", 3, "and the snapshots?", "human", "d2")
D4 = _msg("d4", 4, "take them daily", "assistant", "d3")
D5 = _msg("d5", 5, "and an offsite copy?", "human", "d4")
D6 = _msg("d6", 6, "use a second NAS", "assistant", "d5")


def _conv(messages: list[dict], updated: int) -> dict:
    return {
        "uuid": CONV,
        "name": "NAS mount question",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": _ts(0),
        "updated_at": _ts(updated),
        "current_leaf_message_uuid": messages[-1]["uuid"],
        "is_starred": False,
        "chat_messages": messages,
    }


ARCHIVED = _conv([D1, D2, D3, D4], updated=4)
#: The server's copy now: it no longer returns d1 and d2.
SERVER_NOW = _conv([D3, D4, D5, D6], updated=6)


def _fetcher(out: Path, incremental: bool = True):
    from fetcher.bulk_fetch import ClaudeFetcher

    return ClaudeFetcher(
        session_key="sk-test-fake-key",
        orgs=[{"uuid": ORG, "name": "Personal", "capabilities": ["chat"], "seen_in_response": True}],
        primary_org_id=ORG,
        output_dir=out,
        files_dir=out.parent / "files",
        download_files=False,
        incremental=incremental,
        delay=0.0,
    )


def _remote(out: Path, incremental: bool):
    from fetcher.remote_sessions import RemoteCoworkFetcher

    return RemoteCoworkFetcher(lambda *a, **k: None, out, incremental=incremental, delay=0)


def _task_events_archived() -> list[dict]:
    return [
        user_event(1, f"reconcile the {TASK_WORD} for October"),
        assistant_event(2, "the October totals match"),
        user_event(3, "now November"),
        assistant_event(4, "November is short by 12"),
    ]


def _task_events_now() -> list[dict]:
    return [
        user_event(3, "now November"),
        assistant_event(4, "November is short by 12"),
        user_event(5, "find the missing entry"),
        assistant_event(6, "it is the 14 November refund"),
    ]


def _store(tmp_path: Path, out: Path):
    from backend.store import ConversationStore

    return ConversationStore(
        data_dir=out, claude_dir=tmp_path / "claude", cowork_root=tmp_path / "no-local-cowork"
    )


@pytest.fixture
def out(tmp_path: Path) -> Path:
    path = tmp_path / "conversations"
    path.mkdir()
    (tmp_path / "claude").mkdir()
    return path


@pytest.fixture
def task(out: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    """A remote task archived with events 1-4; the server now returns 3-6."""
    session = FakeSession(SID, "Ledger check", _task_events_archived(), updated_at=iso(4))
    FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    _remote(out, incremental=True).sync_org(ORG)
    session.events = _task_events_now()
    session.updated_at = iso(6)
    return session


def _refetch_desktop(out: Path) -> None:
    _fetcher(out).save_conversation(copy.deepcopy(ARCHIVED))
    _fetcher(out).save_conversation(copy.deepcopy(SERVER_NOW))


def _search_hits(store, word: str) -> set[str]:
    from backend.search import search_conversations

    return {r.conversation_uuid for r in search_conversations(store, word).results}


def _fts_hits(tmp_path: Path, store, word: str, rebuild_after) -> set[tuple[str, str]]:
    """Index the archive, run ``rebuild_after`` (the re-fetch), index again, query."""
    from backend.search_index import SearchIndex, build_full_index, fts5_available

    if not fts5_available():
        pytest.skip("sqlite3 without FTS5")
    index = SearchIndex(tmp_path / "search-index.sqlite")
    try:
        build_full_index(store, index=index)
        rebuild_after()
        build_full_index(store, index=index)
        return {(r["conv_uuid"], r["message_uuid"]) for r in index.query(word)}
    finally:
        index.close()


# ------------------------------------------------------------------ Desktop


def test_desktop_kept_messages_show_in_the_list_count(tmp_path: Path, out: Path) -> None:
    _refetch_desktop(out)

    listed = {c.uuid: c for c in _store(tmp_path, out).list_conversations(source="CLAUDE_AI")}
    assert listed[CONV].message_count == 6
    assert listed[CONV].human_message_count == 3


def test_desktop_kept_messages_show_in_the_viewer(tmp_path: Path, out: Path) -> None:
    _refetch_desktop(out)

    detail = _store(tmp_path, out).get_conversation(CONV)
    assert detail is not None
    assert [m.uuid for m in detail.messages] == ["d1", "d2", "d3", "d4", "d5", "d6"]
    assert DESKTOP_WORD in detail.messages[0].text


def test_desktop_kept_messages_are_found_by_search(tmp_path: Path, out: Path) -> None:
    _fetcher(out).save_conversation(copy.deepcopy(ARCHIVED))
    store = _store(tmp_path, out)

    fts = _fts_hits(
        tmp_path, store, DESKTOP_WORD,
        lambda: _fetcher(out).save_conversation(copy.deepcopy(SERVER_NOW)),
    )

    assert (CONV, "d1") in fts
    assert CONV in _search_hits(store, DESKTOP_WORD)


# ------------------------------------------------------- remote Cowork task


def test_remote_task_kept_events_show_in_the_list_and_the_viewer(
    tmp_path: Path, out: Path, task: FakeSession
) -> None:
    _remote(out, incremental=False).sync_org(ORG)

    store = _store(tmp_path, out)
    listed = {c.uuid: c for c in store.list_conversations(source="CLAUDE_COWORK")}
    assert listed[SID].message_count == 6
    detail = store.get_conversation(SID)
    assert detail is not None
    assert len(detail.messages) == 6
    assert TASK_WORD in detail.messages[0].text
    assert detail.messages[-1].text == "it is the 14 November refund"


def test_remote_task_kept_events_are_found_by_search(
    tmp_path: Path, out: Path, task: FakeSession
) -> None:
    store = _store(tmp_path, out)

    fts = _fts_hits(
        tmp_path, store, TASK_WORD, lambda: _remote(out, incremental=False).sync_org(ORG)
    )

    assert SID in {conv for conv, _msg_uuid in fts}
    assert SID in _search_hits(store, TASK_WORD)


# ------------------------------------------------------------------- routes


@pytest.fixture
def routes(tmp_path: Path, out: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the fetch routes at ``out``, with a server that returns SERVER_NOW."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({
        "schema_version": 2,
        "session_key": "sk-test-fake-key",
        "cf_bm": None,
        "cf_clearance": None,
        "captured_at": "2026-10-13T15:47:00+00:00",
        "orgs": [{"uuid": ORG, "name": "Personal", "capabilities": ["chat"], "seen_in_response": True}],
        "primary_org_id": ORG,
        "legacy_migration_target": ORG,
        "org_id": ORG,
    }))
    monkeypatch.setattr("backend.routers.fetch.DEFAULT_CREDENTIALS_PATH", creds)
    monkeypatch.setattr("backend.routers.fetch.fetch_output_dir", lambda _v=out: _v)
    monkeypatch.setattr("backend.routers.fetch.fetch_files_dir", lambda _v=tmp_path / "files": _v)
    listing = {k: SERVER_NOW[k] for k in ("uuid", "name", "updated_at")}
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation_list", lambda self: [dict(listing)]
    )
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.fetch_conversation",
        lambda self, uuid: copy.deepcopy(SERVER_NOW),
    )
    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher.download_conversation_files",
        lambda self, conv, uuid: conv,
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.routers.fetch.asyncio.sleep", _no_sleep)
    _fetcher(out).save_conversation(copy.deepcopy(ARCHIVED))
    return out


def test_full_refresh_route_keeps_desktop_messages_and_task_events(
    tmp_path: Path, client: TestClient, routes: Path, task: FakeSession
) -> None:
    r = client.get("/api/fetch/start?incremental=false")

    assert r.status_code == 200
    store = _store(tmp_path, routes)
    desktop = store.get_conversation(CONV)
    assert desktop is not None
    assert [m.uuid for m in desktop.messages] == ["d1", "d2", "d3", "d4", "d5", "d6"]
    remote = store.get_conversation(SID)
    assert remote is not None
    assert len(remote.messages) == 6


def test_single_conversation_refetch_route_keeps_messages(
    tmp_path: Path, client: TestClient, routes: Path
) -> None:
    r = client.post(f"/api/fetch/conversation/{CONV}")

    assert r.status_code == 200
    detail = _store(tmp_path, routes).get_conversation(CONV)
    assert detail is not None
    assert [m.uuid for m in detail.messages] == ["d1", "d2", "d3", "d4", "d5", "d6"]
