"""Persistent SQLite-backed cache of Claude Code session metadata.

Backend caches at a glance (Cache landscape, 2026-05-18):
  * ``FileCache`` (``backend/cache.py``) — in-memory, per-path
    mtime-keyed cache of parsed conversation dicts; LRU-bounded;
    lost on process restart.
  * ``SummaryCache`` (this module) — SQLite-persisted sidebar
    summaries; mtime+size invalidation per row; full table wipe on
    ``claude_code_reader.LOGIC_VERSION`` mismatch at lifespan startup.
  * ``SearchIndex`` (``backend/search_index.py``) — SQLite FTS5
    inverted index; drift-first incremental rebuild keyed on
    ``indexed_files`` mtime; full drop+rebuild on ``SCHEMA_VERSION``
    bump or column-set drift in the ``messages`` virtual table.

Powers the fast path for :func:`backend.claude_code_reader.
list_claude_code_conversations`. The fast metadata reader
(``read_conversation_summary_fast``) opens and re-parses every
session JSONL on every sidebar request — for a ~1,200-session corpus
that's a multi-second walk dominated by JSONL re-parse work whose
result hasn't changed since the last request.

This module persists those parse results to a SQLite table co-located
with the existing FTS5 search index (so the watcher's drift pass
refreshes both stores in a single walk). Cache rows are keyed by
on-disk path and stamped with both mtime AND size — a miss on either
forces a re-scan, which catches both genuine edits and the
"file-replaced-with-same-mtime" race.

Lifecycle:
    1. **First request after process start**: cache rows survive
       restarts. Warm path is a single ``SELECT path, summary_json
       FROM conversation_summaries WHERE path IN (...)`` plus an
       in-Python mtime/size compare.
    2. **Cold start (empty cache)**: every path is a miss; the caller
       parallelizes the misses via
       :func:`backend.claude_code_reader._read_summaries_parallel`.
       Misses are then upserted into the cache for next time.
    3. **Drift detection**: the existing CC image watcher's 600 s
       backstop poll (:func:`backend.cc_watcher.scan_once`)
       walks the live data directories anyway for FTS5 drift; it now
       also refreshes the summary cache in the same iteration.
    4. **Auto-invalidation on logic change**:
       :func:`clear_on_logic_mismatch` is called at lifespan startup
       and compares :data:`backend.claude_code_reader.LOGIC_VERSION`
       against the value stored in ``conversation_summaries_meta``.
       Any mismatch wipes the cache table — guarantees we never serve
       rows that were produced by a now-obsolete version of the fast
       reader. Whitespace edits to that function also trigger a wipe
       (acceptable: the function is small and changes rarely).

Threading model mirrors :class:`backend.search_index.SearchIndex`:
    * per-thread read connections via ``threading.local`` (FastAPI
      runs sync route handlers in a thread pool);
    * a single write connection guarded by ``threading.Lock``;
    * WAL mode so readers don't block on the writer.

Fallback semantics:
    * If FTS5 is unavailable in this sqlite3 build (the same gate
      :func:`backend.search_index.get_search_index` uses), or if
      opening the SQLite file fails, :func:`get_summary_cache`
      returns ``None`` and callers MUST fall back to the legacy
      sequential reader. This mirrors the pattern in
      :mod:`backend.search` (linear-scan fallback when the FTS5 index
      is unavailable). Search never goes "down" — neither does
      sidebar metadata listing.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import orjson

from .search_index import default_index_path, fts5_available


logger = logging.getLogger(__name__)


# Key under which the source-hash of read_conversation_summary_fast is
# stored in conversation_summaries_meta. clear_on_logic_mismatch compares
# this row's value to the current LOGIC_VERSION; mismatch wipes the cache.
_LOGIC_VERSION_KEY = "logic_version"


class SummaryCache:
    """SQLite-backed persistent cache of ConversationSummary dicts.

    One instance per index file. The module-level :func:`get_summary_cache`
    singleton wraps this for the canonical location (which is shared with
    the FTS5 search index).

    Invalidation policy:
      * **Trigger (per row)**: :meth:`get_many` requires BOTH the
        on-disk ``mtime`` AND ``size`` to match the values stamped on
        the cache row; any drift on either drops the row to the miss
        bucket. ``None``-producing rows are persisted as a sentinel
        blob so unchanged "phantom" sessions still get negative-cache
        hits instead of re-reads. :meth:`delete_missing` drops rows
        whose paths no longer exist on disk (called from the watcher
        backstop).
      * **Persists across restart**: yes — rows live in the SQLite file
        co-located with the FTS5 index, so a warm restart serves the
        sidebar in one bulk ``SELECT`` plus stat compare.
      * **Full rebuild**: :meth:`clear_on_logic_mismatch` at lifespan
        startup compares :data:`backend.claude_code_reader.
        LOGIC_VERSION` (the first 16 hex chars of a SHA-256 over the
        fast-reader source) against the value stored in
        ``conversation_summaries_meta``; any mismatch wipes
        ``conversation_summaries`` so the next request repopulates from
        the current reader. Logic changes (including whitespace edits
        to the fast reader) thus auto-rebuild.
      * **Failure mode**: builds without FTS5 OR a ``sqlite3.Error``
        opening the file return ``None`` from :func:`get_summary_cache`;
        callers fall back to the legacy sequential reader. Query-time
        ``sqlite3.Error`` in :meth:`get_many` / :meth:`upsert_many` is
        logged and yields an empty result so the caller takes the
        miss path — never blocks the sidebar.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Per-thread read connections. SQLite forbids sharing connections
        # across threads by default; one-per-thread is the robust pattern.
        self._read_local = threading.local()
        # threading.local hides a thread's connection from every other
        # thread, so close() could never reach them. Keep a registry too.
        # POSIX unlinks an open file happily, which hid this; Windows
        # keeps the database locked while any connection is open.
        # Same defect as SearchIndex.close(), fixed 2026-09-22.
        self._read_conns: list[sqlite3.Connection] = []
        self._read_conns_lock = threading.Lock()

        # Single dedicated write connection guarded by a lock. WAL mode
        # ensures readers don't block on the writer.
        #
        # We deliberately keep the default isolation level (deferred
        # auto-BEGIN) so ``with self._write_conn:`` commits on success
        # and rolls back on exception — important for the upsert_many
        # path (a partial write would leave the cache inconsistent with
        # the on-disk file set).
        self._write_conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
        )
        self._write_lock = threading.Lock()

        # WAL + sensible pragmas. Applied via the write connection but
        # affects the database file.
        #
        # 2026-05-24 concurrency fix: bumped busy_timeout from 5000 ->
        # 30000 (5s -> 30s). The summary_cache and the search_index
        # SHARE the same SQLite database file
        # (``<data_dir>.parent/search-index.sqlite``); each module
        # opens its own writer connection guarded by its own Python
        # threading.Lock. WAL mode lets the writers serialize at the
        # SQLite level via busy_timeout, but only IF the timeout is
        # generous enough to outlast the slowest possible writer.
        #
        # The slowest writer is the search_index full rebuild (250K-
        # message FTS5 reindex on the user's real corpus, which can
        # take 10-20 s and holds a writer transaction the whole time).
        # 5 s was not enough; the user hit ``database is locked`` when
        # summary_cache.upsert_many fired during a rebuild. 30 s
        # safely outlasts all known writers; the cost is that a worst-
        # case interactive request can wait up to 30 s for a write
        # slot during a rebuild, which is still better than the
        # alternative (silent data drop / observable 500).
        self._write_conn.execute("PRAGMA journal_mode = WAL")
        self._write_conn.execute("PRAGMA synchronous = NORMAL")
        self._write_conn.execute("PRAGMA busy_timeout = 30000")
        self._write_conn.execute("PRAGMA temp_store = MEMORY")

        self._ensure_schema()

    # ----- schema ----------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create the cache tables if they don't already exist.

        The canonical schema lives in
        :data:`backend.search_index.SCHEMA_SQL`. The SearchIndex
        constructor runs that script unconditionally when the search-
        index module opens the file, but the summary cache may be the
        first thing to touch the file in some test paths, so we
        re-create idempotently here. ``CREATE IF NOT EXISTS`` is
        cheap and matches the policy used for the other tables.
        """
        with self._write_lock:
            with self._write_conn:
                self._write_conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_summaries (
                        path TEXT PRIMARY KEY,
                        mtime REAL NOT NULL,
                        size INTEGER NOT NULL,
                        summary_json BLOB NOT NULL,
                        cached_at REAL NOT NULL
                    )
                    """
                )
                self._write_conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_summaries_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )

    # ----- read connections ------------------------------------------

    def _get_read_conn(self) -> sqlite3.Connection:
        """Get the per-thread read connection, creating it on first call."""
        conn = getattr(self._read_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
                isolation_level=None,
            )
            # 2026-05-24 concurrency fix: same rationale as the writer
            # connection. WAL mode permits a reader to proceed during
            # a long writer transaction, but the reader still needs a
            # busy_timeout for the rare cases where SQLite must
            # synchronize on the file (e.g. checkpoint or VACUUM).
            conn.execute("PRAGMA busy_timeout = 30000")
            self._read_local.conn = conn
            with self._read_conns_lock:
                self._read_conns.append(conn)
        return conn

    # ----- logic-version invalidation --------------------------------

    def get_logic_version(self) -> str | None:
        """Return the cached logic version, or None if no row exists."""
        cur = self._write_conn.execute(
            "SELECT value FROM conversation_summaries_meta WHERE key = ?",
            (_LOGIC_VERSION_KEY,),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def clear_on_logic_mismatch(self, current_version: str) -> bool:
        """Wipe the cache table if the stored logic version differs.

        Returns True iff a wipe happened.

        Called once at lifespan startup. Stamping the meta row AFTER
        the DELETE means that a crash between the two would leave the
        cache empty (safe: next startup re-stamps and the next request
        repopulates) rather than mis-versioned.
        """
        stored = self.get_logic_version()
        if stored == current_version:
            return False

        with self._write_lock:
            with self._write_conn:
                self._write_conn.execute("DELETE FROM conversation_summaries")
                self._write_conn.execute(
                    "INSERT OR REPLACE INTO conversation_summaries_meta "
                    "(key, value) VALUES (?, ?)",
                    (_LOGIC_VERSION_KEY, current_version),
                )
        logger.info(
            "summary_cache: logic version changed (%s -> %s); cache wiped",
            stored, current_version,
        )
        return True

    # ----- read / write ---------------------------------------------

    # Sentinel blob stored in summary_json when the producer returned
    # None for this file (empty session, phantom row, unreadable, etc).
    # Distinguishing "None at producer time" from "ordinary cache miss"
    # is crucial — without it the sidebar re-reads ~10% of the corpus
    # on every request, which adds ~300ms of dead work to the warm
    # path. Using a literal byte sentinel (not a valid JSON value)
    # avoids any chance of colliding with a real summary.
    _NULL_SENTINEL: bytes = b"__SUMMARY_NULL__"

    def get_many(
        self,
        paths: Iterable[Path],
        stat_index: dict[Path, Any],
    ) -> dict[Path, dict[str, Any] | None]:
        """Return cached summaries for paths whose mtime+size still match.

        ``stat_index`` is a pre-built ``{path: os.stat_result}`` map so
        the caller has already paid the stat cost (and can re-use it
        for the miss path). A row is considered fresh when BOTH the
        on-disk mtime and size match the cached values; any drift on
        either falls into the miss bucket.

        Missing paths from ``stat_index`` are silently skipped (the
        caller's miss path will handle them).

        Returns a dict keyed by Path; the VALUE is either a summary
        dict OR ``None`` (negative-cache hit — the producer returned
        None when this row was upserted, and the file is unchanged
        since, so we know it'd still return None without re-reading).
        Callers MUST treat both as "hit" so the miss-resolution path
        skips them.
        """
        paths_list = list(paths)
        if not paths_list:
            return {}

        # SQLite has a default SQLITE_MAX_VARIABLE_NUMBER of 999 on some
        # builds. Chunk the IN-list to stay well below that — 500 leaves
        # headroom and is empirically a wash with larger chunks.
        CHUNK = 500
        path_to_str = {p: str(p) for p in paths_list}
        str_to_path = {s: p for p, s in path_to_str.items()}

        conn = self._get_read_conn()
        rows: list[tuple[str, float, int, bytes]] = []
        path_strs = list(path_to_str.values())
        for i in range(0, len(path_strs), CHUNK):
            chunk = path_strs[i : i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            sql = (
                "SELECT path, mtime, size, summary_json "
                f"FROM conversation_summaries WHERE path IN ({placeholders})"
            )
            try:
                cur = conn.execute(sql, chunk)
                rows.extend(cur.fetchall())
            except sqlite3.Error:
                logger.exception("summary_cache: get_many query failed")
                return {}

        out: dict[Path, dict[str, Any] | None] = {}
        for path_str, cached_mtime, cached_size, blob in rows:
            path = str_to_path.get(path_str)
            if path is None:
                continue
            st = stat_index.get(path)
            if st is None:
                # Caller didn't stat this path; skip rather than serve
                # potentially-stale rows.
                continue
            if float(st.st_mtime) != float(cached_mtime):
                continue
            if int(st.st_size) != int(cached_size):
                continue
            if blob == self._NULL_SENTINEL:
                out[path] = None
                continue
            try:
                out[path] = orjson.loads(blob)
            except orjson.JSONDecodeError:
                logger.warning(
                    "summary_cache: corrupted row for %s; ignoring", path_str,
                )
                continue
        return out

    def upsert_many(
        self,
        rows: dict[Path, dict[str, Any] | None],
        stat_index: dict[Path, Any],
    ) -> int:
        """Insert/replace cache rows for the given path→summary map.

        ``None`` summaries get persisted as the ``_NULL_SENTINEL`` blob
        so the next request gets a negative-cache hit instead of
        re-reading the file. This is critical for performance: ~10%
        of the corpus on a typical workstation are sessions the
        producer returns None for (phantom sessions, leading-Caveat
        rows, etc.), and re-reading them on every request added
        ~300 ms to the warm path before the negative cache landed.

        Entries missing from ``stat_index`` are skipped (we need
        mtime+size to stamp the row).

        Wrapped in a single transaction so a crash mid-upsert leaves
        the cache in its prior state — no torn writes.

        Returns the count of rows actually written.
        """
        if not rows:
            return 0

        now = time.time()
        payload: list[tuple[str, float, int, bytes, float]] = []
        for path, summary in rows.items():
            st = stat_index.get(path)
            if st is None:
                continue
            if summary is None:
                blob: bytes = self._NULL_SENTINEL
            else:
                try:
                    blob = orjson.dumps(summary)
                except (TypeError, orjson.JSONEncodeError):
                    logger.exception(
                        "summary_cache: failed to encode summary for %s", path,
                    )
                    continue
            payload.append(
                (str(path), float(st.st_mtime), int(st.st_size), blob, now)
            )

        if not payload:
            return 0

        with self._write_lock:
            try:
                with self._write_conn:
                    self._write_conn.executemany(
                        "INSERT OR REPLACE INTO conversation_summaries "
                        "(path, mtime, size, summary_json, cached_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        payload,
                    )
            except sqlite3.Error:
                logger.exception("summary_cache: upsert_many failed")
                return 0
        return len(payload)

    def delete_missing(self, live_paths: set[str]) -> int:
        """Drop cache rows for paths that no longer exist on disk.

        Cheap cleanup pass intended for the watcher backstop. Returns
        the count of rows deleted.
        """
        with self._write_lock:
            try:
                with self._write_conn:
                    cur = self._write_conn.execute(
                        "SELECT path FROM conversation_summaries"
                    )
                    cached_paths = {row[0] for row in cur.fetchall()}
                    stale = cached_paths - live_paths
                    if not stale:
                        return 0
                    # Chunk the DELETE for the same reason as get_many.
                    CHUNK = 500
                    stale_list = list(stale)
                    for i in range(0, len(stale_list), CHUNK):
                        chunk = stale_list[i : i + CHUNK]
                        placeholders = ",".join("?" * len(chunk))
                        self._write_conn.execute(
                            f"DELETE FROM conversation_summaries WHERE path IN ({placeholders})",
                            chunk,
                        )
                    return len(stale)
            except sqlite3.Error:
                logger.exception("summary_cache: delete_missing failed")
                return 0

    def stats(self) -> dict[str, int]:
        """Return basic cache counters for diagnostics."""
        try:
            cur = self._write_conn.execute(
                "SELECT COUNT(*) FROM conversation_summaries"
            )
            return {"rows": int(cur.fetchone()[0])}
        except sqlite3.Error:
            return {"rows": -1}

    def close(self) -> None:
        """Close every connection this cache opened. Idempotent.

        Closes the read connections too, including ones other threads made.
        Waiting for thread death is not good enough: Windows keeps the
        database file locked while any connection is open, so a leftover
        reader blocks deletion of the file and of its directory.
        """
        try:
            self._write_conn.close()
        except sqlite3.Error:
            pass

        with self._read_conns_lock:
            conns, self._read_conns = self._read_conns, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        # Drop the per-thread handles so a later call builds fresh ones
        # rather than handing back a closed connection.
        self._read_local = threading.local()


# ----- module-level singleton --------------------------------------

# Mirrors the singleton pattern in backend/search_index.py and
# backend/cache.py. Set to None when FTS5 is unavailable so callers
# fall back to the sequential reader. (We gate on FTS5 to keep the
# cache and search-index modules in lockstep — both live in the
# same SQLite file.)
_summary_cache: SummaryCache | None = None
_summary_cache_lock = threading.Lock()


def get_summary_cache() -> SummaryCache | None:
    """Return the process-wide SummaryCache, or None if unavailable.

    Lazy-initializes on first call. Returns the same instance on every
    subsequent call within this process. Test code may call
    :func:`reset_summary_cache_for_tests` to reset between tests.
    """
    global _summary_cache
    if _summary_cache is not None:
        return _summary_cache

    with _summary_cache_lock:
        if _summary_cache is not None:
            return _summary_cache
        if not fts5_available():
            # We gate on the same FTS5 probe the search index uses
            # because both stores share a SQLite file. Builds without
            # FTS5 are vanishingly rare in practice (Homebrew Python
            # and python.org installers ship it by default) but if it
            # ever happens, callers must fall back to the legacy
            # sequential reader. Logging is left to the search-index
            # module so we don't double-log on cold starts.
            return None
        try:
            _summary_cache = SummaryCache(default_index_path())
        except sqlite3.Error as exc:
            logger.error(
                "summary_cache: failed to open cache: %s", exc, exc_info=True,
            )
            return None
    return _summary_cache


def reset_summary_cache_for_tests() -> None:
    """Test-only: reset the module-level singleton.

    Production code MUST NOT call this. Used by pytest fixtures so
    each test starts with a fresh cache pointed at its own tmp_path.
    """
    global _summary_cache
    if _summary_cache is not None:
        try:
            _summary_cache.close()
        except (AttributeError, sqlite3.Error):
            pass
        _summary_cache = None
