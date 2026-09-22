"""A missing Chromium build must produce an actionable Refresh error.

Earned 2026-09-21. The capture stream yielded ``str(exc)`` straight to the
sidebar Refresh button. When Playwright's Chromium build is absent (the
single most common first-run failure, because the browser download is a
separate one-time step), the user saw a raw Playwright dump naming an
internal path and a bare ``playwright install`` command that a PyPI user
cannot run.

The stream must map that class of failure to a message that names the real
command, and must keep every other failure readable.
"""

from __future__ import annotations

from backend.routers.fetch import BROWSER_MISSING_MESSAGE, classify_capture_error

RAW_PLAYWRIGHT_ERROR = (
    "BrowserType.launch: Executable doesn't exist at "
    r"C:\Users\someone\AppData\Local\ms-playwright\chromium-1234\chrome-win\chrome.exe"
    "\n"
    "Looks like Playwright was just installed or updated.\n"
    "Please run the following command to download new browsers:\n"
    "    playwright install"
)


def test_missing_browser_maps_to_actionable_message() -> None:
    msg = classify_capture_error(RAW_PLAYWRIGHT_ERROR)
    assert msg == BROWSER_MISSING_MESSAGE
    assert "playwright install chromium" in msg


def test_missing_browser_message_names_a_runnable_command() -> None:
    """A PyPI user must get the uvx form, not the bare `playwright install`."""
    assert "uvx --from claude-explorer playwright install chromium" in BROWSER_MISSING_MESSAGE


def test_missing_browser_message_hides_the_internal_path() -> None:
    """The user does not need the ms-playwright cache path."""
    msg = classify_capture_error(RAW_PLAYWRIGHT_ERROR)
    assert "ms-playwright" not in msg
    assert "AppData" not in msg


def test_unrelated_capture_error_stays_readable() -> None:
    """Bidirectional: a non-browser failure must NOT be swallowed."""
    msg = classify_capture_error("Target page, context or browser has been closed")
    assert msg != BROWSER_MISSING_MESSAGE
    assert "Target page, context or browser has been closed" in msg


def test_empty_error_has_a_message() -> None:
    assert classify_capture_error("") == "Capture failed: unknown error"


def test_executable_missing_lowercase_variant() -> None:
    """Playwright has shipped both casings of this string over time."""
    assert classify_capture_error("executable doesn't exist at /x") == BROWSER_MISSING_MESSAGE
