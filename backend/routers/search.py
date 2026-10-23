"""Search router.

Two transports supported (2026-05-14, sidebar-scope propagation):

  * **GET /api/search?...** — existing query-string contract. All scope
    knobs accepted as query params. CSV (``conversation_uuids=a,b,c``)
    is supported but kept for small scopes; large active-filter sets
    blow past h11 / proxy URL-length limits.

  * **POST /api/search** — new variant. Same scope knobs in a JSON body.
    The frontend defaults to POST when it has a ``conversation_uuids``
    list to send; this avoids 414/431 at proxies and dodges SQLite's
    ``SQLITE_MAX_VARIABLE_NUMBER`` (often 999) on the underlying
    ``IN (...)`` clauses.

Both methods delegate to :func:`backend.search.search_conversations` with
identical kwargs — only the transport differs.
"""

import asyncio
import logging
from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..deps import get_store
from ..models import SearchResponse
from ..store import ConversationStore
from ..search import search_conversations

log = logging.getLogger("uvicorn.error")

# Poll interval for client-disconnect detection (2026-05-22). Smaller
# values bail out faster but cost more event-loop wakeups while a
# search runs. 100ms is the typical-network-round-trip ballpark and
# matches the user's "feels instant on cancel" target.
_DISCONNECT_POLL_INTERVAL = 0.1


async def _run_search_with_disconnect(
    request: Request,
    do_search: Callable[[], SearchResponse],
) -> SearchResponse:
    """Offload a sync search to the threadpool, race against client disconnect.

    Returns the search response on success. Raises ``HTTPException(499)``
    on client disconnect — the connection is already gone, so this
    surfaces a non-2xx status to the request-timing middleware and
    closes the request lifecycle promptly.

    The threadpool task is NOT interrupted on cancellation — Python's
    ``asyncio.to_thread`` has no kill-the-thread primitive. The
    underlying ``search_conversations`` call runs to natural completion
    in the background and its return value is discarded. This is
    bounded backend CPU waste (one full search per abandoned client);
    truly stopping the SQL mid-flight requires sqlite3 interrupt(),
    deferred to a future commit. See test_search_client_disconnect.py
    for the contract this fixture pins.
    """
    search_task = asyncio.create_task(asyncio.to_thread(do_search))

    while not search_task.done():
        try:
            await asyncio.wait_for(
                asyncio.shield(search_task),
                timeout=_DISCONNECT_POLL_INTERVAL,
            )
        except asyncio.TimeoutError:
            pass

        if search_task.done():
            break

        if await request.is_disconnected():
            log.info("client disconnected mid-search; bailing out")
            # asyncio.shield kept the task alive across the timeout, so
            # the threadpool worker is still running. We don't await it
            # here — it'll finish in the background and its result is
            # discarded by the GC.
            raise HTTPException(
                status_code=499,
                detail="Client closed request",
            )

    return search_task.result()


# HTTP route LIMIT. Keeps the FTS5 fast path bounded so the snippet()
# pass stays in the ~140 ms ballpark on the user's corpus. The MCP
# server uses a higher LIMIT (5000) for programmatic / LLM consumers
# that can usefully reason about broader result sets; the difference is
# wired in via the per-route ``limit=`` kwarg on
# ``search_conversations`` (plan §C).
HTTP_SEARCH_LIMIT = 1000

router = APIRouter(tags=["search"])


def _parse_csv_uuid_set(raw: str | None) -> set[str] | None:
    """Parse a CSV string into a set of UUIDs, mirroring the bookmarks parser.

    * ``None`` → ``None`` (no constraint)
    * ``""`` → ``set()`` (filter excludes everything; caller short-circuits)
    * ``"a,b,c"`` → ``{"a", "b", "c"}``
    * Whitespace around tokens is stripped; empty tokens are dropped.
    """
    if raw is None:
        return None
    return {u.strip() for u in raw.split(",") if u.strip()} if raw.strip() else set()


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Search across conversations (query-string form)",
)
# 2026-05-22 (Wave 2): ``async def`` + ``asyncio.to_thread`` inside the
# helper. The earlier ``def search(...)`` form correctly off-threaded to
# FastAPI's anyio threadpool, but it had no way to notice when the
# client disconnected mid-search — the sync handler returned only
# AFTER ``search_conversations`` completed (~13 s cold-path waste per
# abandoned keystroke). Async wrapper races the search task against
# ``request.is_disconnected()`` polling and raises 499 on disconnect.
# Parallelism is preserved because ``asyncio.to_thread`` uses the same
# anyio threadpool the sync handler would have used. The dynamic test
# ``test_three_concurrent_searches_run_in_parallel`` keeps the
# parallel-execution contract pinned.
async def search(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = Query(
        "all", description="Filter by source"
    ),
    context_size: Literal["snippet", "full"] = Query(
        "snippet", description="Amount of context per match"
    ),
    sort: Literal["updated_at", "created_at", "name", "project"] = Query(
        "updated_at", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query(
        "desc", description="Sort order"
    ),
    conversation_uuid: str | None = Query(
        None, description="Restrict search to this conversation"
    ),
    project_path: str | None = Query(
        None, description="Restrict to conversations with this project_path"
    ),
    bookmarks: str | None = Query(
        None,
        description="Comma-separated UUIDs to restrict search to bookmarked conversations",
    ),
    organization_id: str | None = Query(
        None,
        description=(
            "Workspace UUID — restrict to conversations whose organization_id "
            "matches exactly. None means no constraint."
        ),
    ),
    conversation_uuids: str | None = Query(
        None,
        description=(
            "Comma-separated UUIDs to restrict search to a specific set "
            "(used by the UI to honor the active-filter graph). "
            "None means no constraint; empty string means the active "
            "filter excludes everything (returns []). For large sets, "
            "prefer POST /api/search with a JSON body — GET has URL-length "
            "limits at proxies and h11."
        ),
    ),
    include_tool_calls: bool = Query(
        True,
        description=(
            "When False, search ignores tool_use / tool_result / thinking "
            "blocks so the sidebar only shows results whose owning message "
            "is rendered in the conversation pane (mirrors the UI's "
            "showToolCalls toggle). Default True preserves backward "
            "compat for external scripts hitting /api/search directly."
        ),
    ),
    include_compactions: bool = Query(
        True,
        description=(
            "When False, search ignores hits inside compaction-summary "
            "rows (isCompactSummary) so the sidebar only shows results "
            "whose owning message is rendered in the conversation pane "
            "(mirrors the UI's 'Show Compactions' toggle in the "
            "conversation header). Default True preserves backward "
            "compat for external scripts hitting /api/search directly."
        ),
    ),
    store: ConversationStore = Depends(get_store),
) -> SearchResponse:
    """Search across all conversations (GET form)."""
    bookmark_set = _parse_csv_uuid_set(bookmarks)
    conversation_uuids_set = _parse_csv_uuid_set(conversation_uuids)
    return await _run_search_with_disconnect(
        request,
        lambda: search_conversations(
            store,
            q,
            source=source,
            context_size=context_size,
            sort=sort,
            sort_order=sort_order,
            conversation_uuid=conversation_uuid,
            project_path=project_path,
            bookmarks=bookmark_set,
            include_tool_calls=include_tool_calls,
            include_compactions=include_compactions,
            organization_id=organization_id,
            conversation_uuids=conversation_uuids_set,
            limit=HTTP_SEARCH_LIMIT,
        ),
    )


class SearchRequest(BaseModel):
    """JSON body for ``POST /api/search``.

    Mirrors the GET query params, but with native types: ``bookmarks``
    and ``conversation_uuids`` are JSON arrays (not CSV) — that's the
    whole point of having a POST variant. Spec §2 (2026-05-14, Council
    convergence): GET CSV blows past URL-length limits at ~1500 UUIDs.
    """

    # Hunt #6: forbid unknown top-level fields. The typical Query-endpoint
    # default of ``extra='ignore'`` would silently drop a typo on an
    # optional field — e.g. ``{"q": "x", "sort_orderr": "asc"}`` collapses
    # to the ``sort_order="desc"`` default, so the user gets results in
    # the OPPOSITE order they asked for with no signal at all. This is a
    # wrong-data-no-signal bug; forbid converts it to a 422 at the wire
    # boundary. Local single-user app, no documented external HTTP
    # callers (mcp_server uses ``backend.search.search_conversations`` as
    # a direct Python import — see mcp_server/server.py line ~32), so
    # tightening the contract is safe. See
    # ``test_post_search_unknown_field_returns_422``.
    model_config = ConfigDict(extra="forbid")

    q: str = Field(..., min_length=1, description="Search query")
    source: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all"
    context_size: Literal["snippet", "full"] = "snippet"
    sort: Literal["updated_at", "created_at", "name", "project"] = "updated_at"
    sort_order: Literal["asc", "desc"] = "desc"
    conversation_uuid: str | None = None
    project_path: str | None = None
    bookmarks: list[str] | None = Field(None, max_length=5000)
    organization_id: str | None = None
    # Hunt #4 (API boundaries): cap at 5000 to bound work in the
    # filter pipeline. The frontend's active-filter UI never sends
    # more than the visible-list size (~1500 at the 99th percentile);
    # 5000 gives 3x headroom AND keeps the underlying SQLite IN(...)
    # clause well under SQLITE_MAX_VARIABLE_NUMBER. Without this cap,
    # a 100k-UUID POST takes ~21s.
    conversation_uuids: list[str] | None = Field(None, max_length=5000)
    include_tool_calls: bool = True
    # 2026-05-26 (v13): "Show Compactions" toggle plumbed via the same
    # mechanism as ``include_tool_calls``. Default True for backward
    # compat. See backend.search.search_conversations docstring.
    include_compactions: bool = True


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Search across conversations (JSON-body form; for large UUID sets)",
)
# ``async def`` + ``asyncio.to_thread`` — see the matching note on the
# GET handler above. Same parallelism + disconnect-detection rationale.
async def search_post(
    request: Request,
    body: SearchRequest,
    store: ConversationStore = Depends(get_store),
) -> SearchResponse:
    """Search across all conversations (POST form).

    Identical semantics to the GET endpoint; the only difference is
    transport. Used by the UI when the active-filter set is large
    enough that GET CSV would risk a 414/431 at proxies. The same
    `search_conversations()` internals back both paths so any
    per-method drift is caught by ``test_post_search_get_parity_*``.
    """
    return await _run_search_with_disconnect(
        request,
        lambda: search_conversations(
            store,
            body.q,
            source=body.source,
            context_size=body.context_size,
            sort=body.sort,
            sort_order=body.sort_order,
            conversation_uuid=body.conversation_uuid,
            project_path=body.project_path,
            bookmarks=set(body.bookmarks) if body.bookmarks is not None else None,
            include_tool_calls=body.include_tool_calls,
            include_compactions=body.include_compactions,
            organization_id=body.organization_id,
            conversation_uuids=(
                set(body.conversation_uuids)
                if body.conversation_uuids is not None
                else None
            ),
            limit=HTTP_SEARCH_LIMIT,
        ),
    )
