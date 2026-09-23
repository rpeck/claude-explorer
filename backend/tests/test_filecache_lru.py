"""Workstream C1 Option 2 — FileCache LRU cap regression test.

PLANS/PERFORMANCE_PHASE_2.md §Workstream C1 R8.

Bug class: ``FileCache`` (backend/cache.py) currently has no eviction.
A heavy user opening the five heaviest CC sessions in a row pins
~1 GB resident in the FastAPI process for the lifetime of the worker.
Workaround today: restart the server. The plan calls for an LRU cap
that bounds memory growth without thrashing under realistic load.

Contract pinned by these tests:
  1. ``FileCache(max_entries=N)`` constructor accepts a cap.
  2. After inserting ``> N`` distinct paths, the cache holds AT MOST
     ``N`` entries.
  3. Eviction order is LRU: when the cap is exceeded the
     LEAST-recently-accessed entry is dropped first; the most
     recently used entries survive.
  4. ``get`` (a HIT, not just ``set``) counts as a "use" — accessing
     a stale-but-present entry moves it to the MRU end.
  5. Default cap (no ``max_entries=`` passed) is the historical
     "unbounded" behavior so existing tests that mass-load fixtures
     don't regress. Tests that need a cap pass it explicitly.

Bidirectional verification per TESTING.md §2: these tests
FAIL today because ``FileCache.__init__`` doesn't accept
``max_entries`` and the cache never evicts.
"""

from __future__ import annotations

import os
from pathlib import Path

from backend.cache import FileCache


def _touch(path: Path, content: str = "x") -> None:
    """Create a file with predictable content + mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _loader_factory(payloads: dict[Path, object]):
    """Build a loader that returns a stable per-path payload.

    Captures payloads in a closure so the test can assert which keys
    survived eviction.
    """

    def _load(path: Path) -> object:
        return payloads[path]

    return _load


def test_filecache_constructor_accepts_max_entries(tmp_path):
    """``FileCache(max_entries=N)`` must be a valid constructor call.

    The historical signature was ``FileCache(max_workers=8)``; we add
    ``max_entries`` as a SECOND keyword arg without changing the
    default ``max_workers`` behavior.
    """
    cache = FileCache(max_entries=3)
    assert cache is not None
    # max_workers default still 8 (unchanged).
    cache_default = FileCache()
    assert cache_default is not None


def test_filecache_evicts_least_recently_used_on_insert(tmp_path):
    """Inserting > max_entries paths evicts the LRU entry.

    Walk: insert A, B, C with cap=3 → cache holds {A, B, C}.
    Insert D → cache holds {B, C, D}; A was the LRU and got dropped.

    Strong assertion: we don't just count entries — we ASSERT which
    keys survived (via ``cache.get(path)`` returning ``(data, True)``
    or ``(None, False)``).
    """
    cache = FileCache(max_entries=3)

    paths = []
    payloads: dict[Path, str] = {}
    for label in ("a", "b", "c", "d"):
        p = tmp_path / f"{label}.json"
        _touch(p, content=label)
        paths.append(p)
        payloads[p] = f"payload-{label}"

    a, b, c, d = paths
    loader = _loader_factory(payloads)

    # Insert A, B, C in order; cap not yet exceeded.
    assert cache.get_or_load(a, loader) == "payload-a"
    assert cache.get_or_load(b, loader) == "payload-b"
    assert cache.get_or_load(c, loader) == "payload-c"
    # All three present.
    assert cache.get(a)[1] is True, "A should still be cached"
    assert cache.get(b)[1] is True, "B should still be cached"
    assert cache.get(c)[1] is True, "C should still be cached"

    # Insert D — cap=3 exceeded. A was the LRU; it must be evicted.
    # Re-check: get(a) above WAS a "use" — to make this deterministic
    # we want A to be the LRU at insert time. Reset state and redo
    # without the intermediate get() calls.
    cache.clear()
    cache.get_or_load(a, loader)
    cache.get_or_load(b, loader)
    cache.get_or_load(c, loader)
    # Insert D — A must drop.
    cache.get_or_load(d, loader)

    assert cache.get(a)[1] is False, (
        "A was the LRU; it should have been evicted when D was inserted"
    )
    assert cache.get(b)[1] is True, "B should survive eviction"
    assert cache.get(c)[1] is True, "C should survive eviction"
    assert cache.get(d)[1] is True, "D should be present"


def test_filecache_get_promotes_to_most_recent(tmp_path):
    """A ``get`` HIT on an existing entry MUST move it to the MRU end.

    Without this, the cache degrades to FIFO — a long-running session
    accessing a hot conversation repeatedly would evict it once a
    burst of fresh conversations exceeds the cap. The user-facing
    symptom would be "the conversation I keep looking at is the one
    that always loads slowly."
    """
    cache = FileCache(max_entries=3)

    paths = []
    payloads: dict[Path, str] = {}
    for label in ("a", "b", "c", "d"):
        p = tmp_path / f"{label}.json"
        _touch(p, content=label)
        paths.append(p)
        payloads[p] = f"payload-{label}"

    a, b, c, d = paths
    loader = _loader_factory(payloads)

    # Seed A, B, C.
    cache.get_or_load(a, loader)
    cache.get_or_load(b, loader)
    cache.get_or_load(c, loader)

    # Use A — promotes A to MRU; B becomes LRU.
    val, valid = cache.get(a)
    assert valid is True
    assert val == "payload-a"

    # Insert D — B should drop (now LRU), A should survive (MRU).
    cache.get_or_load(d, loader)

    assert cache.get(a)[1] is True, (
        "A was just used; promotion to MRU should have spared it"
    )
    assert cache.get(b)[1] is False, (
        "B was the new LRU after A's promotion; it should have evicted"
    )
    assert cache.get(c)[1] is True
    assert cache.get(d)[1] is True


def test_filecache_unbounded_when_no_cap_given(tmp_path):
    """Default ``FileCache()`` (no ``max_entries=``) keeps the historical
    unbounded behavior.

    Existing call sites in this codebase pass no cap; the global
    cache in ``get_conversation_cache()`` opts in to a sensible
    default elsewhere. This test pins the constructor's BACKWARD-
    COMPATIBLE default so a typo in the new code can't silently
    cap every existing cache.
    """
    cache = FileCache()  # no max_entries

    paths = []
    payloads: dict[Path, str] = {}
    for i in range(20):
        p = tmp_path / f"f{i}.json"
        _touch(p, content=str(i))
        paths.append(p)
        payloads[p] = f"payload-{i}"

    loader = _loader_factory(payloads)
    for p in paths:
        cache.get_or_load(p, loader)

    # All 20 must still be present.
    for p in paths:
        _, valid = cache.get(p)
        assert valid is True, f"unbounded cache evicted {p}"


def test_filecache_stale_entry_eviction_doesnt_break_lru_order(tmp_path):
    """A stale-mtime ``get`` call returns ``(data, False)``; the entry
    is invalidated. This must NOT corrupt the LRU bookkeeping.

    Setup: A, B, C with cap=3. Mutate A's mtime (cache miss on next
    get) THEN insert D. Cap-3 should hold {B, C, D}. A is gone via
    BOTH mtime invalidation AND cap pressure.
    """
    import time

    cache = FileCache(max_entries=3)

    paths = []
    payloads: dict[Path, str] = {}
    for label in ("a", "b", "c"):
        p = tmp_path / f"{label}.json"
        _touch(p, content=label)
        paths.append(p)
        payloads[p] = f"payload-{label}"

    a, b, c = paths
    loader = _loader_factory(payloads)

    cache.get_or_load(a, loader)
    cache.get_or_load(b, loader)
    cache.get_or_load(c, loader)

    # Bump A's mtime so the cache marks it stale on next get.
    new_mtime = time.time() + 10
    os.utime(a, (new_mtime, new_mtime))

    # Get A — stale; cache reports (data, False).
    data, valid = cache.get(a)
    assert valid is False, "mtime changed; cache must report stale"

    # Insert D. We should still respect the cap (≤3 entries).
    d = tmp_path / "d.json"
    _touch(d, content="d")
    payloads[d] = "payload-d"
    cache.get_or_load(d, loader)

    # Total live entries ≤ 3.
    assert cache.stats["entries"] <= 3, (
        f"cap=3 violated after stale + insert: {cache.stats}"
    )
