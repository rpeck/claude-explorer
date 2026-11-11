"""Count what this process holds open on a SQLite file, for leak tests.

POSIX counts the descriptors on the database file and its -wal and -shm
files. Windows has no simple way to map a handle to its file, so there the
count is every handle of the process (``GetProcessHandleCount``); callers
allow :data:`TOLERANCE` unrelated handles.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

# The Windows measure counts every handle of the process, so allow a few
# unrelated ones.
TOLERANCE = 10 if sys.platform == "win32" else 0
MEASURE = "process handles (all files) for" if sys.platform == "win32" else "open descriptors on"


def _open_paths() -> list[str]:
    """Return the real path of every file descriptor this process holds."""
    paths: list[str] = []
    if sys.platform.startswith("linux"):
        for name in os.listdir("/proc/self/fd"):
            try:
                paths.append(os.readlink(f"/proc/self/fd/{name}"))
            except OSError:
                continue
        return paths

    import fcntl

    for name in os.listdir("/dev/fd"):
        try:
            raw = fcntl.fcntl(int(name), fcntl.F_GETPATH, bytes(1024))
        except (OSError, ValueError):
            continue
        paths.append(raw.split(b"\0", 1)[0].decode(errors="replace"))
    return paths


def fds_on(*dbs: Path) -> int:
    """Count the descriptors open on each database and on its -wal and
    -shm files."""
    names: set[str] = set()
    for db in dbs:
        real = os.path.realpath(db)
        names |= {real, real + "-wal", real + "-shm"}
    return sum(1 for p in _open_paths() if os.path.realpath(p) in names)


def _windows_handle_count() -> int:
    """Return the number of handles that this process holds open."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessHandleCount.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    count = wintypes.DWORD()
    if not kernel32.GetProcessHandleCount(
        kernel32.GetCurrentProcess(), ctypes.byref(count)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return count.value


def open_handles(*dbs: Path) -> int:
    """POSIX: the descriptors open on ``dbs``. Windows: every handle of
    the process (see the module docstring)."""
    if sys.platform == "win32":
        return _windows_handle_count()
    return fds_on(*dbs)


def settled_handles(*dbs: Path, limit: int) -> int:
    """Count with :func:`open_handles`. Wait up to 2 s for the count to
    reach ``limit``, because a thread can finish its cleanup just after
    join()."""
    deadline = time.monotonic() + 2.0
    count = open_handles(*dbs)
    while count > limit and time.monotonic() < deadline:
        time.sleep(0.02)
        count = open_handles(*dbs)
    return count


def run_on_new_threads(fn: Callable[[], object], passes: int) -> None:
    """Run ``fn`` once on each of ``passes`` new threads, one at a time,
    the way the watcher's debounce Timer does."""
    errors: list[BaseException] = []

    def target() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - report it in the test
            errors.append(exc)

    for _ in range(passes):
        t = threading.Timer(0, target)
        t.daemon = True
        t.start()
        t.join(timeout=30)
        assert not t.is_alive(), "a drift pass did not finish in 30 s"
    assert not errors, f"a pass raised: {errors[0]!r}"


def wait_for_timers(timeout: float = 20.0) -> None:
    """Wait until no ``threading.Timer`` thread is alive, so that every
    drift pass the test started has finished."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = [
            t for t in threading.enumerate()
            if isinstance(t, threading.Timer) and t.is_alive()
        ]
        if not alive:
            return
        for t in alive:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
    raise AssertionError("drift passes still ran after the timeout")
