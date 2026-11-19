"""Read ``state_5.sqlite.threads`` from a checked private copy (plan D17).

A SQLite connection on the live file creates ``-wal`` and ``-shm`` in a
clean Codex home, and it takes locks there (plan §3, §1.2). So this module
never gives SQLite a path in the Codex home. It reads ``state_5.sqlite``
and ``state_5.sqlite-wal`` as bytes into a private temp folder, and it
opens that copy only when the checks prove that the copy is one committed
state (council N5, 2026-11-19, rules 1-13):

- two reads of the main file are equal;
- the main file keeps its inode, size, and mtime, and the WAL keeps its inode;
- the WAL salts at the start, in the copy, and at the end are equal;
- no ``-journal`` exists.

In WAL mode SQLite writes the main file only in a checkpoint, and a new WAL
generation gets new salts. So with one generation in the whole window, the
copied WAL holds every page that a checkpoint wrote into the main file.
A rejected copy is retried; then the read falls back, and the caller keeps
the archived values (rule 11).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

log = logging.getLogger(__name__)

STATE_DB = "state_5.sqlite"
COLUMNS = (
    "id", "rollout_path", "title", "name", "archived", "cwd", "git_sha", "git_branch",
    "git_origin_url", "model", "history_mode", "updated_at_ms", "thread_source", "source",
)
MAX_BYTES = 64 << 20
DEADLINE_S = 0.25
ATTEMPTS = 3
BACKOFF_S = (0.010, 0.040)
CACHE_TTL_S = 60.0
CHUNK_SIZE = 1 << 20
TEMP_PREFIX = "claude-explorer-state5-"
# Another process's folder lives for one read (250 ms); the sweep at the first
# read of a process leaves younger folders alone.
_SWEEP_MIN_AGE_S = 60.0

_Stat = tuple[int, int, int]  # inode, size, mtime_ns
_Key = tuple[_Stat | None, _Stat | None, bytes | None]  # main, -wal, -wal salts


@dataclass(frozen=True)
class StateRead:
    """``rows`` is thread id -> the §3 columns, or ``None`` after a fallback."""

    rows: dict[str, dict[str, Any]] | None
    warning: str | None = None


class _Fallback(Exception):
    """Stop the read. The message is the warning; it holds no path or title."""


class _Changed(Exception):
    """This attempt saw a change; try again."""


# --- Files ---------------------------------------------------------------


def _open(path: Path) -> BinaryIO:
    """Open read-only, for one read. On Windows, share delete too (rule 2)."""
    if sys.platform != "win32":
        return open(path, "rb")
    import ctypes
    import errno
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.restype = wintypes.HANDLE
    create.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    generic_read, share_all, open_existing, normal = 0x80000000, 0x7, 3, 0x80
    handle = create(str(path), generic_read, share_all, None, open_existing, normal, None)
    if handle is None or handle == wintypes.HANDLE(-1).value:
        if ctypes.get_last_error() in (2, 3):  # file or path not found
            raise FileNotFoundError(errno.ENOENT, "not found")
        raise _Fallback("state_5 cannot be opened with shared delete")
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel32.CloseHandle(handle)
        raise
    return os.fdopen(fd, "rb")


def _stat(path: Path) -> _Stat | None:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _salts(header: bytes) -> bytes | None:
    # WAL header bytes 16-23: salt-1 and salt-2 ("WAL file format", fileformat2).
    return header[16:24] if len(header) >= 32 else None


def _key(main: Path, wal: Path) -> _Key:
    wal_stat = _stat(wal)
    salts = None
    if wal_stat is not None:
        try:
            with _open(wal) as stream:
                salts = _salts(stream.read(32))
        except FileNotFoundError:
            pass
    return (_stat(main), wal_stat, salts)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise _Fallback("the state_5 read took too long")


def _copy(src: Path, dst: Path | None, deadline: float, digest: Any = None) -> bytes:
    """Copy (or only hash) ``src`` in chunks; return its first 32 bytes."""
    head = b""
    with _open(src) as stream, (open(dst, "xb") if dst else contextlib.nullcontext()) as out:
        while chunk := stream.read(CHUNK_SIZE):
            if len(head) < 32:
                head += chunk[: 32 - len(head)]
            if digest is not None:
                digest.update(chunk)
            if out is not None:
                out.write(chunk)
            _check_deadline(deadline)
    return head


# --- One attempt (rules 3, 4, 6, 7) ----------------------------------------


def _attempt(home: Path, folder: Path, t0: _Key, deadline: float) -> dict[str, dict[str, Any]]:
    main, wal, journal = home / STATE_DB, home / f"{STATE_DB}-wal", home / f"{STATE_DB}-journal"
    main0, wal0, salts0 = t0
    if main0 is None:
        raise _Fallback("state_5 is missing")
    if os.path.lexists(journal):
        raise _Fallback("state_5 has a rollback journal")
    if main0[1] + (wal0[1] if wal0 else 0) > MAX_BYTES:
        raise _Fallback("state_5 is larger than the size cap")

    folder.mkdir(mode=0o700)
    copy = folder / STATE_DB
    first, second = hashlib.sha256(), hashlib.sha256()
    try:
        _copy(main, copy, deadline, first)
        salts_copy = _salts(_copy(wal, folder / f"{STATE_DB}-wal", deadline)) if wal0 else None
        _copy(main, None, deadline, second)
    except FileNotFoundError:
        raise _Changed from None
    main3, wal3, salts3 = _key(main, wal)
    if os.path.lexists(journal):
        raise _Fallback("state_5 has a rollback journal")
    if (
        first.digest() != second.digest()
        or main0 != main3
        or (wal0 is None) != (wal3 is None)
        or (wal0 is not None and wal3 is not None and wal0[0] != wal3[0])
        or not (salts0 == salts_copy == salts3)
    ):
        raise _Changed
    return _query(copy, deadline)


def _query(copy: Path, deadline: float) -> dict[str, dict[str, Any]]:
    """Rule 9: ``query_only``, ``quick_check``, the §3 columns, one transaction."""
    conn = sqlite3.connect(str(copy), isolation_level=None)
    try:
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
        conn.execute("PRAGMA query_only=1")
        conn.execute("BEGIN")
        if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise _Fallback("state_5 failed its integrity check")
        try:
            cursor = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM threads")
        except sqlite3.OperationalError:
            _check_deadline(deadline)
            raise _Fallback("state_5 has an unknown schema") from None
        rows = {row[0]: dict(zip(COLUMNS, row, strict=True)) for row in cursor if isinstance(row[0], str)}
        conn.execute("COMMIT")
        return rows
    finally:
        conn.close()


# --- The read --------------------------------------------------------------

_cache: dict[str, tuple[_Key, float, StateRead]] = {}
_cache_lock = threading.Lock()
_swept = False


def read_threads(home: Path) -> StateRead:
    """Read the ``threads`` rows of a Codex home, or fall back with a warning.

    The whole read takes at most 250 ms and 3 attempts. The last accepted
    result is reused for 60 s while the files keep the same stat and salts.
    """
    _sweep_once()
    deadline = time.monotonic() + DEADLINE_S
    folder: Path | None = None
    try:
        key = _key(home / STATE_DB, home / f"{STATE_DB}-wal")
        with _cache_lock:
            hit = _cache.get(str(home))
        if hit is not None and hit[0] == key and time.monotonic() - hit[1] < CACHE_TTL_S:
            return hit[2]
        folder = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
        for attempt in range(ATTEMPTS):
            if attempt:
                pause = BACKOFF_S[attempt - 1]
                if time.monotonic() + pause > deadline:
                    raise _Fallback("the state_5 read took too long")
                time.sleep(pause)
                key = _key(home / STATE_DB, home / f"{STATE_DB}-wal")
            try:
                rows = _attempt(home, folder / str(attempt), key, deadline)
            except _Changed:
                continue
            result = StateRead(rows)
            with _cache_lock:
                _cache[str(home)] = (key, time.monotonic(), result)
            return result
        raise _Fallback("state_5 changed during every read")
    except _Fallback as exc:
        reason = str(exc)
    except sqlite3.Error:
        late = time.monotonic() > deadline
        reason = "the state_5 read took too long" if late else "state_5 is damaged or has an unknown format"
    except OSError:
        reason = "state_5 cannot be read"
    finally:
        if folder is not None:
            _remove(folder)
    log.warning("Codex state_5 not used: %s", reason)
    return StateRead(None, reason)


# --- Temp folders (rule 10) --------------------------------------------------


def _remove(folder: Path) -> None:
    try:
        shutil.rmtree(folder)
    except OSError:
        log.warning("a state_5 temp folder cannot be deleted")


def _is_private_dir(st: os.stat_result) -> bool:
    if not stat.S_ISDIR(st.st_mode):  # an lstat result: a symlink is not a folder
        return False
    if getattr(st, "st_file_attributes", 0) & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
        return False
    if sys.platform == "win32":
        # shortcut: Windows has no uid or mode bits here, and %TEMP% is per user;
        # check the owner SID if a shared temp folder must be supported.
        return True
    return st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700


def sweep_stale_temp_dirs(min_age_s: float = 0.0) -> int:
    """Delete leftover temp folders of this module; return how many went.

    Only a folder with the prefix, owned by this user, mode 0700, and not a
    symlink or a reparse point. A folder younger than ``min_age_s`` stays.
    """
    try:
        entries = list(os.scandir(tempfile.gettempdir()))
    except OSError:
        return 0
    removed = 0
    for entry in entries:
        if not entry.name.startswith(TEMP_PREFIX):
            continue
        try:
            st = entry.stat(follow_symlinks=False)
            if not _is_private_dir(st) or time.time() - st.st_mtime < min_age_s:
                continue
            shutil.rmtree(entry.path)
            removed += 1
        except OSError:
            log.warning("a leftover state_5 temp folder cannot be deleted")
    return removed


def _sweep_once() -> None:
    global _swept
    with _cache_lock:
        if _swept:
            return
        _swept = True
    sweep_stale_temp_dirs(_SWEEP_MIN_AGE_S)
