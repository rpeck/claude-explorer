"""F5 council finding: the post-capture banner echoed session_key[:20].

Two call sites previously printed the first 20 characters of the
Anthropic session key to the user's terminal:

* ``cli/main.py:_capture_via_browser`` (formerly ``fetcher/cli.py:274``)
* ``fetcher/playwright_capture.py:408`` — standalone CLI banner

Anthropic session keys begin with the fixed prefix ``sk-ant-sid01-``
(13 chars), so 20 chars exposed exactly 7 chars of secret entropy.
While not enough for direct replay, this still leaked bearer-token
material into:

* terminal scrollback,
* screen recordings / screenshots,
* CI logs (if anyone runs capture in CI),
* shell history exports,
* support pastebins.

The council judged this HIGH for V1-public ship. Fix: drop the
session-key line entirely. The user still gets non-secret
confirmation (saved-path, org-id summary) so the UX isn't worse.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

# A long fake key that we can grep for in CLI output. The shape
# matches a real key (prefix + entropy) but is obviously test data.
_FAKE_SESSION_KEY = "sk-ant-sid01-AAAAAAAAAAAAAAAAAAAA-BBBBBBBBBB-CCCC"
_FAKE_ORG_ID = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"

_FAKE_CREDS = {
    "schema_version": 2,
    "session_key": _FAKE_SESSION_KEY,
    "cf_bm": None,
    "cf_clearance": None,
    "captured_at": "2026-05-21T00:00:00Z",
    "orgs": [{"uuid": _FAKE_ORG_ID, "name": "Test", "capabilities": ["chat"], "seen_in_response": True}],
    "primary_org_id": _FAKE_ORG_ID,
    "legacy_migration_target": None,
    "org_id": _FAKE_ORG_ID,
}


def _assert_no_session_key_leak(output: str, key: str = _FAKE_SESSION_KEY) -> None:
    """Assert the CLI output does NOT contain any non-trivial substring of the key.

    The prefix ``sk-ant-sid01-`` is fixed and well-known, so it's not
    secret. Anything past the prefix is entropy and MUST NOT leak.
    """
    prefix = "sk-ant-sid01-"
    entropy = key[len(prefix):]
    # Even a 7-char slice of entropy is a leak — assert nothing past
    # the prefix appears in the output.
    for slice_len in (7, 10, 15, 20):
        sub = entropy[:slice_len]
        assert sub not in output, (
            f"Session-key entropy substring {sub!r} (len={slice_len}) "
            f"leaked into CLI output:\n{output}"
        )


async def _async_fake_creds(*args, **kwargs):
    """Async stub matching ``capture_credentials``'s signature."""
    return _FAKE_CREDS


def test_capture_browser_banner_does_not_leak_session_key(tmp_path: Path) -> None:
    """``_capture_via_browser`` success path must not print the session key."""
    from cli.main import _capture_via_browser

    runner = CliRunner()

    with runner.isolation() as (stdout, _stderr, _):
        with patch(
            "fetcher.playwright_capture.capture_credentials", side_effect=_async_fake_creds
        ), patch("fetcher.credentials.save_credentials", return_value=None):
            _capture_via_browser(tmp_path / "creds.json", timeout=300)

        output = stdout.getvalue().decode("utf-8", errors="replace")

    _assert_no_session_key_leak(output)


def test_playwright_capture_standalone_banner_does_not_leak_session_key(tmp_path: Path) -> None:
    """``playwright_capture.main`` (standalone CLI) must not print session key."""
    from fetcher import playwright_capture

    runner = CliRunner()
    with patch(
        "fetcher.playwright_capture.capture_credentials", side_effect=_async_fake_creds
    ), patch("fetcher.playwright_capture.save_credentials", return_value=None):
        result = runner.invoke(
            playwright_capture.main,
            ["--output", str(tmp_path / "creds.json"), "--timeout", "1"],
        )

    _assert_no_session_key_leak(result.output)


def test_capture_browser_banner_still_shows_non_secret_confirmation(tmp_path: Path) -> None:
    """Bidirectional negative: dropping the session-key line must NOT regress
    the rest of the banner. The user still needs success-feedback + saved-path."""
    from cli.main import _capture_via_browser

    creds_path = tmp_path / "creds.json"
    runner = CliRunner()

    with runner.isolation() as (stdout, _stderr, _):
        with patch(
            "fetcher.playwright_capture.capture_credentials", side_effect=_async_fake_creds
        ), patch("fetcher.credentials.save_credentials", return_value=None):
            _capture_via_browser(creds_path, timeout=300)

        output = stdout.getvalue().decode("utf-8", errors="replace")

    # Success confirmation — user must see SOMETHING positive.
    assert "CAPTURED SUCCESSFULLY" in output, (
        f"Success banner missing from CLI output:\n{output}"
    )
    # Non-secret confirmation: saved-path is presentation-level info.
    assert str(creds_path) in output, (
        f"Saved-path confirmation missing from CLI output:\n{output}"
    )
    # Org ID is a UUID — identifying but not bearer material. Keeping
    # it gives users single-glance confirmation which workspace was
    # captured for.
    assert _FAKE_ORG_ID in output, (
        f"Org-id confirmation missing from CLI output:\n{output}"
    )
