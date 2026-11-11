"""A thread that ends must not leave its SQLite read connection open.

Earned 2026-11-08 (GitHub issue #33, item 4). The supervised watcher ran
for 46 days and held 1262 read connections to ``search-index.sqlite``:
2524 file descriptors, counting the ``-wal`` file. Each event-driven drift
pass runs on a new ``threading.Timer`` thread, and ``_get_read_conn``
opened one connection per thread and kept it in a registry until
``close()``. After about 30 hours the process hit the open-file limit, and
every drift pass failed with ``OSError: [Errno 24] Too many open files``.
The search index then lagged the live sessions by days.

The user-observable measure here is the count of file descriptors that
the process holds open on the index file. It must not grow with the
number of short-lived threads that read the index.

Windows has no simple way to map a handle to its file, so there the
measure is the count of all the handles that the process holds
(``GetProcessHandleCount``), with a small tolerance for unrelated handles.
On the leaky code, 40 passes add at least 80 handles.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

# More passes than the warm-up can explain. On the leaky code, each pass
# adds two descriptors (the database file and its -wal file).
_PASSES = 40

from backend.tests._fd_count import MEASURE as _MEASURE
from backend.tests._fd_count import TOLERANCE as _TOLERANCE
from backend.tests._fd_count import fds_on as _fds_on
from backend.tests._fd_count import open_handles as _open_handles
from backend.tests._fd_count import run_on_new_threads as _run_on_new_threads
from backend.tests._fd_count import settled_handles as _settled_handles
from backend.tests._fd_count import wait_for_timers as _wait_for_timers


def _write_cc_jsonl(claude_dir: Path, session_uuid: str) -> Path:
    proj = claude_dir / "projects" / "-tmp-proj"
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{session_uuid}.jsonl"
    lines = [
        {
            "type": "user",
            "uuid": f"{session_uuid}-u1",
            "sessionId": session_uuid,
            "timestamp": "2026-11-08T10:00:00Z",
            "cwd": "/tmp/proj",
            "message": {"role": "user", "content": "hello leak"},
        },
        {
            "type": "assistant",
            "uuid": f"{session_uuid}-a1",
            "sessionId": session_uuid,
            "timestamp": "2026-11-08T10:00:01Z",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "id": f"msg_{session_uuid}",
                "content": [{"type": "text", "text": "hi"}],
            },
        },
    ]
    path.write_text("".join(json.dumps(ln) + "\n" for ln in lines))
    return path


@pytest.fixture
def live_dirs(tmp_path, monkeypatch):
    """An isolated ``~/.claude`` with one Claude Code session in it."""
    claude_dir = tmp_path / "claude"
    (claude_dir / "projects").mkdir(parents=True)
    (claude_dir / "image-cache").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    _write_cc_jsonl(claude_dir, "11111111-2222-3333-4444-555555555555")

    from backend import cc_watcher, config

    config.get_settings.cache_clear()
    cc_watcher.reset_seen_for_tests()
    cc_watcher.reset_projects_drift_for_tests()
    yield claude_dir
    config.get_settings.cache_clear()
    cc_watcher.reset_seen_for_tests()
    cc_watcher.reset_projects_drift_for_tests()


def test_event_driven_drift_passes_do_not_leak_descriptors(live_dirs) -> None:
    """The watcher's event-driven path: one new Timer thread per pass."""
    from backend import cc_watcher
    from backend import search_index as si

    idx = si.get_search_index()
    if idx is None:
        pytest.skip("sqlite3 build without FTS5")

    # Warm up: the first pass builds the index rows and opens the writer.
    _run_on_new_threads(cc_watcher._fire_drift_pass, 2)
    before = _settled_handles(idx.path, limit=0)
    assert before > 0, "the measure saw no descriptor on the index file"

    _run_on_new_threads(cc_watcher._fire_drift_pass, _PASSES)
    after = _settled_handles(idx.path, limit=before + _TOLERANCE)

    assert after <= before + _TOLERANCE, (
        f"{_PASSES} drift passes on short-lived threads raised the "
        f"{_MEASURE} the index from {before} to {after}"
    )


def test_backstop_summary_cache_reads_do_not_leak_descriptors(live_dirs) -> None:
    """``scan_once`` also reads the summary cache. If a short-lived thread
    runs it, that thread's summary-cache connection must close too."""
    from backend import cc_watcher
    from backend import search_index as si
    from backend import summary_cache as sc

    idx = si.get_search_index()
    cache = sc.get_summary_cache()
    if idx is None or cache is None:
        pytest.skip("sqlite3 build without FTS5")
    idx.mark_ready()

    # In production both open the same file. In tests the conftest moves
    # only the search index, so count the descriptors on both files.
    dbs = (idx.path, cache.path)
    _run_on_new_threads(cc_watcher.scan_once, 2)
    before = _settled_handles(*dbs, limit=0)
    if sys.platform != "win32":
        assert _fds_on(cache.path) > 0, "the measure saw no descriptor on the cache"

    _run_on_new_threads(cc_watcher.scan_once, _PASSES)
    after = _settled_handles(*dbs, limit=before + _TOLERANCE)

    assert after <= before + _TOLERANCE, (
        f"{_PASSES} backstop scans on short-lived threads raised the "
        f"{_MEASURE} the index and the cache from {before} to {after}"
    )


def test_a_drift_pass_slower_than_the_events_does_not_pile_up(
    live_dirs, monkeypatch
) -> None:
    """Earned 2026-11-11: after #41 the restarted watcher still went from 4
    to 146 descriptors on the index in 11 minutes. Each pass took minutes,
    events kept coming, and every event started one more pass on a new
    Timer thread. Those threads waited for the write lock and held a read
    connection the whole time, so #41 could not close it.

    Here each real drift pass takes 0.5 s longer, and an event comes every
    0.08 s. The open descriptors on the index must stay bounded."""
    from backend import cc_watcher
    from backend import search_index as si

    monkeypatch.setenv("CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC", "0.05")
    idx = si.get_search_index()
    if idx is None:
        pytest.skip("sqlite3 build without FTS5")

    real_drift = si.update_drifted_files

    def slow_drift(store, index=None):
        # The thread holds its read connection during the sleep, as a
        # pass that waits for the write lock does.
        updated = real_drift(store, index=index)
        time.sleep(0.5)
        return updated

    monkeypatch.setattr(si, "update_drifted_files", slow_drift)

    _run_on_new_threads(cc_watcher._fire_drift_pass, 1)
    before = _settled_handles(idx.path, limit=0)
    jsonl = next((live_dirs / "projects").rglob("*.jsonl"))

    peak = before
    for _ in range(25):
        cc_watcher._schedule_drift(jsonl)
        time.sleep(0.08)
        peak = max(peak, _open_handles(idx.path))
    _wait_for_timers()

    # One pass runs at a time. Its thread can be a new one, so allow the
    # two descriptors (database and -wal) of one more connection.
    assert peak <= before + 2 + _TOLERANCE, (
        f"25 events during slow drift passes raised the {_MEASURE} the "
        f"index from {before} to a peak of {peak}"
    )


def test_a_live_thread_keeps_its_read_connection(tmp_path) -> None:
    """Bidirectional: the fix must not close a connection that a live
    thread still uses, and must not open a new one on each read."""
    from backend.search_index import SearchIndex

    idx = SearchIndex(tmp_path / "search-index.sqlite")
    first = idx._get_read_conn()
    for _ in range(_PASSES):
        assert idx._get_read_conn() is first
    assert first.execute("SELECT 1").fetchone() == (1,)
    idx.close()


@pytest.mark.parametrize("kind", ["search_index", "summary_cache"])
def test_an_ended_thread_closes_its_read_connection(tmp_path, kind) -> None:
    """Every platform, Windows too: the connection of an ended thread is
    closed before anyone calls close(). On Windows an open connection
    keeps the database file locked."""
    from backend.search_index import SearchIndex
    from backend.summary_cache import SummaryCache

    cls = SearchIndex if kind == "search_index" else SummaryCache
    store = cls(tmp_path / "search-index.sqlite")
    captured: list = []
    t = threading.Thread(target=lambda: captured.append(store._get_read_conn()))
    t.start()
    t.join()
    assert captured, "the worker thread did not open a read connection"

    deadline = time.monotonic() + 2.0
    while store._read_conns.open_count() and time.monotonic() < deadline:
        time.sleep(0.02)
    with pytest.raises(sqlite3.ProgrammingError):
        captured[0].execute("SELECT 1")
    store.close()
