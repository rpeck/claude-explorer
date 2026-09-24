"""``/api/health/watcher`` — supervised CC image-cache watcher install
state for the frontend banner (PLANS/2026.05.26-watcher-install-detection.md).

The frontend polls this every 5 min. The endpoint invalidates the
module-level cache on each call so a mid-session install
(user ran ``claude-explorer install-watcher`` between polls) is
reflected on the very next poll — no backend restart required.

Per-call cost: one subprocess to launchctl/systemctl/schtasks (~5ms
on macOS, <1ms cached path on Linux/Windows). Polling cost at 12/hr =
trivial.
"""

from __future__ import annotations

import sys

from fastapi import APIRouter
from pydantic import BaseModel

from fetcher.install_hints import watcher_install_hint

from ..watcher_status import invalidate_cache, is_watcher_installed


router = APIRouter()


class WatcherHealth(BaseModel):
    installed: bool
    platform: str
    install_command: str
    docs_url: str


@router.get(
    "/api/health/watcher",
    response_model=WatcherHealth,
    summary="CC image-cache watcher install state",
)
def get_watcher_health() -> WatcherHealth:
    # Bust the process-lifetime cache so a fresh install or uninstall
    # between polls is reflected immediately. The check is cheap; for
    # 12 polls/hour the subprocess overhead is irrelevant.
    invalidate_cache()
    return WatcherHealth(
        installed=is_watcher_installed(),
        platform=sys.platform,
        install_command=watcher_install_hint(),
        docs_url="PLANS/2026.05.26-watcher-install-detection.md",
    )
