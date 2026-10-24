"""The summary-cache fill stops at Quit (2026-10-23).

At startup the lifespan fills the summary cache with
``_read_summaries_parallel(misses)``: one process-pool ``map`` over every
cache miss. The ``with ProcessPoolExecutor`` block waits for all workers, so
a Quit during a first fill kept the server alive until every summary was
read. PR #10 gave the lifespan a stop flag for the index build; the
summary fill now uses it too: it reads in chunks and checks the flag
between chunks.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any, ClassVar, Self
from unittest.mock import patch

import pytest

from backend import claude_code_reader as ccr


class _InProcessPool:
    """A ProcessPoolExecutor stand-in that runs map() in this process."""

    maps: ClassVar[list[int]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def map(self, fn, items, chunksize: int = 1):
        items = list(items)
        _InProcessPool.maps.append(len(items))
        return [fn(i) for i in items]


@pytest.fixture
def in_process_pool(monkeypatch: pytest.MonkeyPatch):
    _InProcessPool.maps = []
    monkeypatch.setattr(ccr, "ProcessPoolExecutor", _InProcessPool)
    return _InProcessPool


def test_reads_every_path_without_a_stop_flag(in_process_pool, monkeypatch) -> None:
    monkeypatch.setattr(ccr, "read_conversation_summary_fast", lambda p: {"uuid": p.stem})
    paths = [Path(f"/x/{i}.jsonl") for i in range(500)]
    out = ccr._read_summaries_parallel(paths)
    assert len(out) == 500
    assert all(out[p] == {"uuid": p.stem} for p in paths)


def test_stops_after_the_chunk_in_progress(in_process_pool, monkeypatch) -> None:
    stop = threading.Event()
    reads: list[Path] = []

    def _read(p: Path) -> dict:
        reads.append(p)
        if len(reads) == 10:
            stop.set()  # Quit arrives during the first chunk
        return {"uuid": p.stem}

    monkeypatch.setattr(ccr, "read_conversation_summary_fast", _read)
    paths = [Path(f"/x/{i}.jsonl") for i in range(2000)]
    out = ccr._read_summaries_parallel(paths, stop=stop)

    assert len(in_process_pool.maps) == 1, f"kept reading after the stop: {in_process_pool.maps}"
    assert len(out) == in_process_pool.maps[0] < 2000
    assert all(out[p] == {"uuid": p.stem} for p in out), "the finished chunk is kept"


def test_stop_already_set_reads_nothing(in_process_pool, monkeypatch) -> None:
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(ccr, "read_conversation_summary_fast", lambda p: {"uuid": p.stem})
    assert ccr._read_summaries_parallel([Path(f"/x/{i}.jsonl") for i in range(500)], stop=stop) == {}


@pytest.fixture
def summary_lifespan_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Lifespan with only the summary-cache fill enabled, over 3 fake sessions."""
    from backend import config as config_mod

    claude_dir = tmp_path / "claude"
    project = claude_dir / "projects" / "-tmp-p"
    project.mkdir(parents=True)
    for i in range(3):
        (project / f"0000000{i}-0000-4000-8000-000000000000.jsonl").write_text("{}\n")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    for name in (
        "CLAUDE_EXPLORER_DISABLE_CC_WATCHER",
        "CLAUDE_EXPLORER_DISABLE_CC_WARM",
        "CLAUDE_EXPLORER_SKIP_MIGRATION",
        "CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION",
        "CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM",
        "CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX",
    ):
        monkeypatch.setenv(name, "1")
    monkeypatch.delenv("CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM", raising=False)
    monkeypatch.delenv("CLAUDE_EXPORTER_DISABLE_SUMMARY_CACHE_WARM", raising=False)
    config_mod.get_settings.cache_clear()
    yield
    config_mod.get_settings.cache_clear()


async def test_lifespan_passes_its_stop_flag_to_the_summary_fill(summary_lifespan_env) -> None:
    """Exit the lifespan during the fill: the fill sees the flag and returns."""
    from backend.main import app
    from backend.summary_cache import get_summary_cache

    if get_summary_cache() is None:
        pytest.skip("summary cache unavailable (sqlite3 without FTS5)")

    started = threading.Event()
    seen: dict[str, Any] = {}

    def _fake_parallel(paths, stop=None):
        seen["stop"] = stop
        started.set()
        if stop is None:
            return {}
        seen["stopped_in_time"] = stop.wait(timeout=10.0)
        seen["returned_at"] = time.monotonic()
        return {}

    with patch("backend.claude_code_reader._read_summaries_parallel", side_effect=_fake_parallel):
        async with app.router.lifespan_context(app):
            assert await asyncio.to_thread(started.wait, 10.0), "the summary fill never started"
            assert seen["stop"] is not None, "the lifespan did not pass a stop flag to the summary fill"
            exit_at = time.monotonic()
        for _ in range(200):
            if "returned_at" in seen:
                break
            await asyncio.sleep(0.01)

    assert seen.get("stopped_in_time") is True
    assert seen["returned_at"] - exit_at < 2.0
