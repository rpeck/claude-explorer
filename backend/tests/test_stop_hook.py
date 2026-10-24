""".claude/hooks/require-green-checks.py: the project's Claude Code Stop hook.

At the end of each turn, it blocks the stop once while a worktree of the
repo has checked changes that no green run of scripts/check.py covers. It
uses its own project's check.py, never a worktree's: a worktree can hold an
untrusted pull request (2026-10-24).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude" / "hooks" / "require-green-checks.py"
SETTINGS = REPO / ".claude" / "settings.json"
CHECK = REPO / "scripts" / "check.py"
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}
GREEN_RUN = (
    "import runpy, sys; m = runpy.run_path('scripts/check.py'); "
    "ok = m['Step']('ok', [sys.executable, '-c', 'pass']); "
    "sys.exit(m['main']([], sections={'python': [ok], 'frontend': [ok]}))"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=ENV)


def _stop(cwd: Path, **extra) -> subprocess.CompletedProcess:
    event = {"hook_event_name": "Stop", "cwd": str(cwd), "stop_hook_active": False, **extra}
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(event),
        capture_output=True, text=True, env=ENV, timeout=60, check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(CHECK, r / "scripts" / "check.py")
    (r / "backend").mkdir()
    (r / "backend" / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "init")
    return r


def test_the_project_settings_run_the_hook() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    commands = [h["command"] for entry in settings["hooks"]["Stop"] for h in entry["hooks"]]
    assert any(".claude/hooks/require-green-checks.py" in c for c in commands)


def test_a_clean_repo_may_stop(repo: Path) -> None:
    assert _stop(repo).returncode == 0


def test_changed_code_blocks_the_stop_once(repo: Path) -> None:
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    result = _stop(repo)
    assert result.returncode == 2
    assert "backend/code.py" in result.stderr
    assert _stop(repo, stop_hook_active=True).returncode == 0


def test_outside_git_and_without_the_protocol(tmp_path: Path, repo: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _stop(plain).returncode == 0
    (repo / "scripts" / "check.py").unlink()
    _git(repo, "commit", "-qam", "no script")
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    assert _stop(repo).returncode == 0, "a worktree without scripts/check.py is not checked"


def test_a_dirty_second_worktree_blocks(tmp_path: Path, repo: Path) -> None:
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    (wt / "backend" / "code.py").write_text("x = 3\n", encoding="utf-8")
    result = _stop(repo)
    assert result.returncode == 2
    assert str(wt) in result.stderr


def test_a_green_run_lets_it_stop(repo: Path) -> None:
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run([sys.executable, "-c", GREEN_RUN], cwd=repo, check=True, capture_output=True, env=ENV)
    assert _stop(repo).returncode == 0


def test_the_hook_never_runs_a_worktrees_script(tmp_path: Path, repo: Path) -> None:
    canary = tmp_path / "ran"
    script = repo / "scripts" / "check.py"
    # Early in the module: the script exits in main(), before any line at its
    # end, and code above `from __future__` is a SyntaxError.
    future = "from __future__ import annotations\n"
    source = script.read_text(encoding="utf-8")
    assert future in source
    script.write_text(source.replace(future, future + f"open({str(canary)!r}, 'w').close()\n", 1), encoding="utf-8")
    _git(repo, "commit", "-qam", "script with a side effect")
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    assert _stop(repo).returncode == 2
    assert not canary.exists(), "the hook ran the worktree's check.py"
