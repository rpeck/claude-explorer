"""Pin the v14 migration's self-repair contract.

Bug observed live 2026-05-26 on rpeck's machine: ``schema_version=14``
was stamped against a ``conversations`` table that still had the v13
column set (no ``is_compaction_titled``). Subsequent ``upsert_conversation``
calls would crash on the first INSERT into the missing column.

Root cause: ``SearchIndex._init_schema``'s early-return gate at
``cols_ok and version_ok`` only checks the ``messages`` columns, not
``conversations``. When a partial prior migration (uvicorn --reload
racing the supervised watcher) stamps the version without finishing
the ALTER, the early-return path skips the repair.

This file pins: on next open with v14 code, a database in the
hybrid-broken state (schema_version=14 + missing
conversations.is_compaction_titled) MUST self-repair (add the
column + backfill) rather than silently early-return.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing


def _make_broken_v14_db(path) -> None:
    """Construct the exact corrupted state observed on the user's machine:

    - schema_version row = 14
    - messages table = v14 (column set is unchanged from v13)
    - conversations table = v13 (NO is_compaction_titled column)
    - indexed_files = v12 (conv_uuid present)
    """
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (14);

        CREATE VIRTUAL TABLE messages USING fts5(
            conv_uuid UNINDEXED, message_uuid UNINDEXED, sender UNINDEXED,
            created_at UNINDEXED, source UNINDEXED, project_path UNINDEXED,
            organization_id UNINDEXED, conv_created_at UNINDEXED,
            conv_updated_at UNINDEXED, is_compaction_summary UNINDEXED,
            title, body, body_text,
            tokenize = "porter unicode61 remove_diacritics 1"
        );

        CREATE TABLE indexed_files (
            path TEXT PRIMARY KEY, mtime REAL NOT NULL,
            indexed_at INTEGER NOT NULL, conv_uuid TEXT
        );

        -- v13-shape conversations: NO is_compaction_titled column.
        CREATE TABLE conversations (
            conv_uuid TEXT PRIMARY KEY,
            title TEXT,
            conv_created_at TEXT,
            conv_updated_at TEXT,
            project_path TEXT,
            source TEXT,
            organization_id TEXT
        );

    """)
    conn.executemany(
        "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("conv-a", "Normal session title", "", "", "", "", ""),
            (
                "conv-b",
                (
                    "This session is being continued from a previous "
                    "conversation that ran out of context. The conversati..."
                ),
                "", "", "", "", "",
            ),
        ],
    )
    conn.commit()
    conn.close()


def test_open_self_repairs_missing_is_compaction_titled_column(tmp_path):
    """RED-first: open a v14-stamped DB whose conversations table is at
    v13. The opener MUST add the column AND backfill it from the title
    text. Today the early-return short-circuits this.
    """
    db_path = tmp_path / "broken-v14.sqlite"
    _make_broken_v14_db(db_path)

    # Sanity: pre-state matches the bug report.
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        cols_before = {
            r[1] for r in conn.execute(
                "PRAGMA table_info(conversations)"
            ).fetchall()
        }
    assert "is_compaction_titled" not in cols_before, (
        "fixture must reproduce the bug — column should be missing pre-open"
    )

    # Open via the production SearchIndex constructor.
    from backend.search_index import SearchIndex
    SearchIndex(db_path)

    # Post-state: column exists and the canonical-prefix row is tagged.
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        cols_after = {
            r[1] for r in conn.execute(
                "PRAGMA table_info(conversations)"
            ).fetchall()
        }
        assert "is_compaction_titled" in cols_after, (
            f"self-repair MUST add the column; got cols={cols_after!r}"
        )
        rows = conn.execute(
            "SELECT conv_uuid, is_compaction_titled FROM conversations "
            "ORDER BY conv_uuid"
        ).fetchall()
    by_uuid = dict(rows)
    assert by_uuid["conv-a"] == 0, (
        f"normal-titled conv must be tagged 0; got {by_uuid!r}"
    )
    assert by_uuid["conv-b"] == 1, (
        f"compaction-titled conv must be tagged 1; got {by_uuid!r}"
    )


def test_open_is_noop_when_v14_db_is_fully_consistent(tmp_path):
    """Bidirectional pair: when the conversations table IS at v14
    (column present), the open path early-returns without re-running
    the ALTER. Pin that the new conv-cols check doesn't cause an
    infinite migrate-on-every-open."""
    db_path = tmp_path / "clean-v14.sqlite"

    # Initial open builds clean v14 schema.
    from backend.search_index import SearchIndex
    SearchIndex(db_path)

    # Snapshot the schema text + table mtime.
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        schema_before = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='conversations'"
        ).fetchone()[0]
        # Insert a row to verify it survives the second open.
        conn.execute(
            "INSERT INTO conversations VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-conv", "test title", "", "", "", "", "", 0),
        )
        conn.commit()

    # Second open should be a no-op for the conversations table.
    SearchIndex(db_path)

    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        schema_after = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='conversations'"
        ).fetchone()[0]
        # Row I inserted must still be there — a stealth rebuild would
        # have dropped it.
        rows = conn.execute(
            "SELECT conv_uuid FROM conversations WHERE conv_uuid='test-conv'"
        ).fetchall()

    assert schema_before == schema_after
    assert rows == [("test-conv",)], "stealth rebuild dropped my row!"
