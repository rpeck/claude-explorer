"""CI jobs that test another Python than .python-version must set UV_PYTHON.

.python-version pins the development Python. After `uv sync --python X`, a
plain `uv run` replaces the venv with the pinned Python, unless UV_PYTHON is
set. So a matrix row for 3.15 would test 3.13 and stay green (2026-11-08,
verified with uv 0.7.2). AGENTS.md, "Python (Backend & Fetcher)", states the
rule; this test pins it.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
CHECK_STEP = "The venv uses the matrix Python"


def _pin() -> str:
    return (REPO / ".python-version").read_text(encoding="utf-8").strip()


def _version(spec: str) -> str:
    """'3.14' from '3.14', '3.14t' from '3.14t', '3.13' from 'cpython-3.13-windows-x86_64-none'."""
    match = re.search(r"\b3\.\d+t?\b", spec)
    assert match, f"no Python version in matrix value {spec!r}"
    return match.group()


def _violations(workflow: dict, pin: str) -> list[str]:
    problems = []
    for name, job in workflow.get("jobs", {}).items():
        rows = (job.get("strategy") or {}).get("matrix", {}).get("include", [])
        others = sorted({_version(r["python"]) for r in rows if "python" in r} - {pin})
        if not others:
            continue
        if (job.get("env") or {}).get("UV_PYTHON") != "${{ matrix.python }}":
            problems.append(f"{name}: tests {others} but does not set UV_PYTHON: ${{{{ matrix.python }}}}")
        if CHECK_STEP not in [s.get("name") for s in job.get("steps", [])]:
            problems.append(f"{name}: tests {others} but has no step {CHECK_STEP!r}")
    return problems


def _workflows() -> dict[str, dict]:
    return {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(WORKFLOWS.glob("*.yml"))}


def test_every_job_that_tests_another_python_sets_uv_python() -> None:
    pin = _pin()
    assert re.fullmatch(r"3\.\d+", pin), pin
    problems = [f"{f}: {p}" for f, w in _workflows().items() for p in _violations(w, pin)]
    assert problems == []


def test_the_rule_applies_to_the_pytest_matrix() -> None:
    """Positive control: at least one job tests another Python, so the first
    test checks something."""
    matrix = _workflows()["install-matrix.yml"]["jobs"]["pytest"]["strategy"]["matrix"]["include"]
    assert {_version(r["python"]) for r in matrix} - {_pin()}


def test_the_rule_finds_a_job_without_uv_python() -> None:
    """Proof that the rule can fail: drop UV_PYTHON from the pytest job."""
    workflow = copy.deepcopy(_workflows()["install-matrix.yml"])
    del workflow["jobs"]["pytest"]["env"]["UV_PYTHON"]
    assert any("pytest: tests" in p and "UV_PYTHON" in p for p in _violations(workflow, _pin()))
