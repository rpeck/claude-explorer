"""Wrapped ``SearchResponse`` envelope + truncation disclosure — RED phase.

The /api/search endpoint goes from returning ``list[SearchResult]`` to
returning a ``SearchResponse(results, total_messages_matched,
returned_messages, truncated)``. The new fields tell the UI (and MCP
consumers) when the bm25 LIMIT clipped the result set so they can show
"showing first N of M" instead of silently truncating.

Plan reference:
``PLANS/SEARCH_TOOL_AWARENESS_AND_LIMIT_DISCLOSURE.md`` §B.

Bidirectional verification per CLAUDE-TESTING.md §2: every
``truncated=True`` assertion is paired with a ``truncated=False`` case so
a bug that flipped the predicate is caught either way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import search_index as si


# ----- helpers ---------------------------------------------------------


def _msg(uuid: str, *, sender: str = "human", text: str,
         created_at: str = "2026-05-16T12:00:00Z") -> dict[str, Any]:
    return {
        "uuid": uuid,
        "sender": sender,
        "text": text,
        "content": [{"type": "text", "text": text}],
        "created_at": created_at,
        "updated_at": created_at,
        "parent_message_uuid": None,
    }


def _conv(uuid: str, name: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "name": name,
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-16T12:00:00Z",
        "updated_at": "2026-05-16T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else "",
        "project_path": None,
        "source": "CLAUDE_AI",
        "chat_messages": messages,
    }


def _write_desktop_conv(data_dir: Path, conv: dict[str, Any]) -> Path:
    by_org = data_dir / "by-org" / "org-default"
    by_org.mkdir(parents=True, exist_ok=True)
    path = by_org / f"{conv['uuid']}.json"
    path.write_text(json.dumps(conv))
    return path


def _seed_corpus(data_dir: Path, *, total_messages: int,
                 needle: str = "envelopecanary",
                 needle_in_count: int | None = None) -> int:
    """Write a corpus of conversations to ``data_dir``. Each conversation
    holds 1 message; ``needle_in_count`` of them contain the needle
    (defaults to ALL messages). Returns the number of needle-bearing
    messages.

    The corpus is intentionally small per-conversation so we can hit
    arbitrarily-high message counts without exploding disk I/O.
    """
    if needle_in_count is None:
        needle_in_count = total_messages
    for i in range(total_messages):
        text = (f"document {i} carries {needle} keyword for search"
                if i < needle_in_count
                else f"document {i} mentions no special tokens")
        conv = _conv(
            f"conv-envelope-{i:05d}",
            f"Envelope corpus conv {i}",
            [_msg(f"m-{i:05d}", text=text)],
        )
        _write_desktop_conv(data_dir, conv)
    return needle_in_count


@pytest.fixture
def envelope_app(tmp_path, monkeypatch):
    """Build a FastAPI app pointed at an isolated data dir with a built FTS5 index.

    Returns ``(TestClient, needle, total_needle_bearing_messages)``.
    """
    from backend import config
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    config.get_settings.cache_clear()
    (tmp_path / "data").mkdir()
    (tmp_path / "claude").mkdir()

    needle = "envelopecanary"
    # 50 conversations, all containing the needle.
    needle_count = _seed_corpus(tmp_path / "data", total_messages=50, needle=needle)

    # Build the FTS5 index over the seeded corpus.
    si.reset_search_index_for_tests()
    idx = si.SearchIndex(tmp_path / "search-index.sqlite")
    monkeypatch.setattr(si, "_search_index", idx)
    from backend.store import ConversationStore
    store = ConversationStore()
    si.build_full_index(store, index=idx)

    from backend.main import app
    client = TestClient(app)
    try:
        yield client, needle, needle_count
    finally:
        client.close()
        si.reset_search_index_for_tests()
        config.get_settings.cache_clear()


# ----- 9. /api/search returns the wrapped envelope ----------------------


def test_search_endpoint_returns_envelope_keys(envelope_app) -> None:
    """The GET /api/search response is a JSON object with the four
    envelope keys, NOT a bare list. Bug it would surface: forgetting to
    update the route's return shape — old contract returned ``[...]``.
    """
    client, needle, _ = envelope_app
    resp = client.get(f"/api/search?q={needle}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict), (
        f"response must be a JSON object (SearchResponse), got {type(body).__name__}"
    )
    for key in ("results", "total_messages_matched", "returned_messages", "truncated"):
        assert key in body, f"envelope missing key {key!r}; got keys {list(body.keys())}"
    assert isinstance(body["results"], list)
    assert isinstance(body["total_messages_matched"], int)
    assert isinstance(body["returned_messages"], int)
    assert isinstance(body["truncated"], bool)


# ----- 10. truncated=False when matches ≤ limit ----------------------


def test_truncated_false_when_within_limit(envelope_app) -> None:
    """50 needle-bearing messages, HTTP LIMIT=1000 — well under cap.
    ``truncated`` must be False; ``returned_messages`` must equal
    ``total_messages_matched`` exactly.

    Bug it would surface: ``truncated = returned > 0`` (always True) or
    ``truncated = total > 0`` (always True for non-empty queries).
    """
    client, needle, needle_count = envelope_app
    resp = client.get(f"/api/search?q={needle}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["truncated"] is False, (
        f"50 < 1000 must not be truncated; got truncated={body['truncated']}, "
        f"total={body['total_messages_matched']}, returned={body['returned_messages']}"
    )
    assert body["total_messages_matched"] == needle_count, (
        f"total must equal the actual number of needle messages ({needle_count}); "
        f"got {body['total_messages_matched']}"
    )
    assert body["returned_messages"] == body["total_messages_matched"], (
        "returned must equal total when within limit"
    )


# ----- 11. truncated=True when matches > limit ------------------------


def test_truncated_true_when_above_limit(tmp_path, monkeypatch) -> None:
    """When the underlying matches exceed the LIMIT, ``truncated=True``
    and ``returned == limit < total``. We force a small test-only LIMIT
    via dependency injection so we don't have to seed 1000+ messages.

    Bug it would surface: ``truncated`` hardcoded to False, or the count
    query reading the limited subset instead of the full COUNT(*).
    """
    from backend import config
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    config.get_settings.cache_clear()
    (tmp_path / "data").mkdir()
    (tmp_path / "claude").mkdir()

    needle = "truncatecanary"
    # Seed 60 needle-bearing messages.
    _seed_corpus(tmp_path / "data", total_messages=60, needle=needle)

    si.reset_search_index_for_tests()
    idx = si.SearchIndex(tmp_path / "search-index.sqlite")
    monkeypatch.setattr(si, "_search_index", idx)
    from backend.store import ConversationStore
    store = ConversationStore()
    si.build_full_index(store, index=idx)

    # Direct call to the dispatcher with a tiny limit — exercises the
    # SearchResponse envelope at the function level without seeding 1000+
    # conversations.
    from backend.search import search_conversations
    response = search_conversations(
        store, needle, limit=5,
    )
    # search_conversations now returns SearchResponse, not list.
    assert hasattr(response, "truncated"), (
        "search_conversations must return SearchResponse with truncated field"
    )
    assert response.truncated is True, (
        f"60 matches with limit=5 must be truncated; got truncated={response.truncated}"
    )
    assert response.total_messages_matched == 60, (
        f"total must be the full COUNT(*) (60); got {response.total_messages_matched}"
    )
    assert response.returned_messages == 5, (
        f"returned must equal limit (5); got {response.returned_messages}"
    )
    assert response.returned_messages < response.total_messages_matched

    si.reset_search_index_for_tests()
    config.get_settings.cache_clear()


# ----- 12. HTTP route uses limit=1000 ---------------------------------


def test_http_route_uses_limit_1000(envelope_app, monkeypatch) -> None:
    """The /api/search GET handler passes ``limit=1000`` down the chain.
    We patch ``query_with_snippets`` to record its limit kwarg.

    Bug it would surface: the route silently passing the function
    default (or some other value) — would mean the truncation behavior
    drifts from the documented 1000-cap promise.
    """
    seen_limits: list[int] = []
    original = si.SearchIndex.query_with_snippets

    def _spy(self, user_query, *, source="all", conversation_uuid=None,
             project_path=None, bookmarks=None, organization_id=None,
             conversation_uuids=None, include_tool_calls=True,
             include_compactions=True, limit=1000):
        seen_limits.append(limit)
        return original(
            self, user_query,
            source=source, conversation_uuid=conversation_uuid,
            project_path=project_path, bookmarks=bookmarks,
            organization_id=organization_id,
            conversation_uuids=conversation_uuids,
            include_tool_calls=include_tool_calls,
            include_compactions=include_compactions,
            limit=limit,
        )

    monkeypatch.setattr(si.SearchIndex, "query_with_snippets", _spy)
    client, needle, _ = envelope_app
    resp = client.get(f"/api/search?q={needle}")
    assert resp.status_code == 200, resp.text
    assert seen_limits, "query_with_snippets must have been called"
    assert seen_limits[0] == 1000, (
        f"HTTP route must pass limit=1000; got {seen_limits[0]}"
    )


# ----- 13. MCP search path uses limit=5000 (conditional on existence) ---


def test_mcp_search_path_uses_limit_5000(monkeypatch) -> None:
    """The MCP ``list_sessions`` tool delegates to
    ``backend.search.search_conversations`` when the user passes a
    query. It must pass ``limit=5000`` so programmatic / LLM consumers
    see broader result sets than the HTTP UI does.

    Bug it would surface: MCP inheriting the HTTP default (1000) and
    silently truncating broad queries.
    """
    captured_limits: list[int] = []

    from backend import search as backend_search

    real_fn = backend_search.search_conversations

    def _spy(store, query, **kwargs):
        captured_limits.append(kwargs.get("limit"))
        return real_fn(store, query, **kwargs)

    # Patch the binding mcp_server.server uses (it imports the name).
    monkeypatch.setattr(
        "mcp_server.server.search_conversations", _spy,
    )

    # Set up a minimal data dir so list_sessions can build a store.
    import tempfile
    from backend import config
    # ignore_cleanup_errors: this test asserts the search limit, not file
    # locking. The real connection leak that caused WinError 32 here is
    # fixed in SearchIndex.close() and pinned by
    # backend/tests/test_search_index_close.py. Windows can still hold the
    # handle briefly after close, and failing teardown would hide the
    # assertion this test exists to make.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        from pathlib import Path
        tdp = Path(td)
        (tdp / "data").mkdir()
        (tdp / "claude").mkdir()
        monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tdp / "data"))
        monkeypatch.setenv("CLAUDE_DIR", str(tdp / "claude"))
        config.get_settings.cache_clear()

        from mcp_server import server as mcp_server
        # The MCP tool is registered via @mcp.tool() — we call the
        # underlying function directly.
        mcp_server.list_sessions(query="canary")

        # The store leaves a SQLite connection open on search-index.sqlite.
        # POSIX happily unlinks an open file; Windows refuses, so the
        # TemporaryDirectory cleanup raises WinError 32. Close it here.
        from backend.search_index import reset_search_index_for_tests

        reset_search_index_for_tests()

    assert captured_limits, (
        "MCP list_sessions(query=...) must call search_conversations"
    )
    assert captured_limits[0] == 5000, (
        f"MCP search path must pass limit=5000; got {captured_limits[0]}"
    )
    config.get_settings.cache_clear()
