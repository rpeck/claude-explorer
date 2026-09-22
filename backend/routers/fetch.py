"""Fetch router - trigger Claude Desktop conversation fetch from frontend."""

import json
import asyncio
import logging
import time
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

# ``fetcher`` is installed as a top-level package via the project's
# ``pyproject.toml`` (``[tool.hatch.build.targets.wheel] packages = ["backend",
# "fetcher", ...]``), so it imports cleanly under any supported run mode
# (``uv run``, editable install, or repo-root invocation). The previous
# ``sys.path.insert`` hack was removed in Council A2 (2026-05-21).
from fetcher.bulk_fetch import (
    ClaudeFetcher,
    DEFAULT_CREDENTIALS_PATH,
    DEFAULT_FILES_DIR,
    DEFAULT_OUTPUT_DIR,
    FetchAuthError,
    FetchTerminalError,
    FetchTransientError,
    PersistedErrorKind,
    extract_http_status_from_message,
    kind_from_http_status,
    load_credentials,
    migrate_legacy_error_code,
)
from fetcher.playwright_capture import capture_credentials
from fetcher.credentials import save_credentials

from ..deps import refuse_if_config_corrupt
# Patch-safe SSE helpers extracted in Council A2 (2026-05-21). The heavier
# pipeline functions stay in this module because their bodies reference
# names (``ClaudeFetcher``, ``load_credentials``, ``capture_credentials``,
# ``DEFAULT_*``) that 60+ tests patch at the router's dotted path.
from ..fetch_pipeline import (
    _drain_retry_events,
    _is_session_expired_error,
    _send_event,
)

# Council code-review D1 (2026-05-21): wire-contract models live in
# backend/models.py as the single source of truth for shapes mirrored
# by frontend/src/lib/types.ts. Re-export the names this router used
# to own so existing test imports (``from backend.routers.fetch import
# FetchProgress``) keep working — see CLAUDE-TESTING.md §5.12 on the
# attribute-patch idiom.
from ..models import FetchProgress, FetchStatus, ForceRefetchResponse  # noqa: F401  re-export

__all__ = [
    "FetchProgress",  # re-export for backward-compat test imports
    "FetchStatus",
    "ForceRefetchResponse",
    "router",
]


logger = logging.getLogger(__name__)


router = APIRouter(tags=["fetch"])


# Build-9: Concurrency guard for the combined capture+fetch pipeline.
# A single in-flight refresh is allowed per worker; a second concurrent
# request gets 409 Conflict. We use a plain module-level boolean rather
# than an ``asyncio.Lock`` because the request handler does a
# check-then-set under the single-threaded asyncio event loop (no real
# race between coroutines at the check; the streamer's ``finally`` block
# clears the flag) and a Lock without explicit acquire/release was
# misleading dead code (Council A1, 2026-05-21).
_refresh_in_progress: bool = False


# Build-9: timeout the user has to complete login in the captured browser.
CAPTURE_TIMEOUT_SECONDS = 300

# Build-9: SSE keep-alive interval during the long capture wait. Must be
# under typical proxy idle timeouts (~30s) to prevent the browser silently
# reconnecting and triggering 409 against our own lock.
CAPTURE_KEEPALIVE_SECONDS = 25


SESSION_EXPIRED_MESSAGE = (
    "Session expired or Cloudflare-blocked. "
    "Re-run claude-explorer capture to refresh credentials."
)

TRANSIENT_USER_MESSAGE = "Network problem reaching claude.ai. Retry?"

# Build-9 Bug 3: friendly copy for the per-conversation force-refetch route.
# When Anthropic returns 404 we don't know WHY (deleted, archived, or in a
# different workspace), so default to the catch-all message and only switch
# to the cross-workspace copy when we can confirm the UUID is missing from
# the current credentials' conversation list (see PLANS/cowork-multi-org.md
# for the long-term fix that actually syncs across workspaces).
CONVERSATION_GONE_MESSAGE = (
    "This conversation isn't available on Anthropic anymore. "
    "It may have been deleted or archived."
)
CONVERSATION_CROSS_WORKSPACE_MESSAGE = (
    "This conversation may belong to a different Anthropic workspace than "
    "your current login. Cross-workspace sync is coming in a future update."
)


ErrorKind = Literal["AUTH", "TRANSIENT", "TERMINAL"]


def _classify_error(exc: BaseException) -> ErrorKind:
    """Map any exception into one of three classes for the SSE pipeline.

    Domain exceptions defined in fetcher.bulk_fetch are checked first;
    plain stringly-typed errors (e.g. RuntimeError("401 ...")) fall back
    to the legacy regex-style detection so existing call sites keep
    working without refactoring.
    """
    if isinstance(exc, FetchAuthError):
        return "AUTH"
    if isinstance(exc, FetchTransientError):
        return "TRANSIENT"
    if isinstance(exc, FetchTerminalError):
        return "TERMINAL"
    msg = str(exc)
    lowered = msg.lower()
    if "401" in msg or "403" in msg or "cf-mitigated" in lowered:
        return "AUTH"
    if any(s in msg for s in ("502", "503", "504")):
        return "TRANSIENT"
    return "TERMINAL"


def _user_message_for(kind: ErrorKind, raw: str) -> str:
    """Pick the user-facing copy for a given error class."""
    if kind == "AUTH":
        return SESSION_EXPIRED_MESSAGE
    if kind == "TRANSIENT":
        return TRANSIENT_USER_MESSAGE
    return f"Fetch failed: {raw}" if raw else "Fetch failed: unknown error"


def _build_error_event(kind: ErrorKind, raw: str) -> dict:
    """Construct the SSE error payload with kind + retryable + message."""
    return {
        "type": "error",
        "kind": kind,
        "retryable": kind == "TRANSIENT",
        "message": _user_message_for(kind, raw),
    }


# A1-hunt: stream-level SSE bucket vocabulary.
# Yielded by `_fetch_phase_stream` upstream of `_send_event` to pick the
# right toast shape (auth = sticky session-expired, transient = retry
# button, fatal = sticky details).
RollupBucket = Literal["auth", "transient", "fatal"]


# Map persisted `error_kind` -> stream-level rollup bucket. Centralized so
# new persisted kinds added in `fetcher.bulk_fetch.PersistedErrorKind` get
# one and only one bucket mapping. Unknown kinds default to "fatal" at
# the call site (forward-compat: a new kind from a newer writer should
# fall through to a safe sticky toast rather than silently bucketed wrong).
_KIND_TO_BUCKET: dict[str, RollupBucket] = {
    "AUTH_EXPIRED": "auth",
    "ORG_FORBIDDEN": "auth",     # 403 from Anthropic/CF is an auth-class signal.
    "ORG_NOT_FOUND": "fatal",    # 404 means org gone — no recovery via retry.
    "TRANSIENT": "transient",
    "TERMINAL": "fatal",
}


def _rollup_bucket_for(record: dict) -> tuple[RollupBucket, str]:
    """Pick the SSE bucket + message for a failing per-org record.

    Prefers the new `error_kind` field; falls back to the legacy
    `error_code` string (via `migrate_legacy_error_code`) for in-flight
    records that pre-date the A1 vocab fix. The frontend keys off the
    bucket via `kind` on SSE error events.

    The returned message is the best human-readable thing on the record:
    `error_message` if present, else a stringified summary.
    """
    kind = record.get("error_kind")
    if kind is None:
        # Legacy in-memory record (or read-time legacy on-disk record).
        legacy = record.get("error_code")
        kind, _legacy_status = migrate_legacy_error_code(legacy)

    bucket: RollupBucket = _KIND_TO_BUCKET.get(kind or "", "fatal")
    msg = (
        record.get("error_message")
        or record.get("error_code")
        or "fetch failed"
    )
    return bucket, msg


BROWSER_MISSING_MESSAGE = (
    "Playwright's Chromium build is not installed, so the login window "
    "cannot open. Install it once with: "
    "uvx --from claude-explorer playwright install chromium "
    "(from a source checkout: uv run playwright install chromium). "
    "Then click Refresh again."
)


def classify_capture_error(error_msg: str) -> str:
    """Map a raw credential-capture error into a user-actionable message.

    The sidebar Refresh button shows this text directly, so a raw Playwright
    dump is useless to the reader. The most common first-run failure is a
    missing Chromium build, because that download is a separate one-time
    step. Playwright's own hint names a bare ``playwright install`` command
    that a PyPI user cannot run, so this replaces it with the real command.

    Every other failure keeps its original text, prefixed for context.
    """
    if not error_msg:
        return "Capture failed: unknown error"

    lowered = error_msg.lower()
    browser_missing_markers = (
        "executable doesn't exist",
        "please run the following command to download new browsers",
        "browsertype.launch: executable",
    )
    if any(marker in lowered for marker in browser_missing_markers):
        return BROWSER_MISSING_MESSAGE
    return f"Capture failed: {error_msg}"


def classify_fetch_error(error_msg: str) -> str:
    """Map a raw fetch error into a user-actionable message.

    Returns SESSION_EXPIRED_MESSAGE for any 401, any 403 (which Anthropic and
    Cloudflare both return on auth failures), or any 'cf-mitigated' marker.
    Otherwise returns the original message prefixed with 'Fetch failed: '.
    """
    if not error_msg:
        return "Fetch failed: unknown error"

    lowered = error_msg.lower()
    if "401" in error_msg or "403" in error_msg or "cf-mitigated" in lowered:
        return SESSION_EXPIRED_MESSAGE
    return f"Fetch failed: {error_msg}"


# NOTE: FetchStatus and FetchProgress were defined here pre-council;
# they now live in backend/models.py (D1 — single source of truth
# for wire shapes mirrored by frontend/src/lib/types.ts). Re-exported
# at the top of this module so existing imports keep working.


@router.get(
    "/fetch/status",
    response_model=FetchStatus,
    summary="Get fetch status — credentials presence and current state",
)
async def get_fetch_status() -> FetchStatus:
    """Check if credentials are available and get current state."""
    credentials_path = DEFAULT_CREDENTIALS_PATH
    output_dir = DEFAULT_OUTPUT_DIR

    has_credentials = credentials_path.exists()

    credentials_age_days: int | None = None
    if has_credentials:
        try:
            mtime = credentials_path.stat().st_mtime
            credentials_age_days = int((time.time() - mtime) // 86400)
        except OSError:
            credentials_age_days = None

    existing_count = 0
    if output_dir.exists():
        existing_count = len([
            p for p in output_dir.glob("*.json")
            if p.stem != "_index"
        ])

    return FetchStatus(
        has_credentials=has_credentials,
        credentials_path=str(credentials_path),
        output_dir=str(output_dir),
        existing_count=existing_count,
        credentials_age_days=credentials_age_days,
    )


async def fetch_conversations_stream(
    incremental: bool = True,
    limit: int | None = None,
) -> AsyncGenerator[str, None]:
    """Stream fetch progress as Server-Sent Events."""

    def send_event(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    try:
        # Load credentials
        try:
            creds = load_credentials(DEFAULT_CREDENTIALS_PATH)
        except Exception:
            yield send_event({
                "type": "error",
                "message": "No credentials found. Run 'claude-explorer capture' first.",
            })
            return

        session_key = creds.get("session_key")
        org_id = creds.get("org_id")

        if not session_key or not org_id:
            yield send_event({
                "type": "error",
                "message": "Invalid credentials file. Missing session_key or org_id.",
            })
            return

        # cowork-multi-org C3: build a single-element orgs list from creds.
        # The v2 creds schema carries an `orgs` array; the v1 schema only has
        # the scalar `org_id`. Fall back to the latter during the migration
        # window. The full multi-org SSE handler ships in C5.
        primary = creds.get("primary_org_id") or org_id
        if creds.get("orgs"):
            orgs_list = list(creds["orgs"])
        else:
            orgs_list = [{"uuid": org_id, "name": None, "capabilities": [], "seen_in_response": False}]

        # Create fetcher
        fetcher = ClaudeFetcher(
            session_key=session_key,
            orgs=orgs_list,
            primary_org_id=primary,
            output_dir=DEFAULT_OUTPUT_DIR,
            files_dir=DEFAULT_FILES_DIR,
            delay=0.3,
            incremental=incremental,
            verbose=False,
            download_files=True,
            cf_bm=creds.get("cf_bm"),
            cf_clearance=creds.get("cf_clearance"),
        )

        # Ensure output directory exists
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Get existing UUIDs if incremental — scoped to the primary org's
        # subdir under the new layout. C3 is single-org so this is exactly
        # equivalent to the prior global glob in behavior; the per-org
        # scope just gets the layout right for C5's multi-org loop.
        existing_uuids = set()
        if incremental:
            existing_uuids = fetcher.existing_uuids_for_current_org()

        yield send_event({
            "type": "start",
            "message": "Fetching conversation list...",
            "current": 0,
            "total": 0,
        })

        # Fetch conversation list (run in thread to not block).
        # ``get_running_loop()`` (not ``get_event_loop()``) is the modern
        # asyncio idiom inside ``async def`` — the legacy form is
        # deprecated since Python 3.10 (Council A1, 2026-05-21).
        loop = asyncio.get_running_loop()
        conversations = await loop.run_in_executor(
            None, fetcher.fetch_conversation_list
        )

        if limit:
            conversations = conversations[:limit]

        # Filter out existing if incremental
        if incremental:
            to_fetch = [c for c in conversations if c.get("uuid") not in existing_uuids]
        else:
            to_fetch = conversations

        total = len(to_fetch)
        skipped = len(conversations) - total

        yield send_event({
            "type": "progress",
            "message": f"Found {len(conversations)} conversations, fetching {total} new" +
                       (f" (skipping {skipped} existing)" if skipped else ""),
            "current": 0,
            "total": total,
        })

        if total == 0:
            yield send_event({
                "type": "complete",
                "message": "No new conversations to fetch.",
                "current": 0,
                "total": 0,
            })
            return

        # Fetch each conversation
        fetched_count = 0
        for i, conv in enumerate(to_fetch, 1):
            uuid = conv.get("uuid", "")
            name = conv.get("name", "Untitled")[:50]

            yield send_event({
                "type": "progress",
                "message": f"Fetching: {name}",
                "current": i,
                "total": total,
                "conversation_name": name,
            })

            if not uuid:
                continue

            # Fetch and save conversation
            try:
                full_conv = await loop.run_in_executor(
                    None, fetcher.fetch_conversation, uuid
                )
                if full_conv:
                    await loop.run_in_executor(
                        None, fetcher.save_conversation, full_conv
                    )
                    fetched_count += 1
            except Exception as e:
                error_msg = str(e)
                lowered = error_msg.lower()
                if "401" in error_msg or "403" in error_msg or "cf-mitigated" in lowered:
                    yield send_event({
                        "type": "error",
                        "message": SESSION_EXPIRED_MESSAGE,
                    })
                    return
                # Continue on other per-conversation errors
                yield send_event({
                    "type": "progress",
                    "message": f"Error fetching {name}: {error_msg}",
                    "current": i,
                    "total": total,
                })

            # Small delay between requests
            if i < total:
                await asyncio.sleep(0.3)

        # Save index
        await loop.run_in_executor(
            None, fetcher.save_index, conversations
        )

        yield send_event({
            "type": "complete",
            "message": f"Fetched {fetched_count} conversations successfully.",
            "current": total,
            "total": total,
        })

    except Exception as e:
        yield send_event({
            "type": "error",
            "message": classify_fetch_error(str(e)),
        })


@router.post(
    "/fetch/conversation/{uuid}",
    response_model=ForceRefetchResponse,
    summary="Force re-fetch a single conversation, bypassing incremental skip",
    # Layer 2 of PLANS/2026.05.18-config-corruption-safe-mode.md:
    # writes a fresh copy of the conversation to data_dir; refuse when
    # the config that resolved data_dir is corrupt. /fetch/status (read)
    # is NOT gated.
    dependencies=[Depends(refuse_if_config_corrupt)],
)
async def force_refetch_conversation(uuid: str) -> ForceRefetchResponse:
    """Force re-fetch of a single conversation, bypassing the incremental skip.

    Useful when a Desktop-side rename or content change needs to propagate
    without a full --full-refresh.
    """
    try:
        creds = load_credentials(DEFAULT_CREDENTIALS_PATH)
    except Exception:
        raise HTTPException(status_code=400, detail="No credentials. Run 'claude-explorer capture' first.")

    session_key = creds.get("session_key")
    org_id = creds.get("org_id")
    if not session_key or not org_id:
        raise HTTPException(status_code=400, detail="Invalid credentials file.")

    primary = creds.get("primary_org_id") or org_id
    orgs_list = list(creds["orgs"]) if creds.get("orgs") else [
        {"uuid": org_id, "name": None, "capabilities": [], "seen_in_response": False}
    ]
    fetcher = ClaudeFetcher(
        session_key=session_key,
        orgs=orgs_list,
        primary_org_id=primary,
        output_dir=DEFAULT_OUTPUT_DIR,
        files_dir=DEFAULT_FILES_DIR,
        delay=0.0,
        incremental=False,
        verbose=False,
        download_files=True,
        cf_bm=creds.get("cf_bm"),
        cf_clearance=creds.get("cf_clearance"),
    )

    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        full_conv = fetcher.fetch_conversation(uuid)
    except Exception as e:
        # Build-9 Bug 3: route auth failures through the same SESSION_EXPIRED
        # message as the bulk pipeline, regardless of whether the underlying
        # exception is a domain `FetchAuthError` or a stringly-typed
        # RuntimeError("401 ..."). _classify_error already handles both.
        kind = _classify_error(e)
        if kind == "AUTH":
            raise HTTPException(status_code=401, detail=SESSION_EXPIRED_MESSAGE)
        if kind == "TRANSIENT":
            raise HTTPException(status_code=503, detail=TRANSIENT_USER_MESSAGE)
        # Council code-review B1 (2026-05-21): do NOT leak raw
        # exception text. Exception strings from the bulk fetcher can
        # embed local paths (``/Users/<name>/.claude-explorer/...``),
        # sessionKey / cf cookies when an HTTP error response is
        # formatted into the exception, and upstream URLs with org
        # UUIDs — CWE-200 in a portfolio-piece app. Log the real
        # exception with ``exc_info=True`` server-side; return a
        # static message so the frontend toast is clean.
        logger.exception("force_refetch_conversation failed for uuid=%s", uuid)
        raise HTTPException(
            status_code=500,
            detail=(
                "Fetch failed due to an internal error. "
                "Check the server logs for details, or retry."
            ),
        )

    if not full_conv:
        # Build-9 Bug 3: 404 from Anthropic for a conversation we asked about
        # explicitly. The most useful disambiguation is "is it in the current
        # credentials' org list?" If yes, it was probably just deleted; if no,
        # the most likely cause is that it lives in another Anthropic
        # workspace (the cowork-multi-org problem). We can't be 100% sure
        # without proper multi-workspace support, but the cross-workspace
        # hint is the single most actionable explanation a user can act on.
        detail = CONVERSATION_GONE_MESSAGE
        try:
            org_list = fetcher.fetch_conversation_list()
        except Exception:
            # If we can't even pull the list, stick with the generic message.
            org_list = None
        # Heuristic: only switch to the cross-workspace message when the
        # list is non-empty AND the UUID isn't in it. An empty list could
        # mean a brand-new account, a transient list failure, or genuinely
        # zero conversations -- none of which warrant the workspace claim.
        if org_list:
            org_uuids = {c.get("uuid") for c in org_list}
            if uuid not in org_uuids:
                detail = CONVERSATION_CROSS_WORKSPACE_MESSAGE
        raise HTTPException(status_code=404, detail=detail)

    fetcher.save_conversation(full_conv)
    return ForceRefetchResponse(
        uuid=uuid,
        status="refetched",
        name=full_conv.get("name", ""),
    )


@router.get(
    "/fetch/start",
    summary="Start a bulk fetch from Claude (Server-Sent Events stream)",
    responses={
        200: {"content": {"text/event-stream": {}}},
        503: {"description": "Config file is corrupt; refuse to write to data_dir"},
    },
    # Layer 2: refuse when config corrupt. The gate fires before the
    # StreamingResponse is constructed so the client sees a real
    # HTTP 503, not a stream emitting SSE `error` frames after 200 OK.
    dependencies=[Depends(refuse_if_config_corrupt)],
)
async def fetch_conversations(
    incremental: bool = True,
    # Hunt #4 (API boundaries): bound to positive ints up to 5000.
    # `?limit=-5` previously reached `conversations[:limit]` and
    # silently returned the LAST 5 — counter to "Max number of
    # conversations to fetch" docs.
    limit: int | None = Query(None, ge=1, le=5000),
) -> StreamingResponse:
    """Fetch conversations from Claude Desktop API.

    Returns Server-Sent Events stream with progress updates.

    Args:
        incremental: If True, skip already-downloaded conversations
        limit: Max number of conversations to fetch
    """
    return StreamingResponse(
        fetch_conversations_stream(incremental=incremental, limit=limit),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Build-9: combined capture + fetch SSE pipeline.
# ---------------------------------------------------------------------------


async def _run_capture_with_keepalive(
    timeout: int = CAPTURE_TIMEOUT_SECONDS,
) -> AsyncGenerator[str | dict, None]:
    """Run capture_credentials and yield SSE-frame strings for keep-alives.

    The final value yielded is a dict (the captured credentials) or None.
    Intermediate yields are SSE-encoded strings — either capture_waiting_login
    progress events or `: ping` keep-alive comments. The caller distinguishes
    by isinstance.
    """
    capture_task = asyncio.create_task(
        capture_credentials(timeout=timeout, headless=False)
    )
    waited = 0
    try:
        while not capture_task.done():
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(capture_task),
                    timeout=CAPTURE_KEEPALIVE_SECONDS,
                )
                yield {"_result": result}
                return
            except asyncio.TimeoutError:
                waited += CAPTURE_KEEPALIVE_SECONDS
                yield _send_event(
                    {
                        "type": "capture_waiting_login",
                        "message": (
                            f"Waiting for you to log in "
                            f"({waited}s elapsed, {timeout - waited}s remaining)..."
                        ),
                        "current": waited,
                        "total": timeout,
                    }
                )
                # SSE comment keep-alive in addition to the data event.
                yield ": ping\n\n"
        result = await capture_task
        yield {"_result": result}
    except Exception as exc:
        capture_task.cancel()
        yield {"_error": str(exc)}


async def _fetch_phase_stream(
    incremental: bool,
    limit: int | None = None,
) -> AsyncGenerator[tuple[str, str | None], None]:
    """Drive the fetch phase against currently-saved credentials.

    Yields (kind, payload) tuples where kind is one of:
        "event"     -> payload is an SSE-encoded data string to forward
        "auth"      -> payload is the underlying error message; caller may capture
        "transient" -> payload is the underlying error message; caller emits a
                       retryable error event and stops
        "fatal"     -> payload is the error message; caller emits final error

    The legacy "expired" kind is preserved as an alias for "auth" so any
    in-flight callers keep working.
    """
    try:
        creds = load_credentials(DEFAULT_CREDENTIALS_PATH)
    except Exception as exc:
        yield ("fatal", f"No credentials: {exc}")
        return

    session_key = creds.get("session_key")
    org_id = creds.get("org_id")
    if not session_key or not org_id:
        yield ("fatal", "Invalid credentials file. Missing session_key or org_id.")
        return

    primary = creds.get("primary_org_id") or org_id
    orgs_list = list(creds["orgs"]) if creds.get("orgs") else [
        {"uuid": org_id, "name": None, "capabilities": [], "seen_in_response": False}
    ]
    fetcher = ClaudeFetcher(
        session_key=session_key,
        orgs=orgs_list,
        primary_org_id=primary,
        output_dir=DEFAULT_OUTPUT_DIR,
        files_dir=DEFAULT_FILES_DIR,
        delay=0.3,
        incremental=incremental,
        verbose=False,
        download_files=True,
        cf_bm=creds.get("cf_bm"),
        cf_clearance=creds.get("cf_clearance"),
    )

    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # cowork-multi-org C5: cross-org dedup via existing_pairs.
    existing_pairs: set[tuple[str, str]] = set()
    if incremental:
        existing_pairs = fetcher.existing_pairs()

    yield (
        "event",
        _send_event(
            {
                "type": "start",
                "message": "Fetching conversation list...",
                "current": 0,
                "total": 0,
            }
        ),
    )

    # ``get_running_loop()`` (not the deprecated ``get_event_loop()``) —
    # this generator is always invoked from inside an async route handler
    # (Council A1, 2026-05-21).
    loop = asyncio.get_running_loop()

    # cowork-multi-org C5: outer loop over orgs. The SSE event types
    # (start/progress/complete/error) stay; new event types (org_start,
    # org_done, primary_demoted) are emitted alongside for the C6 UI to
    # render. The outer current/total counters span all orgs cumulatively.
    cumulative_total = 0
    cumulative_current = 0
    fetched_count = 0
    org_results: list[dict] = []
    primary_demoted_from: str | None = None

    for org in list(fetcher.orgs):
        org_uuid = org["uuid"]
        org_name = org.get("name") or org_uuid[:8]
        is_primary = (org_uuid == fetcher.primary_org_id)

        yield (
            "event",
            _send_event(
                {"type": "org_start", "org_id": org_uuid, "name": org_name}
            ),
        )

        try:
            with fetcher._scoped_org(org):
                try:
                    conversations = await loop.run_in_executor(
                        None, fetcher.fetch_conversation_list
                    )
                except Exception as exc:
                    # Capture exc state up front: `exc` is deleted when the
                    # `except` clause exits, and the lambdas below are
                    # scheduled via `run_in_executor` — ruff F821 catches
                    # the closure-over-deleted-name risk even though `await`
                    # runs the lambda inside the except body in practice.
                    exc_message = str(exc)
                    for frame in _drain_retry_events(fetcher):
                        yield ("event", frame)
                    kind = _classify_error(exc)
                    # A1-hunt: derive persisted (error_kind, http_status)
                    # ONCE from the exception, then use those for both the
                    # disk write and the in-memory org_results entry that
                    # flows through `_rollup_bucket_for`.
                    http_status = extract_http_status_from_message(exc_message)
                    if kind == "AUTH":
                        if is_primary and http_status == 401:
                            # Primary 401 = session expired. Hard abort.
                            yield ("auth", str(exc))
                            return
                        # Secondary AUTH or primary 403/404: skip this org.
                        error_kind: PersistedErrorKind = (
                            kind_from_http_status(http_status) or "AUTH_EXPIRED"
                        )
                        # Persist skipped status.
                        await loop.run_in_executor(
                            None,
                            lambda ek=error_kind, hs=http_status: fetcher.save_index(
                                [], status="skipped",
                                error_kind=ek,
                                http_status=hs,
                                error_message=exc_message,
                            ),
                        )
                        org_results.append({
                            "org_id": org_uuid, "name": org_name,
                            "status": "skipped",
                            "error_kind": error_kind,
                            "http_status": http_status,
                            "error_message": exc_message,
                        })
                        yield (
                            "event",
                            _send_event({
                                "type": "org_done",
                                "org_id": org_uuid,
                                "status": "skipped",
                                "error_kind": error_kind,
                                "http_status": http_status,
                            }),
                        )
                        # Auto-demote primary if this was the primary 403/404.
                        if is_primary and http_status in (403, 404):
                            new_primary = fetcher._pick_new_primary(exclude=[org_uuid])
                            if new_primary:
                                primary_demoted_from = fetcher.primary_org_id
                                fetcher.primary_org_id = new_primary
                                try:
                                    fetcher._persist_demote(new_primary)
                                except Exception:
                                    # In-memory primary_org_id is already
                                    # updated above; the persist step is
                                    # a best-effort write-through. If it
                                    # fails (disk full, perms) the demotion
                                    # still takes effect this session and
                                    # the next persist attempt will retry.
                                    # Log so the failure isn't silent.
                                    logger.warning(
                                        "fetch: failed to persist primary-org "
                                        "demotion to %s; in-memory state holds "
                                        "for this session",
                                        new_primary,
                                        exc_info=True,
                                    )
                                yield (
                                    "event",
                                    _send_event({
                                        "type": "primary_demoted",
                                        "from_org_id": primary_demoted_from,
                                        "to_org_id": new_primary,
                                        "reason": error_kind,
                                    }),
                                )
                        continue
                    if kind == "TRANSIENT":
                        logger.warning("Fetch transient error for %s: %s", org_uuid, exc)
                        # Per-org transient: skip this org, continue.
                        await loop.run_in_executor(
                            None,
                            lambda hs=http_status: fetcher.save_index(
                                [], status="failed",
                                error_kind="TRANSIENT",
                                http_status=hs,
                                error_message=exc_message,
                            ),
                        )
                        org_results.append({
                            "org_id": org_uuid, "name": org_name,
                            "status": "failed",
                            "error_kind": "TRANSIENT",
                            "http_status": http_status,
                            "error_message": exc_message,
                        })
                        yield (
                            "event",
                            _send_event({
                                "type": "org_done",
                                "org_id": org_uuid,
                                "status": "failed",
                                "error_kind": "TRANSIENT",
                                "http_status": http_status,
                            }),
                        )
                        continue
                    logger.error("Fetch terminal error for %s: %s", org_uuid, exc, exc_info=True)
                    await loop.run_in_executor(
                        None,
                        lambda hs=http_status: fetcher.save_index(
                            [], status="failed",
                            error_kind="TERMINAL",
                            http_status=hs,
                            error_message=exc_message,
                        ),
                    )
                    org_results.append({
                        "org_id": org_uuid, "name": org_name,
                        "status": "failed",
                        "error_kind": "TERMINAL",
                        "http_status": http_status,
                        "error_message": exc_message,
                    })
                    yield (
                        "event",
                        _send_event({
                            "type": "org_done",
                            "org_id": org_uuid,
                            "status": "failed",
                            "error_kind": "TERMINAL",
                            "http_status": http_status,
                        }),
                    )
                    continue

                # Drain retry events from the successful list call.
                for frame in _drain_retry_events(fetcher):
                    yield ("event", frame)

                if limit:
                    conversations = conversations[:limit]

                if incremental:
                    to_fetch = [
                        c for c in conversations
                        if (org_uuid, c.get("uuid", "")) not in existing_pairs
                    ]
                else:
                    to_fetch = conversations

                org_total = len(to_fetch)
                cumulative_total += org_total

                yield (
                    "event",
                    _send_event({
                        "type": "progress",
                        "message": (
                            f"[{org_name}] Found {len(conversations)} conversations, "
                            f"fetching {org_total} new"
                        ),
                        "current": cumulative_current,
                        "total": cumulative_total,
                    }),
                )

                org_fetched = 0
                for i, conv in enumerate(to_fetch, 1):
                    uuid = conv.get("uuid", "")
                    name = conv.get("name", "Untitled")[:50]
                    cumulative_current += 1

                    yield (
                        "event",
                        _send_event({
                            "type": "progress",
                            "message": f"[{org_name}] Fetching: {name}",
                            "current": cumulative_current,
                            "total": cumulative_total,
                            "conversation_name": name,
                        }),
                    )

                    if not uuid:
                        continue

                    try:
                        full_conv = await loop.run_in_executor(
                            None, fetcher.fetch_conversation, uuid
                        )
                        if full_conv:
                            await loop.run_in_executor(
                                None, fetcher.save_conversation, full_conv
                            )
                            org_fetched += 1
                            existing_pairs.add((org_uuid, uuid))
                    except Exception as exc:
                        for frame in _drain_retry_events(fetcher):
                            yield ("event", frame)
                        kind = _classify_error(exc)
                        if kind == "AUTH":
                            if is_primary and "401" in str(exc):
                                yield ("auth", str(exc))
                                return
                            # Mid-org auth failure: stop fetching this org.
                            break
                        # Transient/Terminal on a single conversation:
                        # log and continue with next conv (existing behavior).
                        if kind == "TRANSIENT":
                            logger.warning("Transient error fetching %s: %s", name, exc)
                            yield (
                                "event",
                                _send_event({
                                    "type": "progress",
                                    "message": f"Network hiccup on {name}; skipping",
                                    "current": cumulative_current,
                                    "total": cumulative_total,
                                }),
                            )
                        else:
                            logger.error("Terminal error fetching %s: %s", name, exc, exc_info=True)
                            yield (
                                "event",
                                _send_event({
                                    "type": "progress",
                                    "message": f"Error fetching {name}: {exc}",
                                    "current": cumulative_current,
                                    "total": cumulative_total,
                                }),
                            )
                    else:
                        for frame in _drain_retry_events(fetcher):
                            yield ("event", frame)

                    if i < org_total:
                        await asyncio.sleep(0.3)

                # status: ok recorded only AFTER every conversation in this
                # org has been persisted.
                await loop.run_in_executor(None, fetcher.save_index, conversations)
                fetched_count += org_fetched
                org_results.append({
                    "org_id": org_uuid, "name": org_name,
                    "status": "ok", "fetched_count": org_fetched,
                })
                yield (
                    "event",
                    _send_event({
                        "type": "org_done",
                        "org_id": org_uuid,
                        "status": "ok",
                        "fetched_count": org_fetched,
                    }),
                )
        except Exception as exc:
            # Defensive: any unhandled exception in the per-org block.
            logger.error("Unhandled per-org error for %s: %s", org_uuid, exc, exc_info=True)
            org_results.append({
                "org_id": org_uuid, "name": org_name,
                "status": "failed",
                "error_kind": "TERMINAL",
                "http_status": None,
                "error_message": str(exc),
            })

    # If every org failed, escalate to a stream-level error so existing
    # error-toast UI still surfaces (rather than a misleading "complete").
    # A1-hunt: bucket selection moved into `_rollup_bucket_for`, which
    # switches on `error_kind` (new path) and falls back to
    # `migrate_legacy_error_code(error_code)` for any in-flight legacy record.
    successful = [r for r in org_results if r.get("status") == "ok"]
    if org_results and not successful:
        # Find the most informative failure to emit.
        first_fail = next(
            (r for r in org_results if r.get("status") in ("failed", "skipped")),
            org_results[0],
        )
        bucket, msg = _rollup_bucket_for(first_fail)
        yield (bucket, msg)
        return

    yield (
        "event",
        _send_event({
            "type": "complete",
            "message": f"Fetched {fetched_count} conversations across {len(org_results)} workspace(s).",
            "current": cumulative_total,
            "total": cumulative_total,
            "orgs": org_results,
            "primary_demoted_from": primary_demoted_from,
        }),
    )


async def _capture_phase_stream() -> AsyncGenerator[tuple[str, str | dict | None], None]:
    """Run a single capture and yield SSE frames + a terminal result tuple.

    Yields:
        ("event", sse_str)       — pass-through to client
        ("ping",  ": ping\\n\\n") — keep-alive
        ("done",  creds_dict)    — capture succeeded; caller persists creds
        ("error", str)           — capture failed; caller emits error
    """
    yield (
        "event",
        _send_event(
            {
                "type": "capture_start",
                "message": "Opening browser to log in to Claude...",
            }
        ),
    )

    try:
        async for item in _run_capture_with_keepalive():
            if isinstance(item, str):
                if item.startswith(":"):
                    yield ("ping", item)
                else:
                    yield ("event", item)
                continue
            if isinstance(item, dict):
                if "_error" in item:
                    yield ("error", classify_capture_error(str(item["_error"])))
                    return
                creds = item.get("_result")
                if not creds:
                    yield ("error", "Capture cancelled or timed out.")
                    return
                yield ("done", creds)
                return
    except Exception as exc:
        yield ("error", classify_capture_error(str(exc)))


async def refresh_pipeline_stream(
    incremental: bool = True,
    limit: int | None = None,
) -> AsyncGenerator[str, None]:
    """One-button Refresh: capture (if needed) -> fetch.

    Order of operations:
      1. If credentials are missing on disk, run capture immediately.
      2. Otherwise, attempt fetch. If fetch reports session-expired,
         run capture once and retry fetch exactly one more time.
      3. Capture is invoked at most once per request. No re-loop.
    """
    global _refresh_in_progress
    try:
        captured_already = False

        # ---- Phase 1: capture if creds missing ---------------------------
        if not DEFAULT_CREDENTIALS_PATH.exists():
            had_error = False
            async for kind, payload in _capture_phase_stream():
                if kind in ("event", "ping"):
                    yield payload
                elif kind == "done":
                    try:
                        save_credentials(payload, DEFAULT_CREDENTIALS_PATH)
                    except Exception as exc:
                        yield _send_event(
                            {
                                "type": "error",
                                "message": f"Failed to save credentials: {exc}",
                            }
                        )
                        return
                    yield _send_event(
                        {
                            "type": "capture_done",
                            "message": "Credentials captured. Fetching...",
                        }
                    )
                    captured_already = True
                elif kind == "error":
                    yield _send_event(
                        {
                            "type": "error",
                            "message": (
                                "Capture failed: "
                                + (payload or "browser closed or login timed out")
                            ),
                        }
                    )
                    had_error = True
                    break
            if had_error or not captured_already:
                return

        # ---- Phase 2: try fetch -----------------------------------------
        attempt = 1
        max_attempts = 2  # initial + post-capture retry
        while attempt <= max_attempts:
            auth_msg: str | None = None
            async for kind, payload in _fetch_phase_stream(
                incremental=incremental, limit=limit
            ):
                if kind == "event":
                    yield payload
                elif kind == "auth":
                    # Legacy "expired" alias kept for in-flight callers.
                    auth_msg = payload
                    break
                elif kind == "transient":
                    # A transient transport failure that survived the
                    # in-process retry layer (Bug A). Emit a retryable
                    # error and STOP — never trigger capture for a
                    # network blip.
                    logger.warning(
                        "Refresh stopped on transient error: %s", payload
                    )
                    yield _send_event(_build_error_event("TRANSIENT", payload or ""))
                    return
                elif kind == "fatal":
                    logger.error("Refresh fatal: %s", payload)
                    yield _send_event(_build_error_event("TERMINAL", payload or ""))
                    return

            if auth_msg is None:
                return  # success

            if captured_already or attempt >= max_attempts:
                # Already captured once this request; do not loop.
                yield _send_event(_build_error_event("AUTH", auth_msg))
                return

            # Capture once, then retry fetch.
            had_error = False
            async for kind, payload in _capture_phase_stream():
                if kind in ("event", "ping"):
                    yield payload
                elif kind == "done":
                    try:
                        save_credentials(payload, DEFAULT_CREDENTIALS_PATH)
                    except Exception as exc:
                        yield _send_event(
                            {
                                "type": "error",
                                "message": f"Failed to save credentials: {exc}",
                            }
                        )
                        return
                    yield _send_event(
                        {
                            "type": "capture_done",
                            "message": "Credentials captured. Fetching...",
                        }
                    )
                    captured_already = True
                elif kind == "error":
                    yield _send_event(
                        {
                            "type": "error",
                            "message": (
                                "Capture failed: "
                                + (payload or "browser closed or login timed out")
                            ),
                        }
                    )
                    had_error = True
                    break
            if had_error or not captured_already:
                return
            attempt += 1
    finally:
        _refresh_in_progress = False


@router.get(
    "/fetch/refresh",
    summary="One-button Refresh: capture credentials if needed, then fetch (SSE)",
    responses={
        200: {"content": {"text/event-stream": {}}},
        409: {"description": "A refresh is already in progress on this worker"},
        503: {"description": "Config file is corrupt; refuse to write to data_dir"},
    },
    # Layer 2: see /fetch/start for SSE-timing rationale. /fetch/refresh
    # is the primary user-visible writer path (sidebar Refresh button),
    # so this is the single most important gate.
    dependencies=[Depends(refuse_if_config_corrupt)],
)
async def refresh_pipeline(
    incremental: bool = True,
    # Hunt #4 (API boundaries): same bound as /fetch/start.
    limit: int | None = Query(None, ge=1, le=5000),
) -> StreamingResponse:
    """Combined capture + fetch SSE stream — Build-9 one-button Refresh.

    Returns 409 if a refresh is already in progress on this worker.
    """
    global _refresh_in_progress
    if _refresh_in_progress:
        raise HTTPException(
            status_code=409, detail="Refresh already in progress."
        )
    _refresh_in_progress = True

    return StreamingResponse(
        refresh_pipeline_stream(incremental=incremental, limit=limit),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )