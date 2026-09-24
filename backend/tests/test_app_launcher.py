"""`claude-explorer install-app`: the macOS launcher app.

The launcher is an AppleScript "stay-open" applet that the CLI compiles on
the user's Mac. A file made locally carries no quarantine flag, so macOS
opens it without Gatekeeper or notarization.

Contracts, each verified by hand on macOS on 2026-09-23 before this code:

* Opening the app starts ``claude-explorer serve`` if nothing answers on the
  port, waits for it, and opens the browser.
* Quitting the app stops the server, but only a server that the app started.
  A server that the user started in a terminal keeps running.
* The start command must not keep the output pipe of ``do shell script``
  open. ``cd /tmp && nohup cmd & echo $!`` did: the ``&`` put the whole
  ``cd && nohup`` list in a subshell that held the pipe, the run handler
  never returned, and the app could not receive Quit.
* Replacing the icon breaks the ad-hoc signature's resource seal, so the
  bundle must be re-signed afterwards.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli import app_launcher

TOOL_EXE = "/Users/u/.local/share/uv/tools/claude-explorer/bin/claude-explorer"


def _script(**kw) -> str:
    args = dict(
        executable=TOOL_EXE,
        port=8765,
        log_path="/Users/u/Library/Logs/claude-explorer-serve.log",
    )
    args.update(kw)
    return app_launcher.build_applescript(**args)


def test_script_bakes_the_executable_port_and_log() -> None:
    s = _script()
    assert f'"{TOOL_EXE}"' in s
    assert '"http://localhost:8765"' in s
    assert "8765" in s
    assert '"/Users/u/Library/Logs/claude-explorer-serve.log"' in s


def test_start_command_releases_the_output_pipe() -> None:
    """The regression that blocked Quit: see the module docstring."""
    s = _script()
    start = next(line for line in s.splitlines() if "nohup" in line)
    assert "< /dev/null & echo $!" in start
    assert "&& nohup" not in start


def test_script_uses_no_properties() -> None:
    """An applet writes changed properties back into its own bundle, which
    breaks the signature. Globals are never saved."""
    assert "\nproperty " not in _script()


def test_quit_stops_only_a_server_the_app_started() -> None:
    s = _script()
    quit_block = s[s.index("on quit") : s.index("end quit")]
    assert "if ownsServer" in quit_block
    assert "continue quit" in quit_block


def test_paths_are_escaped_for_applescript() -> None:
    s = _script(executable='/Users/o"brien\\x/bin/claude-explorer')
    assert '"/Users/o\\"brien\\\\x/bin/claude-explorer"' in s


def test_install_app_refuses_other_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    from cli.main import main as cli_main

    monkeypatch.setattr(sys, "platform", "linux")
    result = CliRunner().invoke(cli_main, ["install-app"])
    assert result.exit_code != 0
    assert "macOS" in result.output


def test_install_app_refuses_a_uvx_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cli.main import main as cli_main

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        sys, "executable", "/Users/u/.cache/uv/archive-v0/abc/bin/python"
    )
    result = CliRunner().invoke(cli_main, ["install-app", "--dest", str(tmp_path)])
    assert result.exit_code != 0
    assert "uv tool install claude-explorer" in result.output
    assert not any(tmp_path.iterdir())


def test_uninstall_leaves_a_foreign_app_alone(tmp_path: Path) -> None:
    app = tmp_path / app_launcher.APP_NAME
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.example.other"})
    )
    with pytest.raises(Exception, match="not the Claude Explorer launcher"):
        app_launcher.uninstall_app(tmp_path)
    assert app.exists()


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("osacompile") is None,
    reason="builds a real applet with osacompile, sips, iconutil and codesign",
)
def test_build_app_makes_a_valid_signed_bundle(tmp_path: Path) -> None:
    exe = tmp_path / "claude-explorer"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)

    app = app_launcher.build_app(
        dest=tmp_path, executable=str(exe), port=8765, log_path=tmp_path / "s.log"
    )

    assert app == tmp_path / app_launcher.APP_NAME
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == app_launcher.BUNDLE_ID
    assert (app / "Contents" / "Resources" / "applet.icns").stat().st_size > 10_000
    verify = subprocess.run(
        ["codesign", "--verify", "--verbose", str(app)], capture_output=True, text=True
    )
    assert verify.returncode == 0, verify.stderr

    # A rebuild replaces our own app in place.
    again = app_launcher.build_app(
        dest=tmp_path, executable=str(exe), port=8765, log_path=tmp_path / "s.log"
    )
    assert again == app

    app_launcher.uninstall_app(tmp_path)
    assert not app.exists()
