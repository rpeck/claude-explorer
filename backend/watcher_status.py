"""Detect whether the supervised CC image-cache watcher is installed.

Single consumer today: :mod:`backend.cc_image_cache` uses the result
to choose the log level for the "image referenced but not on disk"
warning. Watcher installed → INFO (loss is historical; future losses
prevented). Watcher missing → WARNING (loss is ongoing; user must
act).

Cross-platform check:

* macOS:  ``launchctl list`` contains ``com.claude-explorer.cc-watcher``.
* Linux:  ``systemctl --user is-enabled claude-explorer-cc-watcher.service``
          returns 0.
* Windows: ``schtasks /Query /TN ClaudeExplorerCCWatcher`` returns 0.

Cached for the process lifetime — the install state doesn't change
mid-run in practice (install-watcher is a one-shot CLI; the
supervised job starts at next login). Tests use
:func:`invalidate_cache` between cases.

Env-var override ``CLAUDE_EXPLORER_WATCHER_INSTALLED`` short-circuits
the platform probe — useful for tests, for debugging when
launchctl/systemctl is unreachable (sandboxes, CI containers), or for
explicit operator override. Recognized values: ``1/true/yes`` → True,
``0/false/no`` → False. Garbage values fall through to the real probe
(least-surprise: an unparseable override should not silently flip the
correctness signal).

Detection is advisory. Any unexpected probe failure (missing binary,
permission denied, timeout) is logged at DEBUG and returns False — the
louder, action-required default.
"""

from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)


# Mirrors the identifiers in cli/watcher.py — same logical name in each
# OS's idiomatic style. If you rename either side, rename both.
_LAUNCHD_LABEL = "com.claude-explorer.cc-watcher"
_SYSTEMD_UNIT = "claude-explorer-cc-watcher.service"
_WIN_TASK_NAME = "ClaudeExplorerCCWatcher"

_ENV_VAR = "CLAUDE_EXPLORER_WATCHER_INSTALLED"
_TRUTHY = frozenset({"1", "true", "yes"})
_FALSY = frozenset({"0", "false", "no"})

# 5-second timeout on subprocess probes. launchctl/systemctl/schtasks
# normally return in <50ms; a 5s ceiling catches hung-state weirdness
# without making startup-path callers wait noticeably.
_PROBE_TIMEOUT_SEC = 5.0


@functools.lru_cache(maxsize=1)
def is_watcher_installed() -> bool:
    """Return True if the supervised CC watcher is installed and loaded.

    Cached per-process; see module docstring for invalidation."""
    override = os.environ.get(_ENV_VAR)
    if override is not None:
        v = override.strip().lower()
        if v in _TRUTHY:
            return True
        if v in _FALSY:
            return False
        # Garbage override: fall through to platform probe.
    try:
        return _platform_check()
    except Exception:
        log.debug(
            "Watcher-install probe raised; treating as not installed",
            exc_info=True,
        )
        return False


def invalidate_cache() -> None:
    """Force the next :func:`is_watcher_installed` call to re-probe.

    Used by tests between cases. Production code generally should NOT
    call this; the install state is stable for the process lifetime
    (the supervised job starts at next login, not mid-process)."""
    is_watcher_installed.cache_clear()


def _platform_check() -> bool:
    """Dispatch to the OS-specific probe. Indirected via a function
    (rather than ``if sys.platform`` inlined into ``is_watcher_installed``)
    so tests can monkeypatch this one symbol regardless of host OS."""
    if sys.platform == "darwin":
        return _macos_check()
    if sys.platform.startswith("linux"):
        return _linux_check()
    if sys.platform == "win32":
        return _windows_check()
    return False


def _macos_check() -> bool:
    if shutil.which("launchctl") is None:
        return False
    result = subprocess.run(
        ["launchctl", "list"],
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SEC,
        check=False,
    )
    if result.returncode != 0:
        return False
    # Tab-delimited "PID\tStatus\tLabel" — match the canonical label as
    # a whole field, not a substring (so "com.claude.foo" never
    # false-positives).
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2].strip() == _LAUNCHD_LABEL:
            return True
    return False


def _linux_check() -> bool:
    if shutil.which("systemctl") is None:
        return False
    result = subprocess.run(
        ["systemctl", "--user", "is-enabled", _SYSTEMD_UNIT],
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SEC,
        check=False,
    )
    return result.returncode == 0


def _windows_check() -> bool:
    if shutil.which("schtasks") is None:
        return False
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", _WIN_TASK_NAME],
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SEC,
        check=False,
    )
    return result.returncode == 0
