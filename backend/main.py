"""FastAPI application for Claude Explorer."""

import os
import sys


def _bootstrap_macos_dyld_for_weasyprint() -> None:
    """On macOS, ensure WeasyPrint's CFFI bindings can locate Homebrew-installed
    GLib/Pango/Cairo at runtime.

    macOS SIP strips ``DYLD_*`` env vars from subprocess invocations (e.g.
    ``uv run uvicorn ...``), so prefixing the shell command with
    ``DYLD_LIBRARY_PATH=/opt/homebrew/lib`` silently no-ops once the python
    interpreter is re-execed under SIP. Setting the env var from inside
    Python at import time DOES survive because :func:`ctypes.util.find_library`
    on macOS inherits the updated process environment.

    Mirrors ``backend/tests/conftest.py`` for the live dev server path. Must
    run BEFORE the ``.routers import export`` line below — that import
    transitively triggers WeasyPrint's CFFI loader at module-load time.

    No-op on non-Darwin or when Homebrew lib dir doesn't exist.
    """
    if sys.platform != "darwin":
        return
    for brew_lib in ("/opt/homebrew/lib", "/usr/local/lib"):
        if not os.path.isdir(brew_lib):
            continue
        existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if brew_lib in existing.split(":"):
            return
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
            f"{brew_lib}:{existing}" if existing else brew_lib
        )
        return


_bootstrap_macos_dyld_for_weasyprint()


# Imports below MUST run AFTER the bootstrap above. The ``export`` router
# transitively pulls in WeasyPrint, which loads native libgobject/libpango/
# libcairo via CFFI at import time. Without the DYLD bootstrap, that import
# raises ``OSError: cannot load library 'libgobject-2.0-0'`` and the PDF
# export route returns 500. Do not reorder.
import asyncio  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import threading  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

from .config import get_settings, migrate_legacy_data_dir, read_env  # noqa: E402
from .host_guard import HostGuardMiddleware  # noqa: E402
from .routers import account, conversations, search, export, config, fetch, bookmarks, orgs, files, preferences, shutdown, watcher_health  # noqa: E402


# Exact-match pattern for the conversation-detail route.
# Matches /api/conversations/<uuid-like-string> but NOT /tree or any other
# sub-path. The path segment after .../conversations/ must contain no
# additional slashes — this is the discriminator that keeps the bypass
# scoped (e.g. /tree, /export/markdown, /export/pdf all keep gzip).
_CONV_DETAIL_PATH_RE = re.compile(r"^/api/conversations/[^/]+$")


class SelectiveGZipMiddleware(GZipMiddleware):
    """GZipMiddleware that bypasses compression for /api/conversations/<uuid>.

    Why: a 69 MB ConversationDetail payload (real user corpus, 16K
    messages) takes ~700 ms of synchronous gzip CPU per request on the
    asyncio event loop. While that compresses, EVERY other concurrent
    response is blocked from being sent — three parallel conversation
    fetches serialize at ~3 s instead of ~1 s, and small endpoints like
    /api/config / /api/orgs / /api/preferences all queue behind the
    big one. This produced the user-reported "10 s perceived load" on
    2026-05-23.

    Trade-off (per the 2026-05-23 council decision record): the
    conversation route's wire size goes from ~27 MB (gzipped) to
    ~69 MB (identity). On localhost transfer is ~50 ms either way; on
    a 50 Mbps remote link the bigger payload costs ~6 s extra. V1 is a
    local-only single-user tool, so the trade-off is acceptable.

    Why not solve it globally (off-loop gzip via threadpool):
      * The conversation route is THE pathological case. Other large
        payloads (export endpoints, search results) are ~10-100 KB and
        their gzip-on-loop cost is sub-perceptible.
      * Per-route bypass ships in <50 LOC; an off-loop wrapper around
        GZipMiddleware needs careful interaction with streaming
        responses (Starlette emits the body as chunked
        ``http.response.body`` messages and the gzip middleware
        compresses each chunk). That is a larger V2 refactor.

    Why exact match not prefix:
      * Sub-routes /tree, /export/markdown, /export/pdf return
        normal-sized payloads and benefit from gzip. The exact-match
        regex (``^/api/conversations/[^/]+$``) skips bypass for any
        path with an extra slash, preserving gzip for everything except
        the detail route itself.

    Pinned by:
      * ``test_conversation_detail_does_not_gzip_response``
      * ``test_concurrent_conversation_fetches_do_not_serialize``
      * ``test_other_routes_still_gzip_when_large``
      * ``test_conversation_tree_route_still_gzips``
    """

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and _CONV_DETAIL_PATH_RE.match(scope["path"]):
            # Bypass gzip entirely: pass through to the wrapped app.
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


log = logging.getLogger(__name__)


def _resolve_static_dir() -> Path | None:
    """Locate the bundled frontend assets, or return None if absent.

    Resolution order:
      1. **Installed mode**: ``<backend package>/_static/`` — written by the
         hatch build hook during ``uv build``. This is what end users get
         from PyPI wheels.
      2. **Dev mode**: ``<repo_root>/frontend/dist/`` — written by
         ``npm run build`` in the frontend dir. Lets contributors run
         ``uv run uvicorn backend.main:app`` against a locally-built bundle
         without re-running ``uv build``.

    Returns the first directory containing ``index.html``, or None if
    neither exists (API-only mode).
    """
    # 1. Installed-wheel location (bundled by hatch_build.py).
    installed = Path(__file__).resolve().parent / "_static"
    if (installed / "index.html").is_file():
        return installed

    # 2. Repo dev location.
    repo_dev = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if (repo_dev / "index.html").is_file():
        return repo_dev

    return None


# Telemetry surfaced by /api/health when the lifespan migration repeatedly
# fails to acquire the .fetch.lock — see NEW4-P1-C.
_migration_state: dict = {
    "status": "pending",  # pending | done | deferred | stuck
    "attempts": 0,
    "last_error": None,
    "holder": None,
}


async def _lifespan_migration_task(data_dir, credentials_path) -> None:
    """Background task that runs migrate_to_v2 with retry-on-lock-contention.

    Per NEW3-P0-B + NEW4-P1-C:
      * Each iteration first checks for the sentinel and exits early if present.
      * On LockContentionError, sleep 60s and retry.
      * After 5 consecutive lock-contention failures, set status='stuck' and
        keep retrying — so /api/health can surface a banner suggesting
        `claude-explorer unlock-fetch`.
      * Cancellation by the lifespan teardown is honored.
    """
    from fetcher.migrate_to_v2 import (
        LockContentionError,
        MIGRATION_SENTINEL,
        migrate_to_v2,
    )

    sentinel_path = data_dir / MIGRATION_SENTINEL
    consecutive_failures = 0

    while True:
        if sentinel_path.exists():
            _migration_state["status"] = "done"
            return
        try:
            # Run the (synchronous) migration in a thread so we don't block
            # the event loop.
            await asyncio.to_thread(
                migrate_to_v2,
                data_dir=data_dir,
                credentials_path=credentials_path,
                lock_command="lifespan_migrate",
            )
            _migration_state["status"] = "done"
            _migration_state["attempts"] += 1
            log.info("Lifespan migration complete.")
            return
        except LockContentionError as e:
            _migration_state["attempts"] += 1
            consecutive_failures += 1
            _migration_state["last_error"] = str(e)
            _migration_state["status"] = (
                "stuck" if consecutive_failures >= 5 else "deferred"
            )
            log.warning(
                "Lifespan migration deferred (attempt %d, consecutive_failures=%d): %s",
                _migration_state["attempts"], consecutive_failures, e,
            )
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise
        except Exception as e:
            # Any other error — log and stop. The user can run
            # `claude-explorer migrate` to retry manually.
            _migration_state["status"] = "stuck"
            _migration_state["last_error"] = str(e)
            log.error("Lifespan migration failed: %s", e, exc_info=True)
            return


def get_migration_state() -> dict:
    """Return a snapshot of the migration telemetry for /api/health."""
    return dict(_migration_state)


async def _cancel_background_tasks(background_tasks: list[asyncio.Task]) -> None:
    """Cancel the lifespan's background tasks, and log any real failure.

    Lives outside the lifespan's ``finally`` block on purpose: a
    ``return`` inside ``finally`` discards any in-flight exception
    (PEP 765; Python 3.14+ warns). Here an early return is harmless.
    """
    if not background_tasks:
        return

    log.info(
        "Lifespan shutdown: cancelling %d background task(s)",
        len(background_tasks),
    )

    # Cancel every non-done task. ``Task.cancel`` is idempotent.
    #
    # NOTE: we deliberately do NOT set ``watcher_stop`` first. The
    # cooperative-shutdown path through cc_watcher.run_watcher's
    # finally block synchronously awaits Observer joins (via
    # ``asyncio.to_thread``) before completing — bounded but
    # still O(seconds) for the OS-level thread joins. The
    # CancelledError path through the SAME finally block exits as
    # soon as the cancellation is delivered to the to_thread
    # await; the OS thread keeps running and finishes on its own
    # without holding up the asyncio shutdown. Cancellation is
    # therefore strictly faster than cooperative shutdown for
    # lifespan exit.
    #
    # ``watcher_stop`` is still set by the watcher's finally
    # cleanup (via ``shutdown_projects_drift``) so a pending
    # debounce Timer doesn't fire post-shutdown.
    for task in background_tasks:
        task.cancel()

    # Wait for all cancellations to propagate, with a hard cap on
    # total shutdown latency. ``return_exceptions=True`` collects
    # each task's terminal value (or exception) so we can log
    # anything unexpected without aborting the gather mid-way.
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*background_tasks, return_exceptions=True),
            timeout=5.0,
        )
        for task, result in zip(background_tasks, results):
            if isinstance(result, asyncio.CancelledError):
                # Expected.
                continue
            if isinstance(result, Exception):
                log.warning(
                    "Background task %s raised during shutdown: %r",
                    task.get_name(), result,
                )
    except asyncio.TimeoutError:
        still_running = [
            t.get_name() for t in background_tasks if not t.done()
        ]
        log.error(
            "Lifespan shutdown exceeded 5s budget; tasks still running: %s",
            still_running,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # V1 data-dir rename migration: ~/.claude-exporter/ -> ~/.claude-explorer/.
    # MUST run BEFORE the first get_settings() call so the cached Settings
    # picks up the new path. Idempotent and best-effort — see
    # backend.config.migrate_legacy_data_dir for full semantics.
    migrate_legacy_data_dir()

    # Startup: verify data directory exists
    settings = get_settings()
    if not settings.data_dir.exists():
        print(f"Warning: Data directory does not exist: {settings.data_dir}")
        print("Creating directory...")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    else:
        print(f"Data directory: {settings.data_dir}")

    # cowork-multi-org C4 (NEW2-P0-α + NEW3-P0-B + NEW4-P1-C):
    # Run migrate_to_v2 at startup. If the lock can't be acquired (a CLI
    # fetch is in progress), don't block startup — start the server and let
    # a background task retry. Skip entirely with
    # CLAUDE_EXPLORER_SKIP_MIGRATION=1 (or legacy CLAUDE_EXPORTER_SKIP_MIGRATION).
    migration_task: asyncio.Task | None = None
    if read_env(
        "CLAUDE_EXPLORER_SKIP_MIGRATION", "CLAUDE_EXPORTER_SKIP_MIGRATION"
    ) == "1":
        log.info("Skipping lifespan migration (CLAUDE_EXPLORER_SKIP_MIGRATION=1).")
        _migration_state["status"] = "skipped"
    else:
        from fetcher.migrate_to_v2 import (
            LockContentionError,
            MIGRATION_SENTINEL,
            migrate_to_v2,
        )
        from fetcher.credentials import DEFAULT_CREDENTIALS_PATH

        sentinel_path = settings.data_dir / MIGRATION_SENTINEL
        if sentinel_path.exists():
            _migration_state["status"] = "done"
        else:
            # First, try a quick synchronous attempt with a short timeout.
            # Most users hit this path and it completes instantly.
            try:
                await asyncio.to_thread(
                    migrate_to_v2,
                    data_dir=settings.data_dir,
                    credentials_path=DEFAULT_CREDENTIALS_PATH,
                    timeout_seconds=10.0,
                    lock_command="lifespan_migrate",
                )
                _migration_state["status"] = "done"
                _migration_state["attempts"] = 1
                log.info("Lifespan migration complete.")
            except LockContentionError as e:
                # Lock held by a CLI fetch. Don't block startup; retry in
                # the background.
                log.warning(
                    "Lifespan migration deferred: .fetch.lock held; retrying in background. (%s)", e
                )
                _migration_state["status"] = "deferred"
                _migration_state["attempts"] = 1
                _migration_state["last_error"] = str(e)
                migration_task = asyncio.create_task(
                    _lifespan_migration_task(settings.data_dir, DEFAULT_CREDENTIALS_PATH),
                    name="migration_task",
                )
            except Exception as e:
                # Any other error — log and continue. Legacy fallback in
                # store.py keeps conversations visible.
                log.error("Lifespan migration failed: %s", e, exc_info=True)
                _migration_state["status"] = "stuck"
                _migration_state["last_error"] = str(e)

    # Surface the supervised-watcher install state to supervised-job
    # logs once per startup. See watcher_logging + the frontend
    # WatcherMissingBanner for the user-facing surfaces.
    try:
        from .watcher_logging import log_watcher_status
        log_watcher_status()
    except Exception:  # noqa: BLE001
        log.debug("log_watcher_status failed; ignoring", exc_info=True)

    # Spawn the CC image-cache watcher. Polls ~/.claude/image-cache/
    # every few seconds and copies new files to the permanent cache
    # before Claude Code rotates them. Best-effort: any internal error
    # is logged and swallowed.
    #
    # 2026-05-26: skip the in-process watcher when the SUPERVISED
    # watcher is already running (launchd/systemd/Task Scheduler).
    # Two processes writing to ``search-index.sqlite`` race on every
    # drift pass — observed as ``sqlite3.OperationalError: database is
    # locked`` during ``update_drifted_files`` upserts. The supervised
    # watcher does the same work; running both is redundant AND
    # harmful. The explicit ``CLAUDE_EXPLORER_DISABLE_CC_WATCHER=1``
    # env var still wins for users who want full manual control.
    watcher_stop = asyncio.Event()
    watcher_task: asyncio.Task | None = None

    # Cooperative stop flag for long loops that run in executor threads
    # (the first FTS5 build, the FTS5-off image-warm walk). Cancelling
    # the asyncio task only abandons the future: the thread keeps
    # running, and ``asyncio.run`` plus Python's exit-time join wait for
    # it. A first build over a large corpus could thus keep the process
    # alive for minutes after Quit. Shutdown sets this flag before it
    # cancels the tasks; each loop checks it once per conversation.
    executor_stop = threading.Event()
    disabled_by_env = read_env(
        "CLAUDE_EXPLORER_DISABLE_CC_WATCHER", "CLAUDE_EXPORTER_DISABLE_CC_WATCHER"
    ) == "1"
    try:
        from .watcher_status import is_watcher_installed
        supervised = is_watcher_installed()
    except Exception:  # noqa: BLE001
        supervised = False
    if disabled_by_env:
        log.info("In-process CC watcher disabled by env var.")
    elif supervised:
        log.info(
            "In-process CC watcher skipped (supervised watcher already running). "
            "Avoids search-index write contention."
        )
    else:
        from backend.cc_watcher import run_watcher

        watcher_task = asyncio.create_task(
            run_watcher(watcher_stop), name="watcher_task"
        )

    # Auto-warm the CC image cache: walk every CC session JSONL and
    # ensure referenced [Image: source: ...] files are copied to the
    # permanent cache. Catches the case where a user has CC sessions
    # they haven't yet opened in the explorer (the lazy per-render
    # copy at /api/cc-image only triggers on view). User can never
    # lose images to "I forgot to run warm-cc-cache".
    #
    # Phase-2 Workstream B (PLANS/PERFORMANCE_PHASE_2.md):
    # The FTS5 build's per-file CC load path
    # (backend/search_index.py:_load_conversation_at →
    # read_claude_code_conversation → cache_all_markers) already
    # warms image markers for every drifted CC JSONL it reads.
    # On the FTS5-enabled path (the default) we PIGGYBACK on that
    # work instead of running a parallel corpus walk + 5 s delay.
    # The standalone walk is kept as a fallback ONLY when FTS5 is
    # disabled (CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX=1) so users
    # who opt out of search don't silently lose image protection.
    warm_task: asyncio.Task | None = None
    fts5_disabled = (
        read_env(
            "CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX",
            "CLAUDE_EXPORTER_DISABLE_SEARCH_INDEX",
        )
        == "1"
    )
    cc_warm_disabled = (
        read_env(
            "CLAUDE_EXPLORER_DISABLE_CC_WARM", "CLAUDE_EXPORTER_DISABLE_CC_WARM"
        )
        == "1"
    )
    if not cc_warm_disabled and fts5_disabled:
        # Fallback path: FTS5 is off, so the build won't piggyback
        # image-warm. Run the standalone walk WITHOUT the legacy
        # 5 s head-start — the contention argument the delay
        # mitigated only applied when FTS5 was also walking the
        # same corpus. With FTS5 off, the first /api/conversations
        # request hits the metadata reader (cheap, no contention).
        async def _warm_all_sessions_fallback() -> None:
            from backend.cc_image_cache import warm_all_sessions_async

            await warm_all_sessions_async(stop=executor_stop)

        warm_task = asyncio.create_task(
            _warm_all_sessions_fallback(), name="warm_task"
        )

    # Build the FTS5 search index in the background. Search falls back
    # to the linear-scan path until this completes, so the server is
    # immediately responsive. Set CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX=1
    # (or legacy CLAUDE_EXPORTER_DISABLE_SEARCH_INDEX) to skip
    # (useful when debugging linear-scan equivalence).
    #
    # We use print() not log.info() because uvicorn's default log
    # config doesn't propagate to ``backend`` package loggers (the
    # existing "Data directory:" print at lifespan top is the same
    # pattern). The user wants to see "search index build complete"
    # on stdout without configuring a logging.dictConfig.
    search_index_task: asyncio.Task | None = None
    if not fts5_disabled:
        async def _build_search_index() -> None:
            # 500ms head-start so the first /api/conversations request
            # lands BEFORE the FTS5 build runs. Search falls back to
            # the linear-scan path until this completes, so search
            # never goes "down".
            #
            # The original 5 s delay (and the ~10 s contention window
            # it was hiding) came from the build's
            # `get_all_conversations_raw(source="all")` walk, which
            # loaded every message of every conversation into memory
            # even on warm restarts where every file's mtime was
            # already known. PLANS/SEARCH_INDEX_FRESHNESS.md refactored
            # the build to drift-first: `_drift_first_scan` stats every
            # live path against `indexed_files` and only loads content
            # for the drifted set. Warm restarts now finish the build
            # in ~100-300ms (proportional to drift, not corpus size).
            # No contention to hide behind a delay; 500ms is just the
            # event-loop yield headroom from the original plan.
            #
            # Search-ready time after restart drops from ~15 s to <1 s
            # on warm restarts. First install (every path "drifted")
            # still loads every file once and is bounded by disk I/O.
            #
            # See PLANS/SEARCH_INDEX_FRESHNESS.md and the cold-start
            # bench numbers in articles/part_2_web_app.md "Performance
            # (FTS5 index)".
            await asyncio.sleep(0.5)
            try:
                from backend.search_index import build_full_index, get_search_index
                from backend.store import ConversationStore

                idx = get_search_index()
                if idx is None:
                    print(
                        "search index: skipped (FTS5 not available in sqlite3)",
                        flush=True,
                    )
                    return
                files, msgs = await asyncio.to_thread(
                    build_full_index,
                    ConversationStore(),
                    index=idx,
                    stop=executor_stop,
                )
                if executor_stop.is_set():
                    return
                print(
                    f"search index build complete: {files} files / {msgs} messages",
                    flush=True,
                )

                # W2 (2026-05-23 council decision): pre-execute two
                # FTS5 warmup queries to warm SQLite's page cache
                # before the user's first interactive search.
                #
                # Without this, the first user search after restart
                # takes ~6 s on the user's real corpus because SQLite
                # has to page in the FTS5 inverted-list segments cold.
                # Subsequent same-term queries are sub-second.
                #
                # Two queries (per council Decision Record #1):
                #   1. No-match sentinel — exercises term-dictionary
                #      lookup path (proves the read side is functional).
                #   2. Common-term ("the") with LIMIT 1 — forces the
                #      engine to read doclist + segment pages that
                #      no-match queries short-circuit past.
                #
                # Both fire via asyncio.to_thread to keep the event
                # loop free. Total cost: ~100 ms on the user's corpus,
                # well inside the lifespan budget.
                #
                # Skip via CLAUDE_EXPLORER_DISABLE_FTS5_WARM=1.
                fts5_warm_disabled = (
                    read_env(
                        "CLAUDE_EXPLORER_DISABLE_FTS5_WARM",
                        "CLAUDE_EXPORTER_DISABLE_FTS5_WARM",
                    )
                    == "1"
                )
                if not fts5_warm_disabled:
                    import time as _time
                    for needle, label in (
                        ("warmup_zzzz_xyzzy_nomatch", "nomatch-sentinel"),
                        ("the", "common-term"),
                    ):
                        t0 = _time.monotonic()
                        try:
                            await asyncio.to_thread(
                                idx.query, needle, limit=1
                            )
                            print(
                                f"search index warmup ({label}): "
                                f"{(_time.monotonic() - t0) * 1000:.0f}ms",
                                flush=True,
                            )
                        except Exception as warm_exc:  # noqa: BLE001
                            # Warmup failures are non-fatal — they
                            # just mean the first user search will
                            # be slower. Log and continue.
                            print(
                                f"search index warmup ({label}) "
                                f"failed: {warm_exc!r}",
                                flush=True,
                            )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"search index: initial build failed: {exc!r}",
                    flush=True,
                )

        search_index_task = asyncio.create_task(
            _build_search_index(), name="search_index_task"
        )

    # Initialize the sidebar metadata cache and wipe it if the source
    # hash of read_conversation_summary_fast has changed since the last
    # process start. Cheap (one SELECT + maybe one DELETE) so we run
    # synchronously rather than spawning a task — finishes before the
    # first /api/conversations request comes in. Failures are non-fatal:
    # the metadata path falls back to the legacy sequential reader if
    # the cache module returns None.
    try:
        from backend.summary_cache import get_summary_cache
        from backend.claude_code_reader import LOGIC_VERSION

        summary_cache = get_summary_cache()
        if summary_cache is not None:
            wiped = await asyncio.to_thread(
                summary_cache.clear_on_logic_mismatch, LOGIC_VERSION
            )
            if wiped:
                print(
                    "summary cache: logic version changed; cache wiped",
                    flush=True,
                )
    except Exception as exc:  # noqa: BLE001
        print(
            f"summary cache: startup init failed: {exc!r}",
            flush=True,
        )

    # Eagerly populate the summary cache. Without this, the first
    # /api/conversations request after a cold start (or after the
    # logic-version wipe above) pays the full parallel JSONL re-parse
    # cost — ~1.5s on a ~1,000-session corpus — inline with the
    # request. Spawning the fill at lifespan startup means the first
    # request lands on a warm cache (or, if it races the fill, only
    # pays for the still-unparsed subset).
    #
    # The entire body runs in asyncio.to_thread so the ~1,200 os.stat
    # calls and the ProcessPoolExecutor join don't stall the event
    # loop while other lifespan tasks are still spinning up. Failures
    # are logged and swallowed: a missing cache row just means the
    # request takes the legacy path, same as before this change.
    #
    # Skip via CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM=1 (or legacy
    # CLAUDE_EXPORTER_*). See PLANS/OPTIMIZE_COLD_START.md.
    summary_cache_task: asyncio.Task | None = None
    if read_env(
        "CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM",
        "CLAUDE_EXPORTER_DISABLE_SUMMARY_CACHE_WARM",
    ) != "1":
        def _sync_build_summary_cache() -> None:
            import os
            import time
            from backend import claude_code_reader as ccr
            from backend.summary_cache import get_summary_cache

            cache = get_summary_cache()
            if cache is None:
                # FTS5 unavailable; fall through to the legacy path on
                # demand. Same gate as the cache singleton itself.
                return
            paths = list(ccr.discover_jsonl_files(get_settings().claude_dir))
            if not paths:
                return
            stat_index: dict = {}
            for p in paths:
                try:
                    stat_index[p] = os.stat(p)
                except OSError:
                    # Vanished between discover and stat; on-demand
                    # path will skip it via the same OSError handler.
                    continue
            cached = cache.get_many(paths, stat_index)
            misses = [p for p in paths if p not in cached and p in stat_index]
            if not misses:
                return
            t0 = time.monotonic()
            # NOTE: resolve _read_summaries_parallel via the module
            # attribute (not a `from … import` at the top of the
            # function) so tests that `patch(...)._read_summaries_parallel`
            # actually see the patched version.
            fresh = ccr._read_summaries_parallel(misses)
            cache.upsert_many(fresh, stat_index)
            elapsed = time.monotonic() - t0
            print(
                f"summary cache: filled {len(fresh)} entries in {elapsed:.2f}s",
                flush=True,
            )

        async def _build_summary_cache() -> None:
            try:
                await asyncio.to_thread(_sync_build_summary_cache)
            except asyncio.CancelledError:
                # Cooperative cancellation during shutdown is fine —
                # any rows already upserted survive in SQLite; the
                # next startup picks up the misses idempotently.
                raise
            except Exception as exc:  # noqa: BLE001
                print(
                    f"summary cache: eager fill failed: {exc!r}",
                    flush=True,
                )

        summary_cache_task = asyncio.create_task(
            _build_summary_cache(), name="summary_cache_task"
        )

    # W1 (2026-05-23 council decision): pre-warm the per-conversation
    # FileCache for the N=5 most-recently-updated conversations. First
    # navigation to a known-recent conversation otherwise pays ~1.3 s of
    # cold I/O + JSONL parse; pre-warming makes it warm.
    #
    # The task DEPENDS on the summary cache being filled (it reads
    # ``updated_at`` from the summary cache to pick the "most recent"
    # set). We await summary_cache_task first inside the warm coroutine
    # so the ordering is explicit without entangling the two tasks at
    # the spawn site.
    #
    # Bounded concurrency: ``asyncio.gather`` with N=5 + the default
    # threadpool (8 workers) is safe. If N is ever increased past ~8,
    # gate with an ``asyncio.Semaphore``.
    #
    # "Most recent" heuristic: the explorer doesn't track per-user
    # access history, so updated_at from the source (Claude.ai / CC) is
    # the closest proxy. Acceptable for V1.
    #
    # Skip via ``CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM=1``.
    filecache_warm_task: asyncio.Task | None = None
    filecache_warm_disabled = (
        read_env(
            "CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM",
            "CLAUDE_EXPORTER_DISABLE_FILECACHE_WARM",
        )
        == "1"
    )
    if not filecache_warm_disabled:
        async def _warm_filecache(n: int = 5) -> None:
            # Wait for the summary cache fill to land so the "most
            # recent" sort sees populated rows. If the summary cache
            # task didn't spawn (e.g. it was disabled), just no-op —
            # the warm relies on it.
            if summary_cache_task is not None:
                try:
                    await summary_cache_task
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Summary fill failure is logged separately; we
                    # still try to warm from whatever IS in the
                    # cache.
                    pass

            try:
                from backend.summary_cache import get_summary_cache
                from backend.claude_code_reader import discover_jsonl_files
                from backend.store import ConversationStore
                import os as _os

                cache = get_summary_cache()
                if cache is None:
                    # FTS5 not available — nothing to read from.
                    return

                paths = list(discover_jsonl_files(get_settings().claude_dir))
                if not paths:
                    return

                stat_index: dict = {}
                for p in paths:
                    try:
                        stat_index[p] = _os.stat(p)
                    except OSError:
                        continue

                cached = cache.get_many(paths, stat_index)
                # Build (uuid, updated_at) pairs and pick top-N by
                # updated_at. Missing/None summaries are skipped.
                ranked: list[tuple[str, str]] = []
                for _path, summary in cached.items():
                    if not summary:
                        continue
                    uuid = summary.get("uuid")
                    updated_at = summary.get("updated_at") or ""
                    if uuid and isinstance(updated_at, str):
                        ranked.append((uuid, updated_at))

                ranked.sort(key=lambda x: x[1], reverse=True)
                top = [uuid for uuid, _ in ranked[:n]]
                if not top:
                    return

                # Hand off each uuid to a thread so the synchronous
                # file I/O + JSONL parse happens off the event loop.
                store = ConversationStore()
                await asyncio.gather(
                    *(
                        asyncio.to_thread(store._find_conversation_data, uuid)
                        for uuid in top
                    ),
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                print(
                    f"filecache warm: failed: {exc!r}",
                    flush=True,
                )

        filecache_warm_task = asyncio.create_task(
            _warm_filecache(5), name="filecache_warm_task"
        )

    try:
        yield
    finally:
        # Explicit, uniform shutdown for every background task spawned
        # above (PLANS/2026.05.18-backend-architecture-cleanup.md task B4).
        #
        # The previous code handled each task individually with five
        # near-identical cancel/await/swallow blocks. That worked but
        # had three concrete problems:
        #
        #   1. **No hard cap on shutdown latency.** A task that wedged
        #      its own ``await task`` (e.g. an unawaited cancellation
        #      that left a coroutine in a non-finalised state) would
        #      stall the lifespan exit indefinitely.
        #
        #   2. **The watcher's cooperative shutdown waited up to 2 s
        #      synchronously** on ``asyncio.wait_for(watcher_task,
        #      timeout=2.0)``. With watchdog's Observer threads
        #      sometimes taking the full 5 s to join, this could span
        #      the entire 2 s window even when a CancelledError-based
        #      cleanup would have been instant. The watcher's own
        #      try/finally cleanup
        #      (:func:`backend.cc_watcher.run_watcher`) handles
        #      ``CancelledError`` correctly and offloads blocking
        #      Observer joins to ``asyncio.to_thread`` — so a hard
        #      cancel here returns immediately while the OS threads
        #      finish on their own (Python's ``threading._shutdown``
        #      waits for them at process exit if necessary).
        #
        #   3. **Exception diagnostics varied by task.**
        #      ``gather(*, return_exceptions=True)`` collects per-task
        #      results uniformly so we can log non-cancellation
        #      exceptions explicitly while expected CancelledErrors
        #      are silently absorbed.
        #
        # The 5-second total budget is a diagnostic cap: every task
        # we spawn is trivially cancellable (``asyncio.to_thread``
        # abandons its future instantly; the watcher has its own
        # try/finally). The timeout firing is itself a bug we want
        # to surface in the logs.
        background_tasks: list[asyncio.Task] = [
            t for t in (
                migration_task,
                watcher_task,
                warm_task,
                search_index_task,
                summary_cache_task,
                filecache_warm_task,
            ) if t is not None and not t.done()
        ]

        # Set the executor stop flag first, so that executor threads
        # return at their next check instead of after their whole loop.
        executor_stop.set()
        await _cancel_background_tasks(background_tasks)


def install_request_timing_middleware(app: FastAPI) -> None:
    """Install a single-line per-request timing log.

    Emits ONE INFO log per response with the request method, path, status
    code, and elapsed wall time. Replaces uvicorn's default access log
    (which lacks elapsed time); when `claude-explorer serve` boots
    uvicorn, it passes `access_log=False` to avoid duplicate lines.

    Line format::

        GET /api/search?q=foo 200 elapsed=0.234s

    Routes the log through the ``uvicorn.error`` logger (NOT
    ``uvicorn.access`` — that logger's handler is removed by
    `--no-access-log`, and NOT a module-private logger like
    ``backend.main`` either — that one isn't wired to a handler by
    uvicorn's default LOGGING_CONFIG, so its INFO records get dropped
    silently by the root logger's WARNING-level handler). Routing
    through ``uvicorn.error`` means the line appears in the same
    stream as startup messages and stays visible regardless of the
    `--no-access-log` flag. The 2026-05-22 bug it fixes: live
    backend was silent after `--no-access-log` because the
    ``backend.main`` logger had no handler attached.

    Wrapped in a separate function so the FastAPI test harness can
    install the middleware on a minimal app without re-importing the
    full router stack — see `backend/tests/test_request_timing_log.py`.

    Why a custom middleware vs uvicorn's `--log-config`: keeping the
    timing logic in-tree means it survives any deploy config (Docker,
    systemd, ad-hoc `uvicorn` from a shell) and is unit-testable.

    Implementation: PURE ASGI middleware, NOT the BaseHTTPMiddleware-
    based ``@app.middleware("http")`` decorator. The decorator form
    buffers the response body internally, which makes every response
    look like a streaming response (``more_body=True`` on the first
    chunk) to downstream middleware. That broke ``GZipMiddleware``'s
    ``minimum_size=1024`` small-skip optimization on 2026-05-23 — every
    tiny /api/health / /api/info response was being gzipped and shipping
    a Content-Encoding: gzip header. The pure-ASGI form below preserves
    the ``more_body`` flag end-to-end so GZip's small-skip works as
    designed. Pinned by ``test_small_response_is_not_gzipped``.
    """
    import time

    timing_logger = logging.getLogger("uvicorn.error")

    class _RequestTimingMiddleware:
        def __init__(self, asgi_app):
            self.app = asgi_app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            t0 = time.perf_counter()
            status_code = 500  # Default if the route never sends a response.

            async def _send_capture(message):
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                await send(message)

            try:
                await self.app(scope, receive, _send_capture)
            finally:
                elapsed = time.perf_counter() - t0
                # Path includes the query string so a slow
                # `/api/search?q=foo` is distinguishable from a fast one
                # at a glance.
                path = scope["path"]
                if scope.get("query_string"):
                    path = f"{path}?{scope['query_string'].decode('latin-1')}"
                timing_logger.info(
                    "%s %s %d elapsed=%.3fs",
                    scope["method"],
                    path,
                    status_code,
                    elapsed,
                )

    app.add_middleware(_RequestTimingMiddleware)


app = FastAPI(
    title="Claude Explorer",
    description="API for browsing and exporting Claude Desktop conversations",
    version="0.1.0",
    lifespan=lifespan,
)

# Request-timing middleware FIRST so the elapsed measurement covers all
# downstream middleware (CORS preflight, body parsing, route work).
install_request_timing_middleware(app)

# GZip compression for large API responses (2026-05-23 user-report fix).
#
# The user's 16K-message ConversationDetail payload is ~69 MB
# uncompressed. Gzip-1 reduces this to ~28 MB (60% reduction) at ~50 ms
# of CPU cost on a Mac M-series — far less than the 11+ seconds saved
# on a typical broadband network. Browsers always send
# ``Accept-Encoding: gzip, deflate, br`` and transparently decode the
# response, so no frontend change is needed.
#
# Trade-off (documented per 2026-05-23 council review): compression runs
# on the asyncio event loop. ``compresslevel=1`` keeps the worst-case
# block at ~50 ms even on the 69 MB payload, acceptable for a
# single-user localhost tool. If Claude Explorer ever becomes
# multi-tenant or LAN-shared, move compression to a reverse proxy
# (nginx / caddy) or wrap compression in ``asyncio.to_thread`` — same
# pattern as the ``/api/search`` threadpool fix (commit 7623c12).
#
# ``minimum_size=1024`` (Starlette default-ish, industry-standard):
# below ~1 KB, gzip's framing overhead can produce a LARGER output and
# always wastes CPU. Pinned by ``test_small_response_is_not_gzipped``.
#
# Middleware order: ``add_middleware`` prepends, so the LAST middleware
# added runs FIRST on the request. Currently (``app.user_middleware``):
#   request:  host guard -> gzip -> timing -> route
#   response: route -> timing -> gzip -> host guard
# The request-timing middleware sits inside gzip, so it measures the
# route's own work, NOT compression time. That keeps the elapsed=Xs log
# meaningful for backend perf diagnosis.
app.add_middleware(SelectiveGZipMiddleware, minimum_size=1024, compresslevel=1)

# No CORS middleware. The web page calls /api on its own origin, and the
# Vite dev server proxies /api (frontend/vite.config.ts), so no browser
# request is cross-origin. Answering CORS for http://localhost:5173 let any
# project's dev server on that port read the whole archive (PR #6 review).

# Added last, so it runs first: no route or middleware sees a request whose
# Host is not a loopback name (DNS rebinding). See backend/host_guard.py.
app.add_middleware(HostGuardMiddleware)

# Include routers under /api prefix
app.include_router(conversations.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(fetch.router, prefix="/api")
app.include_router(bookmarks.router, prefix="/api")
app.include_router(preferences.router, prefix="/api")
app.include_router(orgs.router, prefix="/api")
app.include_router(account.router, prefix="/api")
app.include_router(shutdown.router, prefix="/api")
app.include_router(files.router, prefix="/api")
# watcher_health endpoint paths already include the /api prefix.
app.include_router(watcher_health.router)


@app.get(
    "/api/info",
    summary="API metadata (name, version, docs link)",
)
async def api_info():
    """API metadata endpoint.

    Lives at /api/info (not /) so that / can serve the SPA when bundled
    assets are present. The previous JSON-at-root behavior moved here in
    the v0.1.0 PyPI packaging migration.
    """
    return {
        "name": "Claude Explorer",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get(
    "/health",
    summary="Liveness probe (always returns healthy if the process is up)",
)
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get(
    "/api/health",
    summary="Liveness probe plus index-migration telemetry",
)
async def api_health():
    """Health endpoint with migration telemetry (NEW4-P1-C)."""
    state = get_migration_state()
    return {
        "status": "healthy",
        "migration": state,
        "migration_stuck": state.get("status") == "stuck",
    }


# -----------------------------------------------------------------------------
# Static SPA mount + catch-all
# -----------------------------------------------------------------------------
# IMPORTANT: these routes are registered AFTER all /api/* routers + /docs +
# /openapi.json so that FastAPI's first-match-wins ordering routes API
# traffic correctly. The catch-all also explicitly rejects /api/* and the
# OpenAPI surface to belt-and-suspenders the ordering guarantee.

_STATIC_DIR = _resolve_static_dir()

if _STATIC_DIR is not None:
    # Serve hashed Vite bundle assets verbatim.
    _assets_dir = _STATIC_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=_assets_dir),
            name="assets",
        )

    # Anything Vite drops in dist/ that isn't /assets (favicon, vite.svg,
    # robots.txt, etc.) — serve from a single fall-through handler below.

    _RESERVED_PREFIXES = ("api/", "docs", "redoc", "openapi.json", "health")

    @app.get(
        "/",
        summary="Serve the single-page-app shell (HTML)",
        include_in_schema=False,
    )
    async def _spa_root() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get(
        "/{full_path:path}",
        summary="SPA catch-all: serve static assets or fall through to the SPA shell",
        include_in_schema=False,
    )
    async def _spa_catchall(full_path: str) -> FileResponse:
        """Serve static files for known paths, else fall through to index.html
        so client-side React routing works for deep links.

        Explicit /api/, /docs, /openapi.json rejection is defense-in-depth:
        FastAPI's route order already routes those before this catch-all,
        but if a developer reorders router registration in the future this
        guards against returning the SPA HTML for a missing API endpoint.
        """
        if any(full_path == p or full_path.startswith(p) for p in _RESERVED_PREFIXES):
            raise HTTPException(status_code=404)
        # If a real file exists (e.g. vite.svg at the repo dist root),
        # serve it. Otherwise serve index.html for the SPA's client router.
        candidate = _STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
else:
    log.warning(
        "UI assets not found at %s or %s; backend will run as API-only. "
        "Run `npm run build` in frontend/ for dev mode, or install a "
        "pre-built wheel for end-user usage.",
        Path(__file__).resolve().parent / "_static",
        Path(__file__).resolve().parent.parent / "frontend" / "dist",
    )

    # Preserve the legacy JSON-at-root behavior so anyone scripting against
    # `/` still gets something useful in API-only mode.
    @app.get(
        "/",
        summary="API-only mode: report bundle status and docs link",
    )
    async def _root_json() -> dict:
        return {
            "name": "Claude Explorer",
            "version": "0.1.0",
            "docs": "/docs",
            "ui": "not bundled — install from PyPI or run `npm run build` in frontend/",
        }