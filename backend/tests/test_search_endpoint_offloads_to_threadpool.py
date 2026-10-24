"""Pin the contract that ``/api/search`` does not block the event loop.

Background (2026-05-22): the original handlers were ``async def`` but
called the synchronous ``search_conversations(...)`` inline. While a
search ran (warm ~140ms, cold ~13s), the asyncio event loop was
frozen — every other endpoint (and every other tab's search) waited.
The fix is to declare the route handlers as plain ``def`` so FastAPI
auto-routes them to its anyio threadpool.

Two complementary tests live here:

  * **Structural** — ``inspect.iscoroutinefunction(search) is False``.
    Catches a future contributor accidentally re-adding ``async``.

  * **Dynamic** — fire a deliberately-slow ``/api/search`` and a
    concurrent ``/api/health``; assert health resolves quickly while
    search is mid-flight. Pins the event-loop-non-blocking *behavior*,
    which the structural test alone can't catch (someone could re-add
    ``async def`` plus ``await asyncio.to_thread(...)`` and the
    structural test would pass; the dynamic test would still pass
    too — both forms are valid).

Why both: the structural test is the cheap-and-deterministic regression
guard; the dynamic test catches behavior regressions like ``def`` +
``time.sleep()`` inadvertently re-added to a path *inside*
``search_conversations`` itself (which would still block via the
threadpool only if pool exhausted, but it pins the broader
event-loop-yields-promptly invariant the user depends on).
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from unittest.mock import patch

import httpx
import pytest

from backend.routers.search import search as search_get_handler
from backend.routers.search import search_post as search_post_handler

# Bounds only a failing run: in parallel the barrier opens at once.
BARRIER_TIMEOUT_SEC = 10.0

# --------------------------------------------------------------------------- #
# Structural — handler signature contract                                     #
# --------------------------------------------------------------------------- #


def test_search_get_handler_offloads_via_asyncio_to_thread() -> None:
    """GET ``/api/search`` MUST offload the sync search to a threadpool.

    Two acceptable shapes (both protect the event loop):

      1. ``def search(...)`` — FastAPI auto-routes sync handlers to its
         anyio threadpool. The 2026-05-22 Wave 1 fix used this shape.

      2. ``async def search(...)`` that wraps the sync
         ``search_conversations`` call in ``asyncio.to_thread(...)``.
         Wave 2 (2026-05-22) adopted this shape so the handler can
         ALSO race against ``request.is_disconnected()`` and bail out
         when the client cancels mid-search.

    What this test catches: an ``async def`` handler that calls sync
    ``search_conversations(...)`` INLINE — that would freeze the
    event loop for the warm-path ~140ms / cold-path ~13s of the
    search, blocking every other tab's search, every health check,
    every list/detail fetch.

    Detection: read the handler source. If it's async, REQUIRE it to
    contain `asyncio.to_thread`. If it's sync, any body is fine
    (FastAPI off-threads it automatically).
    """
    src = inspect.getsource(search_get_handler)
    if inspect.iscoroutinefunction(search_get_handler):
        assert "to_thread" in src or "run_in_executor" in src, (
            "async GET /api/search handler must wrap search_conversations "
            "in asyncio.to_thread (or equivalent) — otherwise the sync "
            "search call freezes the event loop. Found source:\n" + src
        )


def test_search_post_handler_offloads_via_asyncio_to_thread() -> None:
    """POST ``/api/search`` — same contract as the GET handler.

    The POST variant is hit by the frontend whenever the active-filter
    UI passes a non-trivial conversation_uuids set. Same event-loop-
    blocking concern; same two acceptable shapes.
    """
    src = inspect.getsource(search_post_handler)
    if inspect.iscoroutinefunction(search_post_handler):
        assert "to_thread" in src or "run_in_executor" in src, (
            "async POST /api/search handler must wrap search_conversations "
            "in asyncio.to_thread (or equivalent). Found source:\n" + src
        )


# --------------------------------------------------------------------------- #
# Dynamic — event-loop-not-blocked behavior                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_three_concurrent_searches_run_in_parallel(
    real_async_client: httpx.AsyncClient,
) -> None:
    """Concurrent ``/api/search`` requests MUST run in parallel, not serialize.

    This pins the user's "multiple frontend instances per backend"
    requirement: two browser tabs (or any two concurrent clients) must
    not block each other while searching.

    Methodology: a barrier, not a clock (2026-10-23).
      * Monkeypatch ``backend.routers.search.search_conversations`` with a
        sync stand-in that waits at ``threading.Barrier(3)``.
      * Fire THREE concurrent ``/api/search`` requests via
        ``asyncio.gather``.
      * In parallel (threadpool), all three reach the barrier and it opens
        at once. Serialized (sync work on the event loop), only one request
        can wait at a time: the barrier times out and breaks, and every
        request fails.

    The pass does not depend on timing. The barrier timeout bounds only
    how long a failing run takes. The test used to assert that three
    1-second sleeps finished in under 2.0 s; a slow runner took 2.11 s.

    Why three concurrent (not "search + health"):
      A single search + a single health-check is NOT discriminating —
      the trivial health endpoint can return early via loop mechanics
      that don't actually prove the search isn't blocking. THREE
      concurrent same-handler hits is the cleanest probe.
    """
    from backend.models import SearchResponse

    barrier = threading.Barrier(3, timeout=BARRIER_TIMEOUT_SEC)

    def _search_that_needs_company(*_args, **_kwargs):
        """Sync, like the real ``search_conversations``: the bug under
        test is sync work on the asyncio loop."""
        barrier.wait()
        return SearchResponse()

    with patch("backend.routers.search.search_conversations", side_effect=_search_that_needs_company):
        responses = await asyncio.gather(
            real_async_client.get("/api/search", params={"q": "a"}),
            real_async_client.get("/api/search", params={"q": "b"}),
            real_async_client.get("/api/search", params={"q": "c"}),
            return_exceptions=True,
        )

    statuses = [getattr(r, "status_code", repr(r)) for r in responses]
    assert statuses == [200, 200, 200], (
        f"3 concurrent /api/search requests did not all reach the search at "
        f"once: {statuses}. The event loop is likely blocked: check that the "
        f"search handlers off-thread search_conversations."
    )
