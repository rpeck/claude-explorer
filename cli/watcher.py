"""Cross-platform installer for the CC image-cache watcher (Council A2-WATCHER + A1-CLI-LAYER).

Originally extracted from ``fetcher/cli.py`` on 2026-05-21 as
``fetcher/watcher_install.py`` (council A2-WATCHER). Promoted to
``cli/watcher.py`` later the same day under council A1-CLI-LAYER —
the CLI was lifted from ``fetcher/cli.py`` to a top-level ``cli/``
package so the dependency DAG becomes ``cli -> (backend, fetcher);
backend -> fetcher`` instead of the previous ``fetcher -> backend``
back-edge.

The CLI's ``install-watcher`` Click command lives in
``cli/main.py`` and delegates the platform-specific install/uninstall
work to the helpers here.

Owns:

  * Cross-platform unit/job identifiers (``_LAUNCHD_LABEL``,
    ``_SYSTEMD_UNIT_NAME``, ``_WIN_TASK_NAME``).
  * Template generators: ``_build_watcher_inline_script``,
    ``_build_launchd_plist``, ``_build_systemd_unit``, plus
    ``_xml_escape``.
  * Launcher-file writer: ``_write_watcher_launcher`` (shared between
    Linux + Windows; neither systemd's ``ExecStart`` nor Windows
    ``schtasks /TR`` tolerate multi-line ``-c`` script bodies).
  * Per-platform install/uninstall functions:
    ``_install_macos`` / ``_uninstall_macos`` / ``_install_linux`` /
    ``_uninstall_linux`` / ``_install_windows`` / ``_uninstall_windows``.

The watcher itself (``backend.cc_watcher.run_watcher``) is referenced
by name from the generated launcher script body — backend is NOT
imported here at module-load time, only at run-time inside the
supervised subprocess. That preserves the layering: this module
generates orchestration artifacts, the artifacts later import the
backend module.

XML safety: every interpolation of user-controlled paths into the
launchd plist goes through ``_xml_escape`` (Council A2-PLIST-XSS;
pinned by ``fetcher/tests/test_watcher_install_xml_safety.py``,
which now patches ``cli.watcher.Path.home`` rather than the old
``fetcher.cli.Path.home``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click


# Cross-platform unit/job identifiers. Same logical name ("claude-explorer
# CC image-cache watcher"), spelled in each OS's idiomatic style:
_LAUNCHD_LABEL = "com.claude-explorer.cc-watcher"
_SYSTEMD_UNIT_NAME = "claude-explorer-cc-watcher.service"
_WIN_TASK_NAME = "ClaudeExplorerCCWatcher"


# The unit-file paths are functions, not module constants, so they read
# ``Path.home()`` at call time. A constant froze the developer's real
# home at import, and a test that patched HOME and ran
# ``install-watcher --uninstall`` then deleted the real launchd job.
# See backend/tests/test_watcher_paths_follow_home.py.
def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / _SYSTEMD_UNIT_NAME


# Single source of truth for the watcher loop body. Each platform's
# unit file invokes ``<python> -c <this script>`` (or executes a
# launcher file containing this body) so all three OSes run the same
# exact loop — only the supervisor changes.
#
# The body delegates to ``run_watcher``, which combines the
# ``watchdog`` event-driven primary path (FSEvents/inotify/RDCW)
# with a periodic backstop poll. The ``--interval`` CLI flag is
# stamped into the env var the watcher reads at module import to set
# the BACKSTOP poll interval — events handle the latency-critical
# work, so longer intervals are fine. Defaults to 600s (10 min).
def _build_watcher_inline_script(scan_interval: float) -> str:
    return (
        "import asyncio, logging, os\n"
        # Configure logging FIRST, before any of our imports run their
        # `logging.getLogger(__name__)` calls, so launchd/systemd/Task-
        # Scheduler stdout+stderr capture our INFO-level diagnostics
        # ("Observer started (FSEventsObserver) on ...", per-pass
        # handled counts, search-index drift counts). Without this
        # the supervised job runs silently and you can't tell from
        # the logs whether the event-driven path actually started.
        "logging.basicConfig(\n"
        "    level=logging.INFO,\n"
        "    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',\n"
        ")\n"
        # Stamp the requested backstop interval into the env BEFORE
        # importing the watcher module (the module captures it at
        # import via _resolve_interval()). We always set it even when
        # the user didn't override --interval, so the supervised
        # process is never affected by a stale env from the parent
        # shell.
        f"os.environ['CLAUDE_EXPLORER_CC_WATCHER_INTERVAL_SEC'] = '{scan_interval}'\n"
        "from backend.cc_watcher import run_watcher\n"
        "asyncio.run(run_watcher(asyncio.Event()))\n"
    )


def _xml_escape(s: str) -> str:
    return (s.replace('&', '&amp;')
             .replace('<', '&lt;')
             .replace('>', '&gt;')
             .replace('"', '&quot;'))


def _build_launchd_plist(python_bin: str, scan_interval: float) -> str:
    """Render a macOS launchd plist that runs the CC image watcher
    continuously, independent of `claude-explorer serve`.

    The plist runs a tiny inline script that loops `scan_once()` —
    no FastAPI machinery, just the watcher logic. ``RunAtLoad`` plus
    ``KeepAlive`` means launchd starts it at login and restarts on
    crash. Logs land in `~/Library/Logs/claude-explorer-cc-watcher.{out,err}`.
    """
    log_dir = Path.home() / "Library" / "Logs"
    program_args = [python_bin, "-c", _build_watcher_inline_script(scan_interval)]
    args_xml = "\n        ".join(f"<string>{_xml_escape(a)}</string>" for a in program_args)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '    <key>Label</key>\n'
        f'    <string>{_LAUNCHD_LABEL}</string>\n'
        '    <key>ProgramArguments</key>\n'
        '    <array>\n'
        f'        {args_xml}\n'
        '    </array>\n'
        '    <key>RunAtLoad</key>\n'
        '    <true/>\n'
        '    <key>KeepAlive</key>\n'
        '    <true/>\n'
        # XML-escape user-controlled paths (cwd + home-derived log dir).
        # Without this, a path containing '&', '<', '>', or '"' produces a
        # malformed plist that launchd silently rejects, breaking the
        # supervised watcher. The ProgramArguments strings just above are
        # already escaped via the args_xml generator expression. Council
        # A2-PLIST-XSS; pinned by
        # ``fetcher/tests/test_watcher_install_xml_safety.py``.
        '    <key>StandardOutPath</key>\n'
        f'    <string>{_xml_escape(str(log_dir))}/claude-explorer-cc-watcher.out</string>\n'
        '    <key>StandardErrorPath</key>\n'
        f'    <string>{_xml_escape(str(log_dir))}/claude-explorer-cc-watcher.err</string>\n'
        '    <key>WorkingDirectory</key>\n'
        f'    <string>{_xml_escape(str(Path.cwd()))}</string>\n'
        '</dict>\n'
        '</plist>\n'
    )


# Launcher script written to a stable cross-platform location. The
# Linux systemd unit and the Windows scheduled task both invoke this
# instead of an inline ``-c`` script — systemd's ExecStart can't carry
# embedded newlines and Windows ``schtasks /TR`` can't carry embedded
# quotes, so a launcher file dodges both.
def _watcher_launcher_path() -> Path:
    return Path.home() / ".claude-explorer" / "cc-watcher.py"


def _write_watcher_launcher(scan_interval: float) -> Path:
    """Write the watcher loop body to a stable path and return it.
    Idempotent — overwrites the file each install with the current
    interval baked in.
    """
    body = (
        '"""Auto-generated by `claude-explorer install-watcher`. '
        'Do not edit by hand — re-run install-watcher to regenerate."""\n'
        + _build_watcher_inline_script(scan_interval)
    )
    launcher = _watcher_launcher_path()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(body)
    return launcher


def _build_systemd_unit(python_bin: str, launcher_path: Path, working_dir: str) -> str:
    """Render a Linux systemd user unit that runs the CC image watcher
    continuously, independent of `claude-explorer serve`.

    User-level (not system-level) so no root is required: the unit
    lives at ``~/.config/systemd/user/claude-explorer-cc-watcher.service``
    and is enabled with ``systemctl --user enable --now``.

    ``Restart=always`` matches launchd's ``KeepAlive``: if the loop
    crashes the service is restarted. Stdout/stderr go to the journal
    (``journalctl --user -u claude-explorer-cc-watcher.service``).

    Caveat: by default systemd user units stop when the user logs out.
    For a watcher that should run even when no GUI session is active,
    enable lingering: ``loginctl enable-linger $USER``. The CLI prints
    this hint after install.

    ExecStart calls a launcher file rather than embedding ``-c <script>``
    because systemd treats newlines as directive separators — a multi-
    line ``-c`` body would silently truncate or fail to parse.
    """
    return (
        "[Unit]\n"
        "Description=Claude Explorer CC image-cache watcher\n"
        "After=default.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={python_bin} {launcher_path}\n"
        f"WorkingDirectory={working_dir}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _install_macos(python_bin: str, interval: float) -> None:
    """macOS launchd path."""
    plist = _launchd_plist_path()
    plist_body = _build_launchd_plist(python_bin, interval)
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(plist_body)
    click.echo(f"Wrote {plist}")
    # Reload to pick up changes if already loaded.
    subprocess.run(
        ["launchctl", "unload", str(plist)],
        check=False, capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "load", str(plist)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"launchctl load failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    click.echo("")
    click.echo("Watcher installed and loaded (macOS launchd).")
    click.echo(f"  interval:  {interval}s")
    click.echo(f"  python:    {python_bin}")
    click.echo(f"  cwd:       {Path.cwd()}")
    click.echo("  stdout:    ~/Library/Logs/claude-explorer-cc-watcher.out")
    click.echo("  stderr:    ~/Library/Logs/claude-explorer-cc-watcher.err")
    click.echo("")
    click.echo("Verify with: launchctl list | grep claude-explorer")


def _uninstall_macos() -> None:
    """macOS launchd path."""
    plist = _launchd_plist_path()
    if plist.exists():
        subprocess.run(
            ["launchctl", "unload", str(plist)],
            check=False, capture_output=True,
        )
        plist.unlink()
        click.echo(f"Removed {plist}")
    else:
        click.echo(f"Not installed: {plist} does not exist")


def _install_linux(python_bin: str, interval: float) -> None:
    """Linux systemd user-unit path.

    Prefer ``systemctl --user`` so no root is required. Print a hint
    about ``loginctl enable-linger`` so the watcher keeps running even
    when no GUI session is active (the V1 default that "just works"
    on a typical interactive desktop will NOT survive logout otherwise).
    """
    unit = _systemd_unit_path()
    launcher = _write_watcher_launcher(interval)
    click.echo(f"Wrote {launcher}")
    unit_body = _build_systemd_unit(python_bin, launcher, str(Path.cwd()))
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(unit_body)
    click.echo(f"Wrote {unit}")

    # Reload + enable + start.
    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", _SYSTEMD_UNIT_NAME],
        ["systemctl", "--user", "restart", _SYSTEMD_UNIT_NAME],
    ):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise click.ClickException(
                f"`{' '.join(cmd)}` failed: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    click.echo("")
    click.echo("Watcher installed and started (Linux systemd user unit).")
    click.echo(f"  interval:  {interval}s")
    click.echo(f"  python:    {python_bin}")
    click.echo(f"  cwd:       {Path.cwd()}")
    click.echo("  logs:      journalctl --user -u claude-explorer-cc-watcher.service -f")
    click.echo("")
    click.echo("Verify with: systemctl --user status claude-explorer-cc-watcher.service")
    click.echo("")
    click.echo("IMPORTANT: by default this stops when you log out. To keep it")
    click.echo("running across logout/headless boots, run ONCE:")
    click.echo("    sudo loginctl enable-linger $USER")


def _uninstall_linux() -> None:
    """Linux systemd user-unit path."""
    launcher = _watcher_launcher_path()
    unit = _systemd_unit_path()
    if unit.exists():
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", _SYSTEMD_UNIT_NAME],
            check=False, capture_output=True,
        )
        unit.unlink()
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=False, capture_output=True,
        )
        click.echo(f"Removed {unit}")
    else:
        click.echo(f"Not installed: {unit} does not exist")
    if launcher.exists():
        launcher.unlink()
        click.echo(f"Removed {launcher}")


def _install_windows(python_bin: str, interval: float) -> None:
    """Windows Task Scheduler path.

    Uses the same shared launcher (``%USERPROFILE%\\.claude-explorer\\
    cc-watcher.py``) that systemd uses on Linux — ``schtasks /TR``
    can't tolerate the multi-line ``-c`` script form, and reusing the
    launcher keeps the loop body identical across platforms.

    Prefer ``pythonw.exe`` over ``python.exe`` so the watcher doesn't
    open a console window every login.
    """
    launcher = _write_watcher_launcher(interval)
    click.echo(f"Wrote {launcher}")

    pythonw = str(Path(python_bin).with_name("pythonw.exe"))
    if not Path(pythonw).exists():
        pythonw = python_bin

    # Re-create from scratch each install (idempotent via /F).
    #
    # NOTE: Do NOT pass ``/RL HIGHEST``. That flag asks Task Scheduler to
    # run the task with the highest privileges available to the user,
    # which requires the registration itself to come from an elevated
    # process — non-admin shells get ``ERROR: Access is denied`` when
    # creating the task. The watcher only touches user-owned files
    # (``~\.claude-explorer\``, the CC image cache under ``~\.claude\``);
    # it does not need elevation. Default run-level (limited / standard
    # user) registers cleanly from any user shell and is sufficient for
    # the work the watcher does.
    cmd = [
        "schtasks", "/Create",
        "/TN", _WIN_TASK_NAME,
        "/TR", f'"{pythonw}" "{launcher}"',
        "/SC", "ONLOGON",
        "/F",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException(
            f"schtasks /Create failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    # Start it now so the user doesn't have to log out / back in.
    subprocess.run(
        ["schtasks", "/Run", "/TN", _WIN_TASK_NAME],
        check=False, capture_output=True,
    )

    click.echo("")
    click.echo("Watcher installed and started (Windows Task Scheduler).")
    click.echo(f"  interval:    {interval}s")
    click.echo(f"  python:      {pythonw}")
    click.echo(f"  launcher:    {launcher}")
    click.echo(f"  task name:   {_WIN_TASK_NAME}")
    click.echo("")
    click.echo(f"Verify with: schtasks /Query /TN {_WIN_TASK_NAME}")


def _uninstall_windows() -> None:
    """Windows Task Scheduler path."""
    launcher = _watcher_launcher_path()
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", _WIN_TASK_NAME, "/F"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        click.echo(f"Removed scheduled task: {_WIN_TASK_NAME}")
    else:
        # `schtasks /Delete` returns nonzero if the task doesn't exist.
        # That's the moral equivalent of "not installed", so don't error.
        click.echo(
            f"Not installed (or already removed): {_WIN_TASK_NAME} "
            f"({result.stderr.strip() or result.stdout.strip()})"
        )

    if launcher.exists():
        launcher.unlink()
        click.echo(f"Removed {launcher}")
