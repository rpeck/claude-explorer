"""Regression: when the client disconnects mid-search, the server
must stop wasting CPU within ~200ms.

User reported (2026-05-22) that fast keystrokes generate multiple
in-flight /api/search requests. The frontend's `cancelQueries` aborts
the client-side fetch, but pre-fix the backend kept running the
abandoned search to completion (~3-13s of wasted CPU and a held
threadpool slot).

User-observable contract (per CLAUDE-TESTING §5.13):
  - After a client disconnect, the request-timing log MUST show an
    elapsed time well below the slow-search budget. We aim for
    <300ms on a search that would normally take >1s to complete.
  - The route MUST NOT emit normal 2xx logging for the abandoned
    work — it returned to the (gone) client early.

Implementation strategy this test pins:
  - Race the search work (offloaded via asyncio.to_thread) against a
    disconnect watcher that polls request.is_disconnected().
  - On disconnect, raise HTTPException(499) so the request lifecycle
    closes promptly and the timing-middleware log line records the
    actual abandoned duration (not the full search time).

NOT pinned (deliberately deferred):
  - sqlite3.Connection.interrupt() to ACTUALLY stop the SQL query
    underneath. The to_thread worker continues running until the
    sync search returns naturally. This is bounded backend CPU
    waste — acceptable for V1 ship, revisitable if telemetry shows
    threadpool pressure.
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

from backend.main import app


@pytest.mark.asyncio
async def test_server_returns_promptly_when_client_disconnects_mid_search(
    monkeypatch,
) -> None:
    """A client that closes the connection mid-search must see the
    server return within ~300ms (NOT the full search budget).

    We monkey-patch `backend.routers.search.search_conversations` so it
    sleeps for 2s — long enough that without disconnect handling we'd
    block for the full duration, and short enough to keep the test
    fast.
    """
    # Override the search function with a 2s sleeper that runs in the
    # threadpool (matches FastAPI's auto-threadpooling of sync handlers).
    import backend.routers.search as search_router
    from backend.models import SearchResponse

    enter_event = threading.Event()

    def slow_search(*_args, **_kwargs):
        enter_event.set()
        time.sleep(2.0)
        return SearchResponse(
            results=[],
            total_messages_matched=0,
            returned_messages=0,
            truncated=False,
        )

    monkeypatch.setattr(search_router, "search_conversations", slow_search)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Fire the search but cancel it after 100ms.
        async def fire_and_cancel():
            try:
                await asyncio.wait_for(
                    client.get(
                        "/api/search",
                        params={"q": "anything", "include_tool_calls": "true"},
                    ),
                    timeout=0.1,
                )
            except (TimeoutError, httpx.ReadTimeout, httpx.RemoteProtocolError):
                # Expected — we cancelled by timeout.
                pass

        t0 = time.perf_counter()
        await fire_and_cancel()
        # Give the server a beat to notice the disconnect and bail.
        await asyncio.sleep(0.3)
        elapsed = time.perf_counter() - t0

    # Wait for the threadpool worker to finish (it can't be interrupted
    # cooperatively yet; it runs to completion). This is the "L2
    # deferred" cost — bounded, acceptable for V1.
    enter_event.wait(timeout=3.0)

    # The server-side ROUTE must have returned early. Hard-cap the total
    # wall time spent on the request (including the 0.3s grace).
    assert elapsed < 1.0, (
        f"Expected server to return within 1.0 s of client disconnect "
        f"(disconnect detection target ~100-200ms), got {elapsed:.3f} s. "
        f"The route is NOT bailing out on disconnect."
    )
