"""Tests for the ``get_session_outline`` MCP tool, including its
SQLite outline-cache semantics (fresh / append-only / leaf-change /
shrink / regen).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from mcp_server.server import get_session_outline


def _call(**kwargs):
    fn = getattr(get_session_outline, "fn", get_session_outline)
    return fn(**kwargs)


def _cache_db_path(data_dir: Path) -> Path:
    return data_dir.parent / "cache.db"


def _read_message_summaries(db: Path, session_id: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT message_uuid, position, sender, summary, char_count, "
        "tool_count, timestamp FROM message_summaries "
        "WHERE session_id = ? ORDER BY position",
        (session_id,),
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_outline_returns_metadata_and_summaries(mcp_data):
    u1 = mcp_data.add_desktop_session("u-1", name="Topic")
    result = _call(conversation_id=u1)
    assert result["session_id"] == u1
    assert result["name"] == "Topic"
    assert result["source"] == "CLAUDE_AI"
    assert result["message_count"] == 2
    msgs = result["messages"]
    assert len(msgs) == 2

    # Per-message fields
    assert msgs[0]["position"] == 0
    assert msgs[0]["sender"] == "human"
    assert msgs[0]["message_uuid"] == "msg-h-1"
    assert msgs[0]["char_count"] > 0
    assert msgs[0]["tool_count"] == 0
    assert msgs[0]["timestamp"]  # non-empty ISO string

    assert msgs[1]["position"] == 1
    assert msgs[1]["sender"] == "assistant"
    assert msgs[1]["message_uuid"] == "msg-a-1"


def test_outline_session_not_found_raises(mcp_data):
    with pytest.raises(ValueError, match="not found"):
        _call(conversation_id="11111111-0000-0000-0000-000000000000")


# ---------------------------------------------------------------------------
# Summary semantics
# ---------------------------------------------------------------------------


def test_summary_truncates_at_word_boundary(mcp_data):
    long_text = " ".join(["word"] * 200)  # ~999 chars, plenty of spaces
    u = mcp_data.add_desktop_session(
        "u-long",
        messages=[
            {
                "uuid": "h-1",
                "sender": "human",
                "text": long_text,
                "content": [{"type": "text", "text": long_text}],
                "created_at": "2026-04-01T10:00:00Z",
                "updated_at": "2026-04-01T10:00:00Z",
                "parent_message_uuid": None,
            },
        ],
    )
    result = _call(conversation_id=u)
    summary = result["messages"][0]["summary"]
    # Ends in "..." because content exceeds 200 chars.
    assert summary.endswith("...")
    # Length is at most 203 (200 + "...").
    assert len(summary) <= 203
    # No trailing partial word: char before "..." must be a full word
    # (i.e. the truncation happened at a space).
    body = summary[:-3]
    assert not body.endswith(" "), "should not end with whitespace before ..."
    assert all(w == "word" for w in body.split()), "truncated at word boundary"


def test_summary_excludes_tool_blocks(mcp_data):
    msg = mcp_data.make_tool_message(
        uuid="m-tool",
        sender="assistant",
        text_before="I'll check the file.",
        tool_name="read_file",
        tool_input={"path": "/foo/bar"},
        tool_result="file contents here",
    )
    u = mcp_data.add_desktop_session("u-tool", messages=[msg])
    result = _call(conversation_id=u)
    summary = result["messages"][0]["summary"]
    # Tool input/output strings must NOT appear in the summary.
    assert "file contents here" not in summary
    assert "/foo/bar" not in summary
    # Text-block content IS in the summary.
    assert "I'll check the file" in summary
    # tool_count reflects the tool_use block.
    assert result["messages"][0]["tool_count"] == 1


# ---------------------------------------------------------------------------
# Cache: fresh build populates SQLite
# ---------------------------------------------------------------------------


def test_cache_populated_on_first_call(mcp_data):
    u = mcp_data.add_desktop_session("u-1")
    _call(conversation_id=u)
    db = _cache_db_path(mcp_data.data_dir)
    assert db.exists(), "cache.db should be created on first call"
    rows = _read_message_summaries(db, u)
    assert len(rows) == 2
    uuids = {r["message_uuid"] for r in rows}
    assert uuids == {"msg-h-1", "msg-a-1"}


# ---------------------------------------------------------------------------
# Cache: append-only on growth
# ---------------------------------------------------------------------------


def _append_message_to_session(data_dir: Path, real_uuid: str, new_msg: dict):
    """Append a message to an existing JSON session file and bump the
    leaf UUID + mtime so the cache's stale check fires."""
    path = data_dir / f"{real_uuid}.json"
    blob = json.loads(path.read_text())
    blob["chat_messages"].append(new_msg)
    blob["current_leaf_message_uuid"] = new_msg["uuid"]
    path.write_text(json.dumps(blob))
    new_mtime = time.time() + 1
    os.utime(path, (new_mtime, new_mtime))


def test_cache_append_only_growth(mcp_data):
    u = mcp_data.add_desktop_session("u-1")
    _call(conversation_id=u)
    db = _cache_db_path(mcp_data.data_dir)

    # Capture initial rows
    initial_rows = _read_message_summaries(db, u)
    assert len(initial_rows) == 2
    initial_uuids = {r["message_uuid"] for r in initial_rows}

    # Append a third message
    third_msg = {
        "uuid": "msg-h-2",
        "sender": "human",
        "text": "A follow-up question.",
        "content": [{"type": "text", "text": "A follow-up question."}],
        "created_at": "2026-04-01T10:01:00Z",
        "updated_at": "2026-04-01T10:01:00Z",
        "parent_message_uuid": "msg-a-1",
    }
    _append_message_to_session(mcp_data.data_dir, u, third_msg)

    # Second call should add the new row
    result = _call(conversation_id=u)
    assert len(result["messages"]) == 3
    rows = _read_message_summaries(db, u)
    assert len(rows) == 3
    # Old rows preserved (UUID-keyed, so append-only path didn't touch them).
    assert initial_uuids.issubset({r["message_uuid"] for r in rows})


# ---------------------------------------------------------------------------
# Cache: leaf-UUID change triggers full regen
# ---------------------------------------------------------------------------


def test_cache_full_regen_on_leaf_change(mcp_data):
    u = mcp_data.add_desktop_session("u-1")
    _call(conversation_id=u)
    db = _cache_db_path(mcp_data.data_dir)

    # Mutate the leaf UUID without changing message count (simulate a
    # branch switch / regenerate-from-earlier scenario where the active
    # path tip moves).
    path = mcp_data.data_dir / f"{u}.json"
    blob = json.loads(path.read_text())
    blob["current_leaf_message_uuid"] = "msg-h-1"  # was msg-a-1
    path.write_text(json.dumps(blob))
    new_mtime = time.time() + 1
    os.utime(path, (new_mtime, new_mtime))

    _call(conversation_id=u)
    # Cache should have been regenerated; the new leaf UUID must be
    # reflected in session_files.
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT leaf_message_uuid FROM session_files WHERE session_id = ?",
        (u,),
    ).fetchone()
    conn.close()
    assert row["leaf_message_uuid"] == "msg-h-1"


# ---------------------------------------------------------------------------
# Cache: fresh hit (no work)
# ---------------------------------------------------------------------------


def test_cache_fresh_hit_returns_same_rows(mcp_data):
    """Two back-to-back calls on an unchanged file should produce
    byte-identical message arrays (modulo dict ordering)."""
    u = mcp_data.add_desktop_session("u-1")
    first = _call(conversation_id=u)
    second = _call(conversation_id=u)
    assert first["messages"] == second["messages"]
