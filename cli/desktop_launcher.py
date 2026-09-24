"""Start and stop Claude Explorer from the Windows Start menu or a Linux app menu.

Why: many users never open a terminal. ``claude-explorer install-app``
creates a menu entry that runs this module. macOS uses a native applet
instead (``cli/app_launcher.py``).

Three commands, run as ``python -m cli.desktop_launcher <command>``:

* ``tray`` (the Windows Start menu shortcut): starts the server if nothing
  answers on the port, opens the browser, and shows a notification-area
  (tray) icon. Its menu has **Open Claude Explorer** and **Quit**.
* ``open`` (the Linux menu entry): starts the server if needed, opens the
  browser, and exits. The server keeps running.
* ``stop`` (the Linux entry's right-click action): stops the server that
  ``open`` started.

Every path stops only a server that a launcher started. A server that the
user started in a terminal keeps running. The launcher records the PID and
port of each server it starts in ``~/.claude-explorer/launcher-server.pid``.

Linux gets menu actions instead of a tray icon: GNOME shows no tray icons
without an extension, and pystray's Linux backends need GTK bindings that a
``uv tool`` install cannot provide.

Set ``CLAUDE_EXPLORER_NO_BROWSER=1`` to skip opening the browser (CI).
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_PORT = 8765
START_TIMEOUT_SEC = 60.0
STOP_TIMEOUT_SEC = 10.0
ICON_PNG = Path(__file__).resolve().parent / "assets" / "app-icon.png"


def state_dir() -> Path:
    return Path.home() / ".claude-explorer"


def log_path() -> Path:
    return state_dir() / "serve.log"


def pid_path() -> Path:
    return state_dir() / "launcher-server.pid"


def server_up(port: int, timeout: float = 1.0) -> bool:
    """True if Claude Explorer answers on ``port``."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/config", timeout=timeout
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def serve_command(port: int) -> list[str]:
    """The server command. Same interpreter, so the same environment."""
    return [sys.executable, "-m", "cli.main", "serve", "--port", str(port)]


def start_server(port: int) -> subprocess.Popen:
    """Start the server in the background, and record its PID and port."""
    state_dir().mkdir(parents=True, exist_ok=True)
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.STDOUT,
        "cwd": str(Path.home()),
    }
    if sys.platform == "win32":
        # No console window, and a group of its own.
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        # Keep running after `open` exits.
        kwargs["start_new_session"] = True
    with open(log_path(), "ab") as log:
        proc = subprocess.Popen(serve_command(port), stdout=log, **kwargs)
    pid_path().write_text(f"{proc.pid}\n{port}\n")
    return proc


def wait_until_up(port: int, proc: subprocess.Popen, timeout: float = START_TIMEOUT_SEC) -> bool:
    """Wait for the server to answer. Stop early if it exits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_up(port):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.5)
    return server_up(port)


def open_ui(port: int) -> None:
    if os.environ.get("CLAUDE_EXPLORER_NO_BROWSER") == "1":
        return
    webbrowser.open(f"http://localhost:{port}")


def notify_error(message: str) -> None:
    """Show an error. A menu launch has no terminal to print to."""
    print(message, file=sys.stderr)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "Claude Explorer", 0x10)
    elif shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "Claude Explorer", message], check=False, capture_output=True
        )


def _start_failed_message() -> str:
    return f"Claude Explorer did not start.\n\nThe log is at:\n{log_path()}"


def _read_pid_file() -> tuple[int, int] | None:
    try:
        pid_text, port_text = pid_path().read_text().split()[:2]
        return int(pid_text), int(port_text)
    except (OSError, ValueError):
        return None


def _looks_like_our_server(pid: int, port: int) -> bool:
    """Guard against a PID that the OS reused for another program."""
    if not server_up(port):
        return False
    cmdline = Path(f"/proc/{pid}/cmdline")
    if cmdline.exists():
        try:
            return b"serve" in cmdline.read_bytes()
        except OSError:
            return False
    return True


def _wait_for_exit(pid: int, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sys.platform == "win32":
            # Never probe with os.kill(pid, 0) on Windows: it calls
            # TerminateProcess, which could kill a program that reused
            # the PID. Watch the port instead.
            if not server_up(port, timeout=0.5):
                return True
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
        time.sleep(0.2)
    return False


def stop_recorded_server() -> bool:
    """Stop the server that a launcher started. Return True if one stopped."""
    recorded = _read_pid_file()
    if recorded is None:
        return False
    pid, port = recorded
    stopped = False
    if _looks_like_our_server(pid, port):
        try:
            # On Windows os.kill calls TerminateProcess. SQLite survives
            # that: it rolls back an unfinished transaction on next open.
            os.kill(pid, signal.SIGTERM)
            if not _wait_for_exit(pid, port, STOP_TIMEOUT_SEC) and sys.platform != "win32":
                os.kill(pid, signal.SIGKILL)
            stopped = True
        except OSError:
            pass
    pid_path().unlink(missing_ok=True)
    return stopped


def stop_process(proc: subprocess.Popen | None) -> None:
    """Stop a server that this process started, and forget its record."""
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=STOP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
    recorded = _read_pid_file()
    if proc is not None and recorded is not None and recorded[0] == proc.pid:
        pid_path().unlink(missing_ok=True)


def cmd_open(port: int) -> int:
    if server_up(port):
        open_ui(port)
        return 0
    proc = start_server(port)
    if not wait_until_up(port, proc):
        stop_process(proc)
        notify_error(_start_failed_message())
        return 1
    open_ui(port)
    return 0


def cmd_stop() -> int:
    stop_recorded_server()
    return 0


def cmd_tray(port: int) -> int:
    """Run the Windows tray icon. It owns any server that it starts."""
    if server_up(port):
        # A terminal, or another launcher, already runs the server. Show
        # the app, and do not add a second tray icon.
        open_ui(port)
        return 0

    import pystray
    from PIL import Image

    state: dict = {"proc": None}

    def ensure_server() -> bool:
        if server_up(port):
            return True
        proc = start_server(port)
        state["proc"] = proc
        return wait_until_up(port, proc)

    def on_open(icon, item) -> None:
        if ensure_server():
            open_ui(port)
        else:
            notify_error(_start_failed_message())

    def on_quit(icon, item) -> None:
        stop_process(state["proc"])
        icon.stop()

    def setup(icon) -> None:
        icon.visible = True
        if ensure_server():
            icon.title = "Claude Explorer"
            open_ui(port)
        else:
            notify_error(_start_failed_message())
            stop_process(state["proc"])
            icon.stop()

    icon = pystray.Icon(
        "claude-explorer",
        Image.open(ICON_PNG),
        "Claude Explorer (starting)",
        menu=pystray.Menu(
            pystray.MenuItem("Open Claude Explorer", on_open, default=True),
            pystray.MenuItem("Quit", on_quit),
        ),
    )
    icon.run(setup=setup)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cli.desktop_launcher")
    parser.add_argument("command", choices=["tray", "open", "stop"])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.command == "tray":
        return cmd_tray(args.port)
    if args.command == "open":
        return cmd_open(args.port)
    return cmd_stop()


if __name__ == "__main__":
    sys.exit(main())
