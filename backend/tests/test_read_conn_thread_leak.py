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
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

_needs_fd_count = pytest.mark.skipif(
    sys.platform == "win32",
    reason="counts open file descriptors through /proc or F_GETPATH",
)

# More passes than the warm-up can explain. On the leaky code, each pass
# adds two descriptors (the database file and its -wal file).
_PASSES = 40


def _open_paths() -> list[str]:
    """Return the real path of every file descriptor this process holds."""
    paths: list[str] = []
    if sys.platform.startswith("linux"):
        for name in os.listdir("/proc/self/fd"):
            try:
                paths.append(os.readlink(f"/proc/self/fd/{name}"))
            except OSError:
                continue
        return paths

    import fcntl

    for name in os.listdir("/dev/fd"):
        try:
            raw = fcntl.fcntl(int(name), fcntl.F_GETPATH, bytes(1024))
        except (OSError, ValueError):
            continue
        paths.append(raw.split(b"\0", 1)[0].decode(errors="replace"))
    return paths


def _fds_on(*dbs: Path) -> int:
    """Count the descriptors open on each database and on its -wal and
    -shm files."""
    names: set[str] = set()
    for db in dbs:
        real = os.path.realpath(db)
        names |= {real, real + "-wal", real + "-shm"}
    return sum(1 for p in _open_paths() if os.path.realpath(p) in names)


def _settled_fds_on(*dbs: Path, limit: int) -> int:
    """Count descriptors on ``dbs``. Wait up to 2 s for the count to reach
    ``limit``, because a thread can finish its cleanup just after join()."""
    deadline = time.monotonic() + 2.0
    count = _fds_on(*dbs)
    while count > limit and time.monotonic() < deadline:
        time.sleep(0.02)
        count = _fds_on(*dbs)
    return count


def _run_on_new_threads(fn: Callable[[], object], passes: int) -> None:
    """Run ``fn`` once on each of ``passes`` new threads, one at a time,
    the way the watcher's debounce Timer does."""
    errors: list[BaseException] = []

    def target() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - report it in the test
            errors.append(exc)

    for _ in range(passes):
        t = threading.Timer(0, target)
        t.daemon = True
        t.start()
        t.join(timeout=30)
        assert not t.is_alive(), "a drift pass did not finish in 30 s"
    assert not errors, f"a pass raised: {errors[0]!r}"


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


@_needs_fd_count
def test_event_driven_drift_passes_do_not_leak_descriptors(live_dirs) -> None:
    """The watcher's event-driven path: one new Timer thread per pass."""
    from backend import cc_watcher
    from backend import search_index as si

    idx = si.get_search_index()
    if idx is None:
        pytest.skip("sqlite3 build without FTS5")

    # Warm up: the first pass builds the index rows and opens the writer.
    _run_on_new_threads(cc_watcher._fire_drift_pass, 2)
    before = _settled_fds_on(idx.path, limit=0)
    assert before > 0, "the measure saw no descriptor on the index file"

    _run_on_new_threads(cc_watcher._fire_drift_pass, _PASSES)
    after = _settled_fds_on(idx.path, limit=before)

    assert after <= before, (
        f"{_PASSES} drift passes on short-lived threads raised the open "
        f"descriptors on the index from {before} to {after}"
    )


@_needs_fd_count
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
    before = _settled_fds_on(*dbs, limit=0)
    assert _fds_on(cache.path) > 0, "the measure saw no descriptor on the cache"

    _run_on_new_threads(cc_watcher.scan_once, _PASSES)
    after = _settled_fds_on(*dbs, limit=before)

    assert after <= before, (
        f"{_PASSES} backstop scans on short-lived threads raised the open "
        f"descriptors on the index and the cache from {before} to {after}"
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
