"""One SQLite read connection per thread, closed when its thread ends.

:class:`backend.search_index.SearchIndex` and
:class:`backend.summary_cache.SummaryCache` read through this pool.

Why a pool per thread:
    SQLite forbids sharing a connection across threads by default.
    ``check_same_thread=False`` relaxes that check, but one connection is
    still not safe under concurrent access. So each thread gets its own.

Why the connection closes when its thread ends:
    Many threads live for a short time: the watcher's debounce
    ``threading.Timer`` (one new thread per drift pass) and the anyio
    workers of FastAPI, which end after 10 s idle. The old code kept every
    connection in a registry until ``close()``. The supervised watcher
    ran for 46 days and held 1262 read connections (2524 file
    descriptors), hit the open-file limit, and every drift pass then
    failed with EMFILE (GitHub issue #33, 2026-11-08).

    A thread-local attribute holds the only strong reference to a small
    holder object. When the thread ends, Python deletes the thread's
    locals, and a ``weakref.finalize`` on the holder closes the
    connection. This works for every thread, not only the Timer.

Why there is still a registry:
    ``threading.local`` hides a thread's connection from every other
    thread. :meth:`PerThreadReadConns.close_all` must still close the
    connections of live threads: Windows keeps the database file locked
    while any connection is open (2026-09-22).

Each connection is closed exactly once: by the first caller that removes
it from the registry (the finalizer or :meth:`close_all`).
"""

from __future__ import annotations

import sqlite3
import threading
import weakref
from pathlib import Path


class _Holder:
    """Owns one thread's connection. Only a thread-local refers to it, so
    it dies with its thread, or when :meth:`PerThreadReadConns.close_all`
    drops the thread-local."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn


def _release(registry: set[sqlite3.Connection], conn: sqlite3.Connection) -> None:
    """Close ``conn`` if it is still in ``registry``. Runs when its holder
    dies, on the thread that drops the last reference."""
    try:
        registry.remove(conn)
    except KeyError:
        return  # close_all() already closed it.
    _close_quietly(conn)


def _close_quietly(conn: sqlite3.Connection) -> None:
    try:
        conn.close()
    except sqlite3.Error:
        pass


class PerThreadReadConns:
    """Read connections to one SQLite file, one for each thread."""

    def __init__(self, path: Path) -> None:
        self._path = str(path)
        self._local = threading.local()
        # Every open connection, from every thread. Each set operation is
        # atomic, so the finalizer needs no lock. A lock here could
        # deadlock: a finalizer can run on a thread that holds the lock.
        self._open: set[sqlite3.Connection] = set()

    def get(self) -> sqlite3.Connection:
        """Return this thread's connection. Open it on the first call."""
        holder = getattr(self._local, "holder", None)
        if holder is None:
            conn = sqlite3.connect(
                self._path,
                check_same_thread=False,
                isolation_level=None,
            )
            # 2026-05-24 concurrency fix: the default busy_timeout is 0,
            # which makes a reader fail at once on any contention for the
            # file (checkpoint, VACUUM). 30 s outlasts all known writers.
            conn.execute("PRAGMA busy_timeout = 30000")
            holder = _Holder(conn)
            self._open.add(conn)
            weakref.finalize(holder, _release, self._open, conn)
            self._local.holder = holder
        return holder.conn

    def open_count(self) -> int:
        """Return the number of read connections that are open now."""
        return len(self._open)

    def close_all(self) -> None:
        """Close every read connection, including those of live threads.

        A later :meth:`get` opens a new connection; it never returns a
        closed one.
        """
        while True:
            try:
                conn = self._open.pop()
            except KeyError:
                break
            _close_quietly(conn)
        # Drop the per-thread holders. Their finalizers find nothing to do.
        self._local = threading.local()
