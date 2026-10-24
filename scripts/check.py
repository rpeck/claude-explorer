#!/usr/bin/env python3
"""The project's checks: one list for CI, the pre-commit hook, and agents.

    uv run python scripts/check.py                  # python + frontend (records a green run)
    uv run python scripts/check.py --only e2e       # fixture check + Playwright
    python3 scripts/check.py --verify               # do checked files need a run?
    python3 scripts/check.py --install-hooks        # turn on the pre-commit hook

CI runs the same sections (.github/workflows/test.yml), so the local checks
and CI cannot drift apart. A run of python + frontend that passes records a
digest of the checked files in ``.git`` (per worktree). Then:

- the pre-commit hook (scripts/git-hooks/pre-commit) refuses a commit whose
  checked files are not the content of that green run;
- ``--verify`` and the project's Stop hook (.claude/hooks/) tell an agent
  whether checked files changed without a green run.

There is no flag that records without a run. Earned 2026-10-24: two commits
went in after pytest had failed, and one report read the exit code of
``sed`` instead of pytest's.

Standard library only: the hooks run it with any ``python3``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Files that no check reads. Changes to them need no run. Most other docs
# stay checked: tests read README.md, AGENTS.md, assets/, and every article.
EXCLUDED_DIRS = ("PLANS/", "PROCESS/", "scripts/vm-setup/")
# An untracked file counts only in a code folder, or as a root config file.
UNTRACKED_CODE_DIRS = (
    "backend/", "cli/", "fetcher/", "mcp_server/", "frontend/", "scripts/",
    "benchmarks/", "assets/", ".github/", ".claude/",
)
UNTRACKED_ROOT_SUFFIXES = (".py", ".toml", ".lock", ".cfg", ".ini", ".json")
MARKER_NAME = "check-passed.json"
RECORDING_SECTIONS = {"python", "frontend"}


@dataclass(frozen=True)
class Step:
    name: str
    argv: list[str]
    cwd: str = "."


def _react_doctor_base() -> str:
    for ref in ("main", "origin/main"):
        if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], capture_output=True, check=False).returncode == 0:
            return ref
    return "main"


def default_sections() -> dict[str, list[Step]]:
    tests = ["backend/tests", "fetcher/tests", "mcp_server/tests"]
    return {
        "python": [
            Step("ruff", ["uv", "run", "ruff", "check", "."]),
            Step("compile, SyntaxWarning as error", [
                "uv", "run", "python", "-W", "error::SyntaxWarning", "-m", "compileall", "-q", "-f",
                "backend", "cli", "fetcher", "mcp_server",
            ]),
            Step("pytest", ["uv", "run", "pytest", *tests, "--tb=short"]),
            # addopts deselects the wall-clock tests; run them alone, without xdist.
            Step("pytest, serial tests", [
                "uv", "run", "pytest", *tests, "-o", "addopts=", "-n0", "-m", "serial", "--tb=short",
            ]),
        ],
        "frontend": [
            Step("tsc", ["npx", "tsc", "-b"], cwd="frontend"),
            Step("eslint", ["npx", "eslint", "."], cwd="frontend"),
            Step("vitest", ["npx", "vitest", "run"], cwd="frontend"),
            Step("React Doctor, new errors", [
                "npx", "react-doctor", "--no-dead-code", "--fail-on", "error", "--diff", _react_doctor_base(), ".",
            ], cwd="frontend"),
        ],
        "e2e": [
            Step("e2e fixtures are generated", ["uv", "run", "python", "scripts/generate_e2e_fixtures.py"]),
            Step("e2e fixtures are current", ["git", "diff", "--exit-code", "--stat", "backend/tests/fixtures"]),
            Step("Playwright", ["npx", "playwright", "test", "--reporter=line"], cwd="frontend"),
        ],
    }


# ----- running ------------------------------------------------------------


def run_steps(steps: list[Step], on_start: Callable[[str], None] | None = None) -> int:
    """Run each step. Stop at the first failure and return its exit code."""
    root = _root()
    for step in steps:
        if on_start:
            on_start(step.name)
        print(f"\n== {step.name}", flush=True)
        exe = shutil.which(step.argv[0]) or step.argv[0]  # npx.cmd on Windows
        started = time.monotonic()
        code = subprocess.run([exe, *step.argv[1:]], cwd=root / step.cwd, check=False).returncode
        took = time.monotonic() - started
        if code != 0:
            print(f"\nFAILED: {step.name} (exit {code}, {took:.0f} s)", flush=True)
            return code
        print(f"-- passed: {step.name} ({took:.0f} s)", flush=True)
    return 0


# ----- digests of the checked files -----------------------------------------


def _git(*args: str, input: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=_root(), capture_output=True, text=True, check=True, input=input
    ).stdout


def _root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def _is_checked(path: str, tracked: bool) -> bool:
    if path.startswith(EXCLUDED_DIRS):
        return False
    if tracked:
        return True
    return path.startswith(UNTRACKED_CODE_DIRS) or ("/" not in path and path.endswith(UNTRACKED_ROOT_SUFFIXES))


def worktree_entries() -> dict[str, str]:
    """{path: blob} for the checked files as they are on disk now."""
    root = _root()
    tracked = [p for p in _git("ls-files", "-c", "-z").split("\0") if p]
    untracked = [p for p in _git("ls-files", "-o", "--exclude-standard", "-z").split("\0") if p]
    paths = sorted(
        {p for p in tracked if _is_checked(p, True)} | {p for p in untracked if _is_checked(p, False)}
    )
    paths = [p for p in paths if (root / p).is_file() and "\n" not in p]  # deleted files and gitlinks drop out
    if not paths:
        return {}
    blobs = _git("hash-object", "--stdin-paths", input="\n".join(paths) + "\n").split()
    return dict(zip(paths, blobs, strict=True))


def index_entries() -> dict[str, str]:
    """{path: blob} for the checked files in the index (what a commit takes)."""
    entries = {}
    for line in _git("ls-files", "-s", "-z").split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode, blob, stage = meta.split()
        if mode != "160000" and stage == "0" and _is_checked(path, True):
            entries[path] = blob
    return entries


def head_entries() -> dict[str, str]:
    if subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=_root(), capture_output=True, check=False).returncode:
        return {}
    entries = {}
    for line in _git("ls-tree", "-r", "-z", "HEAD").split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        _mode, kind, blob = meta.split()
        if kind == "blob" and _is_checked(path, True):
            entries[path] = blob
    return entries


def digest(entries: dict[str, str]) -> str:
    h = hashlib.sha256()
    for path in sorted(entries):
        h.update(f"{path}\0{entries[path]}\n".encode())
    return h.hexdigest()


def _changed(a: dict[str, str], b: dict[str, str]) -> list[str]:
    return sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))


def _marker_path() -> Path:
    path = Path(_git("rev-parse", "--git-path", MARKER_NAME).strip())
    return path if path.is_absolute() else _root() / path


def _read_marker() -> dict | None:
    try:
        return json.loads(_marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_marker(entries_digest: str, sections: list[str]) -> None:
    _marker_path().write_text(
        json.dumps({"digest": entries_digest, "sections": sections, "when": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )


# ----- the questions that the hooks ask -------------------------------------


def _report(title: str, paths: list[str], how: str, stream) -> None:
    print(title, file=stream)
    for p in paths[:15]:
        print(f"  {p}", file=stream)
    if len(paths) > 15:
        print(f"  ... and {len(paths) - 15} more", file=stream)
    print(how, file=stream)


def unverified_changes() -> list[str]:
    """The checked files that changed since HEAD without a green run; [] if none.

    For the repo at the current directory. The project's Stop hook
    (.claude/hooks/require-green-checks.py) calls it for each worktree.
    """
    now, head = worktree_entries(), head_entries()
    if digest(now) == digest(head):
        return []
    marker = _read_marker()
    if marker and marker.get("digest") == digest(now):
        return []
    return _changed(now, head)


def verify_worktree() -> int:
    """0 if the checked files on disk are HEAD's, or the content of the last green run."""
    changed = unverified_changes()
    if not changed:
        return 0
    _report(
        "Checked files changed, and no green run of scripts/check.py covers them:",
        changed,
        "Run: uv run python scripts/check.py",
        sys.stdout,
    )
    return 1


def verify_index() -> int:
    """0 if a commit of the index changes no checked file, or commits the green run's content."""
    staged, head = index_entries(), head_entries()
    if digest(staged) == digest(head):
        return 0
    marker = _read_marker()
    if marker and marker.get("digest") == digest(staged):
        return 0
    _report(
        "pre-commit: these staged checked files are not the content of a green run of scripts/check.py:",
        _changed(staged, head),
        "Run `uv run python scripts/check.py` until it passes, then commit the same content.\n"
        "Do not bypass this with --no-verify.",
        sys.stderr,
    )
    return 1


# ----- main -----------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    sections: dict[str, list[Step]] | None = None,
    on_start: Callable[[str], None] | None = None,
) -> int:
    sections = sections if sections is not None else default_sections()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", action="append", choices=sorted(sections), help="run only this section")
    parser.add_argument("--verify", action="store_true", help="exit 1 if checked files need a run")
    parser.add_argument("--verify-staged", action="store_true", help="the pre-commit hook's question")
    parser.add_argument("--install-hooks", action="store_true", help="use scripts/git-hooks for this repo")
    args = parser.parse_args(argv)

    if args.verify:
        return verify_worktree()
    if args.verify_staged:
        return verify_index()
    if args.install_hooks:
        _git("config", "core.hooksPath", "scripts/git-hooks")
        print("pre-commit hook on: git config core.hooksPath scripts/git-hooks")
        return 0

    chosen = args.only or ["python", "frontend"]
    before = worktree_entries()
    for name in chosen:
        print(f"\n######## {name}", flush=True)
        code = run_steps(sections[name], on_start=on_start)
        if code != 0:
            return code
    if RECORDING_SECTIONS <= set(chosen):
        if worktree_entries() != before:
            print("\nAll checks passed, but checked files changed during the run: nothing recorded. Run again.")
            return 1
        _write_marker(digest(before), chosen)
        print(f"\nAll checks passed ({', '.join(chosen)}). Recorded for the pre-commit and Stop hooks.")
    else:
        print(f"\nAll checks passed ({', '.join(chosen)}). Only python + frontend together record a green run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
