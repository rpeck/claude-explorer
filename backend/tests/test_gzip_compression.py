"""Tests for HTTP gzip compression of large API responses.

User report (2026-05-23): "Loading of long sessions is still pretty slow.
Can we support gzip for the payload?" Empirical measurement on the user's
16K-message corpus showed a ~69 MB uncompressed ConversationDetail payload
on the wire — gzip-1 takes that to ~28 MB (60% reduction) at minimal CPU
cost (~50 ms on a Mac M-series).

**Revised contract after Option 4 (2026-05-23 council decision)**:
The conversation detail route (``/api/conversations/<uuid>``) is now
*excluded* from gzip — the same ~700 ms of gzip CPU on the asyncio event
loop was serializing ALL other concurrent requests behind it. V1 is a
local-only single-user tool where the wire-size trade-off (~27 MB →
~69 MB on the conv route) is acceptable: localhost transfer is ~50 ms
either way. See ``backend/main.py:SelectiveGZipMiddleware``.

This file pins the contracts that still apply post-Option-4:

1. Gzip is APPLIED to large responses on routes OTHER than the
   conversation detail route, when the client sends
   ``Accept-Encoding: gzip``. Pinned via a synthetic ``/api/__perf/``
   route in ``test_perf_repro_2026_05_23.py``.
2. Gzip is NOT applied to ``/api/conversations/<uuid>`` even when the
   client sends ``Accept-Encoding: gzip``. Pinned by
   ``test_conversation_detail_is_NOT_gzipped`` below and by the
   companion test in ``test_perf_repro_2026_05_23.py``.
3. Small responses (under the ``minimum_size=1024`` threshold) are NOT
   compressed on any route — gzip overhead for sub-1KB payloads would
   make them LARGER.

Per TESTING.md §5.13, these are user-observable contracts ("the
wire bytes the client receives have header X and body size Y") rather
than implementation rules ("GZipMiddleware is registered on the FastAPI
app"). If a future refactor moves compression to a reverse proxy or
swaps Starlette's middleware for something else, these tests pass as
long as the user-facing behavior is preserved.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import config as cfg
from backend.cache import _conversation_cache
from backend.main import app


def _seed_large_conversation(tmp_path, monkeypatch, *, n_messages: int = 200):
    """Seed a single conversation with enough text content to comfortably
    exceed the gzip ``minimum_size=1024`` threshold (default ~50-100 KB).

    The text body of each message includes a repeating filler string so
    the on-wire JSON for the ConversationDetail response is well above
    the 1 KB compression floor. We do NOT need the user's 69 MB corpus
    to assert correctness — we only need enough bytes that gzip is
    triggered and the diff between compressed and identity is
    unambiguous. ~200 messages at ~500 bytes each = ~100 KB on the
    wire, which fits in <100 ms of test runtime.
    """
    filler = (
        "this is filler text designed to compress extremely well under "
        "gzip because the same phrase is repeated many times " * 5
    )
    chat_messages = []
    parent = None
    for i in range(n_messages):
        msg_uuid = f"msg-{i:04d}"
        chat_messages.append(
            {
                "uuid": msg_uuid,
                "parent_message_uuid": parent,
                "sender": "human" if i % 2 == 0 else "assistant",
                "text": f"message {i}: {filler}",
                "created_at": "2026-05-01T12:00:00Z",
                "updated_at": "2026-05-01T12:00:00Z",
                "content": [{"type": "text", "text": f"message {i}: {filler}"}],
            }
        )
        parent = msg_uuid

    conv = {
        "uuid": "conv-gzip-test",
        "name": "Large conversation for gzip test",
        "summary": "filler " * 100,
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        "current_leaf_message_uuid": f"msg-{n_messages - 1:04d}",
        "chat_messages": chat_messages,
    }
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True)
    (by_org / "conv-gzip-test.json").write_text(json.dumps(conv))
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_DIR", str(empty_claude))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()


@pytest.fixture
def large_conversation(tmp_path, monkeypatch):
    _seed_large_conversation(tmp_path, monkeypatch)
    yield "conv-gzip-test"
    _conversation_cache.clear()
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_conversation_detail_is_NOT_gzipped(large_conversation):
    """Post-Option-4 contract: the conversation detail route MUST NOT
    carry ``Content-Encoding: gzip`` even when the client sends
    ``Accept-Encoding: gzip``.

    Why: gzip compression of the 69 MB ConversationDetail payload
    runs synchronously on the asyncio event loop (~700 ms per request),
    serializing every other concurrent endpoint behind it. The
    user-reported "10 s perceived load" on 2026-05-23 was THIS effect:
    /api/config + /api/orgs + /api/preferences all queued behind one
    conversation fetch's gzip CPU.

    Trade-off accepted: wire size on this route grows from ~27 MB
    (gzip) to ~69 MB (identity). On localhost that's ~50 ms either
    way. V1 is local-only.

    Implementation: ``SelectiveGZipMiddleware`` in ``backend/main.py``
    pattern-matches ``^/api/conversations/[^/]+$`` and bypasses
    compression. Sub-routes (/tree, /export/*) still gzip.
    """
    client = TestClient(app)
    resp = client.get(
        f"/api/conversations/{large_conversation}",
        headers={"Accept-Encoding": "gzip"},
    )
    assert resp.status_code == 200, resp.text
    encoding = resp.headers.get("content-encoding", "identity")
    assert encoding != "gzip", (
        f"Expected NO gzip on /api/conversations/<uuid> (Option 4 bypass), "
        f"but got Content-Encoding: {encoding!r}. The SelectiveGZipMiddleware "
        f"path regex (_CONV_DETAIL_PATH_RE) in backend/main.py may have drifted."
    )
    body = resp.json()
    assert body["uuid"] == large_conversation


def test_conversation_detail_is_not_gzipped_when_client_sends_identity(
    large_conversation,
):
    """Graceful degradation contract: a client that explicitly opts out
    of gzip (``Accept-Encoding: identity``) MUST receive the response
    uncompressed. Otherwise downstream tools that can't gunzip (curl
    without ``--compressed``, naive HTTP libraries, debugging proxies)
    would see binary garbage.
    """
    client = TestClient(app)
    resp = client.get(
        f"/api/conversations/{large_conversation}",
        headers={"Accept-Encoding": "identity"},
    )
    assert resp.status_code == 200, resp.text
    # Either no Content-Encoding header at all, or one that doesn't
    # claim gzip — both are valid uncompressed signals.
    assert "gzip" not in resp.headers.get("content-encoding", ""), (
        f"expected no gzip encoding, got: {resp.headers.get('content-encoding')}"
    )
    # Body must be parseable directly as JSON — TestClient/httpx does
    # NOT auto-decompress when no Content-Encoding is set, so this also
    # proves the server didn't accidentally double-encode.
    body = resp.json()
    assert body["uuid"] == large_conversation


def test_small_response_is_not_gzipped(large_conversation):
    """CPU-cost guard: gzip middleware MUST skip compression for
    responses under its ``minimum_size`` threshold (1024 bytes). For
    sub-1KB payloads, gzip's framing overhead can produce a LARGER
    output and always burns CPU for zero network benefit. The
    ``/api/health`` endpoint returns ~120 bytes — well below the floor.

    This protects against a future maintainer setting ``minimum_size=0``
    (compress everything!) without understanding why the default exists.
    """
    client = TestClient(app)
    resp = client.get("/api/health", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200, resp.text
    assert "gzip" not in resp.headers.get("content-encoding", ""), (
        f"/api/health is tiny and should NOT be gzipped; got Content-Encoding: "
        f"{resp.headers.get('content-encoding')}"
    )


def _raw_wire_size(client: TestClient, url: str, accept_encoding: str) -> tuple[int, str | None]:
    """Get the actual bytes-on-the-wire size and Content-Encoding for a
    GET request, bypassing httpx's transparent decompression.

    ``TestClient.get(...)`` (and ``httpx.Response.content``) auto-decode
    gzip responses, so ``len(resp.content)`` always reports the DECODED
    size — making it useless for measuring compression ratio. The
    ``client.stream(...) + iter_raw()`` path returns the raw wire bytes
    BEFORE decompression, which is what we need to assert the wire-size
    win is real.
    """
    with client.stream(
        "GET", url, headers={"Accept-Encoding": accept_encoding}
    ) as resp:
        assert resp.status_code == 200, resp.read()
        raw = b"".join(resp.iter_raw())
        return len(raw), resp.headers.get("content-encoding")


def test_conversation_detail_wire_size_is_identical_regardless_of_accept_encoding(
    large_conversation,
):
    """Post-Option-4 contract: because the conversation detail route
    bypasses gzip entirely, the wire-size MUST be the same regardless of
    the client's ``Accept-Encoding`` header.

    Replaces the pre-Option-4 ``test_gzipped_response_is_strictly_smaller_than_identity``
    which asserted the OPPOSITE invariant. The user accepted the wire-
    size trade-off for the conv route specifically (see
    ``test_conversation_detail_is_NOT_gzipped`` docstring for the
    full rationale).

    For the gzip-still-applies-elsewhere invariant, see
    ``test_perf_repro_2026_05_23.py::test_other_routes_still_gzip_when_large``.
    """
    client = TestClient(app)
    url = f"/api/conversations/{large_conversation}"
    gz_size, gz_ce = _raw_wire_size(client, url, "gzip")
    id_size, id_ce = _raw_wire_size(client, url, "identity")

    assert "gzip" not in (gz_ce or ""), (
        f"Post-Option-4: conv detail should NOT carry gzip Content-Encoding "
        f"even when client asks for gzip; got {gz_ce!r}"
    )
    assert "gzip" not in (id_ce or ""), (
        f"identity request should NOT carry gzip Content-Encoding, got {id_ce!r}"
    )
    # Both responses should be identical bytes (no compression either way).
    assert gz_size == id_size, (
        f"Post-Option-4: bypass should yield identical wire bytes "
        f"regardless of Accept-Encoding. Got gzip-req={gz_size}B "
        f"identity-req={id_size}B."
    )
