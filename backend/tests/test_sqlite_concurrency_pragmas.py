"""Pin: busy_timeout >= 30000 ms on every SQLite connection that touches
the shared ``search-index.sqlite`` file.

Regression context (2026-05-24): a user running multiple browser tabs
against the same backend hit ``sqlite3.OperationalError: database is
locked`` from ``summary_cache.upsert_many``. Root cause: the
summary_cache and the search_index module share the same SQLite file.
Each opens its own writer connection guarded by its own Python
threading.Lock. WAL mode allows writers to serialize at the SQLite
level via ``busy_timeout``, but ONLY if a timeout is actually set.

Before this fix:
  - summary_cache writer:  busy_timeout = 5000  (5 s — too short for
                                                  a 250K-message FTS5
                                                  rebuild to finish)
  - summary_cache reader:  busy_timeout = 0    (unset)
  - search_index  writer:  busy_timeout = 0    (UNSET — main cause)
  - search_index  reader:  busy_timeout = 0    (unset)

After this fix: all four = 30000 ms (30 s). 30 s outlasts every known
writer transaction in this codebase, so contention waits-and-succeeds
rather than waits-and-fails.

This test pins the user-observable invariant ("the backend doesn't 500
on concurrent requests during a fetch + reindex") via the
implementation-level pragma the invariant requires. TESTING.md
§5.14 user-observable layer is covered by a separate concurrent-
request test (out of scope for this pin — would need a real corpus
fixture and minutes of runtime).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.search_index import SearchIndex
from backend.summary_cache import SummaryCache


REQUIRED_BUSY_TIMEOUT_MS = 30000


def _busy_timeout(conn: sqlite3.Connection) -> int:
    cur = conn.execute("PRAGMA busy_timeout")
    row = cur.fetchone()
    return int(row[0])


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "search-index.sqlite"


def test_summary_cache_writer_busy_timeout_30s(tmp_db_path: Path) -> None:
    cache = SummaryCache(tmp_db_path)
    assert _busy_timeout(cache._write_conn) >= REQUIRED_BUSY_TIMEOUT_MS, (
        "summary_cache writer connection must have busy_timeout >= 30 s. "
        "Concurrent writers from search_index can hold the SQLite writer "
        "lock for >5 s during FTS5 rebuilds; a shorter timeout fails as "
        "`database is locked`."
    )


def test_summary_cache_reader_busy_timeout_30s(tmp_db_path: Path) -> None:
    cache = SummaryCache(tmp_db_path)
    # Just allocate the per-thread reader directly; this test doesn't
    # care about the read result, only the pragma on the connection.
    reader = cache._get_read_conn()
    assert _busy_timeout(reader) >= REQUIRED_BUSY_TIMEOUT_MS, (
        "summary_cache reader connection must have busy_timeout >= 30 s. "
        "Default 0 makes readers fail immediately on checkpoint/VACUUM "
        "contention with the writer."
    )


def test_search_index_writer_busy_timeout_30s(tmp_db_path: Path) -> None:
    idx = SearchIndex(tmp_db_path)
    assert _busy_timeout(idx._write_conn) >= REQUIRED_BUSY_TIMEOUT_MS, (
        "search_index writer connection must have busy_timeout >= 30 s. "
        "Pre-2026-05-24 this was UNSET (default 0), which is the primary "
        "cause of the `database is locked` user report from "
        "summary_cache.upsert_many — search_index would silently win the "
        "race and summary_cache had no timeout slack to recover."
    )


def test_search_index_reader_busy_timeout_30s(tmp_db_path: Path) -> None:
    idx = SearchIndex(tmp_db_path)
    reader = idx._get_read_conn()
    assert _busy_timeout(reader) >= REQUIRED_BUSY_TIMEOUT_MS, (
        "search_index reader connection must have busy_timeout >= 30 s. "
        "Same rationale as the writer reader pin in summary_cache."
    )
