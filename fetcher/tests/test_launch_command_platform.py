"""`capture --proxy` must print a launch command for the user's own OS.

Earned 2026-09-21. The proxy capture path printed the macOS command
(`open -a "Claude" --args ...`) on every platform. A Windows user was told
to run a command that does not exist there, in the middle of the only flow
that recovers an account whose login is lost.
"""

from __future__ import annotations

from fetcher.install_hints import claude_desktop_launch_command


def test_macos_uses_open_dash_a() -> None:
    cmd = claude_desktop_launch_command(8080, "darwin")
    assert cmd.startswith('open -a "Claude"')
    assert '--proxy-server="127.0.0.1:8080"' in cmd


def test_windows_uses_an_exe_path_not_open() -> None:
    cmd = claude_desktop_launch_command(8080, "win32")
    assert "open -a" not in cmd
    assert "Claude.exe" in cmd
    assert '--proxy-server="127.0.0.1:8080"' in cmd


def test_linux_uses_the_bare_binary() -> None:
    cmd = claude_desktop_launch_command(8080, "linux")
    assert "open -a" not in cmd
    assert cmd.startswith("claude ")


def test_every_platform_gets_a_distinct_command() -> None:
    """Bidirectional: the platform argument actually changes the answer."""
    commands = {
        claude_desktop_launch_command(8080, p)
        for p in ("darwin", "win32", "linux")
    }
    assert len(commands) == 3


def test_port_is_honored_on_every_platform() -> None:
    for p in ("darwin", "win32", "linux"):
        assert '"127.0.0.1:9999"' in claude_desktop_launch_command(9999, p)


def test_every_platform_keeps_the_certificate_flag() -> None:
    for p in ("darwin", "win32", "linux"):
        assert "--ignore-certificate-errors" in claude_desktop_launch_command(8080, p)


def test_defaults_to_the_running_platform() -> None:
    import sys

    assert claude_desktop_launch_command(8080) == claude_desktop_launch_command(
        8080, sys.platform
    )
