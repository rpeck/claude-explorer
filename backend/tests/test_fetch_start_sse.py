"""SSE event-order/types/payload/termination tests for ``GET /api/fetch/start``.

Targets ``backend/routers/fetch.py:188-360`` (the ``fetch_conversations_stream``
generator) and ``:442-462`` (the route handler that wraps it in
``StreamingResponse``).

Wire-format reality (see ``TESTING.md`` 5.6 + the P2 plan in
``PLANS/2026.05.08 BACKEND TEST PLAN.md``): the server emits ONLY
``data: {json}\\n\\n`` frames. There are no ``event:`` headers; the
discriminator is ``payload["type"]``. Tests parse via
:func:`backend.tests.conftest.collect_sse_data_events`.

Asymmetry under test (clause BKM-FETCH-START-ERR-LEGACY): the
``/api/fetch/start`` error envelope is the LEGACY ``{type, message}``
shape (``fetch.py:328-330``, ``:356-360``) -- NOT the
``{kind, retryable, message}`` envelope used by ``/api/fetch/refresh``
fetch-phase errors at ``fetch.py:112-119``. We assert the actual
contract here, including the negative-space absence of ``kind`` and
``retryable``, so a future "let's unify the envelopes" refactor breaks
loudly rather than silently changing the wire shape under in-flight
clients.

Allowlist while authoring (per ``TESTING.md`` 1):
``backend/routers/fetch.py``, ``backend/tests/conftest.py``,
``backend/tests/test_refresh_pipeline.py`` (existing patterns),
``PLANS/2026.05.08 BACKEND TEST PLAN.md``,
``PLANS/2026.05.07-frontend-api-contract.md`` (clause IDs).
No frontend code reads while writing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.routers.fetch import SESSION_EXPIRED_MESSAGE
from backend.tests.conftest import collect_sse_data_events


# ---------------------------------------------------------------------------
# Test creds + FakeFetcher (HTTP-boundary mock per CLAUDE-TESTING 5.2)
# ---------------------------------------------------------------------------

# The ClaudeFetcher constructor validates ``primary_org_id in {o["uuid"] for
# o in orgs}``; this v2 creds blob satisfies that on the load_credentials path.
_TEST_ORG_ID = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"


def _v2_creds(*, session_key: str = "sk-test", org_id: str = _TEST_ORG_ID) -> dict:
    """A minimal v2-shape credentials dict suitable for ``load_credentials``."""

    return {
        "schema_version": 2,
        "session_key": session_key,
        "cf_bm": None,
        "cf_clearance": None,
        "captured_at": "2026-05-01T00:00:00+00:00",
        "orgs": [
            {
                "uuid": org_id,
                "name": None,
                "capabilities": [],
                "seen_in_response": False,
            }
        ],
        "primary_org_id": org_id,
        "legacy_migration_target": org_id,
        "org_id": org_id,
    }


class _FakeFetcher:
    """Boundary-mock for :class:`fetcher.bulk_fetch.ClaudeFetcher`.

    Exposes only the surface area the ``/api/fetch/start`` path uses
    (see ``backend/routers/fetch.py:228-354``):
    constructor; ``existing_uuids_for_current_org``;
    ``fetch_conversation_list``; ``fetch_conversation``;
    ``save_conversation``; ``save_index``; ``retry_events`` attr.

    Per-test customization is via class-attribute hooks set by the test
    BEFORE the request is fired -- shared across instances within a
    single test (the route only constructs ONE fetcher per request).
    """

    # Test hooks (set in tests).
    list_result: list[dict] = []
    list_raises: BaseException | None = None
    fetch_raises: BaseException | None = None
    fetch_result: dict | None = None

    # Recording (zeroed at construction).
    save_calls: list[dict] = []
    index_calls: list[list[dict]] = []

    def __init__(self, **_kwargs: Any) -> None:
        # Matches the kwargs the route passes (line 229-241 / 589-601).
        # We accept arbitrary kwargs because that surface is internal
        # plumbing, not a contract under test.
        self.retry_events: list[dict] = []
        # Reset per-instance recordings so tests don't bleed.
        type(self).save_calls = []
        type(self).index_calls = []

    def existing_uuids_for_current_org(self) -> set[str]:
        return set()

    def fetch_conversation_list(self) -> list[dict]:
        if type(self).list_raises is not None:
            raise type(self).list_raises
        return list(type(self).list_result)

    def fetch_conversation(self, uuid: str) -> dict | None:
        if type(self).fetch_raises is not None:
            raise type(self).fetch_raises
        if type(self).fetch_result is not None:
            return dict(type(self).fetch_result, uuid=uuid)
        return {"uuid": uuid, "name": f"Conv {uuid}", "chat_messages": []}

    def save_conversation(self, conv: dict) -> None:
        type(self).save_calls.append(conv)

    def save_index(self, conversations: list[dict]) -> None:
        # ``/fetch/start`` calls save_index POSITIONALLY (fetch.py:346).
        # Refresh's multi-org path uses kwargs but that's tested elsewhere.
        type(self).index_calls.append(list(conversations))


@pytest.fixture
def fake_fetcher_class(monkeypatch: pytest.MonkeyPatch) -> type[_FakeFetcher]:
    """Patch ``backend.routers.fetch.ClaudeFetcher`` with :class:`_FakeFetcher`.

    The router does ``from fetcher.bulk_fetch import ClaudeFetcher`` (a
    value-import; see ``TESTING.md`` 5.1), so the patch site is the
    importer's local binding -- not ``fetcher.bulk_fetch.ClaudeFetcher``.

    Resets the class hooks BEFORE each test (defense-in-depth on top of
    the per-instance reset in :meth:`_FakeFetcher.__init__`).
    """

    _FakeFetcher.list_result = []
    _FakeFetcher.list_raises = None
    _FakeFetcher.fetch_raises = None
    _FakeFetcher.fetch_result = None
    _FakeFetcher.save_calls = []
    _FakeFetcher.index_calls = []

    monkeypatch.setattr(
        "backend.routers.fetch.ClaudeFetcher", _FakeFetcher, raising=True
    )
    return _FakeFetcher


@pytest.fixture
def isolated_creds_with_v2(
    _isolated_credentials_path: Path,
) -> Path:
    """Seed the isolated credentials file with a valid v2 creds blob.

    Writes the actual on-disk JSON rather than mocking ``load_credentials``
    so the test exercises the real schema-parsing path (per the Python
    Expert review and CLAUDE-TESTING 5.2 boundary-mock rule).
    """

    _isolated_credentials_path.parent.mkdir(parents=True, exist_ok=True)
    _isolated_credentials_path.write_text(json.dumps(_v2_creds()))
    return _isolated_credentials_path


@pytest.fixture
def isolated_fetch_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Pin the fetch router's output / files dirs to tmp paths.

    Without this, a route under test would write into the developer's real
    ``~/.claude-explorer/conversations/``. The two constants are imported
    by value into ``backend.routers.fetch`` (line 18-22), so we patch the
    importer's bindings.
    """

    out_dir = tmp_path / "conversations"
    files_dir = tmp_path / "files"
    out_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_OUTPUT_DIR", out_dir, raising=True
    )
    monkeypatch.setattr(
        "backend.routers.fetch.DEFAULT_FILES_DIR", files_dir, raising=True
    )
    return out_dir, files_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test__fetch_start__headers__advertises_sse_no_cache(
    real_async_client: httpx.AsyncClient,
    isolated_creds_with_v2: Path,
    isolated_fetch_dirs: tuple[Path, Path],
    fake_fetcher_class: type[_FakeFetcher],
) -> None:
    """Clause BKM-FETCH-START-HEADERS: ``/api/fetch/start`` advertises ``text/event-stream``.

    The route sets ``media_type`` and ``Cache-Control: no-cache`` at
    ``backend/routers/fetch.py:457-461``. The frontend EventSource client
    relies on these to disable buffering.
    """

    fake_fetcher_class.list_result = []  # complete-quickly path

    async with real_async_client.stream(
        "GET", "/api/fetch/start?incremental=true"
    ) as resp:
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}; "
            f"body={await resp.aread()!r}"
        )
        ctype = resp.headers.get("content-type", "")
        assert ctype.startswith("text/event-stream"), (
            f"content-type must start with text/event-stream; got {ctype!r}"
        )
        assert resp.headers.get("cache-control") == "no-cache", (
            f"cache-control must be 'no-cache' for SSE buffering disable; "
            f"got {resp.headers.get('cache-control')!r}"
        )
        # Drain so the connection closes cleanly.
        async for _ in collect_sse_data_events(resp, stop_on=("complete", "error")):
            pass


async def test__fetch_start__happy_path__emits_start_progress_complete_in_order(
    real_async_client: httpx.AsyncClient,
    isolated_creds_with_v2: Path,
    isolated_fetch_dirs: tuple[Path, Path],
    fake_fetcher_class: type[_FakeFetcher],
) -> None:
    """Clause BKM-FETCH-START-HAPPY: events arrive in [start, progress+, complete] order.

    Negative-space (CLAUDE-TESTING 5.4): ``error`` MUST NOT appear in the
    happy path. Reading to EOF via ``stop_on=()`` proves the server closes
    the stream cleanly AND that no extra ``error`` frame trails the
    ``complete``.
    """

    fake_fetcher_class.list_result = [
        {"uuid": "u-1", "name": "First conv"},
        {"uuid": "u-2", "name": "Second conv"},
    ]

    async with real_async_client.stream(
        "GET", "/api/fetch/start?incremental=true"
    ) as resp:
        assert resp.status_code == 200
        kinds: list[str] = []
        async for etype, _payload in collect_sse_data_events(resp, stop_on=()):
            kinds.append(etype)

    assert kinds, f"stream emitted no events: {kinds!r}"
    assert kinds[0] == "start", f"first event must be 'start'; got {kinds!r}"
    assert kinds[-1] == "complete", (
        f"last event must be 'complete' on happy path; got {kinds!r}"
    )
    assert "progress" in kinds, (
        f"happy path with non-empty list must emit at least one 'progress'; "
        f"got {kinds!r}"
    )
    # Negative-space: no error event in the happy path.
    assert "error" not in kinds, (
        f"happy path must NOT emit 'error'; got {kinds!r}"
    )


async def test__fetch_start__progress_payload__has_current_total_message(
    real_async_client: httpx.AsyncClient,
    isolated_creds_with_v2: Path,
    isolated_fetch_dirs: tuple[Path, Path],
    fake_fetcher_class: type[_FakeFetcher],
) -> None:
    """Clause BKM-FETCH-START-PROGRESS-SHAPE: every ``progress`` payload carries ``current``, ``total``, ``message``.

    The frontend's progress bar reads ``current`` and ``total`` directly;
    a missing field would render a NaN-width bar. Asserts the schema on
    every emitted ``progress`` event, not just the first.
    """

    fake_fetcher_class.list_result = [
        {"uuid": "u-1", "name": "Only conv"},
    ]

    payloads: list[dict[str, Any]] = []
    async with real_async_client.stream(
        "GET", "/api/fetch/start?incremental=true"
    ) as resp:
        async for _etype, payload in collect_sse_data_events(resp, stop_on=()):
            payloads.append(payload)

    progress = [p for p in payloads if p.get("type") == "progress"]
    assert progress, f"no progress events collected; payloads={payloads!r}"
    for p in progress:
        # The route sets all three on every ``progress`` frame
        # (``fetch.py:279-285`` and ``:302-308``).
        assert "current" in p, f"progress missing 'current': {p!r}"
        assert "total" in p, f"progress missing 'total': {p!r}"
        assert "message" in p, f"progress missing 'message': {p!r}"
        assert isinstance(p["current"], int), f"'current' must be int: {p!r}"
        assert isinstance(p["total"], int), f"'total' must be int: {p!r}"


async def test__fetch_start__start_payload__has_total(
    real_async_client: httpx.AsyncClient,
    isolated_creds_with_v2: Path,
    isolated_fetch_dirs: tuple[Path, Path],
    fake_fetcher_class: type[_FakeFetcher],
) -> None:
    """Clause BKM-FETCH-START-START-SHAPE: the ``start`` payload carries ``total``.

    ``fetch.py:254-259`` sets ``total: 0`` on the initial ``start`` frame
    (the actual total is unknown until ``fetch_conversation_list`` returns).
    """

    fake_fetcher_class.list_result = []

    async with real_async_client.stream(
        "GET", "/api/fetch/start?incremental=true"
    ) as resp:
        async for etype, payload in collect_sse_data_events(resp, stop_on=("start",)):
            if etype == "start":
                assert "total" in payload, f"start missing 'total': {payload!r}"
                # total is intentionally 0 at this point per the impl.
                assert payload["total"] == 0
                return

    pytest.fail("did not observe 'start' event")


async def test__fetch_start__complete_payload__current_equals_total(
    real_async_client: httpx.AsyncClient,
    isolated_creds_with_v2: Path,
    isolated_fetch_dirs: tuple[Path, Path],
    fake_fetcher_class: type[_FakeFetcher],
) -> None:
    """Clause BKM-FETCH-START-COMPLETE-SHAPE: ``complete`` has ``current == total``.

    ``fetch.py:349-354``: the final progress frame matches the initial
    plan's total. The frontend uses this to flip the progress bar to 100%.
    """

    fake_fetcher_class.list_result = [
        {"uuid": "u-1", "name": "A"},
        {"uuid": "u-2", "name": "B"},
        {"uuid": "u-3", "name": "C"},
    ]

    complete_payload: dict[str, Any] | None = None
    async with real_async_client.stream(
        "GET", "/api/fetch/start?incremental=true"
    ) as resp:
        async for etype, payload in collect_sse_data_events(resp, stop_on=()):
            if etype == "complete":
                complete_payload = payload

    assert complete_payload is not None, (
        "stream must emit a 'complete' event in the happy path"
    )
    assert "current" in complete_payload, complete_payload
    assert "total" in complete_payload, complete_payload
    assert complete_payload["current"] == complete_payload["total"], (
        f"complete invariant: current must equal total; got {complete_payload!r}"
    )


async def test__fetch_start__no_new_conversations__complete_emits_zero_zero(
    real_async_client: httpx.AsyncClient,
    isolated_creds_with_v2: Path,
    isolated_fetch_dirs: tuple[Path, Path],
    fake_fetcher_class: type[_FakeFetcher],
) -> None:
    """Empty list path (``fetch.py:287-294``): emits ``complete`` directly with ``current==total==0``.

    No ``progress`` between ``start`` and ``complete`` in this case;
    the route short-circuits at line 287.
    """

    fake_fetcher_class.list_result = []

    payloads: list[dict[str, Any]] = []
    async with real_async_client.stream(
        "GET", "/api/fetch/start?incremental=true"
    ) as resp:
        async for _etype, payload in collect_sse_data_events(resp, stop_on=()):
            payloads.append(payload)

    types = [p.get("type") for p in payloads]
    # First event is start; first non-start progress IS emitted (line 279-285)
    # and reports total=0; then complete fires.
    assert types[0] == "start"
    assert types[-1] == "complete"
    complete = payloads[-1]
    assert complete["current"] == 0 and complete["total"] == 0, complete


async def test__fetch_start__auth_failure__emits_legacy_error_envelope(
    real_async_client: httpx.AsyncClient,
    isolated_creds_with_v2: Path,
    isolated_fetch_dirs: tuple[Path, Path],
    fake_fetcher_class: type[_FakeFetcher],
) -> None:
    """Clause BKM-FETCH-START-ERR-LEGACY: 401-class failures use the LEGACY ``{type, message}`` envelope.

    Asymmetry under test: ``/api/fetch/start`` emits the legacy plain-message
    envelope (``fetch.py:328-330`` and outer ``:356-360`` via
    ``classify_fetch_error``), NOT the ``{kind, retryable, message}``
    envelope used by ``/api/fetch/refresh`` fetch-phase errors at
    ``fetch.py:112-119``. Negative-space asserts on ``kind`` and
    ``retryable`` lock that asymmetry into the test suite so a future
    "let's unify" refactor surfaces here first.

    Reading to EOF via ``stop_on=()`` also asserts the stream closes
    after ``error`` (no further frames trail it).
    """

    # Force fetch_conversation_list to raise a 401 -- this falls into the
    # outer except at fetch.py:356, which calls classify_fetch_error and
    # emits SESSION_EXPIRED_MESSAGE.
    fake_fetcher_class.list_raises = RuntimeError(
        "401 Client Error: Unauthorized for url: https://claude.ai/api/..."
    )

    payloads: list[dict[str, Any]] = []
    async with real_async_client.stream(
        "GET", "/api/fetch/start?incremental=true"
    ) as resp:
        assert resp.status_code == 200
        async for _etype, payload in collect_sse_data_events(resp, stop_on=()):
            payloads.append(payload)

    types = [p.get("type") for p in payloads]
    error_payloads = [p for p in payloads if p.get("type") == "error"]

    assert len(error_payloads) == 1, (
        f"auth failure must emit EXACTLY ONE error event; got {types!r}"
    )
    err = error_payloads[0]
    # Legacy envelope contract:
    assert err.get("message") == SESSION_EXPIRED_MESSAGE, (
        f"error.message must be SESSION_EXPIRED_MESSAGE; got {err!r}"
    )
    # Negative-space: legacy envelope has no kind/retryable.
    assert "kind" not in err, (
        f"/api/fetch/start uses LEGACY envelope; 'kind' must be absent. "
        f"If this fails, the wire shape changed -- update frontend FetchToast "
        f"AND the asymmetry doc in PLANS/2026.05.07-frontend-api-contract.md. "
        f"Got: {err!r}"
    )
    assert "retryable" not in err, (
        f"/api/fetch/start uses LEGACY envelope; 'retryable' must be absent. "
        f"Got: {err!r}"
    )
    # Negative-space: no complete after error.
    assert "complete" not in types, (
        f"auth failure must not emit 'complete'; got {types!r}"
    )


async def test__fetch_start__termination__stream_closes_after_complete(
    real_async_client: httpx.AsyncClient,
    isolated_creds_with_v2: Path,
    isolated_fetch_dirs: tuple[Path, Path],
    fake_fetcher_class: type[_FakeFetcher],
) -> None:
    """Clause BKM-FETCH-START-TERMINATION: the SSE stream closes after ``complete``.

    A non-terminating server-side generator would hang the EventSource.
    The wall-clock-bound iteration in :func:`collect_sse_data_events`
    (timeout=2.0 here) guarantees a fast failure if the server hangs.
    Wrapping in :func:`asyncio.wait_for` is belt-and-suspenders for the
    case where the helper itself fails to honor its deadline.
    """

    fake_fetcher_class.list_result = [{"uuid": "u-1", "name": "Only"}]

    async def _drain() -> list[str]:
        out: list[str] = []
        async with real_async_client.stream(
            "GET", "/api/fetch/start?incremental=true"
        ) as resp:
            async for etype, _payload in collect_sse_data_events(
                resp, stop_on=(), timeout=2.0
            ):
                out.append(etype)
        return out

    # A 5s outer wait_for keeps a wedged server from timing out the test
    # runner's SIGALRM/CI deadline -- we want a clear AssertionError here.
    kinds = await asyncio.wait_for(_drain(), timeout=5.0)
    assert kinds[-1] == "complete", (
        f"stream must reach 'complete' before EOF; got {kinds!r}"
    )
