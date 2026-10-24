#!/usr/bin/env python3
"""Stop hook: do not end a turn while code changed without a green check run.

Claude Code runs this at the end of each turn (.claude/settings.json). For
each worktree of the repo that has ``scripts/check.py``, it asks whether
checked files changed since HEAD without a passing run of the project's
checks, the same checks that CI runs. If any worktree answers "yes", it
blocks the stop once, with the reason on stderr (exit 2).

- It uses the ``check.py`` beside this hook (this project's copy), never a
  worktree's: a worktree can hold an untrusted pull request.
- On the next stop (``stop_hook_active``) it lets the turn end, so that a
  failure can still be reported. It is a checkpoint, not a lock.

Earned 2026-10-24: two commits went in after pytest had failed, and a
report read the exit code of ``sed`` instead of pytest's.
"""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[2] / "scripts" / "check.py"


def _worktrees(cwd: str) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=cwd, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    return [Path(line[len("worktree "):]) for line in out.stdout.splitlines() if line.startswith("worktree ")]


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("stop_hook_active") or not CHECK.is_file():
        return 0
    check = runpy.run_path(str(CHECK))

    problems = []
    for tree in _worktrees(event.get("cwd") or "."):
        if not (tree / "scripts" / "check.py").is_file():
            continue  # an old branch without the protocol
        os.chdir(tree)
        try:
            changed = check["unverified_changes"]()
        except (OSError, subprocess.SubprocessError, ValueError):
            continue  # not a readable worktree
        if changed:
            shown = "\n".join(f"  {p}" for p in changed[:15])
            more = f"\n  ... and {len(changed) - 15} more" if len(changed) > 15 else ""
            problems.append(f"{tree}:\n{shown}{more}")

    if not problems:
        return 0
    print(
        "Do not report this work as done yet. In these worktrees, checked files changed and no green "
        "run of scripts/check.py covers them:\n\n" + "\n\n".join(problems) + "\n\n"
        "Run `uv run python scripts/check.py` in each one and fix what fails. If you stop to report a "
        "failure or to ask a question, say plainly that the checks have not passed.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
