"""A Claude Code session is identified by its file name (2026-10-21).

A continued Claude Code session gets a new file, ``<new id>.jsonl``. Its
first few rows can carry the session id of the session it continues, and
all its other rows carry the new id. The reader took the conversation uuid
from the first user row, so the new file claimed the OLD session's uuid:

- The list kept one row per uuid, so one of the two sessions was hidden.
  On one real archive, 98 files were affected; one hidden session had
  2,545 rows.
- The search index stored the new file's messages under the old uuid. A
  click on such a hit opened the old file, which does not contain the
  message, so nothing happened (335 of about 6,000 hits).

On that archive, the file name equals the session id of most rows in every
regular file, and Claude Code names each file after its own session id.
So the file name is the uuid, in the list, the detail view, the summary
cache, and the search index.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

OLD = "11111111-1111-4111-8111-111111111111"
NEW = "22222222-2222-4222-8222-222222222222"


def _row(kind: str, uuid: str, parent: str | None, session_id: str, text: str, ts: str) -> dict:
    row = {
        "type": kind,
        "uuid": uuid,
        "parentUuid": parent,
        "sessionId": session_id,
        "cwd": "/tmp/proj",
        "gitBranch": "main",
        "version": "test",
        "timestamp": ts,
    }
    if kind == "user":
        row["message"] = {"role": "user", "content": text}
    else:
        row["message"] = {
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "id": f"msg_{uuid}",
            "content": [{"type": "text", "text": text}],
        }
    return row


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


@pytest.fixture
def continued(tmp_path: Path) -> dict:
    """An original session and a continuation whose first row is copied."""
    projects = tmp_path / "claude" / "projects" / "-tmp-proj"
    _write(projects / f"{OLD}.jsonl", [
        _row("user", "old-u1", None, OLD, "original question about pelicans", "2026-10-01T10:00:00Z"),
        _row("assistant", "old-a1", "old-u1", OLD, "original answer", "2026-10-01T10:00:05Z"),
    ])
    _write(projects / f"{NEW}.jsonl", [
        # Copied from the original session, with the original's id.
        _row("user", "copy-u1", None, OLD, "original question about pelicans", "2026-10-02T09:00:00Z"),
        # The continuation's own rows.
        _row("user", "new-u1", "copy-u1", NEW, "follow-up about flamingos", "2026-10-02T09:01:00Z"),
        _row("assistant", "new-a1", "new-u1", NEW, "flamingo answer", "2026-10-02T09:01:05Z"),
    ])
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return {"claude_dir": tmp_path / "claude", "data_dir": data_dir, "tmp": tmp_path}


def _store(env: dict):
    from backend.store import ConversationStore

    return ConversationStore(data_dir=env["data_dir"], claude_dir=env["claude_dir"])


def test_the_list_shows_both_sessions(continued) -> None:
    rows = _store(continued).list_conversations(source="CLAUDE_CODE")
    assert sorted(r.uuid for r in rows) == sorted([OLD, NEW])


def test_each_session_opens_its_own_file(continued) -> None:
    store = _store(continued)
    old = store.get_conversation(OLD)
    new = store.get_conversation(NEW)
    assert old is not None and new is not None
    assert {m.uuid for m in old.messages} == {"old-u1", "old-a1"}
    assert {"new-u1", "new-a1"} <= {m.uuid for m in new.messages}


def test_a_search_hit_opens_the_conversation_that_holds_it(continued) -> None:
    from backend.search import search_conversations

    store = _store(continued)
    response = search_conversations(store, "flamingos")
    (result,) = response.results
    assert result.conversation_uuid == NEW
    detail = store.get_conversation(result.conversation_uuid)
    assert detail is not None
    assert {m.message_uuid for m in result.matching_messages} <= {m.uuid for m in detail.messages}


def test_the_search_index_stores_each_file_under_its_own_uuid(continued) -> None:
    from backend.search_index import SearchIndex, build_full_index, fts5_available

    if not fts5_available():
        pytest.skip("sqlite3 without FTS5")
    index_path = continued["tmp"] / "index" / "search-index.sqlite"
    index = SearchIndex(index_path)
    try:
        build_full_index(_store(continued), index=index)
        hits = index.query("flamingos")
    finally:
        index.close()
    assert {h["conv_uuid"] for h in hits} == {NEW}


def test_the_search_index_repairs_a_continuation_stored_under_the_old_uuid(continued) -> None:
    """An index built before this fix holds the continuation under the old
    uuid. Its file does not change, so the drift pass alone never re-reads
    it; the index must re-read it once on open."""
    from backend.claude_code_reader import read_claude_code_conversation
    from backend.search_index import SearchIndex, build_full_index, fts5_available

    if not fts5_available():
        pytest.skip("sqlite3 without FTS5")
    index_path = continued["tmp"] / "index" / "search-index.sqlite"
    projects = continued["claude_dir"] / "projects" / "-tmp-proj"

    # Seed what the old reader wrote: both files under the OLD uuid.
    index = SearchIndex(index_path)
    try:
        for name in (OLD, NEW):
            path = projects / f"{name}.jsonl"
            conv = read_claude_code_conversation(path)
            conv["uuid"] = OLD
            index.upsert_conversation(conv, path, path.stat().st_mtime)
    finally:
        index.close()

    index = SearchIndex(index_path)
    try:
        build_full_index(_store(continued), index=index)
    finally:
        index.close()

    with closing(sqlite3.connect(index_path)) as conn, conn:
        by_file = dict(conn.execute("SELECT path, conv_uuid FROM indexed_files"))
        flamingo_convs = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT conv_uuid FROM messages WHERE message_uuid = 'new-u1'"
            )
        }
        pelican_convs = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT conv_uuid FROM messages WHERE message_uuid = 'old-u1'"
            )
        }
    assert by_file[str(projects / f"{NEW}.jsonl")] == NEW
    assert by_file[str(projects / f"{OLD}.jsonl")] == OLD
    assert flamingo_convs == {NEW}
    assert pelican_convs == {OLD}
