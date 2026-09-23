"""SummaryCache.close() must release read connections from every thread.

Same defect as SearchIndex.close() (fixed 2026-09-22, pinned in
test_search_index_close.py). close() closed only the write connection and
left per-thread readers for thread death. Windows keeps the database file
locked while any connection is open, so a leftover reader blocks deletion.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from backend.summary_cache import SummaryCache


def test_close_closes_a_read_conn_made_on_another_thread(tmp_path: Path) -> None:
    cache = SummaryCache(tmp_path / "cache.db")
    captured: list[sqlite3.Connection] = []

    t = threading.Thread(target=lambda: captured.append(cache._get_read_conn()))
    t.start()
    t.join()
    assert captured

    cache.close()
    with pytest.raises(sqlite3.ProgrammingError):
        captured[0].execute("SELECT 1")


def test_database_file_is_deletable_after_close(tmp_path: Path) -> None:
    db = tmp_path / "cache.db"
    cache = SummaryCache(db)
    cache._get_read_conn()
    cache.close()
    for suffix in ("", "-wal", "-shm"):
        side = Path(str(db) + suffix)
        if side.exists():
            side.unlink()  # must not raise PermissionError on Windows
    assert not db.exists()


def test_usable_again_after_close(tmp_path: Path) -> None:
    """Bidirectional: close() must not poison the cache permanently."""
    cache = SummaryCache(tmp_path / "cache.db")
    cache._get_read_conn()
    cache.close()
    assert cache._get_read_conn().execute("SELECT 1").fetchone() == (1,)
    cache.close()
