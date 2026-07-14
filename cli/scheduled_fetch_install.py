"""Cross-platform periodic installer for the scheduled-fetch job.

Installs a supervised periodic job (launchd StartInterval / systemd
.timer+.service / Windows schtasks hourly) that runs
``backend.scheduled_fetch.run_scheduled_fetch(interval_sec=N)`` once
per tick and exits. Unlike the CC-image-cache watcher (``cli/watcher.py``),
this is NOT a continuous daemon — the supervisor is responsible for
re-running the job on the chosen schedule.

Identifier constants are imported from ``backend.scheduled_fetch_status``
(single source of truth shared with ``is_scheduled_fetch_installed``).

The ``install()`` / ``uninstall()`` entry points are called by the
``install-scheduled-fetch`` Click command in ``cli/main.py`` (Task 7).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from backend.config import canonical_home_dir
from backend.scheduled_fetch_status import (
    _LAUNCHD_LABEL,
    _SYSTEMD_TIMER,
    _WIN_TASK,
)

# Local constant: the systemd *service* unit name (the timer unit name
# comes from scheduled_fetch_status._SYSTEMD_TIMER).
_SYSTEMD_SERVICE = "claude-explorer-scheduled-fetch.service"


# ---------------------------------------------------------------------------
# Launcher path + writer
# ---------------------------------------------------------------------------

def LAUNCHER_PATH() -> Path:  # noqa: N802 — matches brief's function name style
    """Return the stable path for the per-run launcher script."""
    return canonical_home_dir() / "scheduled-fetch.py"


def write_launcher(interval: int) -> Path:
    """Write (or overwrite) the launcher script with the given interval baked in.

    The launcher runs ``run_scheduled_fetch`` once and exits.
    Idempotent — safe to call on every install.
    """
    p = LAUNCHER_PATH()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from backend.scheduled_fetch import run_scheduled_fetch\n"
        f"sys.exit(run_scheduled_fetch(interval_sec={interval}))\n"
    )
    return p


# ---------------------------------------------------------------------------
# Config generators (pure functions — no subprocess, no filesystem writes)
# ---------------------------------------------------------------------------

def build_launchd_plist(python_bin: str, interval: int) -> str:
    """Return a launchd plist string for the periodic scheduled-fetch job.

    Uses ``StartInterval`` (periodic) rather than ``KeepAlive`` (continuous).
    The supervisor re-runs the job every ``interval`` seconds.
    """
    launcher = LAUNCHER_PATH()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f'  <key>Label</key><string>{_LAUNCHD_LABEL}</string>\n'
        '  <key>ProgramArguments</key>\n'
        f'  <array><string>{python_bin}</string><string>{launcher}</string></array>\n'
        f'  <key>StartInterval</key><integer>{interval}</integer>\n'
        '  <key>RunAtLoad</key><true/>\n'
        '</dict></plist>\n'
    )


def build_systemd_service(python_bin: str, launcher: Path) -> str:
    """Return a systemd service unit string for the scheduled-fetch job.

    ``Type=oneshot`` is correct for a job that runs once and exits.
    No ``Restart=always`` — the timer handles re-runs.
    """
    return (
        "[Unit]\n"
        "Description=Claude Explorer scheduled incremental fetch\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={python_bin} {launcher}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def build_systemd_timer(interval: int) -> str:
    """Return a systemd timer unit string that fires the service periodically.

    ``OnBootSec=1min`` fires once shortly after boot; ``OnUnitActiveSec``
    maintains the recurring interval thereafter.
    """
    return (
        "[Unit]\n"
        "Description=Claude Explorer scheduled fetch timer\n"
        "\n"
        "[Timer]\n"
        "OnBootSec=1min\n"
        f"OnUnitActiveSec={interval}s\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


# ---------------------------------------------------------------------------
# Platform-specific install helpers
# ---------------------------------------------------------------------------

def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _systemd_service_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / _SYSTEMD_SERVICE


def _systemd_timer_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / _SYSTEMD_TIMER


def _install_macos(python_bin: str, interval: int) -> None:
    """macOS launchd path — periodic via StartInterval."""
    launcher = write_launcher(interval)
    click.echo(f"Wrote {launcher}")

    plist_path = _launchd_plist_path()
    plist_body = build_launchd_plist(python_bin, interval)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_body)
    click.echo(f"Wrote {plist_path}")

    # Unload first in case it was previously loaded (idempotent).
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        check=False, capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"launchctl load failed: {result.stderr.strip() or result.stdout.strip()}"
        )

    click.echo("")
    click.echo("Scheduled fetch installed and loaded (macOS launchd).")
    click.echo(f"  interval:  {interval}s")
    click.echo(f"  python:    {python_bin}")
    click.echo("")
    click.echo("Verify with: launchctl list | grep claude-explorer")


def _uninstall_macos() -> None:
    """macOS launchd path."""
    plist_path = _launchd_plist_path()
    if plist_path.exists():
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            check=False, capture_output=True,
        )
        plist_path.unlink()
        click.echo(f"Removed {plist_path}")
    else:
        click.echo(f"Not installed: {plist_path} does not exist")

    launcher = LAUNCHER_PATH()
    if launcher.exists():
        launcher.unlink()
        click.echo(f"Removed {launcher}")


def _install_linux(python_bin: str, interval: int) -> None:
    """Linux systemd user-unit path — .service (Type=oneshot) + .timer."""
    launcher = write_launcher(interval)
    click.echo(f"Wrote {launcher}")

    service_path = _systemd_service_path()
    timer_path = _systemd_timer_path()

    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(build_systemd_service(python_bin, launcher))
    click.echo(f"Wrote {service_path}")

    timer_path.write_text(build_systemd_timer(interval))
    click.echo(f"Wrote {timer_path}")

    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", _SYSTEMD_TIMER],
    ):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise click.ClickException(
                f"`{' '.join(cmd)}` failed: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    click.echo("")
    click.echo("Scheduled fetch installed and started (Linux systemd timer).")
    click.echo(f"  interval:  {interval}s")
    click.echo(f"  python:    {python_bin}")
    click.echo(f"  timer:     {_SYSTEMD_TIMER}")
    click.echo(f"  service:   {_SYSTEMD_SERVICE}")
    click.echo("")
    click.echo(f"Verify with: systemctl --user status {_SYSTEMD_TIMER}")
    click.echo("")
    click.echo("IMPORTANT: by default this stops when you log out. To keep it")
    click.echo("running across logout/headless boots, run ONCE:")
    click.echo("    sudo loginctl enable-linger $USER")


def _uninstall_linux() -> None:
    """Linux systemd user-unit path."""
    timer_path = _systemd_timer_path()
    service_path = _systemd_service_path()

    if timer_path.exists() or service_path.exists():
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", _SYSTEMD_TIMER],
            check=False, capture_output=True,
        )
        if timer_path.exists():
            timer_path.unlink()
            click.echo(f"Removed {timer_path}")
        if service_path.exists():
            service_path.unlink()
            click.echo(f"Removed {service_path}")
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=False, capture_output=True,
        )
    else:
        click.echo(f"Not installed: {timer_path} does not exist")

    launcher = LAUNCHER_PATH()
    if launcher.exists():
        launcher.unlink()
        click.echo(f"Removed {launcher}")


def _install_windows(python_bin: str, interval: int) -> None:
    """Windows Task Scheduler path — /SC HOURLY or /SC MINUTE /MO N."""
    launcher = write_launcher(interval)
    click.echo(f"Wrote {launcher}")

    pythonw = str(Path(python_bin).with_name("pythonw.exe"))
    if not Path(pythonw).exists():
        pythonw = python_bin

    if interval == 3600:
        schedule_args = ["/SC", "HOURLY"]
    else:
        schedule_args = ["/SC", "MINUTE", "/MO", str(interval // 60)]

    cmd = [
        "schtasks", "/Create",
        "/TN", _WIN_TASK,
        "/TR", f'"{pythonw}" "{launcher}"',
        *schedule_args,
        "/F",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException(
            f"schtasks /Create failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    click.echo("")
    click.echo("Scheduled fetch installed (Windows Task Scheduler).")
    click.echo(f"  interval:  {interval}s")
    click.echo(f"  python:    {pythonw}")
    click.echo(f"  launcher:  {launcher}")
    click.echo(f"  task name: {_WIN_TASK}")
    click.echo("")
    click.echo(f"Verify with: schtasks /Query /TN {_WIN_TASK}")


def _uninstall_windows() -> None:
    """Windows Task Scheduler path."""
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", _WIN_TASK, "/F"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        click.echo(f"Removed scheduled task: {_WIN_TASK}")
    else:
        click.echo(
            f"Not installed (or already removed): {_WIN_TASK} "
            f"({result.stderr.strip() or result.stdout.strip()})"
        )

    launcher = LAUNCHER_PATH()
    if launcher.exists():
        launcher.unlink()
        click.echo(f"Removed {launcher}")


# ---------------------------------------------------------------------------
# Public dispatch API
# ---------------------------------------------------------------------------

def install(python_bin: str, interval: int) -> None:
    """Install the periodic scheduled-fetch job for the current platform."""
    if sys.platform == "darwin":
        _install_macos(python_bin, interval)
    elif sys.platform.startswith("linux"):
        _install_linux(python_bin, interval)
    elif sys.platform == "win32":
        _install_windows(python_bin, interval)
    else:
        raise click.ClickException(
            f"Unsupported platform: {sys.platform}. "
            "Supported: darwin, linux, win32."
        )


def uninstall() -> None:
    """Remove the periodic scheduled-fetch job for the current platform."""
    if sys.platform == "darwin":
        _uninstall_macos()
    elif sys.platform.startswith("linux"):
        _uninstall_linux()
    elif sys.platform == "win32":
        _uninstall_windows()
    else:
        raise click.ClickException(
            f"Unsupported platform: {sys.platform}. "
            "Supported: darwin, linux, win32."
        )
