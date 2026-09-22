"""SearchIndex.close() must release read connections from every thread.

Earned 2026-09-22 on Windows CI. close() closed only the write connection
and left the per-thread read connections to be collected when their thread
died. POSIX unlinks an open file without complaint, so the leak was
invisible there. Windows keeps the database file locked while any
connection is open, so a leftover reader made the temp directory
undeletable and the test failed in teardown with WinError 32.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from backend.search_index import SearchIndex


def _index(tmp_path: Path) -> SearchIndex:
    idx = SearchIndex(tmp_path / "search-index.sqlite")
    if not hasattr(idx, "_get_read_conn"):
        pytest.skip("build without a read-connection pool")
    return idx


def test_close_closes_a_read_conn_made_on_another_thread(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    captured: list[sqlite3.Connection] = []

    def worker() -> None:
        captured.append(idx._get_read_conn())

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert captured, "worker thread did not create a read connection"

    idx.close()

    with pytest.raises(sqlite3.ProgrammingError):
        captured[0].execute("SELECT 1")


def test_close_is_idempotent(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    idx._get_read_conn()
    idx.close()
    idx.close()  # must not raise


def test_read_conn_after_close_is_usable_again(tmp_path: Path) -> None:
    """Bidirectional: close() must not poison the index permanently."""
    idx = _index(tmp_path)
    idx._get_read_conn()
    idx.close()

    conn = idx._get_read_conn()
    assert conn.execute("SELECT 1").fetchone() == (1,)
    idx.close()


def test_database_file_can_be_deleted_after_close(tmp_path: Path) -> None:
    """The property Windows actually enforces."""
    idx = _index(tmp_path)
    idx._get_read_conn()
    idx.close()

    db = tmp_path / "search-index.sqlite"
    if db.exists():
        db.unlink()  # must not raise PermissionError on Windows
    assert not db.exists()
