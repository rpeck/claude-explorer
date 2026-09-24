"""The Windows and Linux launchers: `install-app` entries and their runtime.

Contracts:

* ``open`` starts the server only if nothing answers, records its PID and
  port, and leaves it running.
* ``stop`` stops only a server that a launcher recorded. A server that the
  user started in a terminal keeps running.
* The Windows tray starts the server, and its Quit stops that server.
  If a server already answers, the tray opens the browser and shows no
  second icon.
* The Linux entry quotes its Exec lines as the Desktop Entry Specification
  requires, and offers a "Stop Claude Explorer" action.
* The Windows shortcut targets ``pythonw.exe``, so no console window opens,
  and PowerShell receives every value through the environment.

A small stand-in server answers ``/api/config``, so these tests run on
every platform in about a second each.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

from backend.tests._platform_home import patch_home
from cli import app_launcher, desktop_launcher

# socketserver.TCPServer, not http.server.HTTPServer: HTTPServer.server_bind
# calls socket.getfqdn("127.0.0.1"), a reverse DNS lookup. On the GitHub
# macOS runners that lookup stalled the stand-in for more than 10 s, and
# the three tests that wait only 10 s failed (2026-09-24).
_FAKE_SERVER = textwrap.dedent(
    """
    import http.server, socketserver, sys

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/api/config" else 404)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    port = int(sys.argv[sys.argv.index("--port") + 1])
    socketserver.TCPServer(("127.0.0.1", port), H).serve_forever()
    """
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


@pytest.fixture
def launcher_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated home, no browser, and a stand-in server command."""
    patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_EXPLORER_NO_BROWSER", "1")
    script = tmp_path / "fake_server.py"
    script.write_text(_FAKE_SERVER)
    # "serve" in argv, like the real command, for the /proc cmdline check.
    monkeypatch.setattr(
        desktop_launcher,
        "serve_command",
        lambda port: [sys.executable, str(script), "serve", "--port", str(port)],
    )
    port = _free_port()
    started: list[subprocess.Popen] = []
    yield port, started
    for proc in started:
        if proc.poll() is None:
            proc.kill()
    desktop_launcher.stop_recorded_server()


def test_open_starts_records_and_leaves_the_server_running(launcher_env) -> None:
    port, _ = launcher_env
    assert not desktop_launcher.server_up(port)

    assert desktop_launcher.cmd_open(port) == 0

    assert desktop_launcher.server_up(port)
    pid, recorded_port = desktop_launcher._read_pid_file()
    assert recorded_port == port and pid > 0

    assert desktop_launcher.cmd_stop() == 0
    assert _wait(lambda: not desktop_launcher.server_up(port))
    assert not desktop_launcher.pid_path().exists()


def test_open_reuses_a_running_server(launcher_env) -> None:
    port, started = launcher_env
    proc = subprocess.Popen(desktop_launcher.serve_command(port))
    started.append(proc)
    assert _wait(lambda: desktop_launcher.server_up(port))

    assert desktop_launcher.cmd_open(port) == 0

    assert not desktop_launcher.pid_path().exists(), "open must not claim a server it did not start"


def test_stop_leaves_a_server_it_did_not_start(launcher_env) -> None:
    port, started = launcher_env
    proc = subprocess.Popen(desktop_launcher.serve_command(port))
    started.append(proc)
    assert _wait(lambda: desktop_launcher.server_up(port))

    desktop_launcher.cmd_stop()

    assert desktop_launcher.server_up(port)
    assert proc.poll() is None


def test_stop_ignores_a_stale_record(launcher_env) -> None:
    """The recorded server is gone: remove the record, and kill nothing."""
    port, _ = launcher_env
    desktop_launcher.state_dir().mkdir(parents=True, exist_ok=True)
    desktop_launcher.pid_path().write_text(f"999999\n{port}\n")

    assert desktop_launcher.stop_recorded_server() is False
    assert not desktop_launcher.pid_path().exists()


def _fake_pystray(monkeypatch: pytest.MonkeyPatch) -> dict:
    made: dict = {}

    class MenuItem:
        def __init__(self, text, action, default=False):
            self.text, self.action, self.default = text, action, default

    class Menu:
        def __init__(self, *items):
            self.items = items

    class Icon:
        def __init__(self, name, image, title, menu):
            self.title, self.menu, self.visible, self.stopped = title, menu, False, False
            made["icon"] = self

        def run(self, setup):
            setup(self)

        def stop(self):
            self.stopped = True

    module = types.SimpleNamespace(Icon=Icon, Menu=Menu, MenuItem=MenuItem)
    monkeypatch.setitem(sys.modules, "pystray", module)
    return made


def test_tray_starts_the_server_and_quit_stops_it(launcher_env, monkeypatch) -> None:
    port, _ = launcher_env
    made = _fake_pystray(monkeypatch)

    assert desktop_launcher.cmd_tray(port) == 0

    icon = made["icon"]
    assert icon.visible and icon.title == "Claude Explorer"
    assert desktop_launcher.server_up(port)
    labels = [item.text for item in icon.menu.items]
    assert labels == ["Open Claude Explorer", "Quit"]
    assert icon.menu.items[0].default

    quit_item = icon.menu.items[1]
    quit_item.action(icon, quit_item)

    assert icon.stopped
    assert _wait(lambda: not desktop_launcher.server_up(port))
    assert not desktop_launcher.pid_path().exists()


def test_tray_adds_no_second_icon_when_a_server_runs(launcher_env, monkeypatch) -> None:
    port, started = launcher_env
    proc = subprocess.Popen(desktop_launcher.serve_command(port))
    started.append(proc)
    assert _wait(lambda: desktop_launcher.server_up(port))
    made = _fake_pystray(monkeypatch)

    assert desktop_launcher.cmd_tray(port) == 0

    assert "icon" not in made
    assert proc.poll() is None


# ------------------------------------------------------------------ Linux


def test_desktop_entry_has_open_and_stop() -> None:
    entry = app_launcher.build_desktop_entry(
        executable="/home/u/.local/share/uv/tools/claude-explorer/bin/python",
        icon=Path("/home/u/.claude-explorer/claude-explorer.png"),
    )
    lines = entry.splitlines()
    assert lines[0] == "[Desktop Entry]"
    assert (
        'Exec="/home/u/.local/share/uv/tools/claude-explorer/bin/python" '
        "-m cli.desktop_launcher open" in lines
    )
    assert "Actions=stop;" in lines
    assert "[Desktop Action stop]" in lines
    assert "Name=Stop Claude Explorer" in lines
    assert lines[-1].endswith("-m cli.desktop_launcher stop")
    assert "Terminal=false" in lines


def test_desktop_exec_quoting_follows_the_spec() -> None:
    # Inside quotes, escape " ` $ \ with a backslash; then the string rule
    # doubles every backslash; a literal % becomes %%.
    assert app_launcher._exec_arg("/a b/py") == '"/a b/py"'
    assert app_launcher._exec_arg('/a"b') == '"/a\\\\"b"'
    assert app_launcher._exec_arg("/a$b") == '"/a\\\\$b"'
    assert app_launcher._exec_arg("/a%b") == '"/a%%b"'


@pytest.mark.skipif(shutil.which("desktop-file-validate") is None, reason="needs desktop-file-utils")
def test_desktop_entry_passes_the_validator(tmp_path: Path) -> None:
    entry = tmp_path / app_launcher.DESKTOP_FILE
    entry.write_text(
        app_launcher.build_desktop_entry(executable="/opt/x y/python", icon=tmp_path / "i.png")
    )
    result = subprocess.run(["desktop-file-validate", str(entry)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_linux_install_and_uninstall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_home(monkeypatch, tmp_path)
    apps = tmp_path / "applications"

    entry = app_launcher.install_linux_entry(dest=apps, executable="/usr/bin/python3")

    assert entry == apps / app_launcher.DESKTOP_FILE
    assert (tmp_path / ".claude-explorer" / "claude-explorer.png").stat().st_size > 1000
    assert app_launcher.uninstall_linux_entry(apps)
    assert not entry.exists()
    assert not app_launcher.uninstall_linux_entry(apps)


# ---------------------------------------------------------------- Windows


def test_shortcut_targets_pythonw(tmp_path: Path) -> None:
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    fields = app_launcher.shortcut_fields(
        executable=str(tmp_path / "python.exe"), icon=tmp_path / "i.ico"
    )
    assert fields["CE_TARGET"] == str(tmp_path / "pythonw.exe")
    assert fields["CE_ARGS"] == "-m cli.desktop_launcher tray"


def test_shortcut_script_takes_values_only_from_the_environment() -> None:
    script = app_launcher._SHORTCUT_PS
    for name in ("CE_LNK", "CE_TARGET", "CE_ARGS", "CE_WORKDIR", "CE_ICON"):
        assert f"$env:{name}" in script


@pytest.mark.skipif(sys.platform != "win32", reason="creates a real Windows shortcut")
def test_windows_shortcut_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_home(monkeypatch, tmp_path)
    menu = tmp_path / "Programs"

    lnk = app_launcher.install_windows_shortcut(dest=menu, executable=sys.executable)

    read = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CE_LNK); "
         "Write-Output $s.TargetPath; Write-Output $s.Arguments"],
        env={**__import__("os").environ, "CE_LNK": str(lnk)},
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert Path(read[0]).name.lower() in ("pythonw.exe", "python.exe")
    assert read[1] == "-m cli.desktop_launcher tray"
    assert (tmp_path / ".claude-explorer" / "claude-explorer.ico").exists()
    assert app_launcher.uninstall_windows_shortcut(menu)
    assert not lnk.exists()
