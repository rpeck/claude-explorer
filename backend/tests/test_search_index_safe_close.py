"""SearchIndex.close() waits for reads in progress (2026-10-23).

close() also closes the read connections that other threads opened, on
purpose: Windows keeps the database file locked while any connection is
open. But it closed them while another thread could still be inside a
query on one of them. A test teardown that closed the index under the
startup index build crashed an xdist worker with a segfault earlier in
this project. close() now marks the index closed, waits for the reads in
progress, and takes the write lock before it closes anything. A read after
close() raises sqlite3.ProgrammingError; before, it silently opened a new
connection that nothing closed.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from backend.search_index import SearchIndex, fts5_available

pytestmark = pytest.mark.skipif(not fts5_available(), reason="sqlite3 without FTS5")


def test_close_waits_for_a_read_in_progress(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "idx.sqlite")
    orig = index._get_read_conn
    in_read = threading.Event()

    def _slow_conn():
        conn = orig()
        in_read.set()
        time.sleep(0.5)  # the query runs after this
        return conn

    index._get_read_conn = _slow_conn  # type: ignore[method-assign]
    outcome: dict = {}

    def _reader():
        try:
            outcome["result"] = index.list_indexed_paths()
            outcome["done_at"] = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    t = threading.Thread(target=_reader)
    t.start()
    assert in_read.wait(5)
    index.close()
    closed_at = time.monotonic()
    t.join(5)

    assert "error" not in outcome, f"close() closed the connection under a running read: {outcome.get('error')!r}"
    assert outcome["result"] == []
    assert closed_at >= outcome["done_at"], "close() returned before the read finished"


def test_a_read_after_close_raises_instead_of_opening_a_new_connection(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "idx.sqlite")
    index.close()
    with pytest.raises(sqlite3.ProgrammingError):
        index.list_indexed_paths()
    assert index._read_conns == [], "a read after close() opened a connection that nothing closes"


def test_close_is_idempotent(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "idx.sqlite")
    index.close()
    index.close()
