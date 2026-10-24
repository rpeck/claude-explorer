"""CLI-contract tests for ``scripts/check-article-formats.py``.

Black-box: invoke the script as a subprocess and assert on exit code + output,
which is the only faithful way to test its argument handling.

Earned 2026-06-03: the script accepted file arguments but silently ignored them,
so ``check-article-formats.py <bogus-path>`` printed the success line and exited
0 — a false green of exactly the kind the project's test-integrity rules exist to
prevent. These tests pin the fixed contract: explicit paths ARE checked, and a
missing path is a hard error, never "OK".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-article-formats.py"
OK_LINE = "article image/link formats OK"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_no_args_scans_all_articles_and_passes() -> None:
    # Baseline behavior preserved: a bare run scans articles/*.md and is green.
    res = _run()
    assert res.returncode == 0, res.stdout + res.stderr
    assert OK_LINE in res.stdout


def test_explicit_clean_file_is_checked_and_passes() -> None:
    res = _run("articles/part_3_mcp_server.md")
    assert res.returncode == 0, res.stdout + res.stderr
    assert OK_LINE in res.stdout


def test_missing_file_is_an_error_not_ok() -> None:
    # THE bug: a nonexistent path must never report success.
    bogus = "articles/__nope_does_not_exist__.md"
    res = _run(bogus)
    assert res.returncode == 2, f"missing file did not hard-error: {res.stdout}"
    assert OK_LINE not in res.stdout
    assert "__nope_does_not_exist__" in (res.stdout + res.stderr)


def test_explicit_bad_file_is_actually_checked(tmp_path: Path) -> None:
    # Proves explicit args are honored: a file with an Obsidian embed must fail.
    bad = tmp_path / "bad_article.md"
    bad.write_text("# Bad\n\n![[Pasted image 20260101.png]]\n", encoding="utf-8")
    res = _run(str(bad))
    assert res.returncode == 1, res.stdout + res.stderr
    assert OK_LINE not in res.stdout
    assert "Obsidian image embed" in res.stdout
