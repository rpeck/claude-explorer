"""scripts/check.py: one list of checks for CI, the pre-commit hook, and agents.

The checks record the content that passed. The pre-commit hook refuses a
commit whose checked files differ from that content, and the project's Stop
hook (.claude/hooks/) refuses to end a turn while checked files changed without a green run
(2026-10-24: two commits went in after pytest failed).
"""

from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check.py"
HOOK = REPO / "scripts" / "git-hooks" / "pre-commit"


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    env.pop("GIT_INDEX_FILE", None)
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, env=env
    ).stdout


def _check(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "GIT_INDEX_FILE"}
    return subprocess.run(
        [sys.executable, "scripts/check.py", *args],
        cwd=repo, capture_output=True, text=True, env=env, check=False,
    )


_GREEN_RUN = (
    "import runpy, sys; m = runpy.run_path('scripts/check.py'); "
    "ok = m['Step']('ok', [sys.executable, '-c', 'pass']); "
    "sys.exit(m['main']([], sections={'python': [ok], 'frontend': [ok]}))"
)


def _green_run(repo: Path) -> None:
    """A run of check.py whose steps all pass (stand-ins for the real ones)."""
    env = {k: v for k, v in os.environ.items() if k != "GIT_INDEX_FILE"}
    subprocess.run([sys.executable, "-c", _GREEN_RUN], cwd=repo, check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with a copy of the script, one code file, and one doc."""
    r = tmp_path / "repo"
    (r / "scripts" / "git-hooks").mkdir(parents=True)
    shutil.copy(SCRIPT, r / "scripts" / "check.py")
    shutil.copy(HOOK, r / "scripts" / "git-hooks" / "pre-commit")
    (r / "backend").mkdir()
    (r / "backend" / "code.py").write_text("x = 1\n", encoding="utf-8")
    (r / "PLANS").mkdir()
    (r / "PLANS" / "plan.md").write_text("plan\n", encoding="utf-8")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "init")
    return r


def test_a_clean_tree_needs_no_run(repo: Path) -> None:
    assert _check(repo, "--verify").returncode == 0


def test_a_changed_code_file_needs_a_green_run(repo: Path) -> None:
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    result = _check(repo, "--verify")
    assert result.returncode == 1
    assert "backend/code.py" in result.stdout + result.stderr


def test_a_recorded_run_covers_exactly_that_content(repo: Path) -> None:
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    _green_run(repo)
    assert _check(repo, "--verify").returncode == 0

    (repo / "backend" / "code.py").write_text("x = 3\n", encoding="utf-8")
    assert _check(repo, "--verify").returncode == 1


def test_a_new_untracked_code_file_needs_a_green_run(repo: Path) -> None:
    (repo / "backend" / "new.py").write_text("y = 1\n", encoding="utf-8")
    assert _check(repo, "--verify").returncode == 1


def test_a_new_project_hook_file_needs_a_run(repo: Path) -> None:
    """.claude/settings.json and .claude/hooks/ change what an agent does."""
    (repo / ".claude" / "hooks").mkdir(parents=True)
    (repo / ".claude" / "hooks" / "hook.py").write_text("pass\n", encoding="utf-8")
    assert _check(repo, "--verify").returncode == 1


def test_docs_and_root_files_do_not_need_a_run(repo: Path) -> None:
    (repo / "PLANS" / "plan.md").write_text("changed\n", encoding="utf-8")
    (repo / "PLANS" / "new.md").write_text("new\n", encoding="utf-8")
    (repo / "screenshot.png").write_bytes(b"\x89PNG")
    assert _check(repo, "--verify").returncode == 0


def test_a_tracked_article_is_checked_but_an_untracked_image_is_not(repo: Path) -> None:
    """test_check_article_formats.py scans every article, so an article edit
    needs a run. An image that nobody committed does not."""
    (repo / "articles").mkdir()
    (repo / "articles" / "part.md").write_text("text\n", encoding="utf-8")
    _git(repo, "add", "articles/part.md")
    _git(repo, "commit", "-q", "-m", "article")
    (repo / "articles" / "image.png").write_bytes(b"\x89PNG")
    assert _check(repo, "--verify").returncode == 0
    (repo / "articles" / "part.md").write_text("changed\n", encoding="utf-8")
    assert _check(repo, "--verify").returncode == 1


def test_the_hook_refuses_a_commit_that_no_green_run_covers(repo: Path) -> None:
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "backend/code.py")
    with pytest.raises(subprocess.CalledProcessError) as refused:
        _git(repo, "commit", "-q", "-m", "untested")
    assert "check.py" in refused.value.stderr

    _green_run(repo)
    _git(repo, "commit", "-q", "-m", "tested")


def test_the_hook_refuses_a_part_of_the_tested_content(repo: Path) -> None:
    """The run covered two changed files; a commit of one of them is a
    state that nobody tested."""
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "backend" / "other.py").write_text("z = 1\n", encoding="utf-8")
    _green_run(repo)
    _git(repo, "add", "backend/code.py")
    with pytest.raises(subprocess.CalledProcessError):
        _git(repo, "commit", "-q", "-m", "half")


def test_the_hook_allows_a_commit_of_docs_only(repo: Path) -> None:
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")  # unstaged, untested
    (repo / "PLANS" / "plan.md").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "PLANS/plan.md")
    _git(repo, "commit", "-q", "-m", "docs")


def test_a_failing_step_stops_the_run_and_records_nothing(repo: Path, monkeypatch) -> None:
    check = runpy.run_path(str(repo / "scripts" / "check.py"))
    Step = check["Step"]
    started: list[str] = []
    ok = Step("ok", [sys.executable, "-c", "pass"])
    fail = Step("fail", [sys.executable, "-c", "import sys; sys.exit(3)"])
    never = Step("never", [sys.executable, "-c", "pass"])
    monkeypatch.chdir(repo)
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")

    code = check["main"]([], sections={"python": [ok, fail, never], "frontend": [ok]}, on_start=started.append)

    assert code == 3
    assert started == ["ok", "fail"]
    assert _check(repo, "--verify").returncode == 1, "a failed run must not record"

    code = check["main"]([], sections={"python": [ok], "frontend": [ok]}, on_start=started.append)
    assert code == 0
    assert _check(repo, "--verify").returncode == 0, "a green run records the content"


def test_a_partial_run_records_nothing(repo: Path, monkeypatch) -> None:
    """Only python + frontend together cover what the hooks need."""
    check = runpy.run_path(str(repo / "scripts" / "check.py"))
    ok = check["Step"]("ok", [sys.executable, "-c", "pass"])
    monkeypatch.chdir(repo)
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")

    assert check["main"](["--only", "python"], sections={"python": [ok], "frontend": [ok]}) == 0
    assert _check(repo, "--verify").returncode == 1


def test_there_is_no_way_to_record_without_a_run(repo: Path) -> None:
    assert _check(repo, "--record").returncode != 0


def test_ci_runs_the_same_script() -> None:
    """The workflows call scripts/check.py, so CI and the hooks cannot drift."""
    workflow = (REPO / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    for section in ("python", "frontend", "e2e"):
        assert f"scripts/check.py --only {section}" in workflow
