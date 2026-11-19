"""Copy append-only source files into the Explorer archive (plan §4.2, §4.3).

The archive keeps every source file even after the source tool deletes it
(CLAUDE.md principle 2). This module holds the generic parts of a copy pass:

- :class:`ArchiveLock`: one writer per archive folder, across threads and
  processes.
- :func:`run_locked_pass`: the home check and the lock around a pass.
- :func:`sync_stream`: steps 2.1-2.7 of §4.2 for one file stem.

A source adapter supplies the file list and the decoder. The copy only grows:
new bytes are appended (D12), a diverged source makes a ``.rev-<n>`` copy, and
nothing is ever deleted.

The MCP closure forbids ``backend.cc_watcher`` and ``backend.deps``, so this
module imports neither.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import portalocker

from fetcher.atomic import atomic_replace, atomic_write_bytes

if TYPE_CHECKING:
    from backend.config import Settings

log = logging.getLogger(__name__)

META_VERSION = 1
LOCK_FILE_NAME = ".sync.lock"

SyncReason = Literal["watcher", "backstop", "refresh", "cli", "mcp"]
SkippedReason = Literal["busy", "timeout", "corrupt_config", "cloud_sync_gate", "no_home"]

# A stem names files in the archive, so it gets no dots, slashes, or "..".
_STEM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
_REV_RE = re.compile(r"\.rev-([0-9]+)\.jsonl")
_LOCK_POLL_S = 0.05


@dataclass
class SyncContext:
    settings: Settings
    reason: SyncReason
    lock_timeout_s: float


def _zero_counts() -> dict[str, int]:
    return {"copied": 0, "unchanged": 0, "appended": 0, "revisions": 0, "decode_errors": 0}


@dataclass
class SyncReport:
    wrote_any: bool = False
    skipped_reason: SkippedReason | None = None
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=_zero_counts)

    def warn(self, message: str) -> None:
        log.warning("%s", message)
        self.warnings.append(message)


def home_key(home: Path) -> str:
    """The archive folder name for a source home: 12 hex chars of SHA-256."""
    real = os.path.realpath(home)
    if sys.platform in ("darwin", "win32"):
        real = real.casefold()
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:12]


# --- Lock ----------------------------------------------------------------

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _thread_lock_for(key: str) -> threading.Lock:
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.Lock())


class ArchiveLock:
    """An exclusive lock on one archive folder, for threads and processes.

    A module ``threading.Lock`` per resolved path excludes other threads of
    this process. A portalocker lock on a fresh handle of the lock file
    excludes other processes: ``flock`` and ``msvcrt.locking`` lock per
    handle, so a shared handle would not block. The file holds no holder
    data, and the OS releases the lock of a process that dies.
    """

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = Path(lock_path)
        key = os.path.realpath(self.lock_path)
        if sys.platform in ("darwin", "win32"):
            key = key.casefold()
        self._thread_lock = _thread_lock_for(key)
        self._fh: Any = None

    def acquire(self, timeout_s: float) -> bool:
        """Take the lock. Wait at most ``timeout_s`` seconds (0 = one try)."""
        deadline = time.monotonic() + max(timeout_s, 0.0)
        if timeout_s > 0:
            got = self._thread_lock.acquire(timeout=timeout_s)
        else:
            got = self._thread_lock.acquire(blocking=False)
        if not got:
            return False
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.lock_path, "a+b")  # noqa: SIM115 - held until release()
        except BaseException:
            self._thread_lock.release()
            raise
        flags = portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING
        while True:
            try:
                portalocker.lock(fh, flags)
                self._fh = fh
                return True
            except portalocker.LockException:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_LOCK_POLL_S)
            except BaseException:
                fh.close()
                self._thread_lock.release()
                raise
        fh.close()
        self._thread_lock.release()
        return False

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            portalocker.unlock(fh)
        finally:
            fh.close()
            self._thread_lock.release()


def run_locked_pass(
    ctx: SyncContext,
    *,
    source_home: Path,
    archive_dir: Path,
    body: Callable[[SyncReport], None],
) -> SyncReport:
    """Run ``body`` under the archive lock, after the home check (§4.2 step 1).

    A missing ``source_home`` stops the pass and creates nothing. When the
    lock is not free within ``ctx.lock_timeout_s``, ``body`` does not run.
    """
    report = SyncReport()
    if not source_home.is_dir():
        report.skipped_reason = "no_home"
        return report
    lock = ArchiveLock(archive_dir / LOCK_FILE_NAME)
    if not lock.acquire(ctx.lock_timeout_s):
        report.skipped_reason = "busy" if ctx.lock_timeout_s <= 0 else "timeout"
        return report
    try:
        body(report)
    finally:
        lock.release()
    return report


# --- One stem --------------------------------------------------------------


def _checked_path(dest_dir: Path, name: str) -> Path:
    path = dest_dir / name
    real_dir = os.path.realpath(dest_dir)
    if os.path.dirname(os.path.realpath(path)) != real_dir:
        raise ValueError(f"archive path escapes {dest_dir}: {name!r}")
    return path


def _read_meta(path: Path) -> dict | None:
    try:
        meta = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _newest_revision(dest_dir: Path, stem: str) -> tuple[int, Path | None]:
    newest, newest_path = 0, None
    try:
        names = os.listdir(dest_dir)
    except OSError:
        return 0, None
    for name in names:
        if not name.startswith(stem):
            continue
        m = _REV_RE.fullmatch(name[len(stem) :])
        if m and int(m.group(1)) > newest:
            newest, newest_path = int(m.group(1)), dest_dir / name
    return newest, newest_path


def _append(path: Path, data: bytes) -> None:
    with open(path, "ab") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def sync_stream(
    report: SyncReport,
    *,
    dest_dir: Path,
    stem: str,
    source_path: Path,
    decode: Callable[[Path], Iterable[bytes]],
    meta_extra: Mapping[str, object] | None = None,
) -> None:
    """Bring ``dest_dir/<stem>.jsonl`` up to date with ``source_path``.

    ``decode(source_path)`` yields the decoded bytes. Any exception from it is
    a decode error: the archive stays as it is, and the next pass tries again.
    An invalid ``stem`` raises ``ValueError`` and writes nothing.
    """
    if not isinstance(stem, str) or not _STEM_RE.fullmatch(stem):
        raise ValueError(f"invalid archive stem: {stem!r}")
    copy_path = _checked_path(dest_dir, f"{stem}.jsonl")
    meta_path = _checked_path(dest_dir, f"{stem}.meta.json")

    # 2.1: an unchanged source needs no read.
    try:
        st = os.stat(source_path)
    except OSError as exc:
        report.warn(f"cannot stat {source_path}: {exc}")
        return
    meta = _read_meta(meta_path)
    version = meta.get("version", 0) if meta is not None else 0
    if not isinstance(version, int) or version > META_VERSION:
        # A newer Explorer wrote this stem. Do not overwrite its files.
        report.warn(f"skipping {meta_path.name}: version {version!r} is newer than {META_VERSION}")
        return
    if (
        meta is not None
        and meta.get("source_path") == str(source_path)
        and meta.get("source_size") == st.st_size
        and meta.get("source_mtime_ns") == st.st_mtime_ns
    ):
        report.counts["unchanged"] += 1
        return

    # 2.2: decode in full before any write. A decode error never makes a revision.
    # shortcut: holds the decoded file in memory; stream to a temp file if
    # decoded rollouts grow past tens of MB.
    try:
        data = b"".join(decode(source_path))
    except Exception as exc:  # noqa: BLE001 - any decoder failure is a decode error
        report.counts["decode_errors"] += 1
        report.warn(f"cannot decode {source_path}: {exc}; the archive copy stays as it is")
        return

    # 2.3: the source tool can still write to a last line that has no newline.
    data = data[: data.rfind(b"\n") + 1]

    try:
        archived = copy_path.read_bytes()
    except FileNotFoundError:
        archived = None

    try:
        if archived is None:
            if data:
                dest_dir.mkdir(parents=True, exist_ok=True)
                _append(copy_path, data)
                report.wrote_any = True
                report.counts["copied"] += 1
            else:
                report.counts["unchanged"] += 1
        elif data == archived:
            report.counts["unchanged"] += 1
        elif archived.startswith(data):
            # 2.4: a short read. The archive already has more; change nothing.
            report.counts["unchanged"] += 1
            return
        elif data.startswith(archived):
            # 2.5: the source grew.
            _append(copy_path, data[len(archived) :])
            report.wrote_any = True
            report.counts["appended"] += 1
        else:
            # 2.6: the source diverged. Keep the old copy as the next revision.
            newest, newest_path = _newest_revision(dest_dir, stem)
            if newest_path is not None and newest_path.read_bytes() == data:
                report.counts["unchanged"] += 1
                return
            rev_path = _checked_path(dest_dir, f"{stem}.rev-{newest + 1}.jsonl")
            atomic_replace(copy_path, rev_path)
            _append(copy_path, data)
            report.wrote_any = True
            report.counts["revisions"] += 1
            report.warn(f"{source_path} diverged from the archive copy; the old copy is {rev_path.name}")

        # 2.7: a crash before this write only makes the next pass read the source again.
        meta_out = {
            **(meta_extra or {}),
            "version": META_VERSION,
            "stem": stem,
            "copied_bytes": len(data),
            "source_path": str(source_path),
            "source_size": st.st_size,
            "source_mtime_ns": st.st_mtime_ns,
            "last_sync_ms": int(time.time() * 1000),
        }
        dest_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(meta_path, json.dumps(meta_out, indent=2, ensure_ascii=False).encode("utf-8"))
    except OSError as exc:
        # The copy only grows, so the next pass continues from what is on disk.
        report.warn(f"cannot write the archive copy of {stem}: {exc}")


__all__ = [
    "LOCK_FILE_NAME",
    "META_VERSION",
    "ArchiveLock",
    "SyncContext",
    "SyncReport",
    "home_key",
    "run_locked_pass",
    "sync_stream",
]
