"""Three rules for the Playwright specs in frontend/e2e (2026-10-24).

1. No `page.waitForTimeout(...)`. A fixed sleep either races the app on a
   slow runner (flaky) or proves nothing ("nothing happened in 500 ms").
   Wait for a signal instead: the fixtures have waitForScrollQuiet,
   waitForKeyboardShortcuts, recordHistory, recordJumps, the page clock
   for the app's own timers, and the <html data-*> signals.
2. No `/api` route registered before `mockBackend()` in the same test.
   mockBackend registers a route for nearly every /api path plus a
   catch-all, and Playwright runs the route registered last first, so an
   earlier /api route never runs: four tests checked nothing this way.
3. A test with a time window installs the fake clock (2026-11-12). The
   fixture rows are dated from the fixed NOW of the date helpers, and the
   page measures the window from its own clock. On the real clock the rows
   leave the window some days after NOW, and the test fails from that day:
   accordion test 46 failed this way from 2026-10-28 (issue #45).

TESTING.md and testing/playwright.md describe the rules.
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
# A time window: a saved one, or one picked in the time dropdown.
WINDOW = re.compile(r"\blistRecentOnly:\s*true\b|\bpickTime\(\s*page\s*,(?!\s*['\"`]Forever['\"`])")
CLOCK = re.compile(r"\bstartClock\(|\.clock\.(install|setFixedTime|setSystemTime|pauseAt)\(")
HELPER = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(")


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


def windows_without_clock(text: str) -> list[int]:
    """First line of each time window in a test block that installs no fake clock.

    The clock call can be in the test, or in a local helper function that
    the test calls (such as `openHiddenByBoth`).
    """
    lines = [line.split("//", 1)[0] for line in text.splitlines()]
    clock_helpers: set[str] = set()
    helper: str | None = None
    for line in lines:
        if BLOCK.match(line):
            helper = None
        elif m := HELPER.match(line):
            helper = m.group(1)
        elif helper and CLOCK.search(line):
            clock_helpers.add(helper)
    calls_clock = CLOCK
    if clock_helpers:
        names = "|".join(sorted(clock_helpers))
        calls_clock = re.compile(rf"{CLOCK.pattern}|\b({names})\(")

    out: list[int] = []
    window: int | None = None
    clocked = False
    in_block = False
    for n, line in enumerate(lines, 1):
        if BLOCK.match(line):
            if window is not None and not clocked:
                out.append(window)
            in_block, window, clocked = True, None, False
            continue
        if not in_block:
            continue
        if window is None and WINDOW.search(line):
            window = n
        if calls_clock.search(line):
            clocked = True
    if window is not None and not clocked:
        out.append(window)
    return out


SPECS = sorted(E2E.glob("*.spec.ts"))


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_no_fixed_sleep(spec: Path) -> None:
    assert sleeps(spec.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_no_api_route_before_mock_backend(spec: Path) -> None:
    assert routes_before_mock(spec.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_time_window_has_fake_clock(spec: Path) -> None:
    assert windows_without_clock(spec.read_text(encoding="utf-8")) == []


def _at(rev: str, rel: str) -> str:
    return subprocess.run(
        ["git", "show", f"{rev}:{rel}"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.parametrize(
    ("rev", "rel", "rule"),
    [
        ("02c997ee", "frontend/e2e/connection-status-reload.spec.ts", routes_before_mock),
        ("02c997ee", "frontend/e2e/settings-nav-no-flush-sync.spec.ts", routes_before_mock),
        ("02c997ee", "frontend/e2e/toggle-preserves-focus-scroll.spec.ts", sleeps),
        # Test 46 before aee5d3fc: "3 days" on the real clock.
        ("944de2ca", "frontend/e2e/sidebar-list-filters-accordion.spec.ts", windows_without_clock),
    ],
)
def test_the_rules_flag_the_specs_that_broke_them(rev: str, rel: str, rule) -> None:
    """Not vacuous: each rule finds the old version of a spec it fixed."""
    try:
        old = _at(rev, rel)
    except subprocess.CalledProcessError:
        pytest.skip(f"commit {rev} is not in this clone")
    assert rule(old) != []


def test_clock_rule_cases() -> None:
    """The window and clock forms that the rule must tell apart."""
    head = "test('a', async ({ page }) => {\n"
    assert windows_without_clock(head + "  await pickTime(page, '3 days')\n})\n") == [2]
    assert windows_without_clock(head + "  preferences: { listRecentOnly: true },\n})\n") == [2]
    # "Forever" is no window, and listRecentOnly: false is none either.
    assert windows_without_clock(head + "  await pickTime(page, 'Forever')\n})\n") == []
    assert windows_without_clock(head + "  preferences: { listRecentOnly: false },\n})\n") == []
    # A comment does not count as a window.
    assert windows_without_clock(head + "  // pickTime(page, '3 days')\n})\n") == []
    # Each clock form satisfies the rule, also after the window.
    for clock in ("startClock(page)", "page.clock.install({ time: NOW })", "page.clock.setFixedTime(NOW)"):
        assert windows_without_clock(head + f"  await pickTime(page, '1 day')\n  await {clock}\n}})\n") == []
    # A clock in a local helper counts; a helper without one does not.
    helper = "async function open(page) {\n  await startClock(page)\n}\n"
    assert windows_without_clock(helper + head + "  await open(page)\n  await pickTime(page, '1 day')\n})\n") == []
    plain = "async function open(page) {\n  await page.goto('/')\n}\n"
    assert windows_without_clock(plain + head + "  await open(page)\n  await pickTime(page, '1 day')\n})\n") == [6]
    # A clock in one test does not cover the next one.
    two = head + "  await startClock(page)\n})\n" + head + "  await pickTime(page, '1 day')\n})\n"
    assert windows_without_clock(two) == [5]
