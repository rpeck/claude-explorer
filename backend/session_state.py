"""Session state for issue #91 (plan §2.2.7): Running, Open, Ended.

- Running: in the live registry of Claude Code (``<claude_dir>/sessions/<pid>.json``)
  with a live pid. Local machine only, read in place, never copied.
- Ended: an end record (``cost-state`` or ``continued-in``) after the last turn.
- Open: no end record after the last turn, and the last activity is less
  than 7 days old.
- Ended (no exit record): no end record, and 7 days old or more.

Codex and Cowork have no exit record: Open or Ended (no exit record) by age
only. Desktop conversations have no state.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import orjson

SessionState = Literal["running", "open", "ended", "ended_no_exit"]

OPEN_WINDOW = timedelta(days=7)

_TURN_TYPES = ("user", "assistant")
_END_TYPES = ("cost-state", "continued-in")
_TAIL_START = 16 * 1024


def cc_end_after_last_turn(records: Iterable[dict]) -> bool:
    """True when an end record comes after the last turn (a resumed session
    appends after its old ``cost-state``)."""
    ended = False
    for r in records:
        kind = r.get("type") if isinstance(r, dict) else None
        if kind in _TURN_TYPES:
            ended = False
        elif kind in _END_TYPES:
            ended = True
    return ended


def _last_decision(lines: list[bytes]) -> bool | None:
    """From the end: True at an end record, False at a turn, None when neither."""
    for line in reversed(lines):
        # Cheap test first: most lines are neither kind.
        if b'"type"' not in line:
            continue
        try:
            r = orjson.loads(line)
        except orjson.JSONDecodeError:
            continue
        kind = r.get("type") if isinstance(r, dict) else None
        if kind in _TURN_TYPES:
            return False
        if kind in _END_TYPES:
            return True
    return None


def _read_ended(path: Path) -> bool:
    """:func:`cc_end_after_last_turn` on the file, read from the end."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        chunk = _TAIL_START
        while True:
            start = max(0, size - chunk)
            f.seek(start)
            lines = f.read(size - start).splitlines()
            if start > 0 and lines:
                lines = lines[1:]  # the first line can be cut
            decision = _last_decision(lines)
            if decision is not None or start == 0:
                return bool(decision)
            chunk *= 4


_ended_cache: dict[str, tuple[float, int, bool]] = {}
_cache_lock = threading.Lock()


def cc_file_ended(path: Path) -> bool:
    """:func:`cc_end_after_last_turn` for a transcript, cached by (path, mtime, size).
    A file that cannot be read is not ended."""
    try:
        st = path.stat()
    except OSError:
        return False
    key = str(path)
    with _cache_lock:
        hit = _ended_cache.get(key)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    try:
        ended = _read_ended(path)
    except OSError:
        return False
    with _cache_lock:
        _ended_cache[key] = (st.st_mtime, st.st_size, ended)
    return ended


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # Never os.kill(pid, 0) on Windows: it calls TerminateProcess.
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def live_session_ids(claude_dir: Path) -> set[str]:
    """The ``sessionId`` of each registry file whose pid is alive.

    The registry is a live format that Claude Code can change: a file that
    does not parse, or that has no pid or sessionId, is skipped, so its
    session falls back to the transcript rule.
    """
    out: set[str] = set()
    try:
        files = list((claude_dir / "sessions").glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            data = orjson.loads(f.read_bytes())
        except (OSError, orjson.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        pid, sid = data.get("pid"), data.get("sessionId")
        if isinstance(pid, int) and not isinstance(pid, bool) and isinstance(sid, str) and sid and _pid_alive(pid):
            out.add(sid)
    return out


def now() -> datetime:
    """The clock of the state rule; a test can patch it to freeze the 7-day window."""
    return datetime.now(UTC)


def state_of(*, ended: bool, last_activity: datetime, now: datetime, running: bool = False) -> SessionState:
    if running:
        return "running"
    if ended:
        return "ended"
    if last_activity.tzinfo is None and now.tzinfo is not None:
        last_activity = last_activity.replace(tzinfo=now.tzinfo)
    elif now.tzinfo is None and last_activity.tzinfo is not None:
        now = now.replace(tzinfo=last_activity.tzinfo)
    return "open" if now - last_activity < OPEN_WINDOW else "ended_no_exit"
