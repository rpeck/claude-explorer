"""Tests for the /api/conversations endpoint.

The sidebar's "Search titles and projects" input has two filter paths:

  1. **Server-side** via `GET /api/conversations?search=...`. The store's
     `list_conversations(search=...)` filters against `name` /
     `summary` / `project_path`. This is the public API contract
     external scripts hit.

  2. **Client-side** via the React hook `useConversations` (see
     `frontend/src/hooks/useConversations.ts`). Filters against
     `name` / `project_path` only (intentionally NOT `summary` — see
     hook docstring P1.2 2026-05-04).

These tests pin the SERVER-SIDE contract. They seed a known corpus
and assert specific UUIDs in the filtered result, so a search that
returned [] for every query (or returned everything regardless of
query) would fail RED. Previous tests asserted only HTTP 200 and were
useless against that failure mode.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import config as cfg
from backend.cache import _conversation_cache
from backend.main import app


def _conv(
    uuid: str,
    name: str,
    *,
    summary: str = "",
    project_path: str | None = None,
    source: str = "CLAUDE_AI",
):
    """Build a Claude Desktop conversation JSON."""
    return {
        "uuid": uuid,
        "name": name,
        "summary": summary,
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": source,
        "project_path": project_path,
        "current_leaf_message_uuid": "msg-1",
        "chat_messages": [
            {
                "uuid": "msg-1",
                "parent_message_uuid": None,
                "sender": "human",
                "text": "hello",
                "created_at": "2026-05-01T12:00:00Z",
                "updated_at": "2026-05-01T12:00:00Z",
                "content": [{"type": "text", "text": "hello"}],
            },
        ],
    }


@pytest.fixture
def title_filter_data_dir(tmp_path, monkeypatch):
    """Three Desktop conversations with intentionally orthogonal fields:

      * `applekey` only in NAME
      * `bananakey` only in SUMMARY
      * `cherrykey` only in PROJECT_PATH

    Lets each test assert that searching for X returns ONLY the
    conversation that has X in the matching field — proving the filter
    is doing real work, not falling back to "always return everything"
    or "always return []".
    """
    convs = [
        _conv("conv-name", "applekey title goes here", summary="plain summary"),
        _conv("conv-summary", "plain title", summary="this summary has bananakey in it"),
        _conv("conv-path", "plain title", summary="plain summary",
              project_path="/work/cherrykey/some-project"),
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


# ---------- Schema + 404 contracts (hermetic; no developer-disk dependency) ----------


def test_list_conversations_schema_contract(title_filter_data_dir):
    """Pin the EXACT serialized shape of one row from ``/api/conversations``.

    This test is intentionally fragile and breaks loudly on any change to
    the ``ConversationListItem`` schema. That is a feature, not a bug: a
    silently renamed or dropped field on the public wire format would
    otherwise pass the bidirectional needle-filter tests below (which
    only assert ``uuids == [...]``).

    Replaces the previous ``test_list_conversations`` which was a
    rubber-stamp: it asserted only ``status_code == 200`` plus
    ``isinstance(response.json(), list)`` against the developer's REAL
    ``~/.claude-explorer/conversations`` (no isolation fixture). That test
    would have passed against a broken implementation that returned
    ``[]`` for every query, returned everything regardless of query, or
    silently renamed every field on every row.

    Dynamic-timestamp note: ``created_at`` / ``updated_at`` are popped
    before the dict-equality and validated only as parseable ISO-8601
    strings, because Pydantic's exact wire format (Z vs +00:00, etc.)
    can drift between library versions. The static schema fields are
    pinned verbatim.
    """
    from datetime import datetime

    client = TestClient(app)
    response = client.get("/api/conversations")
    assert response.status_code == 200, response.text

    convs = {c["uuid"]: c for c in response.json()}
    assert "conv-name" in convs, (
        f"seeded conversation 'conv-name' missing from response; got {list(convs)}"
    )
    apple_conv = convs["conv-name"]

    # Pop + validate dynamic fields separately so a Pydantic
    # serialization-format change doesn't break the contract pin.
    created_at = apple_conv.pop("created_at")
    updated_at = apple_conv.pop("updated_at")
    assert isinstance(created_at, str)
    assert isinstance(updated_at, str)
    # datetime.fromisoformat handles both "Z" and "+00:00" suffixes on
    # Python 3.11+. The replace() is belt-and-suspenders for older
    # interpreters that don't accept the Z suffix natively.
    assert datetime.fromisoformat(created_at.replace("Z", "+00:00")).tzinfo is not None
    assert datetime.fromisoformat(updated_at.replace("Z", "+00:00")).tzinfo is not None

    # Exhaustive dict-equality on the static fields. Mirrors the
    # ConversationListItem schema in backend/models.py:78-127 exactly.
    # If a field is added/renamed/dropped on the model, this assertion
    # fails and the test author is forced to think through whether the
    # public wire shape change is intentional.
    assert apple_conv == {
        "uuid": "conv-name",
        "name": "applekey title goes here",
        "model": "claude-sonnet-4-6",
        "is_starred": False,
        "message_count": 1,
        "has_branches": False,
        "source": "CLAUDE_AI",
        "project_path": None,
        "project_name": None,
        "organization_id": None,
        "organization_name": None,
        "subagents": [],
    }


def test_get_conversation_not_found(title_filter_data_dir):
    """404 on a UUID that does not exist in an ISOLATED, known-empty corpus.

    Previously used the unfixtured ``client`` against the developer's
    real ``~/.claude-explorer/conversations``; harmless in practice
    (the literal ``"nonexistent-uuid"`` collides with nothing) but
    violates hermetic-test discipline. The fixture pins the corpus to
    three known UUIDs (``conv-name`` / ``conv-summary`` / ``conv-path``)
    so ``nonexistent-uuid`` is guaranteed absent regardless of the
    developer's disk state.
    """
    client = TestClient(app)
    response = client.get("/api/conversations/nonexistent-uuid")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_conversation_tree_not_found(title_filter_data_dir):
    """Same hermetic-isolation rationale as ``test_get_conversation_not_found``."""
    client = TestClient(app)
    response = client.get("/api/conversations/nonexistent-uuid/tree")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# None-safety contracts on the detail endpoints (same bug-class as 8ab36fc).
# ---------------------------------------------------------------------------


def test_get_conversation_handles_null_chat_messages_without_crashing(
    tmp_path, monkeypatch
):
    """A conversation with ``chat_messages: null`` (NOT ``[]``) must not
    crash ``GET /api/conversations/{uuid}``.

    Same bug-class as 8ab36fc: ``data.get("chat_messages", [])`` returns
    ``None`` when the key is present with value ``None``, and every
    downstream operation (``any(...for m in chat_messages)``,
    ``len(chat_messages)``, ``for m in chat_messages``,
    ``has_branches(chat_messages)``) raises ``TypeError``. Pinned by
    this test and fixed at ``backend/store.py:get_conversation``.
    """
    conv = {
        "uuid": "conv-detail-null-chats",
        "name": "regular title",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        "chat_messages": None,
    }
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True)
    (by_org / "conv-detail-null-chats.json").write_text(json.dumps(conv))
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_DIR", str(empty_claude))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()

    client = TestClient(app)
    r = client.get("/api/conversations/conv-detail-null-chats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["uuid"] == "conv-detail-null-chats"
    # The empty-messages contract: None on disk → [] on the wire.
    assert body["messages"] == [], body["messages"]
    assert body["message_count"] == 0


def test_get_conversation_handles_null_name_summary_model_without_crashing(
    tmp_path, monkeypatch
):
    """``GET /api/conversations/{uuid}`` with a conv whose ``name`` /
    ``summary`` / ``model`` are explicit ``null`` must not crash with
    a Pydantic ``ValidationError`` → 500.

    Mirrors ``test_list_conversations_handles_null_name_summary_model_without_crashing``
    in test_search.py but for the per-conversation detail endpoint. The
    same ``data.get(k, fallback_str)`` anti-pattern lives in
    ``backend/store.py:get_conversation`` at the
    ``ConversationDetail(...)`` construction (lines 564-585).
    """
    conv = {
        "uuid": "conv-detail-null-strs",
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
    (by_org / "conv-detail-null-strs.json").write_text(json.dumps(conv))
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_DIR", str(empty_claude))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()

    client = TestClient(app)
    r = client.get("/api/conversations/conv-detail-null-strs")
    assert r.status_code == 200, r.text
    body = r.json()
    # Same None→safe-string fallback contract as list_conversations.
    assert isinstance(body["name"], str)
    assert isinstance(body["summary"], str)
    assert isinstance(body["model"], str)


# ---------- Search filter — strong content assertions ----------


def test_sidebar_search_filters_by_name(title_filter_data_dir):
    """search=applekey — only NAME contains it. Filter must return
    exactly that conversation.
    """
    client = TestClient(app)
    r = client.get("/api/conversations", params={"search": "applekey"})
    assert r.status_code == 200
    uuids = [c["uuid"] for c in r.json()]
    assert uuids == ["conv-name"], uuids


def test_sidebar_search_filters_by_summary(title_filter_data_dir):
    """search=bananakey — only SUMMARY contains it. The server-side filter
    matches against summary; the client-side filter (intentionally)
    does not. Server-side test pins the contract for external API users.
    """
    client = TestClient(app)
    r = client.get("/api/conversations", params={"search": "bananakey"})
    assert r.status_code == 200
    uuids = [c["uuid"] for c in r.json()]
    assert uuids == ["conv-summary"], uuids


def test_sidebar_search_filters_by_project_path(title_filter_data_dir):
    """search=cherrykey — only PROJECT_PATH contains it. Returns only
    the conversation whose project_path mentions it.
    """
    client = TestClient(app)
    r = client.get("/api/conversations", params={"search": "cherrykey"})
    assert r.status_code == 200
    uuids = [c["uuid"] for c in r.json()]
    assert uuids == ["conv-path"], uuids


def test_sidebar_search_returns_empty_for_nonmatching_query(title_filter_data_dir):
    """Bidirectional pair: a query that matches nothing must return [].

    If this test passes but the matching tests above fail, the filter is
    broken in the "always return []" direction. If this fails (returns
    everything), the filter is broken in the "ignore query" direction.
    Together they pin both edges.
    """
    client = TestClient(app)
    r = client.get(
        "/api/conversations",
        params={"search": "zzzzznotinanycorpusvaluezzzzz"},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_sidebar_search_is_case_insensitive(title_filter_data_dir):
    """Filter is `search_lower in field.lower()`; APPLEKEY / applekey /
    AppleKey must all return the same conversation.
    """
    client = TestClient(app)
    for q in ("applekey", "APPLEKEY", "AppleKey"):
        r = client.get("/api/conversations", params={"search": q})
        assert r.status_code == 200
        uuids = [c["uuid"] for c in r.json()]
        assert uuids == ["conv-name"], f"q={q!r} → {uuids}"


def test_sidebar_search_different_queries_return_disjoint_results(
    title_filter_data_dir,
):
    """Tautology-protection: three orthogonal queries on disjoint fields
    must return disjoint result sets. If the filter is ignoring the
    query and returning everything, all three sets equal {all 3 uuids}.
    """
    client = TestClient(app)
    a = {c["uuid"] for c in client.get(
        "/api/conversations", params={"search": "applekey"}).json()}
    b = {c["uuid"] for c in client.get(
        "/api/conversations", params={"search": "bananakey"}).json()}
    c = {c["uuid"] for c in client.get(
        "/api/conversations", params={"search": "cherrykey"}).json()}

    assert a == {"conv-name"}, a
    assert b == {"conv-summary"}, b
    assert c == {"conv-path"}, c
    # Disjoint sanity:
    assert a & b == set()
    assert b & c == set()
    assert a & c == set()


def test_sidebar_search_unfiltered_returns_all(title_filter_data_dir):
    """No `search=` param → all conversations. Distinct from the
    `search` cases above; pins that filtering is opt-in.
    """
    client = TestClient(app)
    r = client.get("/api/conversations")
    assert r.status_code == 200
    uuids = sorted(c["uuid"] for c in r.json())
    assert uuids == ["conv-name", "conv-path", "conv-summary"]


def test_sidebar_search_source_filter(title_filter_data_dir):
    """source=CLAUDE_CODE on a Desktop-only seeded corpus → []."""
    client = TestClient(app)
    r = client.get(
        "/api/conversations",
        params={"search": "applekey", "source": "CLAUDE_CODE"},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_sidebar_search_sort_orders_results(title_filter_data_dir):
    """Sort by name asc/desc with a 2-hit query, confirm the order
    reverses. Catches a regression where `sort_order` is ignored.
    """
    client = TestClient(app)
    # All three conversations contain "plain" somewhere; this matches all 3.
    # (conv-name has "applekey title", but conv-summary and conv-path have
    # "plain title", and they all have "plain summary" except conv-name).
    # Use the literal "title" instead since it appears in every name.
    asc = client.get(
        "/api/conversations",
        params={"search": "title", "sort": "name", "sort_order": "asc"},
    ).json()
    desc = client.get(
        "/api/conversations",
        params={"search": "title", "sort": "name", "sort_order": "desc"},
    ).json()
    asc_names = [c["name"] for c in asc]
    desc_names = [c["name"] for c in desc]
    assert asc_names != [], "Sort test must have a non-empty result set."
    assert asc_names == list(reversed(desc_names)), (
        f"asc={asc_names} ; desc={desc_names}"
    )


# ---------------------------------------------------------------------------
# ?starred= and ?model= filter contracts (bidirectional)
#
# Previously: zero tests for either filter. Both flow through
# ``store.list_conversations`` at backend/store.py:431-434 — a regression
# that made either filter a no-op (returning everything regardless of the
# query parameter) would have passed the test suite.
#
# Added in the LLM council None-safety audit follow-up because the
# bug-class that caused the search crash was identical in shape: silent
# filter regression with no content-based test pinning the contract.
# ---------------------------------------------------------------------------


@pytest.fixture
def starred_model_data_dir(tmp_path, monkeypatch):
    """Three Desktop conversations with orthogonal (is_starred, model) tags:

      * conv-star-sonnet: is_starred=True,  model="claude-sonnet-4-6"
      * conv-plain-sonnet: is_starred=False, model="claude-sonnet-4-6"
      * conv-plain-opus:   is_starred=False, model="claude-opus-4-7"

    Lets each filter test assert that a query for X returns ONLY the
    conversations matching X — bidirectional with a non-match assertion
    that pins the "always-return-everything" failure mode.
    """
    convs = [
        {**_conv("conv-star-sonnet", "starred sonnet conv"), "is_starred": True,
         "model": "claude-sonnet-4-6"},
        {**_conv("conv-plain-sonnet", "plain sonnet conv"), "is_starred": False,
         "model": "claude-sonnet-4-6"},
        {**_conv("conv-plain-opus", "plain opus conv"), "is_starred": False,
         "model": "claude-opus-4-7"},
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


def test_starred_filter_true_returns_only_starred(starred_model_data_dir):
    """``?starred=true`` returns ONLY the starred conversation.

    A no-op regression in the filter (always returns everything) would
    yield all 3 conversations and fail this test.
    """
    client = TestClient(app)
    r = client.get("/api/conversations", params={"starred": "true"})
    assert r.status_code == 200
    uuids = sorted(c["uuid"] for c in r.json())
    assert uuids == ["conv-star-sonnet"], uuids


def test_starred_filter_false_returns_only_unstarred(starred_model_data_dir):
    """``?starred=false`` returns ONLY the unstarred conversations.

    Bidirectional with ``test_starred_filter_true_returns_only_starred``:
    if the filter is broken in the "always-return-everything" direction,
    one of these two tests fails. If broken in the "always-return-empty"
    direction, both fail.
    """
    client = TestClient(app)
    r = client.get("/api/conversations", params={"starred": "false"})
    assert r.status_code == 200
    uuids = sorted(c["uuid"] for c in r.json())
    assert uuids == ["conv-plain-opus", "conv-plain-sonnet"], uuids


def test_starred_filter_unset_returns_all(starred_model_data_dir):
    """No ``?starred=`` parameter → all conversations (filter is opt-in)."""
    client = TestClient(app)
    r = client.get("/api/conversations")
    assert r.status_code == 200
    uuids = sorted(c["uuid"] for c in r.json())
    assert uuids == ["conv-plain-opus", "conv-plain-sonnet", "conv-star-sonnet"], uuids


def test_model_filter_matches_exact(starred_model_data_dir):
    """``?model=claude-sonnet-4-6`` returns only the two sonnet conversations.

    Pins the EXACT-match contract: the store uses ``data.get("model") !=
    model`` for filtering (backend/store.py:433), so the filter is
    case-sensitive and does not prefix-match. Both ``claude-sonnet-4-6``
    rows survive; the opus row is excluded.
    """
    client = TestClient(app)
    r = client.get("/api/conversations", params={"model": "claude-sonnet-4-6"})
    assert r.status_code == 200
    uuids = sorted(c["uuid"] for c in r.json())
    assert uuids == ["conv-plain-sonnet", "conv-star-sonnet"], uuids


def test_model_filter_opus_returns_only_opus(starred_model_data_dir):
    """Bidirectional with the sonnet match: ``?model=claude-opus-4-7``
    returns ONLY the opus conversation. The two together prove the
    filter is reading the parameter, not ignoring it.
    """
    client = TestClient(app)
    r = client.get("/api/conversations", params={"model": "claude-opus-4-7"})
    assert r.status_code == 200
    uuids = sorted(c["uuid"] for c in r.json())
    assert uuids == ["conv-plain-opus"], uuids


def test_model_filter_nonmatching_returns_empty(starred_model_data_dir):
    """``?model=claude-nonexistent-model`` returns []. Pins the
    "always-return-everything" failure mode: if the filter is a no-op,
    this test fails because all 3 conversations are returned.
    """
    client = TestClient(app)
    r = client.get(
        "/api/conversations",
        params={"model": "claude-nonexistent-model"},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_starred_and_model_filters_compose(starred_model_data_dir):
    """``?starred=true&model=claude-sonnet-4-6`` returns ONLY the
    intersection. If either filter is broken, this fails:

      * Broken ``starred`` → returns both sonnet conversations.
      * Broken ``model``   → returns only the starred conversation but
        with the wrong shape (would match if both opus and sonnet
        starred convs existed).
    """
    client = TestClient(app)
    r = client.get(
        "/api/conversations",
        params={"starred": "true", "model": "claude-sonnet-4-6"},
    )
    assert r.status_code == 200
    uuids = sorted(c["uuid"] for c in r.json())
    assert uuids == ["conv-star-sonnet"], uuids


# ---------------------------------------------------------------------------
# Coercion-audit hardening: corrupt-JSON-on-disk regression tests.
#
# Same bug-class as the null-safety hunt (8ab36fc) and the parse_datetime
# widening (this commit's sibling change). The on-disk JSON files under
# ``~/.claude-explorer/conversations/`` are producer-controlled in the
# happy path but a hand-edit / partial-write / corrupted-fetch can leave
# fields with the WRONG TYPE (not just None). Each of these tests writes
# a single conversation with a poisoned field, hits the route, and asserts
# 200 — proving the route degrades gracefully instead of returning 500
# and breaking the entire UI.
#
# The blast-radius rationale: ``list_conversations`` iterates and calls
# ``_make_summary`` on every file. ONE corrupt file's unhandled exception
# = sidebar empty for ALL conversations. Pinning the 200 outcome here
# blocks any future "just propagate the exception" refactor.
# ---------------------------------------------------------------------------


def _seed_corrupt_conv(tmp_path, monkeypatch, conv: dict) -> None:
    """Helper: seed a SINGLE Desktop conversation JSON and point the
    backend at the isolated data dir. Mirrors the fixture-setup in
    ``test_get_conversation_handles_null_chat_messages_without_crashing``
    but as a callable so each test gets its own poisoned shape without
    needing N fixtures.
    """
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True)
    (by_org / f"{conv['uuid']}.json").write_text(json.dumps(conv))
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_DIR", str(empty_claude))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()


def test_list_conversations_handles_int_created_at_without_500(
    tmp_path, monkeypatch
):
    """A conversation with ``created_at: 12345`` (int instead of ISO
    string) must not 500 the sidebar list endpoint.

    Pre-fix: ``parse_datetime(12345)`` raised AttributeError on
    ``.endswith("Z")`` because the widening had not happened. Because
    ``_make_summary`` is called inside the ``list_conversations`` loop,
    that single bad row took out the entire sidebar.
    """
    conv = {
        "uuid": "conv-int-created-at",
        "name": "corrupt timestamp",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": 12345,  # poisoned: int, not ISO string
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        "chat_messages": [],
    }
    _seed_corrupt_conv(tmp_path, monkeypatch, conv)

    client = TestClient(app)
    r = client.get("/api/conversations")
    assert r.status_code == 200, r.text
    body = r.json()
    # The conversation IS included — the corrupt-timestamp fallback
    # produces a now-UTC stamp, NOT a row drop.
    uuids = [c["uuid"] for c in body]
    assert "conv-int-created-at" in uuids


def test_list_conversations_handles_dict_updated_at_without_500(
    tmp_path, monkeypatch
):
    """A conversation with ``updated_at: {"weird": "shape"}`` must not 500."""
    conv = {
        "uuid": "conv-dict-updated-at",
        "name": "corrupt updated stamp",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": {"nested": "object"},  # poisoned
        "is_starred": False,
        "source": "CLAUDE_AI",
        "chat_messages": [],
    }
    _seed_corrupt_conv(tmp_path, monkeypatch, conv)

    client = TestClient(app)
    r = client.get("/api/conversations")
    assert r.status_code == 200, r.text


def test_get_conversation_handles_non_numeric_prelude_hidden_count(
    tmp_path, monkeypatch
):
    """``GET /api/conversations/{uuid}`` with a non-numeric
    ``prelude_hidden_count`` value (corrupt JSON: string ``"foo"``)
    must not 500.

    Pre-fix: ``int(data.get("prelude_hidden_count") or 0)`` raised
    ``ValueError: invalid literal for int() with base 10: 'foo'``
    because the ``or 0`` collapses None / 0 but NOT a non-numeric
    truthy string. Council coercion-audit MED finding (store.py:602).

    The fix is a try/except that defaults to 0 on either ValueError
    or TypeError (covers list/dict shapes too). The route should
    return 200 with ``prelude_hidden_count`` absent from the wire
    shape (it's not exposed on ConversationDetail's public fields
    other than internally) OR present as 0 — we don't assert on the
    exact wire shape, only on the non-crash contract.
    """
    conv = {
        "uuid": "conv-bad-prelude",
        "name": "corrupt prelude count",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        "chat_messages": [],
        "prelude_hidden_count": "foo",  # poisoned: non-numeric string
    }
    _seed_corrupt_conv(tmp_path, monkeypatch, conv)

    client = TestClient(app)
    r = client.get("/api/conversations/conv-bad-prelude")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["uuid"] == "conv-bad-prelude"


def test_get_conversation_handles_list_prelude_hidden_count(
    tmp_path, monkeypatch
):
    """Same as above but with a list value (``[1, 2]``) which raises
    ``TypeError: int() argument must be a string... not 'list'`` rather
    than ValueError. Ensures both exception types are guarded."""
    conv = {
        "uuid": "conv-list-prelude",
        "name": "corrupt prelude count list",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        "chat_messages": [],
        "prelude_hidden_count": [1, 2],  # poisoned: list
    }
    _seed_corrupt_conv(tmp_path, monkeypatch, conv)

    client = TestClient(app)
    r = client.get("/api/conversations/conv-list-prelude")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Hunt #1 follow-up — corrupt ``message_count`` / ``human_message_count``
# on the list-summary path.
#
# Same bug-class as the prelude_hidden_count fix (commit 15c4fc5):
# ``_make_summary`` reads these counts straight from disk in the
# no-chat-messages branch (store.py:336-337), then passes them to the
# Pydantic ``int``-typed fields on ``ConversationSummary``. Pydantic v2
# DOES coerce ``"42"`` to 42 (lax mode), but rejects ``"foo"`` /
# ``null`` / list / dict with a ValidationError → HTTP 500 — and
# because the bad row is inside the ``list_conversations`` loop, ONE
# corrupt file empties the sidebar for ALL conversations.
#
# The fast-reader path (cc_jsonl_io / claude_code_reader) only emits
# integers, so production data won't trigger this. The realistic
# source is hand-edited JSON or a partial-write crash mid-fetch.
# ---------------------------------------------------------------------------


def test_list_conversations_handles_non_numeric_message_count_without_500(
    tmp_path, monkeypatch
):
    """A conversation with ``message_count: "foo"`` and an absent /
    null ``chat_messages`` (so the else-branch in _make_summary fires)
    must not 500 the sidebar list endpoint.

    Pre-fix: Pydantic v2 raised
    ``ValidationError: Input should be a valid integer, unable to
    parse string as an integer`` when ``ConversationSummary`` was
    instantiated with ``message_count="foo"``.
    """
    conv = {
        "uuid": "conv-bad-msg-count",
        "name": "corrupt message count",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        # chat_messages absent → else-branch in _make_summary reads
        # message_count from data.get(...) directly.
        "message_count": "foo",  # poisoned: non-numeric string
        "human_message_count": 0,
    }
    _seed_corrupt_conv(tmp_path, monkeypatch, conv)

    client = TestClient(app)
    r = client.get("/api/conversations")
    assert r.status_code == 200, r.text
    body = r.json()
    uuids = [c["uuid"] for c in body]
    assert "conv-bad-msg-count" in uuids, (
        "corrupt row must still appear (with safe-default count), not "
        "vanish from the sidebar"
    )


def test_list_conversations_handles_null_human_message_count_without_500(
    tmp_path, monkeypatch
):
    """A conversation with ``human_message_count: null`` (explicit JSON
    null, distinct from missing) must not 500.

    Pre-fix: ``data.get("human_message_count", 0)`` returns ``None``
    (not the default!) for a key whose value is null. Pydantic v2
    rejects ``int = None`` with ValidationError.
    """
    conv = {
        "uuid": "conv-null-human-count",
        "name": "null human count",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        "message_count": 5,
        "human_message_count": None,  # poisoned: explicit null
    }
    _seed_corrupt_conv(tmp_path, monkeypatch, conv)

    client = TestClient(app)
    r = client.get("/api/conversations")
    assert r.status_code == 200, r.text
    uuids = [c["uuid"] for c in r.json()]
    assert "conv-null-human-count" in uuids


def test_list_conversations_handles_list_message_count_without_500(
    tmp_path, monkeypatch
):
    """List shape on ``message_count`` — exercises the TypeError /
    int_type validation path."""
    conv = {
        "uuid": "conv-list-msg-count",
        "name": "list message count",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        "message_count": [1, 2, 3],  # poisoned: list
        "human_message_count": 0,
    }
    _seed_corrupt_conv(tmp_path, monkeypatch, conv)

    client = TestClient(app)
    r = client.get("/api/conversations")
    assert r.status_code == 200, r.text


