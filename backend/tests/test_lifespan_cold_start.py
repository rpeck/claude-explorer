"""Cold-start lifespan tests for the eager summary-cache fill + delayed
heavy background tasks (per PLANS/OPTIMIZE_COLD_START.md).

These tests are behavioral — they pin the *structure* of the cold-start
sequence rather than its wall-clock latency. The latency win is measured
via `hyperfine`; these tests guard against regressions in:

  1. The eager-fill task runs exactly once at lifespan startup with the
     full discovered JSONL set.
  2. The eager-fill task actually populates `SummaryCache` so the next
     /api/conversations request hits warm rows.
  3. The eager-fill task is non-blocking — the lifespan yields well
     before the fill finishes.
  4. The eager-fill respects `CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM=1`.
  5. The FTS5 build task sleeps at least 500 ms before doing work, so it
     doesn't contend with the eager-fill for first-half-second disk
     bandwidth.
  6. The warm-image-scan task sleeps at least 500 ms before doing work
     (same reason).
  7. Shutdown cancels the eager-fill cleanly — no leaked task, no
     CancelledError leak.
  8. A ProcessPoolExecutor spawned by the eager-fill mid-flight shuts
     down cleanly when lifespan exits (no orphan workers).

Common fixture setup:

* Seeds two small JSONL files under `<tmp>/claude/projects/proj-A/` so
  the `discover_jsonl_files` walk has something real to find.
* Patches the in-process FastAPI `app` via `LifespanManager` so we drive
  the async lifespan directly, without spinning up uvicorn.
* Disables every other lifespan task (CC watcher, FTS5 build, warm
  scan, migration) by default so each test pins exactly one behavior.

Each test isolates its concern with surgical patches — the goal is
*minimum-viable* fixture per test, not a god-fixture that wires
everything.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# `asyncio_mode = "auto"` in pyproject.toml means async tests get the
# event loop automatically; no @pytest.mark.asyncio needed.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, session_uuid: str) -> None:
    """Minimal JSONL session file that the fast reader can parse without
    error. One user message, one assistant message — enough for
    `read_conversation_summary_fast` to return a populated dict."""
    path.parent.mkdir(parents=True, exist_ok=True)
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


@pytest.fixture
def cold_start_claude_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Build a tiny `<tmp>/claude/projects/proj-A/<n>.jsonl` corpus and
    point the app at it via CLAUDE_DIR. Also points data_dir at a tmp
    subdir so we never touch the dev's real config.
    """
    from backend import config as config_mod

    claude_dir = tmp_path / "claude"
    proj = claude_dir / "projects" / "proj-A"
    proj.mkdir(parents=True)
    for i in range(3):
        _write_jsonl(proj / f"sess-{i:04d}.jsonl", f"sess-{i:04d}")

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    # Suppress every OTHER lifespan task by default so each test pins
    # exactly one behavior. Tests that need a specific task enabled
    # will undo the relevant env var.
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_CC_WATCHER", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_CC_WARM", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_MIGRATION", "1")
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION", "1")

    config_mod.get_settings.cache_clear()
    try:
        yield claude_dir
    finally:
        config_mod.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_eager_fill_task_runs_once_with_full_path_set(
    cold_start_claude_dir: Path,
) -> None:
    """Test 1: the eager-fill task runs exactly once during lifespan
    startup, with the full set of JSONL paths the app discovers."""
    from backend.main import app
    from backend.claude_code_reader import discover_jsonl_files

    expected_paths = set(discover_jsonl_files(cold_start_claude_dir))
    assert len(expected_paths) == 3, "fixture sanity check"

    call_args: list[list[Path]] = []

    def _spy(misses: list[Path]) -> dict[Path, dict | None]:
        call_args.append(list(misses))
        # Return realistic shape so upsert_many is exercised too.
        return {p: {"uuid": p.stem, "name": p.stem} for p in misses}

    # Patch the symbol at its module of definition — the eager-fill
    # task imports it from backend.claude_code_reader, but the
    # patched symbol is the one the task will resolve.
    with patch(
        "backend.claude_code_reader._read_summaries_parallel",
        side_effect=_spy,
    ):
        async with app.router.lifespan_context(app):
            # Allow the eager-fill task time to run to completion.
            # The fixture only seeds 3 files so this is fast.
            for _ in range(50):
                if call_args:
                    break
                await asyncio.sleep(0.05)

    assert len(call_args) == 1, (
        f"_read_summaries_parallel should be called exactly once, "
        f"got {len(call_args)}"
    )
    called_paths = set(call_args[0])
    assert called_paths == expected_paths, (
        f"Eager fill called with {called_paths} but expected {expected_paths}"
    )


async def test_eager_fill_task_populates_summary_cache(
    cold_start_claude_dir: Path,
) -> None:
    """Test 2: after lifespan startup the cache has rows for every path
    the eager-fill discovered. A subsequent `get_many` call returns them."""
    from backend.main import app
    from backend.claude_code_reader import discover_jsonl_files
    from backend.summary_cache import get_summary_cache

    paths = list(discover_jsonl_files(cold_start_claude_dir))
    assert paths, "fixture sanity check"

    fake_summaries = {
        p: {"uuid": p.stem, "name": p.stem, "message_count": 2}
        for p in paths
    }

    with patch(
        "backend.claude_code_reader._read_summaries_parallel",
        return_value=fake_summaries,
    ):
        async with app.router.lifespan_context(app):
            # Wait for the eager-fill task to finish writing.
            for _ in range(50):
                cache = get_summary_cache()
                if cache is not None and cache.stats().get("rows", 0) >= len(paths):
                    break
                await asyncio.sleep(0.05)

            cache = get_summary_cache()
            assert cache is not None, "FTS5 should be available in this env"
            stat_index = {p: os.stat(p) for p in paths}
            cached = cache.get_many(paths, stat_index)
            assert set(cached.keys()) == set(paths), (
                f"Cache only has {set(cached.keys())}, expected {set(paths)}"
            )
            for p in paths:
                row = cached[p]
                assert row is not None, f"Row for {p} should be non-None"
                assert row.get("uuid") == p.stem, (
                    f"Cached row for {p} has wrong uuid: {row}"
                )


async def test_eager_fill_task_is_non_blocking(
    cold_start_claude_dir: Path,
) -> None:
    """Test 3: lifespan yield happens within 500 ms even when the
    eager-fill takes 2 s. The server must be up immediately.

    To make this a true non-blocking assertion (rather than passing
    trivially when no eager-fill exists), we ALSO assert the slow
    function actually got called — i.e. the test would fail BOTH if
    the eager-fill blocked startup AND if the eager-fill simply
    never ran.
    """
    from backend.main import app

    call_count = 0

    def _slow(misses: list[Path]) -> dict[Path, dict | None]:
        nonlocal call_count
        call_count += 1
        time.sleep(2.0)
        return {p: {"uuid": p.stem} for p in misses}

    with patch(
        "backend.claude_code_reader._read_summaries_parallel",
        side_effect=_slow,
    ):
        t0 = time.monotonic()
        async with app.router.lifespan_context(app):
            yield_elapsed = time.monotonic() - t0
            assert yield_elapsed < 0.5, (
                f"Lifespan yield blocked for {yield_elapsed:.2f}s; "
                "eager-fill must not block startup"
            )
            # Wait briefly so the eager-fill task gets scheduled and
            # starts running. We don't need it to FINISH (that's 2s);
            # we just need to prove it began. If no eager-fill task
            # exists at all, call_count will still be 0 here and the
            # test fails for the right reason.
            for _ in range(20):
                if call_count > 0:
                    break
                await asyncio.sleep(0.05)
            assert call_count == 1, (
                f"Eager-fill task never started (call_count={call_count}); "
                "test 3 would trivially pass without proving non-blocking"
            )
            # Exit the lifespan without waiting for the slow fill to
            # finish — shutdown handler must cancel it cleanly.


async def test_eager_fill_respects_disable_env_var(
    cold_start_claude_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test 4: setting CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM=1
    causes _read_summaries_parallel to NOT be called by the
    eager-fill task.

    To prevent trivial-pass behavior (the env var doesn't exist yet
    and neither does the eager-fill task), we run TWO trials and
    assert the differential: enabled → ≥1 call, disabled → 0 calls.
    A no-op implementation would fail the enabled trial.
    """
    from backend.main import app
    from backend.config import get_settings as gs

    async def _trial(disabled: bool) -> int:
        # Re-isolate the in-process state so each trial sees a clean
        # cache + settings cache.
        gs.cache_clear()
        if disabled:
            monkeypatch.setenv(
                "CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM", "1"
            )
        else:
            monkeypatch.delenv(
                "CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM",
                raising=False,
            )

        count = 0

        def _spy(misses: list[Path]) -> dict[Path, dict | None]:
            nonlocal count
            count += 1
            return {p: {"uuid": p.stem} for p in misses}

        with patch(
            "backend.claude_code_reader._read_summaries_parallel",
            side_effect=_spy,
        ):
            async with app.router.lifespan_context(app):
                # Generous beat for the eager-fill to fire.
                await asyncio.sleep(0.3)
        return count

    disabled_count = await _trial(disabled=True)
    enabled_count = await _trial(disabled=False)

    assert enabled_count >= 1, (
        f"Eager-fill never ran in the enabled trial "
        f"(enabled_count={enabled_count}); test 4 would trivially pass "
        "without proving the disable-env-var actually disables anything"
    )
    assert disabled_count == 0, (
        f"_read_summaries_parallel should NOT be called when "
        f"CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM=1; "
        f"got {disabled_count} calls"
    )


async def test_fts5_build_honors_500ms_delay(
    cold_start_claude_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test 5: build_full_index is called no sooner than 500 ms after
    lifespan startup, so it doesn't contend with the first
    /api/conversations request for disk bandwidth.

    The behavioral floor is 500ms (the plan's original requirement);
    the production delay is currently 5s — chosen empirically because
    a 500ms delay still left the first request landing mid-FTS5-build
    (~10s contention). 500ms is the LOWER bound; longer is fine and
    intentional. See backend/main.py for the contention rationale.
    """
    from backend.main import app

    # Enable the FTS5 build (default-disabled in the fixture).
    monkeypatch.delenv("CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX", raising=False)

    call_times: list[float] = []

    def _spy(*args: Any, **kwargs: Any) -> tuple[int, int]:
        call_times.append(time.monotonic())
        return (0, 0)

    with patch("backend.search_index.build_full_index", side_effect=_spy):
        t0 = time.monotonic()
        async with app.router.lifespan_context(app):
            # Wait up to 10s — generous headroom for the 5s production
            # delay plus the few-ms `build_full_index` mock call. If
            # the task never fires, the call_times-empty assertion
            # below catches it (with a non-vacuous failure reason).
            for _ in range(200):
                if call_times:
                    break
                await asyncio.sleep(0.05)

    assert call_times, "build_full_index was never called"
    delay = call_times[0] - t0
    assert delay >= 0.5, (
        f"build_full_index fired {delay*1000:.0f}ms after lifespan "
        f"start; expected at least 500ms"
    )


async def test_warm_image_scan_fires_only_when_fts5_disabled(
    cold_start_claude_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test 6 (Phase-2 B): warm_all_sessions_async fires from the
    lifespan ONLY when FTS5 is disabled.

    Default path (FTS5 enabled): the FTS5 build's per-file CC load
    invokes cache_all_markers via read_claude_code_conversation, so
    a parallel warm walk would be duplicate I/O. The standalone
    task is therefore NOT spawned.

    Fallback path (FTS5 disabled): the standalone warm task IS
    spawned WITHOUT the legacy 5 s head-start. The contention
    rationale for the delay only applied when FTS5 was also
    walking the corpus.

    The fixture disables FTS5 by default — so deleting the
    CC_WARM disable env var here exercises the fallback path and
    we expect ONE warm call with NO meaningful delay (< 200 ms,
    i.e. event-loop yield only).
    """
    from backend.main import app

    # Enable the warm scan (default-disabled in the fixture).
    monkeypatch.delenv("CLAUDE_EXPLORER_DISABLE_CC_WARM", raising=False)
    # FTS5 disabled stays — that's the fixture default — so we're
    # exercising the fallback path.

    call_times: list[float] = []

    async def _spy(*args: Any, **kwargs: Any) -> dict:
        call_times.append(time.monotonic())
        return {}

    with patch(
        "backend.cc_image_cache.warm_all_sessions_async",
        side_effect=_spy,
    ):
        t0 = time.monotonic()
        async with app.router.lifespan_context(app):
            for _ in range(200):
                if call_times:
                    break
                await asyncio.sleep(0.05)

    assert call_times, (
        "warm_all_sessions_async was never called even though FTS5 is "
        "disabled (the fallback path should fire it)"
    )
    delay = call_times[0] - t0
    # The 5 s legacy delay is gone — task should fire within ~200 ms
    # (event-loop scheduling slack only). Anything > 500 ms means
    # the legacy sleep is still in place.
    assert delay < 0.5, (
        f"warm_all_sessions_async fired {delay*1000:.0f}ms after "
        f"lifespan start; expected <500ms (legacy 5 s delay removed)"
    )


async def test_warm_image_scan_skipped_when_fts5_enabled(
    cold_start_claude_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test 6b (Phase-2 B): when FTS5 is enabled, the standalone
    warm task does NOT fire. The FTS5 build path
    (search_index._load_conversation_at → read_claude_code_conversation
    → cache_all_markers) covers image-warm implicitly.
    """
    from backend.main import app

    monkeypatch.delenv("CLAUDE_EXPLORER_DISABLE_CC_WARM", raising=False)
    monkeypatch.delenv("CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX", raising=False)

    call_times: list[float] = []

    async def _spy(*args: Any, **kwargs: Any) -> dict:
        call_times.append(time.monotonic())
        return {}

    with patch(
        "backend.cc_image_cache.warm_all_sessions_async",
        side_effect=_spy,
    ):
        async with app.router.lifespan_context(app):
            # Give the lifespan ~2 s to spawn (and potentially
            # invoke) the warm task. If the standalone task is
            # gone (the contract), nothing fires.
            for _ in range(40):
                if call_times:
                    break
                await asyncio.sleep(0.05)

    assert call_times == [], (
        f"warm_all_sessions_async fired {len(call_times)} times with FTS5 "
        "enabled; the standalone task should be skipped — FTS5 build "
        "covers image-warm via cache_all_markers in _load_conversation_at"
    )


async def test_shutdown_cancels_eager_fill_cleanly(
    cold_start_claude_dir: Path,
) -> None:
    """Test 7: when lifespan exits mid-fill, no CancelledError leaks out
    and no asyncio task is left running.

    We patch _read_summaries_parallel to a long sleep so the fill is
    guaranteed to still be running when we exit the lifespan context.

    Prevents trivial-pass: also asserts the eager-fill task actually
    started before shutdown. If no eager-fill task exists, this test
    is vacuous; the call_count guard fails it in the RED state.
    """
    from backend.main import app

    call_count = 0

    def _very_slow(misses: list[Path]) -> dict[Path, dict | None]:
        nonlocal call_count
        call_count += 1
        time.sleep(30.0)  # way longer than the test will wait
        return {}

    pre_tasks = {t for t in asyncio.all_tasks() if not t.done()}

    with patch(
        "backend.claude_code_reader._read_summaries_parallel",
        side_effect=_very_slow,
    ):
        # The async with block MUST NOT raise CancelledError on exit.
        async with app.router.lifespan_context(app):
            # Wait for the eager-fill to actually start. This proves
            # we're exercising real cancellation, not vacuously
            # passing because no task exists.
            for _ in range(20):
                if call_count > 0:
                    break
                await asyncio.sleep(0.05)
            assert call_count == 1, (
                f"Eager-fill never ran (call_count={call_count}); "
                "test 7 would vacuously pass without a real task to cancel"
            )
            # Exiting now triggers shutdown mid-fill.

    # Give the loop a beat to settle any post-cancel scheduling.
    await asyncio.sleep(0.05)
    post_tasks = {t for t in asyncio.all_tasks() if not t.done()}
    leaked = post_tasks - pre_tasks - {asyncio.current_task()}
    # Filter out any post-fixture tasks (e.g. pytest's own asyncio harness).
    leaked_names = [t.get_name() for t in leaked]
    assert not leaked, (
        f"Lifespan shutdown leaked tasks: {leaked_names}"
    )


async def test_eager_fill_processpool_shutdown_does_not_orphan_workers(
    cold_start_claude_dir: Path,
) -> None:
    """Test 8: if the eager-fill uses a ProcessPoolExecutor and the
    lifespan exits mid-fill, the pool's workers shut down cleanly
    (no orphans).

    Strategy: patch _read_summaries_parallel with a real
    ProcessPoolExecutor that spawns a long-sleeping child, then exit
    the lifespan and assert all child PIDs have exited within a
    bounded wait.

    NOTE: this test would be heavyweight under real fork/exec, so we
    use a much smaller pool (1 worker) and a child function that
    sleeps just long enough to be running when shutdown fires.
    """
    from concurrent.futures import ProcessPoolExecutor
    from backend.main import app

    child_pids: list[int] = []
    pool_done = threading_event_class()

    def _record_pid_and_sleep(seconds: float) -> int:
        """Run in the worker process — records its PID via stdout
        coordination would require IPC; instead the parent records
        the worker's PID via `executor._processes` after submit."""
        time.sleep(seconds)
        return os.getpid()

    def _fake_parallel(misses: list[Path]) -> dict[Path, dict | None]:
        """Spawn a ProcessPoolExecutor identical to the real one,
        run a sleeping child, return its pid for the parent to
        verify it exits cleanly."""
        try:
            with ProcessPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_record_pid_and_sleep, 2.0)
                # Surface the worker pid via the executor's internal
                # bookkeeping. This is private API but stable since
                # 3.8; the alternative (IPC over a Queue) would
                # complicate the test without adding value.
                for proc in pool._processes.values():
                    child_pids.append(proc.pid)
                result = fut.result(timeout=10.0)
                pool_done.set()
                return {p: {"pid": result} for p in misses}
        except Exception:
            pool_done.set()
            return {}

    with patch(
        "backend.claude_code_reader._read_summaries_parallel",
        side_effect=_fake_parallel,
    ):
        async with app.router.lifespan_context(app):
            # Let the eager-fill task spawn the pool.
            for _ in range(30):
                if child_pids:
                    break
                await asyncio.sleep(0.05)
            assert child_pids, "Child worker pid was never recorded"
            # Exit the lifespan WHILE the worker is still sleeping
            # so shutdown happens mid-pool.

    # After lifespan exit, the ProcessPoolExecutor's __exit__ should
    # have join()ed its workers (atexit guarantees this even if we
    # cancelled the asyncio task mid-flight, because the executor
    # was created as a context manager in the worker thread).
    # Verify by polling each child pid: it MUST exit within ~10s.
    # Windows spawns workers rather than forking them, so give the slower
    # teardown more room than the POSIX budget.
    budget = 30.0 if sys.platform == "win32" else 10.0
    deadline = time.monotonic() + budget
    for pid in child_pids:
        while time.monotonic() < deadline:
            if not _process_is_alive(pid):
                break
            time.sleep(0.05)
        else:
            pytest.fail(
                f"Worker pid {pid} did not exit within {budget:.0f}s after lifespan "
                "shutdown; ProcessPoolExecutor leaked workers"
            )


def _process_is_alive(pid: int) -> bool:
    """Portable liveness probe for a child process.

    ``os.kill(pid, 0)`` is the POSIX idiom: signal 0 performs the permission
    and existence checks without delivering anything.

    On Windows it is not a probe at all. Python maps ``os.kill`` to
    ``TerminateProcess`` for every signal except the console-control events,
    so this call would KILL the worker it is supposed to observe. A pid that
    has already exited also raises ``OSError`` there rather than
    ``ProcessLookupError``, so the exit is never detected and the poll runs
    to its deadline.

    Windows therefore asks the kernel directly instead.
    """
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, but owned by another user.
        return True


def threading_event_class():
    """Tiny indirection so test_8 reads cleanly without a top-level
    `import threading` (keeps the import list minimal)."""
    import threading

    return threading.Event()
