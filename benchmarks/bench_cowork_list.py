"""Time the conversation list, and its Cowork part, on this machine's real data.

Why: every ``/api/conversations`` call read and parsed every Cowork session's
whole ``audit.jsonl`` (local sessions and the cloud tasks that Refresh
archives), on the event loop, with no cache. See AGENTS.md "Performance
Work" rule 3: measure first, then every fix must move this number.

Usage (from the repo root):

    uv run python benchmarks/bench_cowork_list.py [--runs 7]

It reads your real Cowork and conversation folders, and writes nothing. The
first call of each kind is reported apart as "cold"; the median of the rest
is the number to compare.
"""

from __future__ import annotations

import argparse
import statistics
import time


def _time(fn, runs: int) -> tuple[int, float, list[float]]:
    """(rows from the first call, first-call ms, ms of the other calls)."""
    times = []
    rows = 0
    for i in range(runs):
        start = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - start) * 1000)
        if i == 0:
            rows = len(result)
    return rows, times[0], times[1:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=7, help="calls per measurement (default 7)")
    args = parser.parse_args()

    from backend.cowork_reader import iter_cowork_session_dirs
    from backend.store import ConversationStore

    store = ConversationStore()
    dirs = list(iter_cowork_session_dirs(store.cowork_root, store.remote_cowork_root))
    size_mb = sum(
        (d / "audit.jsonl").stat().st_size for d in dirs if (d / "audit.jsonl").exists()
    ) / 1e6
    print(f"Cowork sessions: {len(dirs)} ({size_mb:.1f} MB of audit.jsonl)")

    for label, source in (("Cowork rows only", "CLAUDE_COWORK"), ("full list", "all")):
        rows, cold, warm = _time(lambda s=source: store.list_conversations(source=s), args.runs)
        print(
            f"{label:17s} {rows:5d} rows  cold {cold:7.1f} ms  "
            f"warm median {statistics.median(warm):7.1f} ms  "
            f"(warm runs: {', '.join(f'{t:.0f}' for t in warm)})"
        )


if __name__ == "__main__":
    main()
