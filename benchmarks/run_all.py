#!/usr/bin/env python3
"""Run-all benchmark harness for Claude Explorer perf work.

PLANS/PERFORMANCE_PHASE_2.md §Workstream D.

One command, one output, easy to copy-paste into PR descriptions:

    make bench
    # or directly:
    uv run python benchmarks/run_all.py
    uv run python benchmarks/run_all.py --json
    uv run python benchmarks/run_all.py --base http://localhost:8765

Covers every perf-sensitive surface called out in the plan:

  * ``/api/conversations`` (sidebar list)
  * ``/api/search?q=python`` (cold + warm)
  * ``/api/search?q=foobar`` (cold + warm)
  * ``/api/conversations/{SMALL_uuid}`` (warm)
  * ``/api/conversations/{MEDIUM_uuid}`` (warm)
  * ``/api/conversations/{LARGE_uuid}`` (warm)
  * ``/api/conversations/{LARGE_uuid}/export/markdown`` (warm)

The harness auto-picks a representative LARGE / MEDIUM / SMALL
conversation from the live local corpus by walking
``~/.claude/projects/`` for CC JSONLs and ``~/.claude-explorer/conversations/``
for Desktop JSONs and choosing files at the 5th, 50th, and 95th
percentile of file size. The chosen UUIDs are printed in every output
mode so runs are reproducible — pin them via ``--small/--medium/--large``
flags if you want byte-for-byte deterministic comparisons across runs.

Cold-restart measurements require the server be restarted between
runs (see ``--cold-search`` flag). Cold-cache measurements at the
OS level (``sudo purge`` on macOS, ``echo 3 > /proc/sys/vm/drop_caches``
on Linux) are NOT automated — print a reminder and let the user run
the privileged step manually.

Output formats:
  * Default: human-readable table on stdout.
  * ``--json``: machine-readable JSON to stdout, suitable for piping
    into PR bodies (``uv run python benchmarks/run_all.py --json
    | tee bench-results.json``).

No new runtime deps — pure stdlib + ``urllib`` for HTTP, matching
``benchmarks/bench_perf.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# UUID-shaped stem (8-4-4-4-12 hex). Filters out subagent files
# (``agent-<hash>.jsonl``) and metadata files (``_index.json``,
# ``.migration_log.json``) so the bench only picks files the
# detail-load API can actually serve.
_UUID_STEM_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Stats container
# ---------------------------------------------------------------------------


@dataclass
class Stats:
    """Per-endpoint timing summary."""

    label: str
    samples_ms: list[float] = field(default_factory=list)
    payload_bytes: int = 0
    runs: int = 0

    @property
    def mean(self) -> float:
        return statistics.mean(self.samples_ms) if self.samples_ms else 0.0

    @property
    def median(self) -> float:
        return statistics.median(self.samples_ms) if self.samples_ms else 0.0

    @property
    def p95(self) -> float:
        if not self.samples_ms:
            return 0.0
        if len(self.samples_ms) == 1:
            return self.samples_ms[0]
        ranked = sorted(self.samples_ms)
        idx = max(0, round(0.95 * (len(ranked) - 1)))
        return ranked[idx]

    @property
    def max(self) -> float:
        return max(self.samples_ms) if self.samples_ms else 0.0

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "runs": self.runs,
            "payload_bytes": self.payload_bytes,
            "mean_ms": round(self.mean, 1),
            "median_ms": round(self.median, 1),
            "p95_ms": round(self.p95, 1),
            "max_ms": round(self.max, 1),
            "samples_ms": [round(s, 1) for s in self.samples_ms],
        }


# ---------------------------------------------------------------------------
# Fixture discovery — pick small / medium / large UUIDs from live corpus
# ---------------------------------------------------------------------------


def _default_cc_dir() -> Path:
    """``~/.claude`` unless overridden by ``CLAUDE_DIR``."""
    override = os.environ.get("CLAUDE_DIR")
    return Path(override) if override else Path.home() / ".claude"


def _default_data_dir() -> Path:
    """``~/.claude-explorer/conversations`` unless overridden."""
    override = os.environ.get("CLAUDE_EXPLORER_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude-explorer" / "conversations"


def discover_corpus_files() -> list[tuple[Path, int]]:
    """Walk live corpus and return ``[(path, size_bytes), ...]``.

    Includes both CC JSONLs (``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl``)
    and Desktop JSONs (``~/.claude-explorer/conversations/[by-org/<org>/]<uuid>.json``).
    Returns an empty list if neither source has files — the caller
    degrades gracefully ("no fixture available; skipping detail bench").
    """
    files: list[tuple[Path, int]] = []

    cc_dir = _default_cc_dir() / "projects"
    if cc_dir.exists():
        for project_dir in cc_dir.iterdir():
            if not project_dir.is_dir():
                continue
            # Top-level <uuid>.jsonl only — subagent files live
            # under <conv-uuid>/subagents/agent-<hash>.jsonl and
            # the detail API can't serve those by UUID.
            for jsonl in project_dir.glob("*.jsonl"):
                if not _UUID_STEM_RE.match(jsonl.stem):
                    continue
                try:
                    size = jsonl.stat().st_size
                except OSError:
                    continue
                files.append((jsonl, size))

    data_dir = _default_data_dir()
    if data_dir.exists():
        # Desktop layout: by-org/<org>/<uuid>.json AND legacy <uuid>.json.
        by_org = data_dir / "by-org"
        candidates: list[Path] = []
        if by_org.exists():
            for org_dir in by_org.iterdir():
                if org_dir.is_dir():
                    candidates.extend(org_dir.glob("*.json"))
        candidates.extend(data_dir.glob("*.json"))
        for jp in candidates:
            # UUID-shaped stems only — skips _index.json,
            # .migration_log.json, and anything else not addressable
            # by the /api/conversations/{uuid} route.
            if not _UUID_STEM_RE.match(jp.stem):
                continue
            try:
                size = jp.stat().st_size
            except OSError:
                continue
            files.append((jp, size))

    return files


def pick_percentile_uuid(
    files: list[tuple[Path, int]], pct: float
) -> tuple[str, int] | None:
    """Return ``(uuid, size_bytes)`` at the given size percentile, or None."""
    if not files:
        return None
    sorted_files = sorted(files, key=lambda fs: fs[1])
    idx = max(0, min(len(sorted_files) - 1, round((pct / 100.0) * (len(sorted_files) - 1))))
    path, size = sorted_files[idx]
    return path.stem, size


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def time_get(url: str, *, timeout: float = 60.0) -> tuple[float, int, int]:
    """Return ``(elapsed_ms, payload_bytes, status_code)``."""
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        status = exc.code
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return elapsed_ms, len(body), status


def measure(
    label: str,
    url: str,
    *,
    runs: int,
    warmups: int,
    timeout: float = 60.0,
    quiet: bool = False,
) -> Stats:
    """Hit ``url`` ``warmups + runs`` times; collect stats over the runs."""
    if not quiet:
        print(f"  {label}: {warmups} warmup + {runs} measured runs...", file=sys.stderr)
    for _ in range(warmups):
        try:
            time_get(url, timeout=timeout)
        except urllib.error.URLError as exc:
            raise SystemExit(f"failed to reach {url}: {exc}") from exc

    stats = Stats(label=label, runs=runs)
    for i in range(runs):
        ms, n, status = time_get(url, timeout=timeout)
        if status >= 400:
            raise SystemExit(
                f"{url} returned {status} (run {i + 1}/{runs}); aborting bench"
            )
        stats.samples_ms.append(ms)
        stats.payload_bytes = n
        if not quiet:
            print(
                f"    run {i + 1}/{runs}: {ms:7.1f} ms ({n:,} bytes)",
                file=sys.stderr,
            )
    return stats


# ---------------------------------------------------------------------------
# Suite
# ---------------------------------------------------------------------------


def run_suite(
    base: str,
    *,
    small_uuid: str | None,
    medium_uuid: str | None,
    large_uuid: str | None,
    xlarge_uuid: str | None = None,
    quick: bool = False,
) -> list[Stats]:
    """Run the canonical benchmark suite. Returns ordered list of Stats."""
    results: list[Stats] = []

    # 1) List sidebar — biggest payload, hottest path.
    runs = 5 if quick else 10
    results.append(
        measure(
            "list /api/conversations",
            f"{base}/api/conversations",
            runs=runs,
            warmups=2,
        )
    )

    # 2) Search canonical queries — both warm only here. For cold,
    # use --cold-search to restart server between runs (separate flag).
    for q in ("python", "foobar"):
        runs = 3 if quick else 5
        results.append(
            measure(
                f"search /api/search?q={q}",
                f"{base}/api/search?q={urllib.parse.quote(q)}",
                runs=runs,
                warmups=2,
                # Search has a worst-case ~30s before C1+A landed; raise timeout.
                timeout=60.0,
            )
        )

    # 3) Detail load — small / medium / large / xlarge. The xlarge
    #    fixture (99th percentile + by-disk-size) targets the C1
    #    bug surface where the bypass cost was ~1,474 ms; p95
    #    misses it because the heavy tail is only ~1% of corpora.
    for label, uuid in (
        ("detail-small", small_uuid),
        ("detail-medium", medium_uuid),
        ("detail-large", large_uuid),
        ("detail-xlarge", xlarge_uuid),
    ):
        if uuid is None:
            print(
                f"  {label}: skipped (no fixture; corpus too small to pick percentile)",
                file=sys.stderr,
            )
            continue
        runs = 3 if quick else 5
        results.append(
            measure(
                f"{label} /api/conversations/{uuid[:8]}…",
                f"{base}/api/conversations/{uuid}",
                runs=runs,
                warmups=1,
                timeout=60.0,
            )
        )

    # 4) Markdown export of the medium-size conversation — proxy for
    #    PDF (PDF = Markdown work + WeasyPrint render). Large is too
    #    slow on cold runs to be friendly default.
    if medium_uuid is not None:
        runs = 3 if quick else 5
        results.append(
            measure(
                f"export-md /api/conversations/{medium_uuid[:8]}…/export/markdown",
                f"{base}/api/conversations/{medium_uuid}/export/markdown",
                runs=runs,
                warmups=1,
                timeout=60.0,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _fmt_human(
    base: str,
    fixtures: dict,
    results: list[Stats],
) -> str:
    lines = []
    lines.append("")
    lines.append(f"== Claude Explorer benchmark ({base}) ==")
    lines.append("")
    lines.append("Fixtures (auto-picked from live corpus):")
    for label, uuid_and_size in fixtures.items():
        if uuid_and_size is None:
            lines.append(f"  {label}: (none — corpus too small)")
        else:
            uuid, size = uuid_and_size
            lines.append(
                f"  {label}: {uuid}  ({size:>12,} bytes)"
            )
    lines.append("")
    lines.append(
        f"{'label':54s}  {'n':>3s}  {'mean':>7s}  {'p50':>7s}  {'p95':>7s}  {'max':>7s}  {'bytes':>10s}"
    )
    lines.append("-" * 110)
    for s in results:
        lines.append(
            f"{s.label[:54]:54s}  {s.runs:>3d}  "
            f"{s.mean:>7.1f}  {s.median:>7.1f}  {s.p95:>7.1f}  {s.max:>7.1f}  "
            f"{s.payload_bytes:>10,d}"
        )
    lines.append("")
    return "\n".join(lines)


def _fmt_json(
    base: str,
    fixtures: dict,
    results: list[Stats],
) -> str:
    blob = {
        "base": base,
        "host": os.uname().nodename if hasattr(os, "uname") else "",
        "platform": sys.platform,
        "fixtures": {
            label: ({"uuid": v[0], "size_bytes": v[1]} if v else None)
            for label, v in fixtures.items()
        },
        "results": [s.to_json() for s in results],
    }
    return json.dumps(blob, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Claude Explorer perf bench suite against a live "
            "backend on http://localhost:8765 (override with --base). "
            "Auto-picks SMALL/MEDIUM/LARGE conversation fixtures from "
            "the live local corpus."
        ),
    )
    parser.add_argument(
        "--base",
        default="http://localhost:8765",
        help="Base URL for the running Claude Explorer backend",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON suitable for piping into PR bodies",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Fewer runs per measurement; useful for iterating during dev",
    )
    parser.add_argument(
        "--small",
        default=None,
        help="Override the auto-picked SMALL fixture UUID",
    )
    parser.add_argument(
        "--medium",
        default=None,
        help="Override the auto-picked MEDIUM fixture UUID",
    )
    parser.add_argument(
        "--large",
        default=None,
        help="Override the auto-picked LARGE fixture UUID",
    )
    parser.add_argument(
        "--xlarge",
        default=None,
        help=(
            "Override the auto-picked XLARGE fixture UUID. Auto picks "
            "the 99th-percentile-by-size conversation file; the C1 "
            "bug surface is in the heavy tail so this matters."
        ),
    )
    parser.add_argument(
        "--cold-search",
        action="store_true",
        help=(
            "Reminder-only flag. Prints the shell command to restart the "
            "server before re-running this script so /api/search hits "
            "the cold path. Does NOT automate the restart — that's a "
            "developer affordance, not an auto-machine task."
        ),
    )
    args = parser.parse_args()

    if args.cold_search:
        print(
            "Cold-search measurement instructions:\n"
            "  1. Stop the dev server (kill the uvicorn process).\n"
            "  2. (macOS) sudo purge   |   (Linux) sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'\n"
            "  3. Restart the server (DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run uvicorn backend.main:app --port 8765).\n"
            "  4. WAIT for 'search index build complete' on stdout (1-2 s warm, 30-60 s first install).\n"
            "  5. Re-run this script.\n"
            "The /api/search?q=python row in the first measured run is the cold number.",
            file=sys.stderr,
        )
        return 0

    base = args.base.rstrip("/")
    files = discover_corpus_files()
    if not files:
        print(
            "WARNING: no conversation fixtures found at ~/.claude/projects "
            "or ~/.claude-explorer/conversations. Detail-load and export "
            "benches will be skipped.",
            file=sys.stderr,
        )

    small = (args.small, -1) if args.small else pick_percentile_uuid(files, 5)
    medium = (args.medium, -1) if args.medium else pick_percentile_uuid(files, 50)
    large = (args.large, -1) if args.large else pick_percentile_uuid(files, 95)
    xlarge = (args.xlarge, -1) if args.xlarge else pick_percentile_uuid(files, 99)
    fixtures = {"small": small, "medium": medium, "large": large, "xlarge": xlarge}

    if not args.json:
        print(
            f"Running bench against {base} (small/med/large picked by "
            f"file-size percentile)...",
            file=sys.stderr,
        )

    results = run_suite(
        base,
        small_uuid=small[0] if small else None,
        medium_uuid=medium[0] if medium else None,
        large_uuid=large[0] if large else None,
        xlarge_uuid=xlarge[0] if xlarge else None,
        quick=args.quick,
    )

    if args.json:
        print(_fmt_json(base, fixtures, results))
    else:
        print(_fmt_human(base, fixtures, results))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
