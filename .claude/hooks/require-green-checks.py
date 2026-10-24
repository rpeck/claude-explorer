#!/usr/bin/env python3
"""Stop hook: do not end a turn while this session's code has no green check run.

Claude Code runs this at the end of each turn (.claude/settings.json). For
each worktree that **this session** changed, it asks whether checked files
changed since HEAD without a passing run of the project's checks, the same
checks that CI runs. If any answers "yes", it blocks the stop once, with
the reason on stderr (exit 2).

- **Only this session's worktrees.** Other agents can work in other
  worktrees of the same repo, in the middle of a change; their work is not
  this session's business (2026-10-24). The hook reads the session's
  transcript and takes the worktrees that hold:
  - the ``file_path`` of an Edit, Write, MultiEdit, or NotebookEdit call;
  - the target of a ``cd`` in a Bash command.
  Without a transcript, it takes the worktree of the session's cwd.
  Limit: a Bash command that changes files without a ``cd`` (in the
  session's cwd) shows no worktree.
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
import re
import runpy
import shlex
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[2] / "scripts" / "check.py"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# `cd <path>` at the start of a command or after && ; || |
CD = re.compile(r"(?:^|&&|;|\|\|?)\s*cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")


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


def _touched_paths(transcript: Path) -> list[str]:
    """The paths that this session's tool calls changed or worked in."""
    paths = []
    try:
        with transcript.open(encoding="utf-8") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue  # the transcript is large; parse only tool calls
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                content = (entry.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_input = block.get("input") or {}
                    if block.get("name") in EDIT_TOOLS:
                        path = tool_input.get("file_path") or tool_input.get("notebook_path")
                        if isinstance(path, str):
                            paths.append(path)
                    elif block.get("name") == "Bash" and isinstance(tool_input.get("command"), str):
                        for target in CD.findall(tool_input["command"]):
                            try:
                                paths.extend(shlex.split(target))
                            except ValueError:
                                continue
    except OSError:
        return []
    return paths


def _owner(path: str, trees: list[Path]) -> Path | None:
    """The worktree that holds ``path`` (the deepest one: worktrees can nest)."""
    real = os.path.realpath(os.path.expanduser(path))
    best = None
    for tree in trees:
        root = os.path.realpath(tree)
        if (real == root or real.startswith(root + os.sep)) and (best is None or len(root) > len(str(best))):
            best = Path(root)
    return best


def _session_worktrees(event: dict, trees: list[Path]) -> set[Path]:
    transcript = event.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        owner = _owner(event.get("cwd") or ".", trees)
        return {owner} if owner else set()
    return {owner for p in _touched_paths(Path(transcript)) if (owner := _owner(p, trees))}


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("stop_hook_active") or not CHECK.is_file():
        return 0
    trees = _worktrees(event.get("cwd") or ".")
    mine = sorted(_session_worktrees(event, trees))
    if not mine:
        return 0
    check = runpy.run_path(str(CHECK))

    problems = []
    for tree in mine:
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
        "Do not report this work as done yet. In these worktrees that this session changed, checked "
        "files changed and no green run of scripts/check.py covers them:\n\n" + "\n\n".join(problems) + "\n\n"
        "Run `uv run python scripts/check.py` in each one and fix what fails. If you stop to report a "
        "failure or to ask a question, say plainly that the checks have not passed.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
