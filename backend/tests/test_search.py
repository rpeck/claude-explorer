"""Tests for the search endpoint.

These tests use an **isolated, seeded corpus** and assert against specific
conversation UUIDs in the results. They fail RED if /api/search returns
empty (or any other wrong content) instead of just rubber-stamping 200.

Replaces the previous "just check HTTP 200" tests which would have
passed against a completely broken search that returned [] for every
query — exactly the failure mode the test suite was missing.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import config as cfg
from backend.cache import _conversation_cache
from backend.main import app


def _conv(uuid: str, name: str, *, summary: str = "", body: str = "the body text"):
    """Build a minimal Claude Desktop conversation JSON."""
    return {
        "uuid": uuid,
        "name": name,
        "summary": summary,
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        "current_leaf_message_uuid": "msg-1",
        "chat_messages": [
            {
                "uuid": "msg-1",
                "parent_message_uuid": None,
                "sender": "human",
                "text": body,
                "created_at": "2026-05-01T12:00:00Z",
                "updated_at": "2026-05-01T12:00:00Z",
                "content": [{"type": "text", "text": body}],
            },
        ],
    }


@pytest.fixture
def search_data_dir(tmp_path, monkeypatch):
    """Seed a small corpus where every conversation has a UNIQUE token.

    The unique tokens (`alphaneedle`, `betaneedle`, `gammaneedle`) let the
    tests assert that a query returns ONLY the conversations that contain
    that specific token — proving the search is actually filtering, not
    just always returning everything or always returning [].
    """
    convs = [
        _conv("conv-alpha", "Alpha session",
              body="this body contains alphaneedle exactly once"),
        _conv("conv-beta", "Beta session",
              body="this body contains betaneedle exactly once"),
        _conv("conv-gamma", "Gamma session",
              body="this body contains gammaneedle exactly once"),
    ]
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True)
    for c in convs:
        (by_org / f"{c['uuid']}.json").write_text(json.dumps(c))
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_DIR", str(empty_claude))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()
    yield tmp_path
    _conversation_cache.clear()
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_search_returns_known_match(search_data_dir):
    """A query for a unique token must return ONLY the conversation that
    contains it. The previous test_search_with_query asserted only HTTP
    200 + dict shape, so a search that always returned [] would still
    pass — defeating the test's purpose.
    """
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "alphaneedle"})
    assert r.status_code == 200
    body = r.json()
    uuids = [item["conversation_uuid"] for item in body["results"]]
    assert uuids == ["conv-alpha"], (
        f"Expected ['conv-alpha'], got {uuids}. If empty, search is broken."
    )


def test_search_returns_empty_for_nonmatching_query(search_data_dir):
    """A query for a token that exists in NO conversation must return [].

    Bidirectional verification: pairs with test_search_returns_known_match
    so we can't pass by always returning [] OR always returning everything.
    """
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "zzzzznotinanycorpusvaluezzzzz"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"] == [], (
        f"Expected empty results for non-matching query, got: {body['results']}"
    )
    assert body["total_messages_matched"] == 0


def test_search_different_queries_return_different_results(search_data_dir):
    """Distinct queries on distinct tokens must return DISJOINT results.

    Tautology-protection: if the implementation returns the same list for
    every query (or ignores the query and returns all conversations), this
    test fails.
    """
    client = TestClient(app)
    alpha = client.get("/api/search", params={"q": "alphaneedle"}).json()["results"]
    beta = client.get("/api/search", params={"q": "betaneedle"}).json()["results"]
    gamma = client.get("/api/search", params={"q": "gammaneedle"}).json()["results"]

    alpha_uuids = {r["conversation_uuid"] for r in alpha}
    beta_uuids = {r["conversation_uuid"] for r in beta}
    gamma_uuids = {r["conversation_uuid"] for r in gamma}

    assert alpha_uuids == {"conv-alpha"}, alpha_uuids
    assert beta_uuids == {"conv-beta"}, beta_uuids
    assert gamma_uuids == {"conv-gamma"}, gamma_uuids
    assert alpha_uuids != beta_uuids != gamma_uuids


def test_search_is_case_insensitive(search_data_dir):
    """Query case must not affect matches. ALPHANEEDLE / alphaneedle /
    AlphaNeedle all hit the same conversation.
    """
    client = TestClient(app)
    for q in ("alphaneedle", "ALPHANEEDLE", "AlphaNeedle"):
        r = client.get("/api/search", params={"q": q})
        assert r.status_code == 200, f"q={q!r}"
        uuids = [item["conversation_uuid"] for item in r.json()["results"]]
        assert uuids == ["conv-alpha"], f"q={q!r} → {uuids}"


def test_search_envelope_shape(search_data_dir):
    """SearchResponse envelope contract: results / total_messages_matched /
    returned_messages / truncated. Frontend Truncation footer + MCP
    pagination depend on these fields existing on every response AND
    being the right TYPE — otherwise a regression that returned
    ``total_messages_matched`` as a string ``"0"`` or ``results`` as a
    dict instead of a list would slip through.

    The previous version asserted only ``"key" in body`` for three of
    the four fields; Critic's call-out (LLM council 2026-05-18 None-
    safety audit) was that key-existence without type validation is a
    rubber stamp on the envelope contract. Pin every field type.
    """
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "alphaneedle"})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert "results" in body and isinstance(body["results"], list)
    assert "total_messages_matched" in body and isinstance(
        body["total_messages_matched"], int
    )
    assert "returned_messages" in body and isinstance(
        body["returned_messages"], int
    )
    assert "truncated" in body and isinstance(body["truncated"], bool)


def test_search_requires_query():
    """Empty query → 422. Distinct from the seeded-corpus tests so it
    runs without setup; doesn't need a fixture.
    """
    client = TestClient(app)
    response = client.get("/api/search")
    assert response.status_code == 422


def test_search_rejects_empty_string():
    """q='' is min_length=1; must 422 not 200-with-everything."""
    client = TestClient(app)
    response = client.get("/api/search", params={"q": ""})
    assert response.status_code == 422


def test_search_handles_null_fields_without_crashing(tmp_path, monkeypatch):
    """Conversation with `name`, `summary`, or `project_path` set to
    explicit `null` (key present, value None) must not crash the route.

    Regression for backend/store.py:list_conversations and
    backend/search.py linear-scan title-match where `data.get("key", "")
    .lower()` was used. `.get(k, "")` defaults to "" ONLY when key is
    missing; if value is None, the call returns None and `.lower()`
    raised AttributeError, surfacing as a 500 — the bug that the
    earlier weak "only assert 200" tests missed entirely.
    """
    # Build conversations with each null-field shape that would
    # historically have crashed.
    null_convs = [
        {**_conv("conv-null-name", "ignored"), "name": None},
        {**_conv("conv-null-summary", "summary-null", summary=""), "summary": None},
        {**_conv("conv-null-path", "path-null"), "project_path": None},
    ]
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True)
    for c in null_convs:
        (by_org / f"{c['uuid']}.json").write_text(json.dumps(c))
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_DIR", str(empty_claude))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()

    # Two endpoints to probe — both used to crash.
    client = TestClient(app)
    r1 = client.get("/api/search", params={"q": "anyterm"})
    assert r1.status_code == 200, r1.text
    r2 = client.get("/api/conversations", params={"search": "anyterm"})
    assert r2.status_code == 200, r2.text


def test_search_handles_null_chat_messages_without_crashing(tmp_path, monkeypatch):
    """Conversation with ``chat_messages: null`` (NOT an empty list, but
    an explicit JSON null) must not crash ``/api/search``.

    Same bug-class as ``test_search_handles_null_fields_without_crashing``:
    ``data.get("chat_messages", [])`` returns ``None`` when the key is
    present with value ``None``, and ``for msg in None:`` raises
    ``TypeError: 'NoneType' object is not iterable``. The linear-scan
    path (``backend/search.py:927``) and the FTS5 fast path
    (``backend/search.py:1220``) both have the unsafe iteration.

    Verified RED against the bug:
        $ uv run python -c "...probe with chat_messages=None..."
        TypeError: 'NoneType' object is not iterable
        at backend/search.py:927
    """
    conv = {
        **_conv("conv-null-chats", "regular title"),
        "chat_messages": None,
    }
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True)
    (by_org / "conv-null-chats.json").write_text(json.dumps(conv))
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_DIR", str(empty_claude))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()

    client = TestClient(app)
    r = client.get("/api/search", params={"q": "anyterm"})
    assert r.status_code == 200, r.text


def test_list_conversations_handles_null_name_summary_model_without_crashing(
    tmp_path, monkeypatch
):
    """Conversation with ``name: null`` / ``summary: null`` / ``model: null``
    must not crash ``/api/conversations`` (no search filter).

    The 8ab36fc fix addressed ``data.get(k, "").lower()`` in the SEARCH
    FILTER step of ``list_conversations``, but ``_make_summary`` at
    ``backend/store.py:340-364`` still passes ``data.get("name",
    "Untitled")`` etc. through to Pydantic. The ``ConversationSummary``
    Pydantic model declares ``name: str`` (not ``str | None``), so
    Pydantic v2 raises ``ValidationError`` on ``None`` input, surfacing
    as HTTP 500. The earlier fix accidentally masked this for the
    ``?search=`` path because the conv is filtered out BEFORE
    ``_make_summary`` runs.

    This test calls ``/api/conversations`` with NO ``search`` param, so
    every conv (including the null-fielded one) reaches
    ``_make_summary``.
    """
    conv = {
        "uuid": "conv-pydantic-null",
        "name": None,
        "summary": None,
        "model": None,
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        "chat_messages": [],
    }
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True)
    (by_org / "conv-pydantic-null.json").write_text(json.dumps(conv))
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_DIR", str(empty_claude))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()

    client = TestClient(app)
    r = client.get("/api/conversations")
    assert r.status_code == 200, r.text
    # The conv must be in the response (with safe fallback strings,
    # not silently dropped). Pin the contract: None-on-disk -> empty
    # string on the wire for the str fields.
    rows = {c["uuid"]: c for c in r.json()}
    assert "conv-pydantic-null" in rows, f"conv silently dropped: {list(rows)}"
    row = rows["conv-pydantic-null"]
    assert isinstance(row["name"], str)
    assert isinstance(row["model"], str)


def test_search_source_filter_excludes_other_source(search_data_dir):
    """source=CLAUDE_CODE on a CLAUDE_AI-only seeded corpus must return
    []. Otherwise the source filter is silently broken.
    """
    client = TestClient(app)
    r = client.get(
        "/api/search",
        params={"q": "alphaneedle", "source": "CLAUDE_CODE"},
    )
    assert r.status_code == 200
    assert r.json()["results"] == [], (
        "CLAUDE_CODE source on a Desktop-only corpus must yield no results"
    )
