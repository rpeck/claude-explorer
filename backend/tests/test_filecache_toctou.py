"""S5 T2c (2026-05-20) — `FileCache.get_or_load + set` TOCTOU.

The bug shape:

  ``get_or_load`` calls ``loader(path)`` to read content, then ``self.set(path, data)``
  with no explicit mtime; ``set`` re-stats inside to capture the mtime.
  Between the loader-read and the set-stat, the on-disk file can be
  mutated. The cache then stores ``(old_content, new_mtime)`` — a stale
  payload labeled with a fresh mtime. Subsequent ``get`` calls see
  ``cached.mtime == on_disk.mtime`` and return the stale data as "fresh."

The fix (this test pins): apply check-read-check inside ``get_or_load``:
capture mtime BEFORE the load, re-stat AFTER, and skip the set on
mismatch (so the next ``get_or_load`` re-loads). ``_load_and_cache``
already captures mtime before load and writes through with the pre-
load mtime — applying the same shape to ``get_or_load`` closes the gap.

The threading.Barrier coordinates the test: the loader and a "mutator"
thread both wait at the barrier, ensuring the file is mutated DURING
the read window.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.cache import FileCache


@pytest.fixture
def fc() -> FileCache:
    return FileCache(max_workers=1, max_entries=128)


def test_get_or_load_skips_cache_when_file_mutates_during_read(
    fc: FileCache, tmp_path: Path
) -> None:
    """A file mutated mid-load must NOT end up in the cache labeled
    with the post-mutation mtime — that would make stale content read
    as fresh on the next ``get``.

    Choreography (threading.Barrier):
      1. Main thread starts the load via ``get_or_load`` in a worker thread.
      2. The loader (instrumented) waits at the barrier.
      3. Main thread waits at the barrier, touches the file to bump mtime.
      4. Both pass the barrier; the loader returns the OLD content.
      5. ``get_or_load`` must observe the post-load stat mismatch and
         NOT cache the (old_content, new_mtime) tuple.
      6. Cache MUST be empty (or holding fresh content). We assert it
         does not hold old-content-as-fresh.
    """
    p = tmp_path / "race.json"
    p.write_text('{"v": "old"}')
    old_content = p.read_text()

    barrier = threading.Barrier(2)
    load_started = threading.Event()

    def slow_loader(path: Path) -> str:
        content = path.read_text()
        load_started.set()
        # Block until the main thread has touched the file.
        barrier.wait(timeout=5.0)
        return content

    result_box: dict[str, object] = {}

    def worker() -> None:
        result_box["data"] = fc.get_or_load(p, slow_loader)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    # Wait until the loader is mid-call.
    assert load_started.wait(timeout=5.0), "loader did not start"

    # Bump the on-disk mtime + content while loader is paused.
    # write_text resets the file, so the next stat sees a new mtime.
    # Use a 1-second offset so mtime is observably different on
    # platforms with low-resolution mtime (HFS+, ext3).
    import os

    new_content = '{"v": "new"}'
    p.write_text(new_content)
    new_stat_time = p.stat().st_mtime + 1.0
    os.utime(p, (new_stat_time, new_stat_time))

    # Release the loader.
    barrier.wait(timeout=5.0)
    t.join(timeout=5.0)

    # The loader returned the OLD content (it read before the mutation).
    assert result_box["data"] == old_content

    # KEY ASSERTION: a fresh ``get`` must NOT return the old content
    # as fresh. The TOCTOU bug would cache (old_content, new_mtime),
    # and ``get`` would see ``current_mtime == cached.mtime`` and
    # return ``(old_content, True)``. Post-fix, the cache either
    # holds nothing (because the set was skipped) or holds the
    # post-mutation content; either way ``get`` must NOT return
    # ``(old_content, True)``.
    data, is_valid = fc.get(p)
    assert not (
        is_valid and data == old_content
    ), (
        "TOCTOU: cache reports old_content as fresh after a mid-load "
        f"mutation. data={data!r}, is_valid={is_valid}"
    )


def test_get_or_load_caches_normally_when_no_concurrent_mutation(
    fc: FileCache, tmp_path: Path
) -> None:
    """Bidirectional sibling: when no mutation occurs during the load,
    ``get_or_load`` MUST cache the content as fresh. Otherwise the TOCTOU
    fix could degenerate into "never cache anything" — which is the wrong
    safety pattern.
    """
    p = tmp_path / "stable.json"
    p.write_text('{"v": "stable"}')

    def loader(path: Path) -> str:
        return path.read_text()

    data = fc.get_or_load(p, loader)
    assert data == '{"v": "stable"}'

    # Second call MUST be a cache hit returning the same content.
    cached_data, is_valid = fc.get(p)
    assert is_valid, "cache miss after stable get_or_load — fix over-zealous"
    assert cached_data == data
