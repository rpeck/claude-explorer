"""The write gates and the archive copy for every caller (plan §4.3).

Two gates protect the archive, in this order:

1. **Corrupt config (D9):** a config that did not parse can point to the
   wrong ``data_dir``, so the copy waits until the user fixes the file.
2. **Cloud sync (D13):** no Explorer writer puts data into a cloud-synced
   folder until the user opts in (``allow_writes_in_cloud_sync``).

The watcher, Refresh, ``/fetch/start``, the CLI, and MCP all copy through
:func:`run_archive_syncs`. The MCP closure forbids ``backend.cc_watcher``,
``backend.deps``, and the routers, so this module imports none of them.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from backend.archive_sync import SyncContext, SyncReason, SyncReport
from backend.cloud_sync import detect_cloud_sync
from backend.config import Settings, get_settings
from backend.sources.codex import _display_home
from backend.sources.registry import get_adapters

if TYPE_CHECKING:
    from backend.store import ConversationStore

log = logging.getLogger(__name__)


def _real(path: str | Path) -> str:
    real = os.path.realpath(path)
    return real.casefold() if sys.platform in ("darwin", "win32") else real


def write_targets(settings: Settings) -> tuple[Path, ...]:
    """The folders and files that Explorer writes: ``data_dir`` and its siblings."""
    data_dir = settings.data_dir
    # Same rule as backend.cc_image_cache.cache_dir, which imports this module.
    images = data_dir.parent / "cc-images" if data_dir.name == "conversations" else data_dir / "cc-images"
    return (data_dir, data_dir.parent / "search-index.sqlite", images)


def cloud_synced_target(settings: Settings) -> tuple[str, Path] | None:
    """``(provider, resolved target)`` for the first cloud-synced write target."""
    for target in write_targets(settings):
        provider = detect_cloud_sync(target)
        if provider is not None:
            return provider, Path(os.path.realpath(target))
    return None


def same_path(a: str | Path, b: str | Path) -> bool:
    """True when both paths resolve to the same place."""
    return _real(a) == _real(b)


def cloud_sync_block(settings: Settings) -> tuple[str, Path] | None:
    """``(provider, resolved target)`` while the cloud-sync gate is closed, else ``None``.

    The gate is open when no write target is in a cloud-sync folder, or when
    ``allow_writes_in_cloud_sync`` equals the resolved ``data_dir``.
    """
    allowed = settings.allow_writes_in_cloud_sync
    if allowed and same_path(allowed, settings.data_dir):
        return None
    return cloud_synced_target(settings)


def cloud_sync_message(block: tuple[str, Path]) -> str:
    provider, folder = block
    return (
        f"Claude Explorer does not write to {folder}, because {provider} syncs it "
        "to the cloud. To allow writes there, confirm in the cloud-sync consent dialog."
    )


def cloud_sync_notice(block: tuple[str, Path]) -> str:
    """:func:`cloud_sync_message` with no absolute path, for MCP: the path can
    hold the user name, and MCP text reaches a model (D14)."""
    provider, folder = block
    shown = _display_home(str(folder))
    where = shown if shown.startswith("~") else "its data folder"
    return (
        f"Claude Explorer does not write to {where}, because {provider} syncs it "
        "to the cloud. To allow writes there, confirm in the cloud-sync consent dialog."
    )


def run_archive_syncs(
    store: ConversationStore, reason: SyncReason, lock_timeout_s: float
) -> list[SyncReport]:
    """Check both gates, then copy each source into the archive, in registry order.

    A closed gate gives one report with its ``skipped_reason`` and calls no
    adapter. A source that copies nothing gives no report.
    """
    # Read the config again: the user can fix or break it while we run.
    cache_clear = getattr(get_settings, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()
    settings = get_settings()
    if settings.config_corrupt_reason:
        log.warning(
            "archive copy (%s) skipped: the config file is corrupt: %s",
            reason, settings.config_corrupt_reason,
        )
        return [SyncReport(skipped_reason="corrupt_config")]
    block = cloud_sync_block(settings)
    if block is not None:
        log.warning("archive copy (%s) skipped: %s", reason, cloud_sync_message(block))
        # Only MCP reads the warnings, so they get the text with no absolute path.
        return [SyncReport(skipped_reason="cloud_sync_gate", warnings=[cloud_sync_notice(block)])]

    ctx = SyncContext(settings=settings, reason=reason, lock_timeout_s=lock_timeout_s)
    reports: list[SyncReport] = []
    for adapter in get_adapters(store):
        try:
            report = adapter.archive_sync(ctx)
        except Exception:
            # One broken source must not stop the copy of the others.
            log.exception("archive copy (%s) failed for %s", reason, adapter.descriptor().id)
            continue
        if report is not None:
            reports.append(report)
    return reports


def sync_busy(reports: list[SyncReport]) -> bool:
    """True when a copy did not run because another writer held the lock."""
    return any(r.skipped_reason in ("busy", "timeout") for r in reports)


__all__ = [
    "cloud_sync_block",
    "cloud_sync_message",
    "cloud_sync_notice",
    "cloud_synced_target",
    "run_archive_syncs",
    "same_path",
    "sync_busy",
    "write_targets",
]
