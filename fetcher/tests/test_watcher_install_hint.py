"""The watcher install command must match how the user installed the app.

Earned 2026-09-23, from two defects:

* The UI banner, the ``serve`` warning, and the watcher log all said
  ``uv run claude-explorer install-watcher``. ``uv run`` works only in a
  source checkout, so a PyPI user copied a command that fails.
* ``install-watcher`` registers ``sys.executable`` with the OS supervisor.
  Under ``uvx`` that interpreter lives in uv's cache (``archive-v0``),
  which ``uv cache clean`` or a cache prune deletes. The watcher then
  stops at the next login with no visible error, and Claude Code images
  are lost. The command must refuse that interpreter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from fetcher.install_hints import is_ephemeral_interpreter, watcher_install_hint

UVX_POSIX = "/home/u/.cache/uv/archive-v0/X0o8A14W3qNHDdxTd9Ad_/bin/python"
UVX_WIN = r"C:\Users\u\AppData\Local\uv\cache\archive-v0\abc123\Scripts\python.exe"
TOOL_POSIX = "/home/u/.local/share/uv/tools/claude-explorer/bin/python"
TOOL_WIN = r"C:\Users\u\AppData\Roaming\uv\tools\claude-explorer\Scripts\python.exe"


@pytest.mark.parametrize("exe", [UVX_POSIX, UVX_WIN])
def test_uvx_interpreter_is_ephemeral(exe: str) -> None:
    assert is_ephemeral_interpreter(exe)


@pytest.mark.parametrize("exe", [TOOL_POSIX, TOOL_WIN, "/usr/bin/python3"])
def test_lasting_interpreter_is_not_ephemeral(exe: str) -> None:
    assert not is_ephemeral_interpreter(exe)


def test_checkout_gets_uv_run(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    hint = watcher_install_hint(tmp_path, executable=UVX_POSIX)
    assert hint == "uv run claude-explorer install-watcher"


def test_tool_install_gets_the_bare_command(tmp_path: Path) -> None:
    hint = watcher_install_hint(tmp_path, executable=TOOL_POSIX)
    assert hint == "claude-explorer install-watcher"


def test_uvx_run_is_told_to_install_persistently(tmp_path: Path) -> None:
    hint = watcher_install_hint(tmp_path, executable=UVX_POSIX, platform_name="darwin")
    assert hint == "uv tool install claude-explorer && claude-explorer install-watcher"


def test_uvx_hint_on_windows_avoids_ampersands(tmp_path: Path) -> None:
    """Windows PowerShell 5.1 rejects `&&`; `;` works in every PowerShell."""
    hint = watcher_install_hint(tmp_path, executable=UVX_WIN, platform_name="win32")
    assert hint == "uv tool install claude-explorer; claude-explorer install-watcher"


def test_no_hint_uses_uv_run_outside_a_checkout(tmp_path: Path) -> None:
    for exe in (UVX_POSIX, TOOL_POSIX):
        assert "uv run" not in watcher_install_hint(tmp_path, executable=exe)


def _record_installs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the per-platform installers, which cli.main binds by name."""
    registered: list[str] = []
    for name in ("_install_macos", "_install_linux", "_install_windows"):
        monkeypatch.setattr(
            f"cli.main.{name}",
            lambda python_bin, interval: registered.append(python_bin),
        )
    return registered


def test_install_watcher_refuses_a_uvx_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from cli.main import main as cli_main

    registered = _record_installs(monkeypatch)
    monkeypatch.setattr(sys, "executable", UVX_POSIX)

    result = CliRunner().invoke(cli_main, ["install-watcher"])

    assert result.exit_code != 0, result.output
    assert registered == [], "nothing may be registered with a temporary interpreter"
    assert "uv tool install claude-explorer" in result.output


def test_install_watcher_accepts_an_explicit_python(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--python` is the escape hatch: the user chose the interpreter."""
    import sys

    from cli.main import main as cli_main

    registered = _record_installs(monkeypatch)
    monkeypatch.setattr(sys, "executable", UVX_POSIX)
    chosen = tmp_path / "python"
    chosen.write_text("")

    result = CliRunner().invoke(cli_main, ["install-watcher", "--python", str(chosen)])

    assert result.exit_code == 0, result.output
    assert registered == [str(chosen)]


def test_install_watcher_accepts_a_lasting_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from cli.main import main as cli_main

    registered = _record_installs(monkeypatch)
    monkeypatch.setattr(sys, "executable", TOOL_POSIX)

    result = CliRunner().invoke(cli_main, ["install-watcher"])

    assert result.exit_code == 0, result.output
    assert registered == [TOOL_POSIX]
