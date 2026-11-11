"""Replacing or removing one conversation must not scan the whole index.

Earned 2026-11-11 (issue #33, item 4, after #41). ``conv_uuid`` is an
UNINDEXED column of the FTS5 ``messages`` table, and FTS5 cannot index
it, so ``DELETE FROM messages WHERE conv_uuid = ?`` read all 393,517 rows
of the real index. It took 8.9 s on an idle machine to find the 328 rows
of one conversation, and minutes under load. Every upsert held the write
lock for that time, so the drift passes queued up behind it.

The measure is the number of SQLite virtual-machine steps (a progress
handler with N = 1), which counts the rows that SQLite visits, also inside
FTS5. It is exact and does not depend on the speed of the machine. The cost
of one operation must not grow when the rest of the index grows 10 times.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.search_index import SearchIndex

_OTHER_CONVS = 30


def _conv(uuid: str, n_messages: int, word: str = "hello") -> dict:
    return {
        "uuid": uuid,
        "name": f"title {uuid}",
        "source": "CLAUDE_AI",
        "created_at": "2026-11-11T10:00:00Z",
        "updated_at": "2026-11-11T10:00:00Z",
        "chat_messages": [
            {
                "uuid": f"{uuid}-{i}",
                "sender": "human",
                "created_at": "2026-11-11T10:00:00Z",
                "text": f"{word} message {i}",
                "content": [{"type": "text", "text": f"{word} message {i}"}],
            }
            for i in range(n_messages)
        ],
    }


def _index(root: Path, rows_per_other_conv: int) -> SearchIndex:
    root.mkdir(parents=True)
    idx = SearchIndex(root / "search-index.sqlite")
    for i in range(_OTHER_CONVS):
        idx.upsert_conversation(
            _conv(f"other-{i}", rows_per_other_conv), root / f"other-{i}.json", 1.0
        )
    idx.upsert_conversation(_conv("target", 5), root / "target.json", 1.0)
    # Merge the FTS5 index into one segment. Else the commit of the measured
    # operation can start an FTS5 segment merge, whose cost grows with the
    # index but is FTS5's amortized upkeep, not a scan of the rows.
    with idx._write_conn:
        idx._write_conn.execute("INSERT INTO messages(messages) VALUES ('optimize')")
    return idx


def _steps(idx: SearchIndex, action) -> int:
    count = 0

    def tick() -> int:
        nonlocal count
        count += 1
        return 0

    idx._write_conn.set_progress_handler(tick, 1)
    try:
        action()
    finally:
        idx._write_conn.set_progress_handler(None, 1)
    return count


def _run(op: str, idx: SearchIndex, root: Path) -> None:
    if op == "re-upsert":
        idx.upsert_conversation(_conv("target", 5, "again"), root / "target.json", 2.0)
    elif op == "delete_conversation":
        idx.delete_conversation("target")
    else:
        idx.delete_by_path(root / "target.json")


@pytest.mark.parametrize("op", ["re-upsert", "delete_conversation", "delete_by_path"])
def test_the_cost_of_one_conversation_does_not_grow_with_the_index(
    tmp_path: Path, op: str
) -> None:
    small = _index(tmp_path / "small", rows_per_other_conv=4)  # 120 other rows
    large = _index(tmp_path / "large", rows_per_other_conv=40)  # 1,200 other rows

    small_steps = _steps(small, lambda: _run(op, small, tmp_path / "small"))
    large_steps = _steps(large, lambda: _run(op, large, tmp_path / "large"))

    assert large_steps <= small_steps * 1.5, (
        f"{op}: {small_steps} SQLite steps with 120 other rows, "
        f"{large_steps} with 1,200: the cost grows with the index"
    )


def _rows_of(idx: SearchIndex, conv_uuid: str) -> list[str]:
    return [
        r[0]
        for r in idx._write_conn.execute(
            "SELECT body FROM messages WHERE conv_uuid = ? ORDER BY rowid", (conv_uuid,)
        )
    ]


def _old_code_upsert(idx: SearchIndex, conv: dict) -> None:
    """Write a conversation the way the code before this fix did: a DELETE
    by ``conv_uuid`` and plain INSERTs, without the rowid side table. A
    process that runs old code (an old ``serve``, an MCP server) still
    writes the shared index this way until the user restarts it."""
    with idx._write_conn:
        idx._write_conn.execute(
            "DELETE FROM messages WHERE conv_uuid = ?", (conv["uuid"],)
        )
        for m in conv["chat_messages"]:
            idx._write_conn.execute(
                "INSERT INTO messages (conv_uuid, message_uuid, sender, created_at, "
                "source, project_path, organization_id, conv_created_at, "
                "conv_updated_at, is_compaction_summary, title, body, body_text) "
                "VALUES (?, ?, 'human', '', 'CLAUDE_AI', '', '', '', '', 0, ?, ?, ?)",
                (conv["uuid"], m["uuid"], conv["name"], m["text"], m["text"]),
            )


@pytest.mark.parametrize("old_rows", [3, 5, 8])
def test_rows_that_old_code_wrote_are_replaced_too(tmp_path: Path, old_rows: int) -> None:
    """While old and new code share the index, an upsert must still replace
    every row of the conversation, also the rows that old code wrote. Else
    search shows the conversation twice."""
    idx = SearchIndex(tmp_path / "search-index.sqlite")
    idx.upsert_conversation(_conv("other", 4), tmp_path / "other.json", 1.0)
    idx.upsert_conversation(_conv("target", 5, "first"), tmp_path / "target.json", 1.0)

    _old_code_upsert(idx, _conv("target", old_rows, "old"))
    idx.upsert_conversation(_conv("target", 5, "new"), tmp_path / "target.json", 2.0)

    assert _rows_of(idx, "target") == [f"new message {i}" for i in range(5)]
    assert len(_rows_of(idx, "other")) == 4, "the upsert removed rows of another conversation"


def test_a_delete_after_old_code_wrote_removes_every_row(tmp_path: Path) -> None:
    idx = SearchIndex(tmp_path / "search-index.sqlite")
    idx.upsert_conversation(_conv("other", 4), tmp_path / "other.json", 1.0)
    idx.upsert_conversation(_conv("target", 5), tmp_path / "target.json", 1.0)
    _old_code_upsert(idx, _conv("target", 7, "old"))

    idx.delete_conversation("target")

    assert _rows_of(idx, "target") == []
    assert len(_rows_of(idx, "other")) == 4


def test_an_index_from_before_the_fix_opens_with_every_row_reachable(
    tmp_path: Path,
) -> None:
    """The first open after the upgrade fills the rowid side table from the
    existing rows. No SCHEMA_VERSION bump: old code treats a different
    version as a reason to drop and rebuild the whole index."""
    path = tmp_path / "search-index.sqlite"
    idx = SearchIndex(path)
    idx.upsert_conversation(_conv("target", 5), tmp_path / "target.json", 1.0)
    _old_code_upsert(idx, _conv("legacy", 6, "legacy"))
    with idx._write_conn:
        idx._write_conn.execute("DROP TABLE IF EXISTS message_rowids")
    idx.close()

    reopened = SearchIndex(path)
    reopened.delete_conversation("legacy")
    assert _rows_of(reopened, "legacy") == []
    assert len(_rows_of(reopened, "target")) == 5
    version = reopened._write_conn.execute("SELECT version FROM schema_version").fetchone()
    assert version == (14,)
    reopened.close()


def test_the_side_table_stays_consistent_after_clear_all(tmp_path: Path) -> None:
    idx = SearchIndex(tmp_path / "search-index.sqlite")
    idx.upsert_conversation(_conv("target", 5), tmp_path / "target.json", 1.0)
    idx.clear_all()
    idx.upsert_conversation(_conv("target", 2, "after"), tmp_path / "target.json", 2.0)
    assert _rows_of(idx, "target") == ["after message 0", "after message 1"]
    side_rows = idx._write_conn.execute("SELECT count(*) FROM message_rowids").fetchone()[0]
    assert side_rows == 2, f"the rowid side table has {side_rows} rows for 2 messages"
