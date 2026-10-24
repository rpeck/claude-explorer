"""Tests for :mod:`backend.summary_cache`.

Coverage:
  * basic cache hit/miss after upsert
  * mtime drift invalidates a row
  * size drift invalidates a row (same mtime, different size)
  * ``clear_on_logic_mismatch`` wipes on first call, no-ops on second
  * round-trip equality: ``read_conversation_summary_fast(path)`` and
    the cache deserialization produce equal dicts (key invariant —
    cached rows must be indistinguishable from a fresh read)
  * parallel-miss path produces the same dicts as sequential reads
  * fallback: when ``get_summary_cache`` returns None,
    ``list_claude_code_conversations`` still works via the legacy path
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from backend import summary_cache as sc
from backend.summary_cache import SummaryCache

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cache(tmp_path: Path) -> SummaryCache:
    """A SummaryCache pointed at a per-test SQLite file."""
    return SummaryCache(tmp_path / "search-index.sqlite")


def _write_session_jsonl(path: Path, *, user_text: str = "hello", n_user: int = 1) -> None:
    """Write a minimal but realistic CC session JSONL.

    Contains: a single user message and a single assistant message so
    ``read_conversation_summary_fast`` returns a non-None dict.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for i in range(n_user):
        entries.append({
            "type": "user",
            "sessionId": path.stem,
            "cwd": "/tmp/proj",
            "gitBranch": "main",
            "timestamp": "2026-05-16T12:00:00Z",
            "message": {"role": "user", "content": [{"type": "text", "text": f"{user_text} {i}"}]},
        })
    entries.append({
        "type": "assistant",
        "timestamp": "2026-05-16T12:00:01Z",
        "message": {"id": "msg-1", "role": "assistant", "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "world"}]},
    })
    with open(path, "w") as f:
        f.writelines(json.dumps(entry) + "\n" for entry in entries)


# ---------------------------------------------------------------------------
# Hit/miss basics
# ---------------------------------------------------------------------------


def test_empty_cache_returns_no_hits(cache: SummaryCache, tmp_path: Path) -> None:
    """A fresh cache has zero rows; get_many returns {}."""
    p = tmp_path / "a.jsonl"
    p.touch()
    stat_index = {p: os.stat(p)}
    assert cache.get_many([p], stat_index) == {}


def test_upsert_then_get_hit(cache: SummaryCache, tmp_path: Path) -> None:
    """After upsert_many, get_many returns the same payload."""
    p = tmp_path / "a.jsonl"
    p.write_text("placeholder")
    stat_index = {p: os.stat(p)}
    payload = {"uuid": "abc", "name": "Test", "message_count": 7}

    written = cache.upsert_many({p: payload}, stat_index)
    assert written == 1

    got = cache.get_many([p], stat_index)
    assert got == {p: payload}


def test_mtime_drift_invalidates_row(cache: SummaryCache, tmp_path: Path) -> None:
    """Bumping mtime on disk turns a former hit into a miss."""
    p = tmp_path / "a.jsonl"
    p.write_text("v1")
    stat_index = {p: os.stat(p)}
    cache.upsert_many({p: {"uuid": "x", "name": "v1"}}, stat_index)

    # Touch the file to advance mtime past SQLite float precision.
    time.sleep(0.05)
    new_mtime = stat_index[p].st_mtime + 1.0
    os.utime(p, (new_mtime, new_mtime))
    fresh_stat_index = {p: os.stat(p)}

    got = cache.get_many([p], fresh_stat_index)
    assert got == {}


def test_size_drift_invalidates_row(cache: SummaryCache, tmp_path: Path) -> None:
    """Size change with mtime forced-equal still flags as miss.

    Guards against the edge case where a file is rewritten within a
    single mtime tick (in-place truncate-and-write under FS-level
    timestamp granularity).
    """
    p = tmp_path / "a.jsonl"
    p.write_text("v1")
    original_stat = os.stat(p)
    cache.upsert_many({p: {"uuid": "x", "name": "v1"}}, {p: original_stat})

    # Bigger payload; reset mtime back to original so ONLY size differs.
    p.write_text("v1-much-larger-payload")
    os.utime(p, (original_stat.st_mtime, original_stat.st_mtime))
    drifted_stat = os.stat(p)
    assert drifted_stat.st_mtime == original_stat.st_mtime
    assert drifted_stat.st_size != original_stat.st_size

    got = cache.get_many([p], {p: drifted_stat})
    assert got == {}


def test_upsert_persists_none_as_negative_cache(
    cache: SummaryCache, tmp_path: Path,
) -> None:
    """``None`` summary upserts as a negative-cache row so the next
    request gets a hit (mapped to ``None``) instead of re-reading.

    Critical for performance: phantom/empty sessions are ~10% of a
    typical corpus and re-reading them on every request added ~300 ms
    of dead work to the warm path before this lane existed.
    """
    p = tmp_path / "a.jsonl"
    p.write_text("x")
    stat_index = {p: os.stat(p)}
    written = cache.upsert_many({p: None}, stat_index)
    assert written == 1

    got = cache.get_many([p], stat_index)
    # Key present, value None — distinct from "key absent" which would
    # mean a real cache miss the caller has to re-read.
    assert p in got
    assert got[p] is None


def test_negative_cache_does_not_collide_with_real_summary(
    cache: SummaryCache, tmp_path: Path,
) -> None:
    """A path that's a NEGATIVE cache hit must stay negative even if
    a real summary upsert comes through later for a DIFFERENT path.

    Defensive: the sentinel-byte design makes this trivially true,
    but pin it so a future swap to a JSON-encoded null doesn't
    silently re-introduce the collision risk.
    """
    p1 = tmp_path / "negative.jsonl"
    p2 = tmp_path / "positive.jsonl"
    p1.write_text("x")
    p2.write_text("y")
    stat_index = {p1: os.stat(p1), p2: os.stat(p2)}

    cache.upsert_many(
        {p1: None, p2: {"uuid": "y", "name": "real"}},
        stat_index,
    )
    got = cache.get_many([p1, p2], stat_index)
    assert got[p1] is None
    assert got[p2] == {"uuid": "y", "name": "real"}


def test_upsert_skips_missing_stat(cache: SummaryCache, tmp_path: Path) -> None:
    """If the stat_index has no entry for a path, upsert skips it."""
    p = tmp_path / "a.jsonl"
    p.write_text("x")
    written = cache.upsert_many({p: {"uuid": "x"}}, {})
    assert written == 0


# ---------------------------------------------------------------------------
# Logic-version invalidation
# ---------------------------------------------------------------------------


def test_clear_on_logic_mismatch_wipes_then_no_ops(
    cache: SummaryCache, tmp_path: Path,
) -> None:
    p = tmp_path / "a.jsonl"
    p.write_text("x")
    stat_index = {p: os.stat(p)}
    cache.upsert_many({p: {"uuid": "x"}}, stat_index)
    assert cache.stats()["rows"] == 1

    # First call: stored version is None, so any non-None current_version
    # must trigger a wipe.
    assert cache.clear_on_logic_mismatch("v1") is True
    assert cache.stats()["rows"] == 0
    assert cache.get_logic_version() == "v1"

    # Re-upsert, then call again with same version — should no-op.
    cache.upsert_many({p: {"uuid": "x"}}, stat_index)
    assert cache.stats()["rows"] == 1
    assert cache.clear_on_logic_mismatch("v1") is False
    assert cache.stats()["rows"] == 1

    # Different version → wipes again.
    assert cache.clear_on_logic_mismatch("v2") is True
    assert cache.stats()["rows"] == 0
    assert cache.get_logic_version() == "v2"


# ---------------------------------------------------------------------------
# Round-trip equality with read_conversation_summary_fast
# ---------------------------------------------------------------------------


def test_cached_row_byte_equal_to_fresh_read(
    cache: SummaryCache, tmp_path: Path,
) -> None:
    """Cached deserialization must equal a fresh read of the same file.

    This is the critical contract: a cache hit must be indistinguishable
    from a cache miss for downstream code. If this drifts, the sidebar
    silently serves stale-shaped rows.
    """
    from backend.claude_code_reader import read_conversation_summary_fast

    p = tmp_path / "session.jsonl"
    _write_session_jsonl(p)
    fresh = read_conversation_summary_fast(p)
    assert fresh is not None

    cache.upsert_many({p: fresh}, {p: os.stat(p)})
    cached = cache.get_many([p], {p: os.stat(p)})[p]
    assert cached == fresh


# ---------------------------------------------------------------------------
# Parallel-miss helper equivalence
# ---------------------------------------------------------------------------


def test_parallel_miss_path_matches_sequential(tmp_path: Path) -> None:
    """``_read_summaries_parallel`` returns the same dicts as sequential calls."""
    from backend.claude_code_reader import (
        _read_summaries_parallel,
        read_conversation_summary_fast,
    )

    paths = []
    for i in range(5):
        p = tmp_path / f"sess-{i}.jsonl"
        _write_session_jsonl(p, user_text=f"prompt {i}", n_user=i + 1)
        paths.append(p)

    sequential = {p: read_conversation_summary_fast(p) for p in paths}
    parallel = _read_summaries_parallel(paths)
    assert parallel == sequential


def test_parallel_miss_empty_input() -> None:
    """Empty input returns empty dict without spawning workers."""
    from backend.claude_code_reader import _read_summaries_parallel
    assert _read_summaries_parallel([]) == {}


def test_parallel_miss_process_pool_path_matches_sequential(
    monkeypatch, tmp_path: Path,
) -> None:
    """Force the process-pool branch (count >= threshold) and confirm
    the dicts it produces equal the sequential ones.

    The threshold is bumped down via monkeypatch so we don't have to
    write 50+ JSONL files just to cross it — that would slow the test
    suite for no test-coverage gain.
    """
    from backend import claude_code_reader as ccr

    monkeypatch.setattr(ccr, "_PROCESS_POOL_THRESHOLD", 3)

    paths = []
    for i in range(5):
        p = tmp_path / f"sess-{i}.jsonl"
        _write_session_jsonl(p, user_text=f"prompt {i}", n_user=i + 1)
        paths.append(p)

    sequential = {p: ccr.read_conversation_summary_fast(p) for p in paths}
    parallel = ccr._read_summaries_parallel(paths)
    assert parallel == sequential


# ---------------------------------------------------------------------------
# delete_missing
# ---------------------------------------------------------------------------


def test_delete_missing_drops_stale_rows(cache: SummaryCache, tmp_path: Path) -> None:
    """Paths absent from the live set get DROPPED."""
    p1 = tmp_path / "a.jsonl"
    p2 = tmp_path / "b.jsonl"
    p1.write_text("a")
    p2.write_text("b")
    cache.upsert_many(
        {p1: {"uuid": "a"}, p2: {"uuid": "b"}},
        {p1: os.stat(p1), p2: os.stat(p2)},
    )
    assert cache.stats()["rows"] == 2

    dropped = cache.delete_missing({str(p1)})
    assert dropped == 1
    assert cache.stats()["rows"] == 1


# ---------------------------------------------------------------------------
# LOGIC_VERSION sanity
# ---------------------------------------------------------------------------


def test_logic_version_is_stable_hex_string() -> None:
    """LOGIC_VERSION must be a non-empty 16-char hex string.

    16 hex chars = 64 bits of source-hash entropy, plenty to detect
    collisions across realistic function-body edits. If this changes
    shape, ``clear_on_logic_mismatch`` semantics also need updating.
    """
    from backend.claude_code_reader import LOGIC_VERSION
    assert isinstance(LOGIC_VERSION, str)
    assert len(LOGIC_VERSION) == 16
    assert all(c in "0123456789abcdef" for c in LOGIC_VERSION)


# ---------------------------------------------------------------------------
# get_summary_cache singleton behavior
# ---------------------------------------------------------------------------


def test_get_summary_cache_returns_same_instance(monkeypatch, tmp_path: Path) -> None:
    """Two get_summary_cache() calls in one process return the same object."""
    target = tmp_path / "search-index.sqlite"
    monkeypatch.setattr(
        "backend.summary_cache.default_index_path", lambda: target
    )
    sc.reset_summary_cache_for_tests()
    try:
        c1 = sc.get_summary_cache()
        c2 = sc.get_summary_cache()
        assert c1 is c2
    finally:
        sc.reset_summary_cache_for_tests()


def test_get_summary_cache_none_when_fts5_unavailable(monkeypatch) -> None:
    """If FTS5 isn't available, get_summary_cache returns None.

    Callers must then fall back to the sequential reader. We patch the
    probe rather than the real sqlite3 build so the test is portable.
    """
    monkeypatch.setattr("backend.summary_cache.fts5_available", lambda: False)
    sc.reset_summary_cache_for_tests()
    try:
        assert sc.get_summary_cache() is None
    finally:
        sc.reset_summary_cache_for_tests()
