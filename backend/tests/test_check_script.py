"""scripts/check.py: one list of checks for CI, the pre-commit hook, and agents.

The checks record the content that passed. The pre-commit hook refuses a
commit whose checked files differ from that content, and the project's Stop
hook (.claude/hooks/) refuses to end a turn while checked files changed without a green run
(2026-10-24: two commits went in after pytest failed).
"""

from __future__ import annotations

import json
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


def test_a_new_root_file_that_the_run_saw_can_be_committed(repo: Path) -> None:
    """An untracked root file such as .python-version does not count until
    it is tracked. The run had it on disk, so a commit of that same content
    is tested content (2026-11-08: the hook refused it after a green run)."""
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    (repo / ".python-version").write_text("3.13\n", encoding="utf-8")
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    _green_run(repo)
    _git(repo, "add", ".python-version", "backend/code.py")
    _git(repo, "commit", "-q", "-m", "tested")


def test_a_staged_root_file_that_the_run_saw_needs_no_new_run(repo: Path) -> None:
    """The Stop hook asks --verify. Staging the file must not undo the run."""
    (repo / ".python-version").write_text("3.13\n", encoding="utf-8")
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    _green_run(repo)
    _git(repo, "add", ".python-version")
    result = _check(repo, "--verify")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_root_file_changed_after_the_run_needs_a_new_run(repo: Path) -> None:
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    (repo / ".python-version").write_text("3.13\n", encoding="utf-8")
    _green_run(repo)
    (repo / ".python-version").write_text("3.12\n", encoding="utf-8")
    _git(repo, "add", ".python-version")
    with pytest.raises(subprocess.CalledProcessError) as refused:
        _git(repo, "commit", "-q", "-m", "untested")
    assert ".python-version" in refused.value.stderr
    assert _check(repo, "--verify").returncode == 1


def test_a_new_python_version_file_needs_a_run(repo: Path) -> None:
    """.python-version picks the Python that runs the checks, so it counts
    like pyproject.toml, also while untracked."""
    (repo / ".python-version").write_text("3.13\n", encoding="utf-8")
    result = _check(repo, "--verify")
    assert result.returncode == 1
    assert ".python-version" in result.stdout + result.stderr


def test_an_uncounted_file_that_no_check_read_is_refused_when_tracked(repo: Path) -> None:
    """Some checks read only tracked files (test_resource_cleanup.py runs
    `git ls-files "*.py"`). So an untracked file that did not count was not
    tested, although it was on disk during the green run. A commit that
    starts to track it needs a new run."""
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    (repo / "docs").mkdir()
    (repo / "docs" / "x.py").write_text("y = 1\n", encoding="utf-8")
    _green_run(repo)
    _git(repo, "add", "docs/x.py")
    with pytest.raises(subprocess.CalledProcessError) as refused:
        _git(repo, "commit", "-q", "-m", "untested")
    assert "docs/x.py" in refused.value.stderr


def _marker(repo: Path) -> Path:
    return repo / _git(repo, "rev-parse", "--git-path", "check-passed.json").strip()


posix_modes = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no exec bit")


@posix_modes
def test_a_mode_change_needs_a_run(repo: Path) -> None:
    """chmod changes what a commit records, for example whether a hook runs."""
    (repo / "backend" / "code.py").chmod(0o755)
    result = _check(repo, "--verify")
    assert result.returncode == 1
    assert "backend/code.py" in result.stdout + result.stderr


@posix_modes
def test_the_hook_refuses_a_mode_change_that_no_run_covers(repo: Path) -> None:
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    _green_run(repo)
    (repo / "backend" / "code.py").chmod(0o755)
    _git(repo, "add", "backend/code.py")
    with pytest.raises(subprocess.CalledProcessError) as refused:
        _git(repo, "commit", "-q", "-m", "untested mode")
    assert "backend/code.py" in refused.value.stderr

    _green_run(repo)
    _git(repo, "commit", "-q", "-m", "tested mode")


@posix_modes
def test_a_marker_from_before_modes_still_covers_its_content(repo: Path) -> None:
    """A marker that an older check.py wrote has no "modes" field. It still
    covers its content, but not a mode change."""
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    _green_run(repo)
    marker = json.loads(_marker(repo).read_text(encoding="utf-8"))
    marker.pop("modes", None)
    _marker(repo).write_text(json.dumps(marker), encoding="utf-8")
    assert _check(repo, "--verify").returncode == 0

    (repo / "backend" / "code.py").chmod(0o755)
    assert _check(repo, "--verify").returncode == 1


@pytest.mark.parametrize("content", ['[1]', '"text"', '{"digest": 7}', "not json"])
def test_a_malformed_marker_counts_as_no_run(repo: Path, content: str) -> None:
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")
    _marker(repo).write_text(content, encoding="utf-8")
    result = _check(repo, "--verify")
    assert result.returncode == 1, result.stderr
    assert "backend/code.py" in result.stdout
    assert "Traceback" not in result.stderr


def test_removing_a_tracked_file_needs_a_run(repo: Path) -> None:
    """A green run with the file, then `git rm`: the commit is untested."""
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    (repo / "backend" / "other.py").write_text("z = 1\n", encoding="utf-8")
    _git(repo, "add", "backend/other.py")
    _green_run(repo)
    _git(repo, "commit", "-q", "-m", "tested")
    _git(repo, "rm", "-q", "backend/other.py")
    with pytest.raises(subprocess.CalledProcessError) as refused:
        _git(repo, "commit", "-q", "-m", "untested removal")
    assert "backend/other.py" in refused.value.stderr


def test_a_tracked_python_file_in_an_excluded_dir_needs_a_run(repo: Path) -> None:
    """ruff and test_resource_cleanup.py read every tracked .py file, also
    in PROCESS/. Other files there still need no run."""
    (repo / "PROCESS").mkdir()
    (repo / "PROCESS" / "tool.py").write_text("a = 1\n", encoding="utf-8")
    (repo / "PROCESS" / "notes.md").write_text("notes\n", encoding="utf-8")
    _git(repo, "add", "PROCESS")
    _git(repo, "commit", "-q", "-m", "process files")
    (repo / "PROCESS" / "notes.md").write_text("changed\n", encoding="utf-8")
    assert _check(repo, "--verify").returncode == 0
    (repo / "PROCESS" / "tool.py").write_text("a = 2\n", encoding="utf-8")
    result = _check(repo, "--verify")
    assert result.returncode == 1
    assert "PROCESS/tool.py" in result.stdout


def test_the_hook_allows_a_commit_of_docs_only(repo: Path) -> None:
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    (repo / "backend" / "code.py").write_text("x = 2\n", encoding="utf-8")  # unstaged, untested
    (repo / "PLANS" / "plan.md").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "PLANS/plan.md")
    _git(repo, "commit", "-q", "-m", "docs")


def test_the_hook_does_not_need_python3_on_path(tmp_path: Path, repo: Path) -> None:
    """The `python3` on PATH can be Xcode's Python 3.9 (check.py does not
    import there) or, on Windows, the Microsoft Store stub. The hook runs
    check.py through uv, which picks a Python that fits."""
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    for name in ("python3", "python"):
        (fake / name).write_text("#!/bin/sh\necho 'not this python' >&2\nexit 1\n", encoding="utf-8")
        (fake / name).chmod(0o755)
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    (repo / "PLANS" / "plan.md").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "PLANS/plan.md")
    env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    env.pop("GIT_INDEX_FILE", None)
    done = subprocess.run(
        ["git", "commit", "-q", "-m", "docs"], cwd=repo, capture_output=True, text=True, env=env, check=False
    )
    assert done.returncode == 0, done.stderr


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
