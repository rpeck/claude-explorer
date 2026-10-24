#!/usr/bin/env python3
"""Benchmark the article's perf claims.

The Medium series (`articles/part_2_web_app.md` line 92) cites two
numbers that need to be backed by reproducible measurements:

  - Full-text search returns in around 50 ms on a warm cache
  - Conversation listing comes back in around 0.07 s on a warm cache

This script hits both endpoints N times each, discards the first call as
cold-cache warm-up, and prints mean / median / p95 / max in milliseconds
so we can ship article numbers we actually believe.

Usage (server must be running on http://localhost:8765):

    uv run python benchmarks/bench_perf.py
    uv run python benchmarks/bench_perf.py --runs 20 --query handshake
    uv run python benchmarks/bench_perf.py --base http://localhost:8766

The harness uses urllib (stdlib) so it has zero dependencies; it deliberately
does not start its own server because we want to measure real on-disk
load against the user's actual conversations directory.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Stats:
    label: str
    samples_ms: list[float]
    n_payload_bytes: int

    @property
    def mean(self) -> float:
        return statistics.mean(self.samples_ms)

    @property
    def median(self) -> float:
        return statistics.median(self.samples_ms)

    @property
    def p95(self) -> float:
        if len(self.samples_ms) < 2:
            return self.samples_ms[0]
        # Discrete p95 — good enough for n=10..50.
        ranked = sorted(self.samples_ms)
        idx = max(0, int(round(0.95 * (len(ranked) - 1))))
        return ranked[idx]

    @property
    def max(self) -> float:
        return max(self.samples_ms)

    def format(self) -> str:
        return (
            f"{self.label}\n"
            f"  n={len(self.samples_ms)}  payload={self.n_payload_bytes:,} bytes\n"
            f"  mean   = {self.mean:7.1f} ms\n"
            f"  median = {self.median:7.1f} ms\n"
            f"  p95    = {self.p95:7.1f} ms\n"
            f"  max    = {self.max:7.1f} ms"
        )


def time_get(url: str) -> tuple[float, int]:
    """Return (elapsed_ms, payload_bytes). Raises on non-2xx."""
    start = time.perf_counter()
    with urllib.request.urlopen(url, timeout=30) as resp:
        body = resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"{url} returned {resp.status}")
    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms, len(body)


def measure(label: str, url: str, runs: int, warmup: int) -> Stats:
    """Hit url (warmup + runs) times; return stats over the runs."""
    print(f"warming up {label} ({warmup} discarded calls)...", file=sys.stderr)
    for _ in range(warmup):
        try:
            time_get(url)
        except urllib.error.URLError as exc:
            raise SystemExit(f"failed to reach {url}: {exc}") from exc
    samples: list[float] = []
    bytes_seen = 0
    for i in range(runs):
        ms, n = time_get(url)
        samples.append(ms)
        bytes_seen = n
        print(f"  [{label}] run {i + 1}/{runs}: {ms:7.1f} ms ({n:,} bytes)", file=sys.stderr)
    return Stats(label=label, samples_ms=samples, n_payload_bytes=bytes_seen)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark search + list endpoints.")
    parser.add_argument(
        "--base",
        default="http://localhost:8765",
        help="Base URL for the running Claude Explorer backend",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=10,
        help="Measured runs per endpoint (default 10)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=2,
        help="Warm-up runs (discarded) per endpoint",
    )
    parser.add_argument(
        "--query",
        default="claude",
        help="Search query for the /api/search endpoint",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the human-readable summary",
    )
    args = parser.parse_args()

    base = args.base.rstrip("/")
    list_url = f"{base}/api/conversations"
    search_url = f"{base}/api/search?q={urllib.parse.quote(args.query)}"

    list_stats = measure("conversations.list", list_url, args.runs, args.warmup)
    search_stats = measure(f"search?q={args.query!r}", search_url, args.runs, args.warmup)

    if args.json:
        out = {
            s.label: {
                "n": len(s.samples_ms),
                "payload_bytes": s.n_payload_bytes,
                "mean_ms": s.mean,
                "median_ms": s.median,
                "p95_ms": s.p95,
                "max_ms": s.max,
                "samples_ms": s.samples_ms,
            }
            for s in (list_stats, search_stats)
        }
        print(json.dumps(out, indent=2))
    else:
        print()
        for s in (list_stats, search_stats):
            print(s.format())
            print()
        print("Article claim references (warm cache):")
        print("  * /api/search ≈ 50 ms")
        print("  * /api/conversations ≈ 70 ms")

    return 0


if __name__ == "__main__":
    # Re-import urllib.parse lazily so the top-level import block stays minimal.
    import urllib.parse

    raise SystemExit(main())
