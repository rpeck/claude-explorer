"""Best-effort cross-platform desktop notification. CLI-only; never
raises. Returns False when no notifier is available (caller falls back
to the status file + doctor). Must stay OUT of the MCPB closure."""

from __future__ import annotations

import shutil
import subprocess
import sys


def _run(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def notify(title: str, message: str) -> bool:
    if sys.platform == "darwin":
        script = f'display notification {message!r} with title {title!r}'
        return _run(["osascript", "-e", script])
    if sys.platform.startswith("linux"):
        if shutil.which("notify-send") is None:
            return False
        return _run(["notify-send", title, message])
    if sys.platform == "win32":
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
            " ContentType = WindowsRuntime] > $null; "
            f"Write-Output {message!r}"
        )
        # Minimal balloon via powershell; best-effort only.
        return _run(["powershell", "-NoProfile", "-Command", ps])
    return False
