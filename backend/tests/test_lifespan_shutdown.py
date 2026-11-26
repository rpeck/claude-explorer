"""Lifespan shutdown tests for Task B4: explicit background-task cancellation.

The FastAPI lifespan handler in ``backend/main.py`` spawns several background
tasks during startup (migration retry, CC watcher, image-warm scan, FTS5 index
build, summary-cache eager fill). On shutdown, every non-completed task must
be cancelled explicitly and awaited so the process exits cleanly without
debug-unfriendly hangs.

This module pins:

  1. ``test_shutdown_completes_under_500ms``: with the CC watcher disabled
     (its synchronous ``observer.join`` is tested separately), shutdown of all
     other in-flight background tasks completes in < 500 ms.

  2. ``test_shutdown_cancels_in_flight_tasks``: a background task that would
     otherwise sleep for 30 s is cancelled on lifespan exit and never raises
     ``CancelledError`` out of the lifespan context.

The CC watcher's clean shutdown (Observer.stop + join in a finally block,
joined via ``asyncio.to_thread`` so the event loop stays responsive) has its
own coverage in ``test_cc_watcher.py``; this file focuses on the
top-level lifespan contract.

See PLANS/2026.05.18-backend-architecture-cleanup.md task B4.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixture: minimal-task lifespan environment
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_lifespan_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the app at a clean tmp claude_dir/data_dir and disable every
    optional lifespan task by default. Individual tests undo specific env
    vars to enable the task they want to exercise.

    Returns the ``claude_dir`` path.
    """
    from backend import config as config_mod

    claude_dir = tmp_path / "claude"
    (claude_dir / "projects").mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    # Disable every optional task by default; tests enable only what they
    # care about. The CC watcher in particular is disabled here because its
    # observer.join cleanup is bounded by watchdog's own timeout (not the
    # asyncio cancel) and is covered separately.
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_CC_WATCHER", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_CC_WARM", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_MIGRATION", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM", "1")

    config_mod.get_settings.cache_clear()
    try:
        yield claude_dir
    finally:
        config_mod.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.serial
async def test_shutdown_completes_under_500ms_with_inflight_summary_fill(
    minimal_lifespan_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown must complete in < 500ms even with an in-flight summary
    cache fill running.

    Strategy: enable the summary-cache eager-fill task and patch
    ``_read_summaries_parallel`` to a 30-second sleep so the task is
    guaranteed to be mid-flight when we exit the lifespan context. The
    explicit cancellation in the shutdown handler must abandon the
    task's ``asyncio.to_thread`` future immediately — total shutdown
    elapsed time stays well under 500ms.

    Pre-refactor failure mode: if the shutdown handler awaits each task
    individually without bounded timeouts, OR if a task swallows
    CancelledError, the test sleeps the full 30 s and fails.

    The summary-cache fill is the right canary because it uses the same
    asyncio.to_thread + cancel pattern as several other lifespan tasks
    (search_index_task, migration_task). If this one cancels in <500ms,
    the pattern is sound.
    """
    from backend.main import app

    # Enable the eager-fill task (disabled by default in the fixture).
    monkeypatch.delenv(
        "CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM", raising=False
    )
    # Seed at least one JSONL so the eager-fill task has work to do.
    proj = minimal_lifespan_env / "projects" / "proj-A"
    proj.mkdir(parents=True, exist_ok=True)
    import json
    (proj / "sess-0001.jsonl").write_text(json.dumps({
        "type": "user",
        "uuid": "sess-0001-u1",
        "sessionId": "sess-0001",
        "timestamp": "2026-05-01T10:00:00Z",
        "cwd": "/tmp/proj",
        "message": {"role": "user", "content": "hello"},
    }) + "\n")

    started = asyncio.Event()

    def _very_slow(misses: list[Path], stop: Any = None) -> dict[Path, dict | None]:
        # Signal that we're inside the to_thread, then block. The
        # outer asyncio task awaiting this to_thread future will be
        # cancelled on lifespan exit; the future is abandoned and
        # the asyncio side wakes up immediately even though this
        # function keeps sleeping in its OS thread.
        sentinel = minimal_lifespan_env / ".fill_started"
        sentinel.write_text("1")
        # 5s is plenty: the asyncio task is cancelled and abandons the
        # future essentially instantly; we don't actually wait for this
        # function to finish during shutdown. Keeping the sleep finite
        # (vs 30s) bounds pytest-teardown latency at ~5s in the worst
        # case where Python's threading shutdown waits for the OS thread.
        time.sleep(5.0)
        return {}

    with patch(
        "backend.claude_code_reader._read_summaries_parallel",
        side_effect=_very_slow,
    ):
        async with app.router.lifespan_context(app):
            # Wait for the eager-fill task to start its slow work.
            sentinel = minimal_lifespan_env / ".fill_started"
            for _ in range(40):  # up to 2 s
                if sentinel.exists():
                    break
                await asyncio.sleep(0.05)
            assert sentinel.exists(), (
                "Eager-fill task never started; test cannot prove "
                "cancellation behavior without an in-flight task"
            )
            # Trigger shutdown by exiting the context. Time the unwind.
            t0 = time.perf_counter()
        # __aexit__ has returned at this point.
        elapsed = time.perf_counter() - t0
        _ = started  # silence unused warning

    assert elapsed < 0.5, (
        f"Lifespan shutdown took {elapsed:.3f}s; expected < 0.5s. "
        "Background tasks are not being cancelled explicitly — the "
        "shutdown handler is waiting for the in-flight to_thread to "
        "complete instead of cancelling it."
    )


async def test_shutdown_does_not_leak_cancellederror(
    minimal_lifespan_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation of in-flight background tasks must not propagate
    ``CancelledError`` out of the lifespan ``__aexit__``.

    A naive ``await task`` (no try/except, no gather(return_exceptions=True))
    would re-raise the CancelledError from each cancelled task as the
    lifespan unwinds. The explicit cancel pattern collects exceptions via
    ``gather(*, return_exceptions=True)`` and logs them.
    """
    from backend.main import app

    monkeypatch.delenv(
        "CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM", raising=False
    )
    # Seed at least one JSONL.
    proj = minimal_lifespan_env / "projects" / "proj-B"
    proj.mkdir(parents=True, exist_ok=True)
    import json
    (proj / "sess.jsonl").write_text(json.dumps({
        "type": "user",
        "uuid": "sess-u1",
        "sessionId": "sess",
        "timestamp": "2026-05-01T10:00:00Z",
        "cwd": "/tmp/proj",
        "message": {"role": "user", "content": "hi"},
    }) + "\n")

    started = [False]

    def _slow(misses: list[Path], stop: Any = None) -> dict[Path, dict | None]:
        started[0] = True
        time.sleep(5.0)
        return {}

    with patch(
        "backend.claude_code_reader._read_summaries_parallel",
        side_effect=_slow,
    ):
        # Lifespan context exit MUST NOT raise.
        async with app.router.lifespan_context(app):
            for _ in range(40):
                if started[0]:
                    break
                await asyncio.sleep(0.05)
            assert started[0], "eager-fill never started"
        # If we get here without exception, the test passed for that bit.

    # Settle the loop so post-cancel scheduling is consumed.
    await asyncio.sleep(0.05)


@pytest.mark.serial
async def test_watcher_shutdown_does_not_wait_for_a_slow_observer_join(
    minimal_lifespan_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The lifespan exit must not wait for ``watchdog.Observer.join``.

    The lifespan cancels the watcher task. On cancellation the watcher's
    ``finally`` calls ``observer.stop()`` and no ``join()``; the observer
    threads are daemons (``backend.cc_watcher.run_watcher``).

    Issue #100: this test used to assert a wall-clock budget (exit in
    < 0.5 s). Under machine load it failed at random (0.66 to 1.52 s),
    although the code never waited: in every run the fake ``join()`` was
    never called, and the exit took about 1 ms, also with 24 CPU burners
    on 14 cores. The budget measured the machine, not the code.

    Now the proof is an event, not a clock: the fake ``join()`` blocks
    until the test releases it after the exit. Two regressions fail:

    * a join called on the event loop: the exit blocks until the join
      returns (the 10 s safety timeout), so a join returns before the exit;
    * a join awaited through ``to_thread``: the exit waits until the 5 s
      hard cap in ``_cancel_background_tasks``, which logs "exceeded 5s
      budget".
    """
    import logging
    import threading

    from backend.main import app

    caplog.set_level(logging.ERROR, logger="backend.main")

    # Enable the watcher (disabled by default in the fixture).
    monkeypatch.delenv(
        "CLAUDE_EXPLORER_DISABLE_CC_WATCHER", raising=False
    )
    # Force-import the watcher module so the patch targets resolve.
    import backend.cc_watcher  # noqa: F401

    release = threading.Event()
    joins_returned: list[float] = []
    started: list[str] = []

    class _FakeObserver:
        """A watchdog Observer whose join blocks until the test releases it."""

        def stop(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:
            release.wait(10.0)
            joins_returned.append(time.perf_counter())

    def _fake_start_observer() -> Any:
        started.append("observer")
        return _FakeObserver()

    # Patch BOTH observers (image-cache + projects-dir) with slow joiners.
    try:
        with patch(
            "backend.cc_watcher._try_start_observer",
            side_effect=_fake_start_observer,
        ), patch(
            "backend.cc_watcher._try_start_projects_observer",
            side_effect=_fake_start_observer,
        ):
            async with app.router.lifespan_context(app):
                # Give the watcher a beat to start and enter its main loop.
                await asyncio.sleep(0.1)
            exited_at = time.perf_counter()
            joins_before_exit = [t for t in joins_returned if t <= exited_at]
    finally:
        release.set()

    assert started, "the watcher did not start its observers, so the test proves nothing"
    assert joins_before_exit == [], (
        "The lifespan exit waited for observer.join() on the event loop. "
        "On cancellation, call observer.stop() and do not join."
    )
    capped = [r.getMessage() for r in caplog.records if "exceeded 5s budget" in r.getMessage()]
    assert capped == [], (
        f"The lifespan exit waited for a task until the 5 s hard cap: {capped}. "
        "On cancellation, the watcher must not await observer.join()."
    )


async def test_no_background_tasks_leaked_after_shutdown(
    minimal_lifespan_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After lifespan exit, no background asyncio tasks created by the
    lifespan handler are still running.

    Uses the standard pattern: snapshot ``asyncio.all_tasks()`` before and
    after, diff, and assert the set is empty (modulo the current task and
    pytest's own harness).
    """
    from backend.main import app

    monkeypatch.delenv(
        "CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM", raising=False
    )
    # Seed.
    proj = minimal_lifespan_env / "projects" / "proj-C"
    proj.mkdir(parents=True, exist_ok=True)
    import json
    (proj / "sess.jsonl").write_text(json.dumps({
        "type": "user",
        "uuid": "u1",
        "sessionId": "sess",
        "timestamp": "2026-05-01T10:00:00Z",
        "cwd": "/tmp/proj",
        "message": {"role": "user", "content": "x"},
    }) + "\n")

    def _slow(misses: list[Path], stop: Any = None) -> dict[Path, dict | None]:
        time.sleep(5.0)
        return {}

    pre_tasks = {t for t in asyncio.all_tasks() if not t.done()}

    with patch(
        "backend.claude_code_reader._read_summaries_parallel",
        side_effect=_slow,
    ):
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.1)  # let eager-fill spawn

    # Allow the loop to settle so cancelled tasks are reaped.
    await asyncio.sleep(0.1)
    post_tasks = {t for t in asyncio.all_tasks() if not t.done()}
    leaked = post_tasks - pre_tasks - {asyncio.current_task()}
    leaked_names = [t.get_name() for t in leaked]
    assert not leaked, (
        f"Lifespan shutdown leaked tasks: {leaked_names}"
    )
