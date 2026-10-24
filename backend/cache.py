"""
High-performance caching for Claude Code conversation files.

This module provides:
1. Memory cache with mtime-based invalidation
2. Parallel file reading with ThreadPoolExecutor
3. Fast JSON parsing with orjson
4. Optional LRU eviction cap (``max_entries``) to bound memory growth
   on long-running servers (see PLANS/PERFORMANCE_PHASE_2.md §C1 R8).

Backend caches at a glance (Cache landscape, 2026-05-18):
  * ``FileCache`` (this module) — in-memory, per-path mtime-keyed cache
    of parsed conversation dicts; LRU-bounded; lost on process restart.
  * ``SummaryCache`` (``backend/summary_cache.py``) — SQLite-persisted
    sidebar summaries; mtime+size invalidation per row; full table wipe
    on ``claude_code_reader.LOGIC_VERSION`` mismatch at lifespan startup.
  * ``SearchIndex`` (``backend/search_index.py``) — SQLite FTS5 inverted
    index; drift-first incremental rebuild keyed on ``indexed_files``
    mtime; full drop+rebuild on ``SCHEMA_VERSION`` bump or column-set
    drift in the ``messages`` virtual table.
"""

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """A cached file entry with its modification time."""
    mtime: float
    data: Any


class FileCache:
    """Thread-safe file cache with mtime-based invalidation.

    When ``max_entries`` is set to a positive integer, the cache evicts
    the least-recently-used entry on insert past the cap. ``get`` hits
    promote the entry to most-recently-used so a hot conversation
    survives bursts of fresh reads.

    When ``max_entries`` is ``None`` (the default), the cache grows
    without bound — preserves the historical behavior for tests and
    call sites that haven't opted in to a cap.

    Threading: an ``RLock`` guards both the backing ``OrderedDict``
    and the LRU bookkeeping. ``get`` takes the lock to record the
    use, so concurrent readers serialize briefly. Acceptable for our
    workload (file I/O dominates).

    Invalidation policy:
      * **Trigger**: every ``get`` re-stats the file and returns
        ``(data, False)`` when the on-disk mtime no longer matches the
        cached entry; an ``OSError`` (file gone) yields the same
        ``(None, False)`` signal but the stale ``CacheEntry`` stays
        resident until a subsequent ``set``, ``invalidate``, ``clear``,
        or LRU eviction overwrites it. The caller treats a non-fresh
        hit as "reload via ``set``", which re-promotes to MRU. Explicit
        ``invalidate(path)`` and ``clear()`` are available for
        orchestrated purges.
      * **Persists across restart**: nothing — the cache is purely
        in-memory; restart is a cold cache.
      * **Full rebuild**: not applicable; entries are re-populated
        lazily on demand. ``clear()`` (or process restart) achieves the
        same effect.
      * **Failure mode**: there's no "fallback" — a ``stat()`` failure
        returns ``(None, False)`` and callers re-load from disk via
        the supplied loader. A loader exception inside
        ``load_many_parallel`` logs and stores ``None`` for that slot;
        ``MemoryError`` propagates so OOM stays visible.
    """

    def __init__(
        self,
        max_workers: int = 8,
        max_entries: int | None = None,
    ):
        # ``OrderedDict`` so we can call ``move_to_end`` for O(1) MRU
        # promotion and ``popitem(last=False)`` for O(1) LRU eviction.
        self._cache: OrderedDict[Path, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._max_workers = max_workers
        if max_entries is not None and max_entries < 1:
            raise ValueError(
                f"max_entries must be >= 1 or None; got {max_entries!r}"
            )
        self._max_entries = max_entries

    def get(self, path: Path) -> tuple[Any | None, bool]:
        """Get cached data if still valid.

        Returns (data, is_valid) - if is_valid is False, data is stale/missing.

        On a HIT (entry present AND mtime matches), promotes the
        entry to most-recently-used so a hot path survives eviction
        under cap pressure. Stale/missing entries are NOT promoted
        (returning ``(data, False)`` signals "drop and reload").
        """
        with self._lock:
            entry = self._cache.get(path)
            if entry is None:
                return None, False

            try:
                current_mtime = path.stat().st_mtime
                if current_mtime == entry.mtime:
                    # Hit — promote to MRU end.
                    self._cache.move_to_end(path)
                    return entry.data, True
                # File changed, cache is stale; do NOT promote — the
                # caller will reload via set() which re-positions.
                return entry.data, False
            except OSError:
                # File no longer exists
                return None, False

    def set(self, path: Path, data: Any, mtime: float | None = None) -> None:
        """Cache data for a file.

        Inserts at the MRU end. If a cap is configured and the cache
        exceeds it after insert, evicts the LRU entry (the first item
        in the OrderedDict).
        """
        if mtime is None:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                return  # Don't cache if we can't get mtime

        with self._lock:
            # If the key already exists, move_to_end after assignment
            # would be redundant — re-assigning a key in OrderedDict
            # does not reposition it. Pop-then-reinsert is the canonical
            # MRU-promotion pattern.
            if path in self._cache:
                del self._cache[path]
            self._cache[path] = CacheEntry(mtime=mtime, data=data)
            # Evict LRU entries until we're under the cap.
            if self._max_entries is not None:
                while len(self._cache) > self._max_entries:
                    self._cache.popitem(last=False)

    def invalidate(self, path: Path) -> None:
        """Remove a file from cache."""
        with self._lock:
            self._cache.pop(path, None)

    def clear(self) -> None:
        """Clear all cached data."""
        with self._lock:
            self._cache.clear()

    def get_or_load(
        self,
        path: Path,
        loader: Callable[[Path], Any],
    ) -> Any:
        """Get from cache or load using the provided function.

        TOCTOU-safe (S5 T2c, 2026-05-20): capture mtime BEFORE the loader
        runs, then re-stat AFTER. If the file mutated during the read,
        the loaded content is stale-relative-to-disk; skip the cache
        write so the next ``get_or_load`` re-loads against the fresh
        on-disk state. The original implementation called ``self.set(path,
        data)`` with no mtime, which re-stat'd inside ``set`` and stored
        ``(old_content, new_mtime)`` — a stale payload labeled fresh.

        Pinned by ``backend/tests/test_filecache_toctou.py``.
        """
        data, is_valid = self.get(path)
        if is_valid:
            return data

        try:
            mtime_before = path.stat().st_mtime
        except OSError:
            # File vanished between cache miss and stat — let the loader
            # surface the error rather than papering over it here.
            return loader(path)

        loaded = loader(path)
        if loaded is None:
            return loaded

        try:
            mtime_after = path.stat().st_mtime
        except OSError:
            # File vanished mid-load. Don't cache (the loaded payload
            # is detached from any current on-disk state); return it
            # to the caller for one-shot use.
            return loaded

        if mtime_before != mtime_after:
            # File mutated during the read. Cache nothing; the caller
            # gets the loaded payload, but the next ``get_or_load`` will
            # re-load against the fresh state instead of returning the
            # stale payload as a hit.
            logger.info(
                "FileCache: %s mutated mid-load (%.6f -> %.6f); "
                "skipping cache to avoid stale-as-fresh entry",
                path,
                mtime_before,
                mtime_after,
            )
            return loaded

        self.set(path, loaded, mtime_before)
        return loaded

    def load_many_parallel(
        self,
        paths: list[Path],
        loader: Callable[[Path], Any],
    ) -> list[Any]:
        """Load multiple files in parallel, using cache where valid.

        Returns results in the same order as paths.
        """
        results: dict[int, Any] = {}
        paths_to_load: list[tuple[int, Path]] = []

        # Check cache first
        for i, path in enumerate(paths):
            data, is_valid = self.get(path)
            if is_valid:
                results[i] = data
            else:
                paths_to_load.append((i, path))

        # Load missing files in parallel
        if paths_to_load:
            with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                future_to_idx = {
                    executor.submit(self._load_and_cache, path, loader): idx
                    for idx, path in paths_to_load
                }
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        results[idx] = future.result()
                    except MemoryError:
                        # Heap is exhausted — logging would allocate and
                        # compound the failure. Let the OOM propagate so
                        # callers see it explicitly rather than receive a
                        # phantom None and limp along on a corrupted heap.
                        raise
                    except Exception:
                        # _load_and_cache already logs its own loader/stat
                        # failures with the path. This block fires only for
                        # executor-internal errors (broken callable,
                        # cancellation, etc.) — defense-in-depth log.
                        logger.exception(
                            "FileCache.load_many_parallel: worker failed for index %d",
                            idx,
                        )
                        results[idx] = None

        # Return in order
        return [results.get(i) for i in range(len(paths))]

    def _load_and_cache(
        self,
        path: Path,
        loader: Callable[[Path], Any],
    ) -> Any:
        """Load a file and cache the result.

        Logs unexpected exceptions at ERROR level (with traceback) and
        returns ``None`` so callers degrade gracefully — the historical
        bare-except behavior is preserved for normal failures, but the
        failure is no longer invisible.

        ``MemoryError`` is re-raised rather than logged: building a
        ``LogRecord`` allocates, and doing that during heap exhaustion
        risks a secondary failure and masks the original OOM.
        """
        try:
            mtime = path.stat().st_mtime
            data = loader(path)
            if data is not None:
                self.set(path, data, mtime)
            return data
        except MemoryError:
            raise
        except Exception:
            logger.exception("FileCache: failed to load %s", path)
            return None

    @property
    def stats(self) -> dict[str, int]:
        """Get cache statistics."""
        with self._lock:
            return {
                "entries": len(self._cache),
                "size_estimate_mb": sum(
                    len(str(e.data)) for e in self._cache.values()
                ) // (1024 * 1024),
            }


def parse_jsonl_fast(path: Path) -> list[dict]:
    """Parse a JSONL file using orjson for speed."""
    entries = []
    try:
        with open(path, "rb") as f:  # Binary mode for orjson
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = orjson.loads(line)
                    entries.append(entry)
                except orjson.JSONDecodeError:
                    pass
    except OSError:
        pass
    return entries


def parse_jsonl_fast_limited(path: Path, max_lines: int = 30) -> list[dict]:
    """Parse only first N lines of a JSONL file using orjson."""
    entries = []
    lines_read = 0
    try:
        with open(path, "rb") as f:
            for line in f:
                lines_read += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = orjson.loads(line)
                    entries.append(entry)
                except orjson.JSONDecodeError:
                    pass
                if lines_read >= max_lines:
                    break
    except OSError:
        pass
    return entries


# Global cache instance - shared across requests.
#
# ``max_entries=4096`` caps in-memory growth at ~4k parsed conversation
# dicts. The cap exists to bound long-running server memory (defending
# against the R8 risk in PLANS/PERFORMANCE_PHASE_2.md §C1), NOT to
# trim the working set — search and sidebar paths call
# ``store.get_all_conversations_raw`` which warms every conversation
# in the corpus on first call. A tight cap (e.g. 64) would thrash
# under that path: the corpus walk warms N entries, evicts down to
# the cap, and the FTS5 scatter-gather then has to re-load most of
# them.
#
# Sizing: the heaviest known CC corpus today is ~1k conversations;
# 4x headroom (~4k) protects us against organic growth while still
# bounding pathological cases (e.g. a test or script that creates
# millions of conversations).
#
# Rationale for the value (PLANS/PERFORMANCE_PHASE_2.md §C1 R8):
#   * Typical CC session: <5 MB parsed in memory.
#   * 4,096 typical entries: ~20 GB resident in the worst case where
#     every entry is "typical". In practice corpora are mixed; mean
#     resident on the user's heavy 991-conv corpus is ~1-2 GB.
#   * Heavy sessions (288 MB on disk) are rare; the LRU keeps the
#     hot N in cache where N ≪ 4096.
#
# A future refinement could weight by serialized size and cap on
# memory bytes rather than entry count. Out of scope for V1.
_conversation_cache = FileCache(max_workers=8, max_entries=4096)


def get_conversation_cache() -> FileCache:
    """Get the global conversation cache."""
    return _conversation_cache


def clear_cache() -> None:
    """Clear the global cache (useful for testing or manual refresh)."""
    _conversation_cache.clear()