"""Start and stop Claude Explorer from the Windows Start menu or a Linux app menu.

Why: many users never open a terminal. ``claude-explorer install-app``
creates a menu entry that runs this module. macOS uses a native applet
instead (``cli/app_launcher.py``).

Three commands, run as ``python -m cli.desktop_launcher <command>``:

* ``tray`` (the Windows Start menu shortcut): starts the server if nothing
  answers on the port, opens the browser, and shows a notification-area
  (tray) icon. Its menu has **Open Claude Explorer** and **Quit**. The icon
  goes away when its server stops on its own, as after the web UI's Quit
  (power) button.
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
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_PORT = 8765
START_TIMEOUT_SEC = 60.0
STOP_TIMEOUT_SEC = 10.0
#: How often the tray checks that the server it started is still running.
WATCH_INTERVAL_SEC = 2.0
ICON_PNG = Path(__file__).resolve().parent / "assets" / "app-icon.png"


def state_dir() -> Path:
    return Path.home() / ".claude-explorer"


def port_path() -> Path:
    """The port that the last ``install-app`` gave the launcher."""
    return state_dir() / "launcher-port"


def saved_port() -> int | None:
    try:
        port = int(port_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def log_path() -> Path:
    return state_dir() / "serve.log"


def pid_path() -> Path:
    return state_dir() / "launcher-server.pid"


def server_up(port: int, timeout: float = 1.0, host: str = "127.0.0.1") -> bool:
    """True if Claude Explorer answers on ``host:port``."""
    netloc = f"[{host}]" if ":" in host else host
    try:
        with urllib.request.urlopen(
            f"http://{netloc}:{port}/api/config", timeout=timeout
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """True if the server could not bind ``host:port``.

    A bind test, the same as the server's own bind, so the answer matches
    what uvicorn sees. asyncio, which uvicorn uses, sets SO_REUSEADDR on
    POSIX only: there a closed connection in TIME_WAIT does not block the
    port, and on Windows SO_REUSEADDR would let two programs share it.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return True
    return False


def free_port_after(port: int, host: str = "127.0.0.1") -> int:
    """The first free port after ``port``, to suggest in a conflict message."""
    for candidate in range(port + 1, min(port + 50, 65536)):
        if not port_in_use(candidate, host):
            return candidate
    return port + 1 if port < 65535 else port - 1


def app_port_conflict_message(
    port: int, new_port: int, *, platform: str = sys.platform, copied: bool = False
) -> str:
    """Plain-words help for a launcher user whose port another program holds.

    The user may not be a developer, so the text names the problem, then
    gives numbered steps with the one command that fixes it for good.
    """
    command = f"claude-explorer install-app --port {new_port}"
    if platform == "darwin":
        steps = [
            "Click Copy Command.",
            "Open Terminal, paste the command, and press Return.",
        ]
    elif platform == "win32" and copied:
        steps = ["Open PowerShell.", "Press Ctrl+V, then press Enter."]
    elif platform == "win32":
        steps = ["Open PowerShell.", "Type the command, then press Enter."]
    else:
        steps = ["Open a terminal.", "Type the command, then press Enter."]
    steps.append("Open Claude Explorer again.")
    lines = [
        (
            "Claude Explorer could not start, because another program on this "
            f"computer already uses port {port}."
        ),
        "",
        "To fix this, give Claude Explorer a different port. The command is:",
        "",
        f"    {command}",
        "",
    ]
    if copied:
        lines += ["The command is already on the clipboard.", ""]
    lines += [f"{n}. {step}" for n, step in enumerate(steps, 1)]
    return "\n".join(lines)


def copy_to_clipboard(text: str) -> bool:
    """Put ``text`` on the Windows clipboard. False where that is not possible."""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(["clip"], input=text, text=True, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def report_port_conflict(port: int) -> bool:
    """If another program holds ``port``, tell the user how to fix it.

    Call this only after ``server_up`` is False: a running Claude Explorer
    also holds the port.
    """
    if not port_in_use(port):
        return False
    new_port = free_port_after(port)
    copied = copy_to_clipboard(f"claude-explorer install-app --port {new_port}")
    notify_error(app_port_conflict_message(port, new_port, copied=copied))
    return True


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
    pid_path().write_text(f"{proc.pid}\n{port}\n", encoding="utf-8")
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
        pid_text, port_text = pid_path().read_text(encoding="utf-8").split()[:2]
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


def _wait_for_exit(port: int, timeout: float) -> bool:
    """Wait until the server stops answering on ``port``.

    The port, not the PID, is the signal on every platform:

    * On Windows, ``os.kill(pid, 0)`` does not probe. It calls
      TerminateProcess, and could kill a program that reused the PID.
    * On POSIX, a stopped server whose parent has not collected it stays a
      zombie, and ``os.kill(pid, 0)`` still succeeds on a zombie. That made
      every stop wait its full timeout, then send SIGKILL.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not server_up(port, timeout=0.5):
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
            if not _wait_for_exit(port, STOP_TIMEOUT_SEC) and sys.platform != "win32":
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
    if report_port_conflict(port):
        return 1
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
    if report_port_conflict(port):
        return 1

    import pystray
    from PIL import Image

    state: dict = {"proc": None}

    def ensure_server() -> bool:
        """Start the server if needed. On failure, show one message."""
        if server_up(port):
            return True
        if report_port_conflict(port):
            return False
        proc = start_server(port)
        state["proc"] = proc
        if wait_until_up(port, proc):
            return True
        notify_error(_start_failed_message())
        return False

    def on_open(icon, item) -> None:
        if ensure_server():
            open_ui(port)

    def on_quit(icon, item) -> None:
        state["quitting"] = True
        stop_process(state["proc"])
        icon.stop()

    def watch_server(icon) -> None:
        """Remove the tray icon when the server it started exits on its own:
        the web UI's Quit (power) button, or a crash."""
        while not state.get("quitting"):
            proc = state["proc"]
            if proc is not None and proc.poll() is not None:
                stop_process(proc)  # forget the PID record
                icon.stop()
                return
            time.sleep(WATCH_INTERVAL_SEC)

    def setup(icon) -> None:
        icon.visible = True
        if ensure_server():
            icon.title = "Claude Explorer"
            threading.Thread(target=watch_server, args=(icon,), daemon=True).start()
            open_ui(port)
        else:
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
