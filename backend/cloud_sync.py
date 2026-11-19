"""Detect a write target inside a cloud-sync folder (plan D13, §4.3).

Work transcripts must not reach a cloud service by accident, so every
Explorer writer asks :func:`detect_cloud_sync` before it writes. The rule is
the "U2 Rule" in ``PLANS/reviews/2026.11.18-codex-source/council_report.md``.

Detection is best effort: a sync client that is not in the list below is not
detected. The client install paths are not verified on every platform, so
every root is checked on every platform. When the path cannot be resolved,
detection cannot decide, and the folder counts as cloud-synced.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

#: The provider name for a folder that detection cannot decide on.
UNKNOWN_PROVIDER = "Unknown cloud provider"

_ONEDRIVE_ENV_VARS = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")

# The first part of a ~/Library/CloudStorage/<name> folder, before the "-".
_CLOUDSTORAGE_PROVIDERS = {
    "dropbox": "Dropbox",
    "onedrive": "OneDrive",
    "googledrive": "Google Drive",
    "icloud": "iCloud Drive",
    "icloud drive": "iCloud Drive",
    "box": "Box",
}


def _fold(path: str) -> str:
    # macOS and Windows file systems ignore case by default.
    return path.casefold() if sys.platform in ("darwin", "win32") else path


def _within(target: str, root: str) -> bool:
    root = root.rstrip("/\\") or root
    return target == root or target.startswith((root + os.sep, root + "/"))


def _dropbox_roots(home: Path, environ: Mapping[str, str]) -> Iterator[Path]:
    info_files = [home / ".dropbox" / "info.json"]
    info_files += [
        Path(environ[var]) / "Dropbox" / "info.json"
        for var in ("APPDATA", "LOCALAPPDATA")
        if environ.get(var)
    ]
    for info in info_files:
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # No Dropbox install here, or a file that we cannot read.
        if not isinstance(data, dict):
            continue
        for account in data.values():
            if isinstance(account, dict) and isinstance(account.get("path"), str):
                yield Path(account["path"])


def _roots(home: Path, environ: Mapping[str, str]) -> Iterator[tuple[str, Path]]:
    yield "iCloud Drive", home / "Library" / "Mobile Documents"
    yield "iCloud Drive", home / "iCloudDrive"
    yield "Dropbox", home / "Dropbox"
    for root in _dropbox_roots(home, environ):
        yield "Dropbox", root
    for var in _ONEDRIVE_ENV_VARS:
        if environ.get(var):
            yield "OneDrive", Path(environ[var])
    try:
        onedrive_dirs = sorted(home.glob("OneDrive*"))
    except OSError:
        onedrive_dirs = []
    for root in onedrive_dirs:
        yield "OneDrive", root
    yield "Google Drive", home / "Google Drive"
    yield "Google Drive", home / "My Drive"


def _cloudstorage_provider(folder_name: str) -> str:
    prefix = folder_name.split("-", 1)[0].strip()
    return _CLOUDSTORAGE_PROVIDERS.get(prefix.casefold(), prefix or UNKNOWN_PROVIDER)


def detect_cloud_sync(
    path: Path,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the cloud provider whose sync folder holds ``path``, or ``None``.

    ``path`` need not exist. Symlinks are followed, so a link from a local
    folder into a cloud root is detected. ``home`` and ``environ`` default to
    ``Path.home()`` and ``os.environ``.
    """
    environ = os.environ if environ is None else environ
    try:
        home = Path.home() if home is None else home
        real = os.path.realpath(path, strict=False)
    except (OSError, RuntimeError, ValueError):
        return UNKNOWN_PROVIDER  # Fail closed: an unresolved folder counts as synced.
    target = _fold(real)

    # Newer Dropbox, OneDrive, and Google Drive clients use macOS File Provider.
    storage = os.path.realpath(home / "Library" / "CloudStorage")
    depth = len(Path(storage).parts)
    if _within(target, _fold(storage)) and len(Path(real).parts) > depth:
        return _cloudstorage_provider(Path(real).parts[depth])

    for provider, root in _roots(home, environ):
        try:
            resolved = _fold(os.path.realpath(root))
        except (OSError, RuntimeError, ValueError):
            continue
        if _within(target, resolved):
            return provider

    # Google Drive for desktop on Windows mounts a drive whose top folder is "My Drive".
    parts = Path(target).parts
    if len(parts) > 1 and parts[1].casefold() == "my drive":
        return "Google Drive"
    return None


__all__ = ["UNKNOWN_PROVIDER", "detect_cloud_sync"]
