"""Event-driven search-index drift via the projects-dir observer
(PLANS/SEARCH_INDEX_FRESHNESS.md).

The image-cache watcher already runs a ``watchdog`` Observer on
``~/.claude/image-cache/``. This file pins a SECOND observer (or a
second ``schedule()`` on the same Observer) that watches
``~/.claude/projects/`` for ``*.jsonl`` modifications. On event:

  1. Queue the changed path in a module-level needs-reindex set.
  2. Reset a debounce timer (default 2 s, env-overridable via
     ``CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC``).
  3. When the timer fires, call ``update_drifted_files`` once
     (covers ALL queued paths via the existing drift-scan).

Without debouncing, CC's append-only writes (5-20 ``on_modified``
events per user message) would trigger 5-20 SQL upserts in rapid
succession.

These tests use ``PollingObserver`` for determinism, matching the
pattern in ``test_cc_watcher.py`` (the FSEvents/inotify
backends fire on the kernel's schedule with macOS-specific
coalescing latencies that are hard to test reliably).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest


def _write_cc_jsonl(claude_dir: Path, project: str, session_uuid: str) -> Path:
    """Drop a minimal CC JSONL session file. Returns the on-disk path."""
    proj = claude_dir / "projects" / project
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{session_uuid}.jsonl"
    lines = [
        {
            "type": "user",
            "uuid": f"{session_uuid}-u1",
            "sessionId": session_uuid,
            "timestamp": "2026-05-01T10:00:00Z",
            "cwd": "/tmp/proj",
            "gitBranch": "main",
            "version": "1.0",
            "message": {"role": "user", "content": "hello"},
        },
        {
            "type": "assistant",
            "uuid": f"{session_uuid}-a1",
            "sessionId": session_uuid,
            "timestamp": "2026-05-01T10:00:01Z",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "id": f"msg_{session_uuid}",
                "content": [{"type": "text", "text": "hi"}],
            },
        },
    ]
    with path.open("w") as fh:
        for ln in lines:
            fh.write(json.dumps(ln) + "\n")
    return path


@pytest.fixture
def watcher_env(tmp_path, monkeypatch):
    """Isolated ``~/.claude`` and ``~/.claude-explorer`` for watcher tests.

    Yields a dict with the live paths. Resets the per-process
    ``_seen`` set on entry/exit so back-to-back tests don't bleed.
    """
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    (claude_dir / "image-cache").mkdir()
    (claude_dir / "projects").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    # Force a short debounce so tests don't wait 2 s of real time.
    monkeypatch.setenv("CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC", "0.2")

    from backend import config, cc_watcher

    config.get_settings.cache_clear()
    cc_watcher.reset_seen_for_tests()
    # Reset any drift-related module state introduced by the plan.
    if hasattr(cc_watcher, "reset_projects_drift_for_tests"):
        cc_watcher.reset_projects_drift_for_tests()

    yield {
        "claude_dir": claude_dir,
        "data_dir": data_dir,
        "projects": claude_dir / "projects",
        "image_cache": claude_dir / "image-cache",
    }

    config.get_settings.cache_clear()
    cc_watcher.reset_seen_for_tests()
    if hasattr(cc_watcher, "reset_projects_drift_for_tests"):
        cc_watcher.reset_projects_drift_for_tests()


# ----- 7. Projects-dir observer exists --------------------------


def test_run_watcher_schedules_projects_dir_observer(watcher_env, monkeypatch):
    """``run_watcher`` must end up with at least one Observer watch on
    the projects directory. Otherwise CC session edits during an
    explorer session take up to 600 s (the backstop poll) to appear
    in FTS5 search results.

    Strategy: stub ``_try_start_observer`` (image-cache path) to a
    PollingObserver we capture, and stub ``_try_start_projects_observer``
    to another PollingObserver we capture. After ``run_watcher``
    starts, both observers must be alive and the projects one must
    have a watch on the projects dir.
    """
    from watchdog.observers.polling import PollingObserver

    from backend import cc_watcher

    captured = {}

    def _fake_image_obs():
        obs = PollingObserver(timeout=0.1)
        obs.start()
        captured["image"] = obs
        return obs

    def _fake_projects_obs():
        from watchdog.events import FileSystemEventHandler

        class _Noop(FileSystemEventHandler):
            pass

        obs = PollingObserver(timeout=0.1)
        root = watcher_env["projects"]
        root.mkdir(parents=True, exist_ok=True)
        obs.schedule(_Noop(), str(root), recursive=True)
        obs.start()
        captured["projects"] = obs
        return obs

    monkeypatch.setattr(
        cc_watcher, "_try_start_observer", _fake_image_obs
    )
    monkeypatch.setattr(
        cc_watcher, "_try_start_projects_observer", _fake_projects_obs
    )
    monkeypatch.setattr(cc_watcher, "SCAN_INTERVAL_SEC", 3600.0)

    async def _scenario():
        stop_event = asyncio.Event()
        watcher_task = asyncio.create_task(
            cc_watcher.run_watcher(stop_event)
        )
        await asyncio.sleep(0.2)
        # Both observers must be alive while run_watcher is running.
        assert "projects" in captured, (
            "run_watcher must call _try_start_projects_observer to "
            "register the projects-dir watch."
        )
        assert captured["projects"].is_alive(), (
            "projects observer must be started, not just constructed."
        )
        stop_event.set()
        await asyncio.wait_for(watcher_task, timeout=5.0)

    asyncio.run(_scenario())


# ----- 8. JSONL on_modified event triggers debounced drift -------


def test_jsonl_modify_event_triggers_debounced_drift(
    watcher_env, monkeypatch
):
    """A single ``on_modified`` event on a JSONL must result in EXACTLY
    one ``update_drifted_files`` call after the debounce window.

    Bug it would surface: the projects observer fires drift inline per
    event, hammering the SQL writer; OR the debounce never fires, and
    drift is never picked up.
    """
    from backend import cc_watcher

    drift_calls = {"n": 0}

    def _fake_drift(store=None, *, index=None):
        drift_calls["n"] += 1
        return 0

    monkeypatch.setattr(
        "backend.search_index.update_drifted_files", _fake_drift
    )
    # Bypass image-observer to keep the test focused on projects.
    monkeypatch.setattr(
        cc_watcher, "_try_start_observer", lambda: None
    )

    # Build the projects event handler directly so we control event
    # delivery (deterministic; no PollingObserver poll lag).
    handler = cc_watcher._build_projects_event_handler()

    jsonl = _write_cc_jsonl(
        watcher_env["claude_dir"],
        "proj-A",
        "a0000001-0000-0000-0000-000000000001",
    )

    class _FakeEvent:
        def __init__(self, src_path, is_directory=False):
            self.src_path = src_path
            self.is_directory = is_directory

    handler.on_modified(_FakeEvent(str(jsonl)))

    # Wait > debounce window (env-set to 0.2 s above; add headroom).
    time.sleep(0.6)
    # Drain any pending timer threads.
    if hasattr(cc_watcher, "_drain_projects_drift_for_tests"):
        cc_watcher._drain_projects_drift_for_tests()

    assert drift_calls["n"] == 1, (
        f"One JSONL modify event must trigger exactly one drift call; "
        f"got {drift_calls['n']}. Debounce broken or projects handler "
        f"never fired."
    )


# ----- 9. Multiple rapid events → one drift call -----------------


def test_event_storm_collapses_to_one_drift_call(watcher_env, monkeypatch):
    """5 rapid events on the same path within the debounce window must
    collapse into ONE drift call.

    Bug it would surface: per-event drift firing (one SQL transaction
    per CC keystroke append). Without debouncing, a user typing for
    a minute would generate hundreds of redundant SQL upserts.
    """
    from backend import cc_watcher

    drift_calls = {"n": 0}

    def _fake_drift(store=None, *, index=None):
        drift_calls["n"] += 1
        return 0

    monkeypatch.setattr(
        "backend.search_index.update_drifted_files", _fake_drift
    )

    handler = cc_watcher._build_projects_event_handler()
    jsonl = _write_cc_jsonl(
        watcher_env["claude_dir"],
        "proj-A",
        "b0000001-0000-0000-0000-000000000002",
    )

    class _FakeEvent:
        def __init__(self, src_path, is_directory=False):
            self.src_path = src_path
            self.is_directory = is_directory

    # 5 rapid events within ~50 ms — well inside the 200 ms debounce.
    for _ in range(5):
        handler.on_modified(_FakeEvent(str(jsonl)))
        time.sleep(0.01)

    # Wait past debounce window.
    time.sleep(0.6)
    if hasattr(cc_watcher, "_drain_projects_drift_for_tests"):
        cc_watcher._drain_projects_drift_for_tests()

    assert drift_calls["n"] == 1, (
        f"5 rapid events must collapse to 1 drift call; got "
        f"{drift_calls['n']}. Debounce timer reset broken."
    )


# ----- 10. Non-JSONL events are ignored --------------------------


def test_non_jsonl_event_is_ignored(watcher_env, monkeypatch):
    """An ``on_modified`` event on a ``.txt`` (or anything not
    ``.jsonl``) in the projects tree must NOT trigger drift.

    Bug it would surface: indiscriminate event handling spending SQL
    cycles on files the index doesn't care about. (CC drops the
    occasional ``.log`` / ``.tmp`` next to its sessions; those should
    be skipped.)
    """
    from backend import cc_watcher

    drift_calls = {"n": 0}

    def _fake_drift(store=None, *, index=None):
        drift_calls["n"] += 1
        return 0

    monkeypatch.setattr(
        "backend.search_index.update_drifted_files", _fake_drift
    )

    handler = cc_watcher._build_projects_event_handler()
    proj = watcher_env["projects"] / "proj-A"
    proj.mkdir(parents=True, exist_ok=True)
    txt = proj / "notes.txt"
    txt.write_text("ignored")

    class _FakeEvent:
        def __init__(self, src_path, is_directory=False):
            self.src_path = src_path
            self.is_directory = is_directory

    handler.on_modified(_FakeEvent(str(txt)))
    time.sleep(0.6)
    if hasattr(cc_watcher, "_drain_projects_drift_for_tests"):
        cc_watcher._drain_projects_drift_for_tests()

    assert drift_calls["n"] == 0, (
        f"Non-JSONL events must NOT trigger drift; got "
        f"{drift_calls['n']} calls."
    )


# ----- 11. Shutdown cancels pending debounce timer ----------------


def test_shutdown_cancels_pending_debounce_timer(watcher_env, monkeypatch):
    """A JSONL event queued just before shutdown must NOT fire drift
    after the watcher has stopped.

    Bug it would surface: a leaked ``threading.Timer`` thread still
    holding a reference to ``update_drifted_files``, firing after
    SQLite has been closed. Causes spurious "database is locked" or
    "Cannot operate on a closed database" warnings at shutdown — and
    in tests, leaks threads across tests.
    """
    from backend import cc_watcher

    drift_calls = {"n": 0}

    def _fake_drift(store=None, *, index=None):
        drift_calls["n"] += 1
        return 0

    monkeypatch.setattr(
        "backend.search_index.update_drifted_files", _fake_drift
    )

    handler = cc_watcher._build_projects_event_handler()
    jsonl = _write_cc_jsonl(
        watcher_env["claude_dir"],
        "proj-A",
        "c0000001-0000-0000-0000-000000000003",
    )

    class _FakeEvent:
        def __init__(self, src_path, is_directory=False):
            self.src_path = src_path
            self.is_directory = is_directory

    handler.on_modified(_FakeEvent(str(jsonl)))
    # Immediately invoke shutdown BEFORE the debounce window elapses
    # (debounce env-set to 0.2 s; we shut down at < 50 ms).
    time.sleep(0.02)
    cc_watcher.shutdown_projects_drift()

    # Sleep well past the debounce; no drift call must fire.
    time.sleep(0.6)

    assert drift_calls["n"] == 0, (
        f"Drift fired after shutdown; got {drift_calls['n']} calls. "
        f"Pending debounce Timer was not cancelled on shutdown."
    )


# ----- 12. The drain helper waits for a pass that already started --------


def test_drain_waits_for_a_drift_pass_that_already_started(
    watcher_env, monkeypatch
):
    """The drain helper must wait for a timer whose pass is still running.

    The timer fires, clears the pending state, and only then opens the
    search index and calls ``update_drifted_files``. On a slow CI machine
    (Windows x86_64, PR #13, 2026-10-21) the index opened after the
    tests' 0.6 s sleep. The drain saw nothing pending and returned, and
    two tests read 0 drift calls. A slow index open makes that race
    certain here.
    """
    from backend import cc_watcher
    from backend import search_index as si

    drift_calls = {"n": 0}
    opened = threading.Event()

    def _fake_drift(store=None, *, index=None):
        drift_calls["n"] += 1
        return 0

    def _slow_index():
        opened.set()
        time.sleep(0.5)
        return None

    monkeypatch.setattr("backend.search_index.update_drifted_files", _fake_drift)
    monkeypatch.setattr(si, "get_search_index", _slow_index)

    handler = cc_watcher._build_projects_event_handler()
    jsonl = _write_cc_jsonl(
        watcher_env["claude_dir"],
        "proj-A",
        "c0000001-0000-0000-0000-000000000003",
    )

    class _FakeEvent:
        def __init__(self, src_path, is_directory=False):
            self.src_path = src_path
            self.is_directory = is_directory

    handler.on_modified(_FakeEvent(str(jsonl)))

    # Settle signal: the timer fired and its pass is inside the slow open.
    assert opened.wait(timeout=5.0), "the debounce timer never fired"
    cc_watcher._drain_projects_drift_for_tests()

    assert drift_calls["n"] == 1, (
        f"the drain returned before the running pass called the drift "
        f"function; got {drift_calls['n']} calls"
    )
