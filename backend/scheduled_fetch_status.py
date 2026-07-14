"""Read/write the scheduled-fetch run-status file.

Single source of truth for `doctor` and the notification transition
check. CLI-only — must stay OUT of the MCPB import closure. Never
raises on read (missing/corrupt → defaults).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import canonical_home_dir


@dataclass
class FetchStatus:
    last_run_at: str | None = None
    last_success_at: str | None = None
    last_result: str = "unknown"          # ok | auth_expired | needs_auth | error | unknown
    auth_expired: bool = False
    fetched_count: int | None = None
    error: str | None = None
    interval_sec: int | None = None


def status_path() -> Path:
    return canonical_home_dir() / "scheduled-fetch-status.json"


def read_status(path: Path | None = None) -> FetchStatus:
    p = path or status_path()
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return FetchStatus()
    if not isinstance(data, dict):
        return FetchStatus()
    _defaults = FetchStatus()
    known = {f: data.get(f, getattr(_defaults, f)) for f in _defaults.__dict__}
    return FetchStatus(**known)


def write_status(status: FetchStatus, path: Path | None = None) -> None:
    p = path or status_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(json.dumps(asdict(status), indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except OSError:
        if tmp.exists():
            tmp.unlink()
        raise


_LAUNCHD_LABEL = "com.claude-explorer.scheduled-fetch"
_SYSTEMD_TIMER = "claude-explorer-scheduled-fetch.timer"
_WIN_TASK = "ClaudeExplorerScheduledFetch"
_ENV = "CLAUDE_EXPLORER_SCHEDULED_FETCH_INSTALLED"
_TRUTHY = {"1", "true", "yes"}
_FALSY = {"0", "false", "no"}


def is_scheduled_fetch_installed() -> bool:
    """Return True if scheduled fetch is installed (via the platform's supervisor).

    Env override CLAUDE_EXPLORER_SCHEDULED_FETCH_INSTALLED (1/true/yes → True,
    0/false/no → False) short-circuits; else platform probe:
    - macOS: launchctl list contains com.claude-explorer.scheduled-fetch
    - Linux: systemctl --user is-enabled claude-explorer-scheduled-fetch.timer rc 0
    - Windows: schtasks /Query /TN ClaudeExplorerScheduledFetch rc 0

    Any probe error → False (never raises).
    """
    override = os.environ.get(_ENV, "").strip().lower()
    if override in _TRUTHY:
        return True
    if override in _FALSY:
        return False
    try:
        if sys.platform == "darwin":
            result = subprocess.run(
                ["launchctl", "list"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return _LAUNCHD_LABEL in result.stdout
        if sys.platform.startswith("linux"):
            result = subprocess.run(
                ["systemctl", "--user", "is-enabled", _SYSTEMD_TIMER],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        if sys.platform == "win32":
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", _WIN_TASK],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    return False
