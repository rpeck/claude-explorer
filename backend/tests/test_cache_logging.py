"""FileCache logging regression tests — Task B2.

Bug class: ``backend/cache.py`` previously swallowed all exceptions in
``_load_and_cache`` and ``load_many_parallel`` with bare ``except Exception:
return None`` (or ``results[idx] = None``). This masked real bugs —
permission errors, corrupt files, broken loader callables — making them
invisible. Operators saw a missing cache entry with no diagnostic trail.

Contract pinned by these tests:
  1. ``_load_and_cache`` logs at ERROR with the path and a traceback
     (``exc_info``) when the loader raises any non-``MemoryError`` exception.
  2. OSError subclasses (``PermissionError``, ``FileNotFoundError``) are
     NOT downgraded to WARNING — the Council resolved Disagreement 1 in
     favor of a single uniform ``logger.exception`` policy. OSError is too
     broad (PermissionError, ENOSPC, EIO are real bugs, not routine).
  3. ``MemoryError`` propagates out of both ``_load_and_cache`` AND
     ``load_many_parallel`` (symmetric fix per Critic's Disagreement 2).
     Logging in an OOM would allocate, compounding the crash.
  4. Caller contract preserved: on non-MemoryError failure, ``None`` is
     still returned (no behavior change for normal callers).

Bidirectional verification per TESTING.md \u00a72: these tests
FAIL today because the current bare excepts neither log nor re-raise
``MemoryError`` — they just return ``None``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.cache import FileCache


# -----------------------------------------------------------------------------
# _load_and_cache — inner block
# -----------------------------------------------------------------------------


def test_load_and_cache_logs_loader_failure_with_traceback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Generic loader exception (ValueError) -> ERROR log with traceback."""
    cache = FileCache()
    target = tmp_path / "conv.jsonl"
    target.write_text("{}")

    def boom(_p: Path) -> None:
        raise ValueError("simulated corrupt JSON")

    with caplog.at_level(logging.ERROR, logger="backend.cache"):
        result = cache._load_and_cache(target, boom)

    assert result is None, "Caller contract: returns None on failure."
    error_records = [
        r
        for r in caplog.records
        if r.name == "backend.cache" and r.levelno == logging.ERROR
    ]
    assert error_records, (
        f"Expected an ERROR-level record from backend.cache; "
        f"got {[(r.name, r.levelname) for r in caplog.records]}"
    )
    rec = error_records[0]
    assert "conv.jsonl" in rec.getMessage(), (
        f"Expected path in log message; got: {rec.getMessage()!r}"
    )
    assert rec.exc_info is not None, (
        "Expected exc_info (traceback) to be captured via logger.exception."
    )
    # Confirm the actual exception type is preserved.
    assert rec.exc_info[0] is ValueError


def test_load_and_cache_logs_oserror_at_error_level(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """PermissionError (an OSError subclass) is NOT downgraded to WARNING.

    Council Disagreement 1 resolution: OSError encompasses
    PermissionError, ENOSPC, EIO — none of which are routine in a
    single-user FastAPI app reading its own files. Unified
    ``logger.exception`` policy.
    """
    cache = FileCache()
    target = tmp_path / "conv.jsonl"
    target.write_text("{}")

    def denied(_p: Path) -> None:
        raise PermissionError("simulated denied")

    with caplog.at_level(logging.ERROR, logger="backend.cache"):
        result = cache._load_and_cache(target, denied)

    assert result is None
    error_records = [
        r
        for r in caplog.records
        if r.name == "backend.cache" and r.levelno == logging.ERROR
    ]
    assert error_records, (
        "PermissionError must log at ERROR, not WARNING — unified policy."
    )
    assert error_records[0].exc_info is not None
    assert error_records[0].exc_info[0] is PermissionError


def test_load_and_cache_logs_stat_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If ``path.stat()`` itself raises (file vanished), log + return None."""
    cache = FileCache()
    # Build a path that does NOT exist; stat() will raise FileNotFoundError.
    missing = tmp_path / "never_existed.jsonl"

    def should_not_be_called(_p: Path) -> None:  # pragma: no cover
        raise AssertionError("loader called despite stat() failure")

    with caplog.at_level(logging.ERROR, logger="backend.cache"):
        result = cache._load_and_cache(missing, should_not_be_called)

    assert result is None
    error_records = [
        r
        for r in caplog.records
        if r.name == "backend.cache" and r.levelno == logging.ERROR
    ]
    assert error_records, "stat() failure must be logged."
    assert "never_existed.jsonl" in error_records[0].getMessage()


def test_load_and_cache_propagates_memory_error(tmp_path: Path) -> None:
    """``MemoryError`` from loader must propagate, not return None.

    Logging in an OOM would allocate memory to build the LogRecord,
    compounding the failure. The Council (Disagreement 2) ruled that
    callers should see the MemoryError explicitly rather than receive
    a phantom None and limp along on a corrupted heap.
    """
    cache = FileCache()
    target = tmp_path / "conv.jsonl"
    target.write_text("{}")

    def oom(_p: Path) -> None:
        raise MemoryError("simulated heap exhaustion")

    with pytest.raises(MemoryError):
        cache._load_and_cache(target, oom)


def test_load_and_cache_success_does_not_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Sanity check: happy path emits no ERROR-level records."""
    cache = FileCache()
    target = tmp_path / "conv.jsonl"
    target.write_text("{}")

    def ok(_p: Path) -> dict:
        return {"hello": "world"}

    with caplog.at_level(logging.ERROR, logger="backend.cache"):
        result = cache._load_and_cache(target, ok)

    assert result == {"hello": "world"}
    assert not [r for r in caplog.records if r.name == "backend.cache"], (
        "Happy path must not log."
    )


# -----------------------------------------------------------------------------
# load_many_parallel — outer block (ThreadPoolExecutor result handler)
# -----------------------------------------------------------------------------


def test_load_many_parallel_propagates_memory_error(tmp_path: Path) -> None:
    """Outer block in load_many_parallel must re-raise MemoryError.

    This is Disagreement 2's symmetric fix. Without it, ``future.result()``
    re-raises the worker's MemoryError, the outer ``except Exception:``
    swallows it, and ``results[idx] = None`` masks the OOM silently.
    """
    cache = FileCache()
    paths = []
    for i in range(3):
        p = tmp_path / f"f{i}.jsonl"
        p.write_text("{}")
        paths.append(p)

    def oom(_p: Path) -> None:
        raise MemoryError("simulated heap exhaustion")

    with pytest.raises(MemoryError):
        cache.load_many_parallel(paths, oom)


def test_load_many_parallel_returns_none_on_loader_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Non-MemoryError failures: caller still gets None per index.

    Worker-level failure is logged by ``_load_and_cache``; the outer
    block need not log again on the happy "worker already logged" path.
    But the caller contract — None at each failed index — must hold.
    """
    cache = FileCache()
    paths = []
    for i in range(3):
        p = tmp_path / f"f{i}.jsonl"
        p.write_text("{}")
        paths.append(p)

    def boom(_p: Path) -> None:
        raise RuntimeError("loader broke")

    with caplog.at_level(logging.ERROR, logger="backend.cache"):
        results = cache.load_many_parallel(paths, boom)

    assert results == [None, None, None]
    # Each worker's failure should produce at least one log record per path.
    error_records = [
        r
        for r in caplog.records
        if r.name == "backend.cache" and r.levelno == logging.ERROR
    ]
    assert len(error_records) >= len(paths), (
        f"Expected >={len(paths)} ERROR records (one per failed worker); "
        f"got {len(error_records)}"
    )
