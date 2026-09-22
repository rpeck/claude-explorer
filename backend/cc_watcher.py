"""Continuous protection for two independent Claude Code on-disk surfaces.

This module runs two cooperating ``watchdog`` observers inside the
same backend process. The module name covers BOTH jobs; a reader who
skims past the top will miss the second:

  * **(A) Image-cache mirror.** Watches ``~/.claude/image-cache/`` and
    copies new files into ``~/.claude-explorer/cc-images/<sess>/...``
    BEFORE Claude Code rotates them off disk. Without this, the
    conversation JSONL keeps the ``[Image: source: ...]`` marker but
    the underlying file is gone, so the viewer breaks.
  * **(B) Projects JSONL drift detector.** Watches
    ``~/.claude/projects/*/*.jsonl`` for live append-edits. On each
    debounced batch, runs the search-index drift pass
    (``search_index.update_drifted_files``) and the summary-cache
    upsert in the same iteration so search results and the
    conversation list reflect the new turns within ~debounce + I/O
    (default 2 s) instead of waiting up to 600 s for the backstop
    poll.

Both observers share one periodic backstop poll (default 600 s,
overridable via ``CLAUDE_EXPLORER_CC_WATCHER_INTERVAL_SEC``) that
re-runs the full ``scan_once`` walk as a correctness net against OS
event misses (FSEvents coalescing under load, inotify queue overflow,
sandboxed/NFS Pythons that fall back to ``PollingObserver``).

Installed OS launchers (launchd plist, systemd user unit, Windows
Task Scheduler launcher at ``~/.claude-explorer/cc-watcher.py``) bake
``from backend.cc_watcher import run_watcher`` into the supervised
script body at install time. If this module ever moves again, the
launcher template in ``cli/watcher.py:_build_watcher_inline_script``
must be updated in the same commit and every installed user must
re-run ``claude-explorer install-watcher``.

Details of each observer follow.

Claude Code drops image attachments into ``~/.claude/image-cache/<sess>/<N>.<ext>``
and rotates them off disk on its own schedule (often within minutes
of the conversation ending). The conversation JSONL keeps the
``[Image: source: ...]`` marker intact, so the explorer's viewer
breaks the moment CC reaps the file.

The eager fetch-time path (`backend.cc_image_cache.cache_all_markers`)
and the lazy request-time path (`/api/cc-image` Option B) only catch
images that are referenced by a conversation we've already read. New
images that CC writes between reads can be rotated before any read
ever sees them.

This watcher closes that gap with an **event-driven primary +
backstop poll**:

  * ``watchdog`` (FSEvents on macOS, inotify on Linux,
    ReadDirectoryChangesW on Windows) gives us per-file create/modify
    events with ~sub-second latency and ~zero idle CPU. Events fire
    into ``handle_one_path``, which delegates to the same
    ``copy_marker_image_to_cache`` the eager and lazy paths use.
  * A periodic backstop poll (default 600s — ten minutes) re-runs
    the full ``scan_once`` walk to catch anything events missed.
    Documented edge cases for missed events: FSEvents coalescing
    under extreme load; inotify watch-queue overflow; sandboxed/NFS
    Pythons where the OS-native backend isn't available
    (watchdog falls back to ``PollingObserver`` automatically — we
    log which backend got selected so misconfigurations are
    diagnosable).

The destination layout is unchanged:
``~/.claude-explorer/cc-images/<sess>/<sess>--<N>.<sha8>.<ext>``.
For CC sessions the conversation UUID equals the session UUID equals
the parent dir name in the live tree, so the destination layout
matches what the eager and lazy paths produce.

A SECOND observer watches ``~/.claude/projects/`` for ``*.jsonl``
edits (per PLANS/SEARCH_INDEX_FRESHNESS.md). On event, the changed
path is queued and a debounce ``threading.Timer`` (default 2 s,
overridable via ``CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC``) is
reset. When the timer fires, ``update_drifted_files`` runs once
covering every queued path. Without debouncing, CC's append-only
write pattern (5–20 ``on_modified`` events per user message) would
trigger 5–20 redundant SQL upserts in rapid succession. Search
freshness drops from up to 600 s (backstop poll) to ~debounce + I/O.

The watcher is best-effort: any error is logged and swallowed so a
transient I/O failure never crashes the backend.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from .cc_image_cache import copy_marker_image_to_cache
from .config import get_settings, read_env


logger = logging.getLogger(__name__)


def _resolve_interval() -> float:
    """Backstop-poll interval in seconds. Overridable via
    ``CLAUDE_EXPLORER_CC_WATCHER_INTERVAL_SEC`` (legacy
    ``CLAUDE_EXPORTER_CC_WATCHER_INTERVAL_SEC`` honored for one release).

    Default 600s (10 min). With event-driven capture handling the
    latency-critical path, the backstop only exists for correctness
    against the rare OS-event miss; longer intervals are fine.

    History: original 5s, bumped to 60s (08e9458) as wakeup-cost
    optimization, reverted to 5s for V1 ("data integrity > idle CPU"
    — citing an anecdotal report of an image rotated off disk within
    hours of creation), bumped back to 60s with empirical evidence
    that CC's rotation cadence is not seconds-scale, then bumped
    again to 600s once the watchdog migration moved the fast path
    onto FSEvents/inotify/RDCW. The env var meaning is now "backstop
    poll interval"; users overriding it pre-migration get the same
    behavior just at a less critical timer.
    """
    raw = read_env(
        "CLAUDE_EXPLORER_CC_WATCHER_INTERVAL_SEC",
        "CLAUDE_EXPORTER_CC_WATCHER_INTERVAL_SEC",
    )
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            logger.warning(
                "Bad CC watcher interval %r; using default", raw
            )
    return 600.0


# Public name preserved for backward compat with anything reading it
# (the install-watcher launcher script captures this at install time).
SCAN_INTERVAL_SEC = _resolve_interval()
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Per-process record of source paths we've already attempted to cache
# (regardless of whether the destination already existed). CC writes
# new files with incrementing slot numbers rather than modifying in
# place, so path uniqueness is sufficient — we never need to re-hash a
# file we've already processed.
_seen: set[Path] = set()


def _live_image_cache_root() -> Path:
    """Where Claude Code stores image-cache files. Honors CLAUDE_DIR."""
    return get_settings().claude_dir / "image-cache"


def handle_one_path(path: Path) -> bool:
    """Process a single candidate image-cache file.

    Idempotent: if the path is already in ``_seen`` (from any earlier
    event or scan), return False without doing any work. Otherwise
    copy via :func:`copy_marker_image_to_cache` and mark seen.

    Returns True iff a NEW file was successfully cached on this call.
    Used by:
      * :func:`scan_once` (one call per file in the rglob walk)
      * the watchdog event handler
        (:class:`_ImageCacheEventHandler`) — one call per OS event
    """
    if path in _seen:
        return False
    if not path.is_file():
        return False
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        _seen.add(path)
        return False
    sess = path.parent.name
    try:
        copy_marker_image_to_cache(str(path), sess)
    except Exception:  # noqa: BLE001
        logger.exception("watcher: failed to cache %s", path)
        # Don't add to _seen so a transient error retries next pass.
        return False
    _seen.add(path)
    return True


def scan_once() -> int:
    """Walk every file under ``~/.claude/image-cache/`` and ensure it
    has been copied into the permanent cache. Returns the count of
    paths newly handled this pass (i.e. not in ``_seen`` before).

    Used by:
      * :func:`run_watcher` (initial warm pass + periodic backstop)
      * the ``claude-explorer warm-cc-cache`` CLI command
      * the FastAPI lifespan task that fires once at startup

    Side effect: also runs the search-index drift-detection pass
    (:func:`backend.search_index.update_drifted_files`). Co-locating
    the two passes here means the backstop catches both
    image-cache and search-index drift in one shot. Failures in
    each pass are isolated by their own try/except so an
    image-cache error can't break search and vice versa.
    """
    root = _live_image_cache_root()
    handled = 0
    if root.exists():
        for path in root.rglob("*"):
            if handle_one_path(path):
                handled += 1
        if handled:
            logger.info(
                "CC image watcher backstop: handled %d new path(s)",
                handled,
            )

    # Search-index drift pass. Isolated from the image-cache pass —
    # any error here is logged and swallowed; image-cache continues to
    # run regardless.
    try:
        from backend.search_index import get_search_index, update_drifted_files
        from backend.store import ConversationStore

        idx = get_search_index()
        if idx is not None and idx.is_ready():
            updated = update_drifted_files(ConversationStore(), index=idx)
            if updated:
                logger.info(
                    "search index drift pass: re-indexed %d file(s)", updated
                )
    except Exception:  # noqa: BLE001
        logger.exception("watcher: search-index drift pass failed")

    # Summary-cache drift pass. Walks every CC session JSONL on
    # disk and refreshes any rows whose mtime or size has changed
    # since the cache was last stamped. Co-located with the FTS5
    # drift pass on purpose — both stores live in the same SQLite
    # file, both want the same answer to "what has changed since
    # last scan." Lazy read-through from /api/conversations still
    # catches files modified between backstop poll intervals.
    #
    # Cleanup pass also drops cache rows whose paths no longer exist
    # — analogous to the FTS5 cleanup in update_drifted_files.
    #
    # Isolated try/except so any failure here doesn't break the
    # image-cache or search-index passes.
    try:
        import os
        from backend.summary_cache import get_summary_cache
        from backend.claude_code_reader import (
            _read_summaries_parallel,
            discover_jsonl_files,
        )

        cache = get_summary_cache()
        if cache is not None:
            # Honor CLAUDE_DIR via get_settings() rather than walking
            # the import-time DEFAULT_CLAUDE_DIR — important for tests
            # and for users with a non-standard claude_dir env var
            # (the FTS5 drift pass goes through ConversationStore for
            # the same reason).
            claude_dir = get_settings().claude_dir
            live_paths = list(discover_jsonl_files(claude_dir))
            stat_index: dict = {}
            for p in live_paths:
                try:
                    stat_index[p] = os.stat(p)
                except OSError:
                    continue

            # Drop rows whose underlying files have disappeared.
            cleaned = cache.delete_missing(
                {str(p) for p in stat_index.keys()}
            )

            # Re-read only the drifted files (mtime or size mismatch).
            # get_many returns ONLY fresh rows, so the difference is
            # exactly the drifted-or-uncached set.
            cached = cache.get_many(live_paths, stat_index)
            drifted = [p for p in live_paths if p not in cached]
            if drifted:
                fresh = _read_summaries_parallel(drifted)
                refreshed = cache.upsert_many(fresh, stat_index)
                logger.info(
                    "summary cache drift pass: refreshed %d row(s)"
                    "%s",
                    refreshed,
                    f"; dropped {cleaned} stale row(s)" if cleaned else "",
                )
            elif cleaned:
                logger.info(
                    "summary cache drift pass: dropped %d stale row(s)",
                    cleaned,
                )
    except Exception:  # noqa: BLE001
        logger.exception("watcher: summary-cache drift pass failed")

    return handled


def reset_seen_for_tests() -> None:
    """Test hook: clear the process-level _seen set so back-to-back
    scans re-process the same paths. Production code should never call
    this."""
    _seen.clear()


# ---------------------------------------------------------------------------
# Event-driven path
# ---------------------------------------------------------------------------

# Imported lazily so a missing watchdog wheel (extremely unusual on
# supported platforms — it ships wheels for everything ≥ Python 3.7)
# doesn't break the polling fallback. See _try_start_observer.

def _build_event_handler():
    """Return a watchdog FileSystemEventHandler that funnels events
    through :func:`handle_one_path`. Lazy import so test environments
    without watchdog can still exercise the polling path.

    Handles ``on_created`` AND ``on_modified`` because some editors /
    file copy operations fire either or both. ``on_moved`` covers the
    "atomic rename into cache" pattern. The shared ``_seen`` memo
    plus ``handle_one_path``'s idempotency means redundant events
    are cheap.
    """
    from watchdog.events import FileSystemEventHandler

    class _ImageCacheEventHandler(FileSystemEventHandler):
        def _handle(self, src: str) -> None:
            try:
                handle_one_path(Path(src))
            except Exception:  # noqa: BLE001
                logger.exception("watcher event handler failed for %s", src)

        def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory:
                self._handle(event.src_path)

        def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory:
                self._handle(event.src_path)

        def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory and getattr(event, "dest_path", None):
                self._handle(event.dest_path)

    return _ImageCacheEventHandler()


def _try_start_observer():
    """Build and start a watchdog Observer on the live image-cache
    root. Returns the started Observer or None on any failure
    (missing wheel, sandboxed Python, NFS mount, etc.). The caller
    falls back to polling-only if None.
    """
    try:
        from watchdog.observers import Observer
    except ImportError:
        logger.warning(
            "CC image watcher: watchdog not installed; falling back "
            "to pure polling. Install with `pip install watchdog`."
        )
        return None

    root = _live_image_cache_root()
    # We need the dir to exist BEFORE Observer.schedule, otherwise
    # we'd silently fail to register a watch. Create it eagerly; CC
    # will populate it on first paste / tool result.
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        logger.exception(
            "CC image watcher: failed to create %s; events disabled", root,
        )
        return None

    handler = _build_event_handler()
    observer = Observer()
    # Do not inherit the library default. watchdog picks a different
    # backend per platform (FSEvents, inotify, ReadDirectoryChangesW),
    # and a non-daemon observer keeps the interpreter alive at exit.
    # Windows CI hung at shutdown on exactly this.
    observer.daemon = True
    observer.schedule(handler, str(root), recursive=True)
    try:
        observer.start()
    except Exception:  # noqa: BLE001
        logger.exception(
            "CC image watcher: Observer failed to start; falling back "
            "to pure polling",
        )
        return None

    backend_name = type(observer).__name__
    logger.info(
        "CC image watcher: event-driven Observer started (%s) on %s",
        backend_name, root,
    )
    return observer


# ---------------------------------------------------------------------------
# Projects-dir event-driven search-index drift
# ---------------------------------------------------------------------------
#
# Per PLANS/SEARCH_INDEX_FRESHNESS.md. The image-cache observer above
# fires sub-second on the latency-critical image-protection path; this
# block is the analogous fast-path for the FTS5 search index, watching
# ``~/.claude/projects/`` for ``*.jsonl`` modifications.
#
# Design notes:
#
#   * CC writes JSONL append-only as the user types. A single user
#     message can fire 5–20 ``on_modified`` events in rapid succession.
#     A naive "run drift on every event" wiring would batter the SQL
#     writer with redundant upserts. So events queue into a needs-
#     reindex set and reset a ``threading.Timer``; when the timer
#     fires we run ``update_drifted_files`` once.
#
#   * The Timer pattern is simpler than asyncio coordination here:
#     watchdog's Observer runs in its own background thread, so an
#     asyncio.Queue would force us to schedule a wakeup on the main
#     event loop from inside a different thread. A bare Timer thread
#     is the path of least resistance and matches how the image-cache
#     handler also calls back into module-level state from the
#     watchdog thread.
#
#   * The debounce default (2 s) is tunable via
#     ``CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC``. Tests set it
#     to 0.2 s so they don't wait real wall-clock seconds.


def _resolve_search_drift_debounce() -> float:
    """Debounce window in seconds for the projects-dir drift timer.

    Default 2 s. Override via
    ``CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC``. Clamped to a
    floor of 0.05 s so a misconfiguration can't reduce the debounce
    to a per-event fire.
    """
    raw = read_env("CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC")
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            logger.warning(
                "Bad CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC %r; "
                "using default 2.0", raw,
            )
    return 2.0


# Module-level debounce state. The Timer is reset on every JSONL event
# inside the debounce window; the needs-reindex set tracks which paths
# we still need to consider (currently only used for diagnostics — the
# drift scan re-stats every live path on its own).
_drift_lock = threading.Lock()
_drift_timer: threading.Timer | None = None
_drift_pending: set[Path] = set()
_drift_shutdown = False


def _live_projects_root() -> Path:
    """Where Claude Code stores per-project session JSONLs."""
    return get_settings().claude_dir / "projects"


def _live_cowork_root() -> Path:
    """Where Claude Desktop stores Cowork local-agent-mode sessions."""
    return (
        get_settings().claude_desktop_app_dir / "local-agent-mode-sessions"
    )


def _fire_drift_pass() -> None:
    """Timer callback: run the search-index drift pass once for every
    path queued since the last fire.

    Resolves ``update_drifted_files`` through the module attribute (not
    a top-of-file ``from x import y``) so tests can monkeypatch the
    function and have the patched version actually run. Same pattern
    ``scan_once`` uses for its own drift call.
    """
    global _drift_timer

    # Snapshot + clear the pending set under the lock so a concurrent
    # event handler doesn't see a half-drained queue.
    with _drift_lock:
        if _drift_shutdown:
            return
        _drift_pending.clear()
        _drift_timer = None

    try:
        from backend import search_index as si
        from backend.store import ConversationStore

        idx = si.get_search_index()
        # Run even when not ready: the same call is what makes the
        # index ready (build_full_index reuses this codepath for its
        # warm-start drift absorption). For event-driven fires after
        # startup, idx.is_ready() is virtually always True; we still
        # call so the cleanup pass for deleted JSONLs runs.
        si.update_drifted_files(ConversationStore(), index=idx)
    except Exception:  # noqa: BLE001
        logger.exception(
            "search-index drift pass failed (event-driven path)"
        )


def _schedule_drift(path: Path) -> None:
    """Queue a path for the debounced drift pass. Resets the timer."""
    global _drift_timer
    debounce = _resolve_search_drift_debounce()
    with _drift_lock:
        if _drift_shutdown:
            return
        _drift_pending.add(path)
        if _drift_timer is not None:
            _drift_timer.cancel()
        _drift_timer = threading.Timer(debounce, _fire_drift_pass)
        # Daemon=True so a leaked timer doesn't block Python interpreter
        # shutdown if `shutdown_projects_drift` somehow isn't called.
        _drift_timer.daemon = True
        _drift_timer.start()


def _build_projects_event_handler():
    """Return a watchdog FileSystemEventHandler that queues debounced
    drift passes for every ``*.jsonl`` modification.

    Non-JSONL events are silently ignored (CC occasionally drops
    ``.log``/``.tmp`` files in the projects tree). Directory events
    are skipped: those fire when a new project directory is created;
    the per-file modify events will catch the JSONLs as they appear.
    """
    from watchdog.events import FileSystemEventHandler

    class _ProjectsEventHandler(FileSystemEventHandler):
        def _maybe_queue(self, src: str) -> None:
            path = Path(src)
            if path.suffix.lower() != ".jsonl":
                return
            try:
                _schedule_drift(path)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "projects event handler failed to schedule drift for %s", src,
                )

        def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory:
                self._maybe_queue(event.src_path)

        def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory:
                self._maybe_queue(event.src_path)

        def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory and getattr(event, "dest_path", None):
                self._maybe_queue(event.dest_path)

    return _ProjectsEventHandler()


def _try_start_projects_observer():
    """Build and start a watchdog Observer on the live projects root.

    Returns the started Observer or None on any failure (missing
    wheel, sandboxed Python, NFS mount, etc.). The caller continues
    with the backstop-poll-only path if None.

    Eagerly mkdirs the projects root so the watch registers even on
    a brand-new install (CC will populate it on first session).
    """
    try:
        from watchdog.observers import Observer
    except ImportError:
        logger.warning(
            "search-index watcher: watchdog not installed; in-flight "
            "search freshness falls back to the 600 s backstop poll.",
        )
        return None

    root = _live_projects_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        logger.exception(
            "search-index watcher: failed to create %s; projects-dir "
            "events disabled", root,
        )
        return None

    handler = _build_projects_event_handler()
    observer = Observer()
    # Do not inherit the library default. watchdog picks a different
    # backend per platform (FSEvents, inotify, ReadDirectoryChangesW),
    # and a non-daemon observer keeps the interpreter alive at exit.
    # Windows CI hung at shutdown on exactly this.
    observer.daemon = True
    observer.schedule(handler, str(root), recursive=True)
    try:
        observer.start()
    except Exception:  # noqa: BLE001
        logger.exception(
            "search-index watcher: Observer failed to start; falling "
            "back to backstop-poll-only search freshness",
        )
        return None

    backend_name = type(observer).__name__
    logger.info(
        "search-index watcher: projects-dir Observer started (%s) on %s",
        backend_name, root,
    )
    return observer


def _build_cowork_event_handler():
    """Watchdog FileSystemEventHandler that queues debounced drift
    passes for every ``audit.jsonl`` modification under the cowork root.

    Cowork sessions contain other files (``outputs/``, ``uploads/``,
    ``shim-lib/``, etc.) we don't index — we filter for the audit.jsonl
    basename specifically so unrelated writes (e.g. tool output dumps)
    don't hammer the SQL writer.
    """
    from watchdog.events import FileSystemEventHandler

    class _CoworkEventHandler(FileSystemEventHandler):
        def _maybe_queue(self, src: str) -> None:
            path = Path(src)
            if path.name != "audit.jsonl":
                return
            try:
                _schedule_drift(path)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "cowork event handler failed to schedule drift for %s",
                    src,
                )

        def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory:
                self._maybe_queue(event.src_path)

        def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory:
                self._maybe_queue(event.src_path)

        def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
            if not event.is_directory and getattr(event, "dest_path", None):
                self._maybe_queue(event.dest_path)

    return _CoworkEventHandler()


def _try_start_cowork_observer():
    """Build and start a watchdog Observer on the live cowork root.

    Returns the started Observer or None on any failure (missing
    wheel, sandboxed Python, cowork root not present because the
    user doesn't use Cowork, etc.). The caller continues with the
    backstop-poll-only path if None.

    Eagerly mkdirs the cowork root so the watch registers even on
    a user who's never opened Cowork yet — Desktop will populate it
    on first session.
    """
    try:
        from watchdog.observers import Observer
    except ImportError:
        logger.warning(
            "search-index watcher: watchdog not installed; cowork "
            "freshness falls back to the 600 s backstop poll.",
        )
        return None

    root = _live_cowork_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        logger.exception(
            "search-index watcher: failed to create %s; cowork-dir "
            "events disabled", root,
        )
        return None

    handler = _build_cowork_event_handler()
    observer = Observer()
    # Do not inherit the library default. watchdog picks a different
    # backend per platform (FSEvents, inotify, ReadDirectoryChangesW),
    # and a non-daemon observer keeps the interpreter alive at exit.
    # Windows CI hung at shutdown on exactly this.
    observer.daemon = True
    observer.schedule(handler, str(root), recursive=True)
    try:
        observer.start()
    except Exception:  # noqa: BLE001
        logger.exception(
            "search-index watcher: cowork Observer failed to start; "
            "falling back to backstop-poll-only freshness for Cowork",
        )
        return None

    backend_name = type(observer).__name__
    logger.info(
        "search-index watcher: cowork-dir Observer started (%s) on %s",
        backend_name, root,
    )
    return observer


def shutdown_projects_drift() -> None:
    """Cancel any pending debounce Timer + flag shutdown so a fresh
    event right at this moment doesn't re-schedule.

    Called from ``run_watcher``'s cleanup branch AND from test
    teardown via the ``reset_projects_drift_for_tests`` hook.
    """
    global _drift_timer, _drift_shutdown
    with _drift_lock:
        _drift_shutdown = True
        if _drift_timer is not None:
            _drift_timer.cancel()
            _drift_timer = None
        _drift_pending.clear()


def reset_projects_drift_for_tests() -> None:
    """Test hook: clear ALL projects-drift state.

    Cancels any pending Timer (defense-in-depth — pytest shouldn't
    leak Timer threads across tests) and re-arms the module so the
    next test's event traffic is processed normally.

    Production code MUST NOT call this.
    """
    global _drift_timer, _drift_shutdown, _drift_pending
    with _drift_lock:
        if _drift_timer is not None:
            _drift_timer.cancel()
            _drift_timer = None
        _drift_pending = set()
        _drift_shutdown = False


def _drain_projects_drift_for_tests() -> None:
    """Test hook: synchronously fire any pending drift work.

    Cancels the pending Timer (if any) and invokes ``_fire_drift_pass``
    inline. Used by tests that don't want to sleep past the debounce
    window AGAIN after they've already waited for it (handles the
    race where the Timer scheduling thread hasn't quite woken yet).

    Production code MUST NOT call this.
    """
    global _drift_timer
    with _drift_lock:
        had_pending = bool(_drift_pending) or _drift_timer is not None
        if _drift_timer is not None:
            _drift_timer.cancel()
            _drift_timer = None
    if had_pending:
        _fire_drift_pass()


async def run_watcher(stop_event: asyncio.Event) -> None:
    """Run the watcher until ``stop_event`` is set.

    Lifecycle:
      1. Eager initial :func:`scan_once` — catch anything already on
         disk before the Observer is even started.
      2. Start a watchdog Observer for sub-second event-driven
         capture. If that fails for any reason (missing wheel,
         sandboxed Python, mount type without inotify support,
         etc.) we keep going with poll-only — strictly worse
         latency, same correctness.
      3. Periodic backstop loop: every ``SCAN_INTERVAL_SEC`` seconds
         (default 600s = 10 min) run :func:`scan_once` again to
         catch any events the OS dropped or coalesced.
      4. On ``stop_event``, cleanly stop the Observer (with a
         bounded join timeout) and return.

    Spawned as a background task by FastAPI's lifespan hook, and as
    the supervised process started by ``claude-explorer install-watcher``.
    """
    logger.info(
        "CC image watcher starting; backstop_interval=%.0fs root=%s",
        SCAN_INTERVAL_SEC,
        _live_image_cache_root(),
    )

    # Eager first pass before events start, so any pre-existing files
    # are captured even if events would have missed them on launch.
    try:
        scan_once()
    except Exception:  # noqa: BLE001
        logger.exception("CC image watcher initial scan failed")

    observer = _try_start_observer()
    # Re-arm the projects-drift module state in case a prior watcher
    # invocation left _drift_shutdown=True (e.g., the dev-reload path
    # in uvicorn `--reload`). reset_for_tests is the same op.
    reset_projects_drift_for_tests()
    projects_observer = _try_start_projects_observer()
    # Cowork Observer: same shared debounce + drift pipeline as
    # projects_observer, just watching a different root for Cowork
    # audit.jsonl appends.
    cowork_observer = _try_start_cowork_observer()

    cancelled = False
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=SCAN_INTERVAL_SEC
                )
                # If we exit the wait without TimeoutError, stop_event was set.
                break
            except asyncio.TimeoutError:
                pass
            try:
                scan_once()
            except Exception:  # noqa: BLE001
                logger.exception("CC image watcher backstop scan failed")
    except asyncio.CancelledError:
        # Lifespan teardown (or any caller) called ``task.cancel()``.
        # Mark and re-raise after the finally cleanup so the parent
        # gather sees CancelledError, but the finally MUST be allowed
        # to run first to stop the watchdog observers.
        cancelled = True
        raise
    finally:
        # Cleanup MUST run even when the task is cancelled mid-loop:
        # without the try/finally, ``asyncio.CancelledError`` unwinds
        # the stack past these lines and we leak the Observer threads.
        #
        # ``observer.stop()`` is non-blocking — it signals the
        # watchdog event-loop thread to exit on its next iteration.
        # ``observer.join()`` is what actually blocks (waiting for
        # the thread to die). Strategy:
        #
        #   * Cooperative path (``stop_event`` was set, no
        #     CancelledError): do a bounded synchronous join via
        #     ``asyncio.to_thread`` so we know the threads are gone
        #     by the time we return. This is the uvicorn graceful-
        #     reload path: callers want crisp lifecycle ordering.
        #
        #   * Cancellation path (``task.cancel()``): call
        #     ``observer.stop()`` but DO NOT join. We set
        #     ``observer.daemon = True`` explicitly at every start
        #     site, so the threads die with the process on every
        #     platform. We don't need
        #     to wait for them, and waiting would block the
        #     lifespan-shutdown ``asyncio.gather`` for up to
        #     ``join_timeout`` per observer. Skipping the join
        #     keeps shutdown latency at O(milliseconds).
        #
        # If a future caller needs deterministic Observer teardown
        # on cancellation, raise this back into design — but the
        # only current callers are the lifespan teardown (which has
        # its own hard cap) and the install-watcher script (which
        # uses cooperative shutdown via stop_event, not cancel).
        if cancelled:
            # Best-effort stop, no join. Errors are logged and
            # swallowed: a transient stop() failure shouldn't block
            # process shutdown.
            if observer is not None:
                try:
                    observer.stop()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "CC image watcher Observer stop failed during cancel"
                    )
            if projects_observer is not None:
                try:
                    projects_observer.stop()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "search-index watcher Observer stop failed during cancel"
                    )
            if cowork_observer is not None:
                try:
                    cowork_observer.stop()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "search-index watcher cowork Observer stop failed during cancel"
                    )
        else:
            def _shutdown_observers_sync() -> None:
                join_timeout = 0.5
                if observer is not None:
                    try:
                        observer.stop()
                        observer.join(timeout=join_timeout)
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "CC image watcher Observer shutdown failed"
                        )
                if projects_observer is not None:
                    try:
                        projects_observer.stop()
                        projects_observer.join(timeout=join_timeout)
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "search-index watcher projects Observer shutdown failed"
                        )
                if cowork_observer is not None:
                    try:
                        cowork_observer.stop()
                        cowork_observer.join(timeout=join_timeout)
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "search-index watcher cowork Observer shutdown failed"
                        )

            try:
                await asyncio.to_thread(_shutdown_observers_sync)
            except asyncio.CancelledError:
                # Cooperative shutdown was racing a cancel; treat as
                # cancellation from here (we already called stop()
                # via the to_thread but didn't get to join). Re-raise
                # after the debounce-timer cleanup below.
                cancelled = True
                raise
            except Exception:  # noqa: BLE001
                logger.exception(
                    "CC image watcher: asyncio.to_thread observer shutdown failed"
                )

        # Cancel any in-flight debounce Timer so a pending event scheduled
        # just before shutdown doesn't fire drift afterwards.
        shutdown_projects_drift()

        logger.info(
            "CC image watcher stopped (via %s)",
            "cancel" if cancelled else "cooperative",
        )
