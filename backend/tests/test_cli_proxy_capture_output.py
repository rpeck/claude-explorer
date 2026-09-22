"""`capture --proxy` must run and must print the caller's own launch command.

Two contracts, both earned 2026-09-21:

1. The function must not raise. A function-local ``import sys`` later in the
   body makes ``sys`` local for the whole function, so an earlier
   ``sys.platform`` read raises UnboundLocalError on every platform.
2. The printed launch command must match the running OS.
"""

from __future__ import annotations

import pytest

import cli.main as cli_main


@pytest.fixture
def no_mitmproxy(monkeypatch: pytest.MonkeyPatch):
    """Stop the real mitmproxy subprocess from starting."""
    monkeypatch.setattr(cli_main.subprocess, "run", lambda *a, **k: None)


def test_proxy_capture_does_not_raise(no_mitmproxy, capsys) -> None:
    cli_main._capture_via_proxy(8080)
    assert "Proxy listening on port 8080" in capsys.readouterr().out


def test_proxy_capture_prints_this_platforms_command(no_mitmproxy, capsys) -> None:
    import sys

    from fetcher.install_hints import claude_desktop_launch_command

    cli_main._capture_via_proxy(8080)
    out = capsys.readouterr().out
    assert claude_desktop_launch_command(8080, sys.platform) in out


def test_windows_gets_the_store_app_warning(no_mitmproxy, capsys, monkeypatch) -> None:
    """On Windows the user needs the Store-install caveat and the fallback."""
    monkeypatch.setattr(cli_main.sys, "platform", "win32")
    cli_main._capture_via_proxy(8080)
    out = capsys.readouterr().out
    assert "Claude.exe" in out
    assert "open -a" not in out
    assert "Microsoft Store" in out
    assert "claude-explorer capture" in out


def test_macos_gets_no_windows_notes(no_mitmproxy, capsys, monkeypatch) -> None:
    """Bidirectional: the Windows block must not leak onto other platforms."""
    monkeypatch.setattr(cli_main.sys, "platform", "darwin")
    cli_main._capture_via_proxy(8080)
    out = capsys.readouterr().out
    assert "Microsoft Store" not in out
    assert 'open -a "Claude"' in out
