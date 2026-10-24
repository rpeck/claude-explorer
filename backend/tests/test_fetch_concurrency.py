"""Concurrency / 409-on-second-request semantics for /api/fetch/refresh.

The ``/api/fetch/refresh`` endpoint serializes refresh attempts via a
module-level boolean (``_refresh_in_progress``). An earlier version
also declared a never-acquired ``asyncio.Lock`` next to the flag; it
was removed in Council A1 (2026-05-21) as dead code — the boolean
check-then-set under the single-threaded asyncio event loop is the
actual mechanism, and the streamer's ``finally`` block clears the
flag when the stream ends. This test validates THAT mechanism: a
second concurrent request returns 409 Conflict; once the first
stream completes, the flag is cleared and a third sequential request
returns 200.

Spec: ``PLANS/2026.05.07-frontend-api-contract.md`` (clause BKM-FETCH-409).
Plan: ``PLANS/2026.05.08 BACKEND TEST PLAN.md`` (P2.3, Tier 4 Concurrency).
Targets:
    * ``backend/routers/fetch.py:1085-1108`` — the route handler with
      its check-then-set on ``_refresh_in_progress`` and the
      ``StreamingResponse`` return.
    * ``backend/routers/fetch.py:1081-1082`` — the streamer's
      ``finally`` block that resets the flag once the stream ends.

Per TESTING.md §5.7 (concurrency tests) + §5.8 (lock-under-
contention template).

Allowlist for spec-driven authoring (TESTING.md §1):
    * ``backend/routers/fetch.py``
    * ``backend/main.py`` (verify ``/api`` prefix)
    * ``backend/tests/conftest.py`` (the P0 fixtures)
    * ``PLANS/2026.05.08 BACKEND TEST PLAN.md``
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from starlette.requests import ClientDisconnect

import backend.routers.fetch as fetch_mod


@pytest.mark.asyncio
async def test__get_fetch_refresh__second_concurrent_request__returns_409(
    real_async_client,
    _isolated_credentials_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BKM-FETCH-409: while one ``/api/fetch/refresh`` stream is in
    flight, a second concurrent GET returns 409 Conflict; after the
    first finishes, a third sequential GET returns 200.

    Validates the flag-based serialization at ``fetch.py:1095-1099``
    and the streamer's ``finally``-block reset at ``fetch.py:1081-1082``.

    Determinism: the test uses ``asyncio.create_task`` + an
    ``asyncio.Event`` barrier (``started``) to GUARANTEE the first
    request has acquired the flag before the second request fires —
    much more stable on slow CI than ``asyncio.gather``. The literal
    plan-doc invariant ``sorted([resp1, resp2]) == [200, 409]`` is
    preserved as a final consistency check on the same response
    objects.
    """
    # Two events orchestrate the patched streamer:
    #   * ``started`` — set by the patched stream AFTER it has yielded
    #     its first frame. By the time this fires, the route handler
    #     has flipped ``_refresh_in_progress = True`` and the response
    #     status (200) has been materialized at the httpx client.
    #   * ``release`` — when set by the test, the patched stream emits
    #     its terminal frame, exits, and (via ``finally``) clears the
    #     flag.
    started = asyncio.Event()
    release = asyncio.Event()

    async def patched_stream(
        incremental: bool = True, limit: int | None = None
    ) -> AsyncGenerator[str, None]:
        # Yield ONE frame BEFORE awaiting ``release`` so the response's
        # 200 status is materialized at the httpx client before the
        # second concurrent request reaches the route. The real
        # streamer similarly emits a ``start`` event before any
        # awaitable work.
        try:
            yield 'data: {"type": "start"}\n\n'
            started.set()
            await release.wait()
            yield 'data: {"type": "complete"}\n\n'
        finally:
            # Mirror ``fetch.py:1081-1082`` (the real streamer clears
            # the flag in its own ``finally``). If we omit this, the
            # third sequential request below would erroneously 409
            # because the autouse ``reset_refresh_flag`` fixture only
            # runs between tests, not inside one.
            fetch_mod._refresh_in_progress = False

    # ``refresh_pipeline_stream`` is looked up by bare name in
    # ``backend.routers.fetch``'s module globals at call time, so
    # rebinding the module attribute intercepts the route handler's
    # call without touching ``StreamingResponse``.
    monkeypatch.setattr(fetch_mod, "refresh_pipeline_stream", patched_stream)

    # ---------- Phase 1: first request enters the streamer ----------
    task1 = asyncio.create_task(real_async_client.get("/api/fetch/refresh"))
    try:
        # ``started`` proves the handler ran, set the flag, and yielded
        # one frame. ``httpx.AsyncClient.get`` eagerly drains the body,
        # so ``task1`` will remain suspended on ``await release.wait()``
        # inside the patched generator.
        await asyncio.wait_for(started.wait(), timeout=3.0)

        # ---------- Phase 2: second concurrent request → 409 -------
        # The route handler's check-then-set on ``_refresh_in_progress``
        # has no ``await`` between read and write, so this is race-free
        # under single-threaded asyncio.
        resp2 = await real_async_client.get("/api/fetch/refresh")
        assert resp2.status_code == 409, (
            f"Expected 409 while refresh in progress; got "
            f"{resp2.status_code}: {resp2.text}"
        )
        body2 = resp2.json()
        assert "already in progress" in body2.get("detail", ""), (
            f"Expected canonical 409 detail per fetch.py:1097; "
            f"got {body2!r}"
        )

        # Sanity: first request must still be blocked on ``release``.
        # If ``task1`` is already done here, the patched stream did not
        # actually intercept the route — flag the failure mode loudly.
        assert not task1.done(), (
            "First request unexpectedly completed before release.set(); "
            "the patched stream may not be intercepting the route."
        )
    finally:
        # Guarantee ``task1`` can exit even if an assertion above
        # fails; otherwise pytest-asyncio raises ``Task was destroyed
        # but it is pending`` on teardown, masking the real failure.
        release.set()

    resp1 = await task1
    assert resp1.status_code == 200, (
        f"First (in-flight) request should return 200; got "
        f"{resp1.status_code}: {resp1.text}"
    )

    # Literal plan-doc invariant from
    # ``PLANS/2026.05.08 BACKEND TEST PLAN.md`` P2.3: the {200, 409}
    # multiset must hold across the two concurrent requests,
    # regardless of which one arrived "first".
    assert sorted([resp1.status_code, resp2.status_code]) == [200, 409], (
        f"Expected sorted statuses [200, 409]; got "
        f"{sorted([resp1.status_code, resp2.status_code])}"
    )

    # ---------- Phase 3: flag cleared → third request → 200 -------
    # The streamer's ``finally`` block already cleared the flag when
    # ``task1`` resolved above; a fresh sequential request must
    # therefore succeed.
    started.clear()
    release.clear()
    task3 = asyncio.create_task(real_async_client.get("/api/fetch/refresh"))
    try:
        await asyncio.wait_for(started.wait(), timeout=3.0)
    finally:
        release.set()

    resp3 = await task3
    assert resp3.status_code == 200, (
        f"Third (sequential) request should return 200 after the "
        f"flag clears; got {resp3.status_code}: {resp3.text}"
    )


# ---------------------------------------------------------------------------
# PR #8 review (2026-10-21): /fetch/start (the Details modal's "Fetch New"
# and "Full Refresh") had no guard. Two read-merge-replace cycles could run
# at once and race on the same audit.jsonl. Both routes now share one flag.
# ---------------------------------------------------------------------------


async def _within(coro, seconds: float = 3.0):
    """Await ``coro`` or fail: a request that hangs is a failed guard."""
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except TimeoutError:
        pytest.fail("the request hung: a second fetch stream was opened")


def _blocking_stream(started: asyncio.Event, release: asyncio.Event):
    """A stand-in stream that does NOT clear the flag itself: the route must."""

    async def stream(
        incremental: bool = True, limit: int | None = None
    ) -> AsyncGenerator[str, None]:
        yield 'data: {"type": "start"}\n\n'
        started.set()
        await release.wait()
        yield 'data: {"type": "complete"}\n\n'

    return stream


@pytest.mark.asyncio
async def test__fetch_start__blocks_a_second_fetch_and_a_refresh__returns_409(
    real_async_client,
    _isolated_credentials_path,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    monkeypatch.setattr(
        fetch_mod, "fetch_conversations_stream", _blocking_stream(started, release)
    )
    monkeypatch.setattr(fetch_mod, "fetch_output_dir", lambda _v=tmp_path / "conversations": _v)

    task1 = asyncio.create_task(real_async_client.get("/api/fetch/start"))
    try:
        await asyncio.wait_for(started.wait(), timeout=3.0)

        # Without the guard the second request opens a second stream that
        # waits on ``release`` too: the timeout turns that hang into a failure.
        second = await _within(real_async_client.get("/api/fetch/start?incremental=false"))
        assert second.status_code == 409, second.text
        assert "already in progress" in second.json()["detail"]

        refresh = await _within(real_async_client.get("/api/fetch/refresh"))
        assert refresh.status_code == 409, refresh.text

        # The web page asks /fetch/status why its stream did not open.
        status = await real_async_client.get("/api/fetch/status")
        assert status.status_code == 200
        assert status.json()["fetch_in_progress"] is True

        assert not task1.done()
    finally:
        release.set()

    first = await task1
    assert first.status_code == 200
    # The route cleared the flag when the stream ended.
    assert fetch_mod._refresh_in_progress is False
    status = await real_async_client.get("/api/fetch/status")
    assert status.json()["fetch_in_progress"] is False

    started.clear()
    release.clear()
    task3 = asyncio.create_task(real_async_client.get("/api/fetch/start"))
    try:
        await asyncio.wait_for(started.wait(), timeout=3.0)
    finally:
        release.set()
    assert (await task3).status_code == 200


@pytest.mark.asyncio
async def test__fetch_refresh__in_flight__fetch_start_returns_409(
    real_async_client,
    _isolated_credentials_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def refresh_stream(
        incremental: bool = True, limit: int | None = None
    ) -> AsyncGenerator[str, None]:
        try:
            yield 'data: {"type": "start"}\n\n'
            started.set()
            await release.wait()
            yield 'data: {"type": "complete"}\n\n'
        finally:
            fetch_mod._refresh_in_progress = False  # as the real streamer does

    monkeypatch.setattr(fetch_mod, "refresh_pipeline_stream", refresh_stream)
    fetch_calls: list[bool] = []

    async def fetch_stream(
        incremental: bool = True, limit: int | None = None
    ) -> AsyncGenerator[str, None]:
        fetch_calls.append(incremental)
        yield 'data: {"type": "complete"}\n\n'

    monkeypatch.setattr(fetch_mod, "fetch_conversations_stream", fetch_stream)

    task1 = asyncio.create_task(real_async_client.get("/api/fetch/refresh"))
    try:
        await asyncio.wait_for(started.wait(), timeout=3.0)
        resp = await _within(real_async_client.get("/api/fetch/start"))
        assert resp.status_code == 409, resp.text
        assert fetch_calls == []  # the stream never started
    finally:
        release.set()
    assert (await task1).status_code == 200

    # Refresh finished: Fetch New works again.
    resp = await real_async_client.get("/api/fetch/start")
    assert resp.status_code == 200
    assert fetch_calls == [True]


# ---------------------------------------------------------------------------
# A client that leaves early must not keep the flag set (PR #6 follow-up,
# 2026-10-21). The handler sets the flag, and before this fix only the
# stream's own ``finally`` cleared it. When the browser left before the
# first chunk, Starlette never started the stream, so that ``finally`` never
# ran: every later fetch and Refresh got 409 until a restart.
# ---------------------------------------------------------------------------


def _scope() -> dict:
    return {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "GET", "path": "/"}


async def _never_disconnects() -> dict:
    await asyncio.sleep(3600)
    return {"type": "http.disconnect"}


def _send_failing_after(ok_messages: int):
    """An ASGI ``send`` whose client goes away after ``ok_messages`` messages."""
    sent: list[dict] = []

    async def send(message: dict) -> None:
        if len(sent) >= ok_messages:
            raise OSError("client went away")
        sent.append(message)

    return send


async def _open_route(route: str):
    if route == "start":
        return await fetch_mod.fetch_conversations(incremental=True, limit=None)
    return await fetch_mod.refresh_pipeline(incremental=True, limit=None)


async def _let_finalizers_run() -> None:
    import gc

    gc.collect()
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["start", "refresh"])
async def test__client_leaves_before_the_first_chunk__flag_is_cleared(
    route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_mod, "_refresh_in_progress", False)
    response = await _open_route(route)
    assert fetch_mod._refresh_in_progress is True

    # The response start already fails: the stream never starts.
    # Starlette reports the failed send as ClientDisconnect, as for a real client.
    with pytest.raises(ClientDisconnect):
        await response(_scope(), _never_disconnects, _send_failing_after(0))

    assert fetch_mod._refresh_in_progress is False, (
        "a client that left before the first chunk kept the fetch flag set"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["start", "refresh"])
async def test__client_leaves_mid_stream__old_stream_never_clears_a_new_fetch_flag(
    route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def endless(
        incremental: bool = True, limit: int | None = None
    ) -> AsyncGenerator[str, None]:
        try:
            while True:
                yield 'data: {"type": "progress"}\n\n'
                await asyncio.sleep(0)
        finally:
            if route == "refresh":
                fetch_mod._refresh_in_progress = False  # as the real streamer does

    monkeypatch.setattr(fetch_mod, "fetch_conversations_stream", endless)
    monkeypatch.setattr(fetch_mod, "refresh_pipeline_stream", endless)
    monkeypatch.setattr(fetch_mod, "_refresh_in_progress", False)

    response = await _open_route(route)
    # The response start and one chunk go out, then the client leaves.
    # Starlette reports the failed send as ClientDisconnect, as for a real client.
    with pytest.raises(ClientDisconnect):
        await response(_scope(), _never_disconnects, _send_failing_after(2))
    assert fetch_mod._refresh_in_progress is False

    # A new fetch takes the flag. The abandoned stream, when Python
    # finalizes it, must not clear it.
    fetch_mod._refresh_in_progress = True
    await _let_finalizers_run()
    assert fetch_mod._refresh_in_progress is True, (
        "the abandoned stream cleared the flag of a newer fetch"
    )
