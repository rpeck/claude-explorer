"""Benchmark FTS5 vs linear-scan against the same current corpus.

Calls ``backend.search._search_via_index`` and
``backend.search._search_via_linear_scan`` directly, skipping HTTP and
the dispatcher (which always prefers FTS5 when the index is ready).
That lets us compare the two code paths apples-to-apples against the
live corpus, rather than comparing today's FTS5 numbers against
historical pre-FTS5 numbers measured on an earlier corpus.

Usage (no server required):

    uv run python benchmarks/bench_search_paths.py

The script picks up the canonical data directory via the default
``ConversationStore()`` constructor and the existing FTS5 index at
``~/.claude-explorer/search-index.sqlite``. It prints median / p95
for each path over four canonical query shapes (narrow, broad,
very-broad, no-match) plus the hit count, which doubles as a
correctness check: both paths must return identical counts.
"""

from __future__ import annotations

import statistics
import sys
import time

from backend.search import _search_via_index, _search_via_linear_scan
from backend.search_index import get_search_index

# Ensure we use the canonical data dir without env shenanigans.
from backend.store import ConversationStore


def time_call(fn, *args, **kwargs) -> float:
    t0 = time.perf_counter()
    fn(*args, **kwargs)
    return (time.perf_counter() - t0) * 1000


def measure(label: str, fn, runs: int, warmup: int, *args, **kwargs) -> dict:
    for _ in range(warmup):
        fn(*args, **kwargs)
    samples = [time_call(fn, *args, **kwargs) for _ in range(runs)]
    samples.sort()
    n = len(samples)
    p95_idx = max(0, round(0.95 * (n - 1)))
    return {
        "label": label,
        "median_ms": statistics.median(samples),
        "p95_ms": samples[p95_idx],
        "min_ms": min(samples),
        "max_ms": max(samples),
        "n": n,
    }


def main() -> int:
    store = ConversationStore()
    idx = get_search_index()
    if idx is None:
        print("ERROR: SearchIndex unavailable (FTS5 missing?)", file=sys.stderr)
        return 2
    # In a standalone script the lifespan task that calls mark_ready() never
    # runs, so flip the flag ourselves — the on-disk SQLite file is the
    # canonical state and the schema check below is what matters.
    idx.mark_ready()
    if not idx.is_ready():
        print("ERROR: FTS5 index schema not OK; can't bench the fast path", file=sys.stderr)
        return 2

    print(f"corpus: {len(store.list_conversations())} conversations", file=sys.stderr)

    queries = ["cron", "python", "claude", "xyzzyqqzzz-no-match-string"]
    runs = 5  # linear-scan is slow; keep small
    warmup = 1

    # Common kwargs required by _search_via_index's keyword-only args.
    idx_kwargs = {
        "source": "all",
        "context_size": "snippet",
        "sort": "updated_at",
        "sort_order": "desc",
        "conversation_uuid": None,
        "project_path": None,
        "bookmarks": None,
    }

    print(f"{'query':<32} {'path':<14} {'median (ms)':>12} {'p95 (ms)':>12}  hits")
    print("-" * 80)
    for q in queries:
        # Linear-scan path (slow)
        linear = measure(
            f"linear:{q}",
            _search_via_linear_scan,
            runs, warmup,
            store, q,
        )
        # Get a hit count
        n_hits = len(_search_via_linear_scan(store, q))
        print(f"{q:<32} {'linear-scan':<14} {linear['median_ms']:>12.1f} {linear['p95_ms']:>12.1f}  {n_hits}")

        # FTS5 path
        fts = measure(
            f"fts5:{q}",
            _search_via_index,
            runs, warmup,
            store, idx, q,
            **idx_kwargs,
        )
        print(f"{q:<32} {'fts5':<14} {fts['median_ms']:>12.1f} {fts['p95_ms']:>12.1f}  {n_hits}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
