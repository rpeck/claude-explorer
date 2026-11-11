"""At most one search-index drift pass runs at a time in a process.

Earned 2026-11-11 (issue #33, item 4, after #41). One drift pass took
minutes on the real index, and JSONL events kept coming. ``_fire_drift_pass``
cleared ``_drift_timer`` when it started, so the next event started another
pass on a new Timer thread, and nothing limited how many ran. The watcher
went from 27 to 77 threads in 8 minutes: 51 of them waited on a lock, and
each held an open read connection.

The contract: an event during a pass does not start a second pass. It
makes the running pass run once more when it ends, so the event is not
lost. The backstop scan follows the same rule.
"""

from __future__ import annotations

import threading
import time

import pytest

from backend.tests._fd_count import wait_for_timers


@pytest.fixture
def drift_env(tmp_path, monkeypatch):
    claude_dir = tmp_path / "claude"
    (claude_dir / "projects").mkdir(parents=True)
    (claude_dir / "image-cache").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC", "0.05")

    from backend import cc_watcher, config

    config.get_settings.cache_clear()
    cc_watcher.reset_seen_for_tests()
    cc_watcher.reset_projects_drift_for_tests()
    yield claude_dir
    wait_for_timers()
    config.get_settings.cache_clear()
    cc_watcher.reset_seen_for_tests()
    cc_watcher.reset_projects_drift_for_tests()


class _SlowDrift:
    """A fake ``update_drifted_files`` that records how many calls overlap."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0
        self.running = 0
        self.max_running = 0
        self._lock = threading.Lock()

    def __call__(self, store, index=None) -> int:
        with self._lock:
            self.calls += 1
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            time.sleep(self.seconds)
        finally:
            with self._lock:
                self.running -= 1
        return 0

    def wait_until_idle(self, quiet: float = 0.3, timeout: float = 20.0) -> None:
        """Wait until no call has run for ``quiet`` seconds. A rerun starts
        right after a pass ends, so a single look at ``running`` can miss it."""
        deadline = time.monotonic() + timeout
        idle_since = None
        while time.monotonic() < deadline:
            if self.running:
                idle_since = None
            elif idle_since is None:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since >= quiet:
                return
            time.sleep(0.02)
        raise AssertionError("the drift passes did not stop")


def _install(monkeypatch, seconds: float) -> _SlowDrift:
    from backend import search_index as si

    fake = _SlowDrift(seconds)
    monkeypatch.setattr(si, "update_drifted_files", fake)
    return fake


def test_events_during_a_slow_pass_run_one_pass_and_one_rerun(
    drift_env, monkeypatch
) -> None:
    from backend import cc_watcher

    fake = _install(monkeypatch, seconds=1.0)
    jsonl = drift_env / "projects" / "-p" / "s.jsonl"
    baseline_threads = threading.active_count()

    peak_threads = baseline_threads
    for _ in range(8):  # about 1 s of events, all during the first pass
        cc_watcher._schedule_drift(jsonl)
        time.sleep(0.12)
        peak_threads = max(peak_threads, threading.active_count())
    fake.wait_until_idle()
    wait_for_timers()

    assert fake.max_running == 1, (
        f"{fake.max_running} drift passes ran at the same time"
    )
    # The events during the first pass are not lost: they cause one more
    # pass after it. A slow machine can let the last event land in the
    # rerun, which then runs once more.
    assert 2 <= fake.calls <= 3, f"{fake.calls} passes ran for one burst"
    # The Timer of the newest event, plus the thread of the running pass.
    assert peak_threads <= baseline_threads + 2, (
        f"the thread count rose from {baseline_threads} to {peak_threads}"
    )


def test_an_event_after_a_pass_ends_still_runs_a_pass(drift_env, monkeypatch) -> None:
    """Bidirectional: single-flight must not swallow a later event."""
    from backend import cc_watcher

    fake = _install(monkeypatch, seconds=0.1)
    jsonl = drift_env / "projects" / "-p" / "s.jsonl"

    cc_watcher._schedule_drift(jsonl)
    fake.wait_until_idle()
    cc_watcher._schedule_drift(jsonl)
    fake.wait_until_idle()
    wait_for_timers()

    assert fake.calls == 2


def test_the_backstop_scan_does_not_overlap_a_running_pass(
    drift_env, monkeypatch
) -> None:
    """``scan_once`` (the 600 s backstop) also runs a drift pass. While an
    event-driven pass runs, it must not start a second one. It also must
    not block the watcher's event loop for the length of that pass."""
    from backend import cc_watcher
    from backend import search_index as si

    fake = _install(monkeypatch, seconds=1.0)
    idx = si.get_search_index()
    if idx is None:
        pytest.skip("sqlite3 build without FTS5")
    idx.mark_ready()

    t = threading.Thread(target=cc_watcher._fire_drift_pass, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while fake.calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    started = time.monotonic()
    cc_watcher.scan_once()
    scan_seconds = time.monotonic() - started

    t.join(timeout=20)
    fake.wait_until_idle()

    assert fake.max_running == 1, (
        f"{fake.max_running} drift passes ran at the same time"
    )
    # The backstop's request runs as one more pass after the running one.
    assert fake.calls == 2, f"{fake.calls} passes ran"
    assert scan_seconds < 0.5, (
        f"scan_once waited {scan_seconds:.2f} s for the running pass"
    )
