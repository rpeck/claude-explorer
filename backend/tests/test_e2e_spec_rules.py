"""Two rules for the Playwright specs in frontend/e2e (2026-10-24).

1. No `page.waitForTimeout(...)`. A fixed sleep either races the app on a
   slow runner (flaky) or proves nothing ("nothing happened in 500 ms").
   Wait for a signal instead: the fixtures have waitForScrollQuiet,
   waitForKeyboardShortcuts, recordHistory, recordJumps, the page clock
   for the app's own timers, and the <html data-*> signals.
2. No `/api` route registered before `mockBackend()` in the same test.
   mockBackend registers a route for nearly every /api path plus a
   catch-all, and Playwright runs the route registered last first, so an
   earlier /api route never runs: four tests checked nothing this way.

TESTING.md and testing/playwright.md describe both rules.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
E2E = REPO / "frontend" / "e2e"

SLEEP = re.compile(r"\bwaitForTimeout\(")
BLOCK = re.compile(r"^\s*(test(\.only|\.skip)?\(|test\.beforeEach\()")
API_ROUTE = re.compile(r"\b(page|p|ctx|context)\.route\(\s*['\"`]\*\*/api")
HELPER_ROUTE = re.compile(r"await (mock\w+|install\w+|hold\w+)\(page")
MOCK_CALL = re.compile(r"\bmockBackend\(")


def sleeps(text: str) -> list[int]:
    """Line numbers of waitForTimeout calls (comments do not count)."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("//", 1)[0]
        if SLEEP.search(code) and not code.lstrip().startswith("*"):
            out.append(n)
    return out


def routes_before_mock(text: str) -> list[int]:
    """Line numbers of /api routes that a test block registers before its mockBackend()."""
    out: list[int] = []
    pending: list[int] = []
    in_block = False
    for n, line in enumerate(text.splitlines(), 1):
        if BLOCK.match(line):
            in_block, pending = True, []
            continue
        if not in_block:
            continue
        if API_ROUTE.search(line) or (HELPER_ROUTE.search(line) and "mockBackend" not in line):
            pending.append(n)
        if MOCK_CALL.search(line) and "async ({" not in line and "mockBackend," not in line.strip():
            out.extend(pending)
            pending = []
    return out


SPECS = sorted(E2E.glob("*.spec.ts"))


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_no_fixed_sleep(spec: Path) -> None:
    assert sleeps(spec.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_no_api_route_before_mock_backend(spec: Path) -> None:
    assert routes_before_mock(spec.read_text(encoding="utf-8")) == []


def _at(rev: str, rel: str) -> str:
    return subprocess.run(
        ["git", "show", f"{rev}:{rel}"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.parametrize(
    ("rel", "rule"),
    [
        ("frontend/e2e/connection-status-reload.spec.ts", routes_before_mock),
        ("frontend/e2e/settings-nav-no-flush-sync.spec.ts", routes_before_mock),
        ("frontend/e2e/toggle-preserves-focus-scroll.spec.ts", sleeps),
    ],
)
def test_the_rules_flag_the_specs_that_broke_them(rel: str, rule) -> None:
    """Not vacuous: each rule finds the old version of a spec it fixed."""
    try:
        old = _at("02c997ee", rel)
    except subprocess.CalledProcessError:
        pytest.skip("history before 2026-10-24 is not in this clone")
    assert rule(old) != []
