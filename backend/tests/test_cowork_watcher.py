"""Tests for the Cowork-dir Observer in backend.cc_watcher.

The watcher already runs two Observers (image-cache + projects-dir).
Phase 4 adds a THIRD Observer on the cowork-app local-agent-mode-sessions
tree so an audit.jsonl append fires the same shared debounced drift
pass that updates FTS5 — within ~5 s instead of the 600 s backstop.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

HAPPY_AUDIT = (
    Path(__file__).parent
    / "fixtures"
    / "cowork"
    / "d_deployment1"
    / "o_org1"
    / "local_aaaa1111-2222-3333-4444-555566667777"
    / "audit.jsonl"
)
HAPPY_SIDECAR = (
    Path(__file__).parent
    / "fixtures"
    / "cowork"
    / "d_deployment1"
    / "o_org1"
    / "local_aaaa1111-2222-3333-4444-555566667777.json"
)


@pytest.fixture
def cowork_watcher_env(tmp_path, monkeypatch):
    """Isolated CLAUDE_DESKTOP_APP_DIR for watcher tests.

    The autouse _isolate_cowork_app_dir fixture also sets this env
    var, but to a different (per-session) tmp dir. We override it
    here for this test only.
    """
    cowork_app_dir = tmp_path / "claude_desktop_app"
    cowork_root = cowork_app_dir / "local-agent-mode-sessions"
    cowork_root.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(cowork_app_dir))

    from backend import cc_watcher, config

    config.get_settings.cache_clear()
    cc_watcher.reset_seen_for_tests()
    if hasattr(cc_watcher, "reset_projects_drift_for_tests"):
        cc_watcher.reset_projects_drift_for_tests()

    yield {
        "cowork_app_dir": cowork_app_dir,
        "cowork_root": cowork_root,
    }


def test_run_watcher_schedules_cowork_observer(cowork_watcher_env, monkeypatch):
    """``run_watcher`` must register an Observer watch on the cowork
    root so a new audit.jsonl is picked up sub-second.

    Without this, Cowork session edits during an explorer session
    take up to 600 s (the backstop poll) to appear in FTS5 search.
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
        obs = PollingObserver(timeout=0.1)
        obs.start()
        captured["projects"] = obs
        return obs

    def _fake_cowork_obs():
        from watchdog.events import FileSystemEventHandler

        class _Noop(FileSystemEventHandler):
            pass

        obs = PollingObserver(timeout=0.1)
        root = cowork_watcher_env["cowork_root"]
        obs.schedule(_Noop(), str(root), recursive=True)
        obs.start()
        captured["cowork"] = obs
        return obs

    monkeypatch.setattr(cc_watcher, "_try_start_observer", _fake_image_obs)
    monkeypatch.setattr(cc_watcher, "_try_start_projects_observer", _fake_projects_obs)
    monkeypatch.setattr(cc_watcher, "_try_start_cowork_observer", _fake_cowork_obs)
    monkeypatch.setattr(cc_watcher, "SCAN_INTERVAL_SEC", 3600.0)

    async def _scenario():
        stop_event = asyncio.Event()
        watcher_task = asyncio.create_task(cc_watcher.run_watcher(stop_event))
        await asyncio.sleep(0.2)
        assert "cowork" in captured, (
            "run_watcher must call _try_start_cowork_observer to "
            "register the Cowork-dir watch."
        )
        assert captured["cowork"].is_alive(), (
            "cowork observer must be started, not just constructed."
        )
        stop_event.set()
        await asyncio.wait_for(watcher_task, timeout=5.0)

    asyncio.run(_scenario())


def test_audit_jsonl_modify_event_triggers_debounced_drift(
    cowork_watcher_env, monkeypatch
):
    """An ``on_modified`` event on an audit.jsonl fires exactly one
    ``update_drifted_files`` call after the debounce window."""
    from watchdog.events import FileModifiedEvent

    from backend import cc_watcher

    drift_calls = {"n": 0}

    def _fake_drift(store=None, *, index=None):
        drift_calls["n"] += 1
        return 0

    monkeypatch.setattr(
        "backend.search_index.update_drifted_files", _fake_drift
    )
    # Shrink the debounce so the test runs fast.
    monkeypatch.setenv("CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC", "0.05")

    handler = cc_watcher._build_cowork_event_handler()

    # Drop a fixture session under the cowork root.
    sess_dir = (
        cowork_watcher_env["cowork_root"]
        / "d_test"
        / "o_test"
        / "local_aaaa1111-2222-3333-4444-555566667777"
    )
    sess_dir.mkdir(parents=True)
    shutil.copy(HAPPY_AUDIT, sess_dir / "audit.jsonl")

    handler.on_modified(
        FileModifiedEvent(str(sess_dir / "audit.jsonl"))
    )

    # Wait for debounce + drift fire.
    import time
    deadline = time.time() + 5.0
    while time.time() < deadline and drift_calls["n"] == 0:
        time.sleep(0.05)

    assert drift_calls["n"] == 1, (
        f"audit.jsonl modify must trigger exactly one drift pass; got "
        f"{drift_calls['n']}"
    )


def test_non_audit_jsonl_events_are_ignored(cowork_watcher_env, monkeypatch):
    """Cowork's tree includes outputs/, uploads/, etc. — only
    audit.jsonl events should queue a drift pass."""
    from watchdog.events import FileModifiedEvent

    from backend import cc_watcher

    drift_calls = {"n": 0}

    def _fake_drift(store=None, *, index=None):
        drift_calls["n"] += 1
        return 0

    monkeypatch.setattr(
        "backend.search_index.update_drifted_files", _fake_drift
    )
    monkeypatch.setenv("CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC", "0.05")

    handler = cc_watcher._build_cowork_event_handler()

    sess_dir = (
        cowork_watcher_env["cowork_root"] / "d" / "o" / "local_x"
    )
    sess_dir.mkdir(parents=True)
    (sess_dir / "outputs").mkdir()
    junk = sess_dir / "outputs" / "stuff.txt"
    junk.write_text("hi")

    handler.on_modified(FileModifiedEvent(str(junk)))

    import time
    time.sleep(0.3)

    assert drift_calls["n"] == 0, (
        "Only audit.jsonl events should queue drift — got drift fired "
        f"on a non-audit file. drift_calls={drift_calls['n']}"
    )
