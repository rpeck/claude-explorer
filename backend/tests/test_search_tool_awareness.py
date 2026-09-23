"""Tool-aware FTS5 projection — RED phase (will fail until impl lands).

This file pins the contract for the two-column FTS5 schema (v6 → v7):
``messages.body`` keeps the full projection (text + tool_use + tool_result),
``messages.body_text`` carries the text-only projection (tool blocks
stripped). The query path selects the right column based on the per-
request ``include_tool_calls`` flag, so a hit whose only token lives in a
tool block is excluded BEFORE bm25 ranks — exact parity with the linear-
scan path's behavior.

Plan reference:
``PLANS/SEARCH_TOOL_AWARENESS_AND_LIMIT_DISCLOSURE.md`` §A.

Bidirectional verification per TESTING.md §2: every "absent" or
"present" assertion is paired with its opposite under the flipped toggle.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from backend import search_index as si
from backend.search import (
    _search_via_index_fast,
    _search_via_linear_scan,
)


# ----- fixtures --------------------------------------------------------


class FakeStore:
    """Stand-in for ConversationStore.get_all_conversations_raw()."""

    def __init__(self, conversations: list[dict[str, Any]]):
        self._conversations = conversations

    def get_all_conversations_raw(self, source: str = "all") -> list[dict[str, Any]]:
        return self._conversations


def _msg(
    uuid: str,
    *,
    sender: str = "assistant",
    text: str = "",
    content: list[dict[str, Any]] | None = None,
    created_at: str = "2026-05-16T12:00:00Z",
) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "sender": sender,
        "text": text,
        "content": content or [],
        "created_at": created_at,
        "updated_at": created_at,
        "parent_message_uuid": None,
    }


def _conv(
    uuid: str,
    name: str,
    messages: list[dict[str, Any]],
    *,
    source: str = "CLAUDE_AI",
    project_path: str | None = None,
) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "name": name,
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-16T12:00:00Z",
        "updated_at": "2026-05-16T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else "",
        "project_path": project_path,
        "source": source,
        "chat_messages": messages,
    }


# Token-unique fixtures so search results map to exactly one conv.
TOKEN_TOOL_ONLY = "ripgrepfoo"  # only inside a tool_use input
TOKEN_TEXT_ONLY = "alphacheck"  # only inside a text block
TOKEN_MIXED_TEXT = "betacheck"  # text + tool_use both carry it
TOKEN_PHRASE = "deltagamma"  # for phrase query test


def _conv_tool_only() -> dict[str, Any]:
    return _conv("conv-tool-only", "Tool-only conv", [
        _msg("m-tu", text="",
             content=[{
                 "type": "tool_use",
                 "id": "tu-1",
                 "name": "Bash",
                 "input": {"command": f"echo {TOKEN_TOOL_ONLY}"},
             }]),
    ])


def _conv_text_only() -> dict[str, Any]:
    return _conv("conv-text-only", "Text-only conv", [
        _msg("m-text", text=f"Let me {TOKEN_TEXT_ONLY} this thing",
             content=[{"type": "text",
                       "text": f"Let me {TOKEN_TEXT_ONLY} this thing"}]),
    ])


def _conv_mixed() -> dict[str, Any]:
    """A message with the token in BOTH a text block and a tool block.

    Under include_tool_calls=False the body_text column still has it via
    the text block, so the message must still be found.
    """
    return _conv("conv-mixed", "Mixed conv", [
        _msg("m-mixed", text=f"Let me {TOKEN_MIXED_TEXT} this thing",
             content=[
                 {"type": "text",
                  "text": f"Let me {TOKEN_MIXED_TEXT} this thing"},
                 {"type": "tool_use", "id": "tu-2", "name": "Bash",
                  "input": {"command": f"echo {TOKEN_MIXED_TEXT}"}},
             ]),
    ])


@pytest.fixture(autouse=True)
def _reset_singleton():
    si.reset_search_index_for_tests()
    yield
    si.reset_search_index_for_tests()


@pytest.fixture
def fresh_index(tmp_path):
    """Per-test SearchIndex pointed at tmp_path — no module-level singleton."""
    idx = si.SearchIndex(tmp_path / "fixture-index.sqlite")
    yield idx
    idx.close()


@pytest.fixture
def fixture_idx(tmp_path):
    """SearchIndex pre-populated with the tool-awareness fixtures."""
    idx = si.SearchIndex(tmp_path / "tool-awareness.sqlite")
    for c in [
        _conv_tool_only(),
        _conv_text_only(),
        _conv_mixed(),
    ]:
        idx.upsert_conversation(c, tmp_path / f"{c['uuid']}.json", 1.0)
    idx.mark_ready()
    yield idx
    idx.close()


# ----- 1. Schema has body_text column ----------------------------------


def test_schema_has_body_text_column(fresh_index) -> None:
    """``messages`` virtual table includes a ``body_text`` indexed column
    alongside ``body``. Bug it would surface: forgetting to add the new
    column to SCHEMA_SQL or to _EXPECTED_MESSAGES_COLS.
    """
    conn = fresh_index._get_read_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    assert "body" in cols, "legacy body column must remain"
    assert "body_text" in cols, (
        "two-column projection requires body_text alongside body "
        "(plan §A — SCHEMA_VERSION 6 → 7)"
    )


# ----- 2. Schema version bumped → auto-rebuild fires --------------------


def test_schema_version_bumped_to_at_least_7() -> None:
    """SCHEMA_VERSION is at least 7 so existing v6 indexes drop+rebuild.

    Originally pinned to ==7 for the body_text rollout. Loosened to
    >=7 once the doubled-snippet fix bumped to v8 — the contract this
    test pins is "v6-and-earlier installs MUST rebuild," not "the
    current version is exactly 7."

    Bug it would surface: bumping the column set without bumping the
    version. Old installs would either keep the old schema indefinitely
    (column-drift detector flag would have to catch it) or rebuild only
    by accident if a different column-set check fired.
    """
    assert si.SCHEMA_VERSION >= 7, (
        f"SCHEMA_VERSION must be ≥7 for body_text rollout; got {si.SCHEMA_VERSION}"
    )


def test_schema_v6_to_current_triggers_rebuild(tmp_path) -> None:
    """Open an on-disk index whose messages table predates body_text.
    Constructing a SearchIndex must drop+rebuild so the current schema
    lands and ``body_text`` becomes a real column.

    Bug it would surface: the version check passing on a v6 file (e.g.
    by reading SCHEMA_VERSION from a stale constant) — body_text would
    never appear and the column-MATCH SQL would raise "no such column".
    """
    db_path = tmp_path / "legacy-v6.sqlite"
    # Hand-craft a v6 messages table (no body_text) plus version row.
    with sqlite3.connect(str(db_path)) as raw:
        raw.execute("""
            CREATE VIRTUAL TABLE messages USING fts5(
                conv_uuid UNINDEXED,
                message_uuid UNINDEXED,
                sender UNINDEXED,
                created_at UNINDEXED,
                source UNINDEXED,
                project_path UNINDEXED,
                organization_id UNINDEXED,
                conv_created_at UNINDEXED,
                conv_updated_at UNINDEXED,
                title,
                body,
                tokenize = "porter unicode61 remove_diacritics 1"
            )
        """)
        raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        raw.execute("INSERT INTO schema_version (version) VALUES (6)")
        raw.execute(
            "INSERT INTO messages "
            "(conv_uuid, message_uuid, sender, created_at, source, "
            " project_path, organization_id, conv_created_at, "
            " conv_updated_at, title, body) "
            "VALUES ('stale','m','human','','CLAUDE_AI','','','','','t','b')"
        )
        raw.commit()

    # Open with the real SearchIndex — must rebuild to the current
    # SCHEMA_VERSION (≥7) so body_text appears.
    idx = si.SearchIndex(db_path)
    try:
        conn = idx._get_read_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        assert "body_text" in cols, (
            "v6 → current open must drop+rebuild so body_text appears"
        )
        # And the stale row is gone (rebuild dropped it).
        row_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        assert row_count == 0, (
            "rebuild must DROP the messages table; got "
            f"{row_count} stale rows"
        )
        sv = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        assert sv == (si.SCHEMA_VERSION,), (
            f"schema_version row must be ({si.SCHEMA_VERSION},); got {sv}"
        )
    finally:
        idx.close()


# ----- 3. Upsert populates both columns ---------------------------------


def test_upsert_populates_both_body_columns(fresh_index, tmp_path) -> None:
    """A message with both text and tool content lands twice in the table:
    ``body`` contains the FULL projection (text + tool_use input); body_text
    excludes the tool input.

    Bug it would surface: upsert writing the same projection to both
    columns (would defeat the whole point of the schema bump).
    """
    conv = _conv_mixed()
    fresh_index.upsert_conversation(conv, tmp_path / f"{conv['uuid']}.json", 1.0)
    conn = fresh_index._get_read_conn()
    rows = conn.execute(
        "SELECT body, body_text FROM messages WHERE conv_uuid = ?",
        ("conv-mixed",),
    ).fetchall()
    assert len(rows) == 1, f"expected one upserted row; got {len(rows)}"
    body, body_text = rows[0]
    # FULL projection has both the text AND the tool_use input.
    assert TOKEN_MIXED_TEXT in body, "body must include text token"
    assert "echo" in body, "body must include tool_use input verbatim"
    # Text-only projection has the text but NOT the tool_use input.
    assert TOKEN_MIXED_TEXT in body_text, (
        "body_text must include text-block content"
    )
    assert "echo" not in body_text, (
        "body_text must EXCLUDE tool_use input — that's the whole point"
    )


# ----- 4 & 5. include_tool_calls toggle gating on FTS5 path ---------------


def test_body_match_includes_tool_only_when_flag_true(fixture_idx) -> None:
    """``query_with_snippets(include_tool_calls=True)`` matches a token
    that ONLY appears inside a tool_use block (legacy behavior).

    Bug it would surface: defaulting to body_text always — tool hits
    would silently vanish for legacy callers that haven't been updated.
    """
    rows = fixture_idx.query_with_snippets(
        TOKEN_TOOL_ONLY,
        include_tool_calls=True,
    )
    conv_uuids = {r["conv_uuid"] for r in rows}
    assert "conv-tool-only" in conv_uuids, (
        f"include_tool_calls=True must find tool-only token; got {conv_uuids}"
    )


def test_body_match_excludes_tool_only_when_flag_false(fixture_idx) -> None:
    """``query_with_snippets(include_tool_calls=False)`` does NOT match a
    token that ONLY appears inside a tool_use block — column-MATCH on
    body_text excludes it BEFORE bm25 ranks.

    Bug it would surface: ignoring the flag and always querying body.
    """
    rows = fixture_idx.query_with_snippets(
        TOKEN_TOOL_ONLY,
        include_tool_calls=False,
    )
    conv_uuids = {r["conv_uuid"] for r in rows}
    assert "conv-tool-only" not in conv_uuids, (
        "tool-only match must vanish with include_tool_calls=False; "
        f"got {conv_uuids}"
    )


# ----- 6. Mixed-content: text match survives the toggle -----------------


def test_mixed_message_text_match_present_when_flag_false(fixture_idx) -> None:
    """A message with text content "Let me {TOKEN_MIXED_TEXT} this thing"
    and a tool_use block carrying the same token. Querying the text-half
    word ``Let`` (or the shared token) under include_tool_calls=False
    MUST still find the message — the text block lives in body_text too.

    Bug it would surface: body_text projector accidentally dropping
    text-block content as well as tool content.
    """
    rows = fixture_idx.query_with_snippets(
        TOKEN_MIXED_TEXT,
        include_tool_calls=False,
    )
    conv_uuids = {r["conv_uuid"] for r in rows}
    assert "conv-mixed" in conv_uuids, (
        "mixed message must still match when text-half carries the token"
    )


# ----- 7. Equivalence with linear-scan path -----------------------------


@pytest.mark.parametrize(
    "token,include_tool_calls,expected_conv_uuids",
    [
        # Tool-only hit: linear scan drops under filter ON; FTS5 must agree.
        (TOKEN_TOOL_ONLY, False, set()),
        (TOKEN_TOOL_ONLY, True, {"conv-tool-only"}),
        # Text-only hit: present in both modes.
        (TOKEN_TEXT_ONLY, False, {"conv-text-only"}),
        (TOKEN_TEXT_ONLY, True, {"conv-text-only"}),
        # Mixed: present in both modes (text half carries it).
        (TOKEN_MIXED_TEXT, False, {"conv-mixed"}),
        (TOKEN_MIXED_TEXT, True, {"conv-mixed"}),
    ],
)
def test_fast_path_matches_linear_under_toggle(
    fixture_idx, token, include_tool_calls, expected_conv_uuids,
) -> None:
    """For each (token, toggle) pair, the FTS5 fast path's
    ``(conv_uuid)`` result set must match what the linear-scan path
    returns AND match the documented expectation.

    Bug it would surface: silent FTS5/linear-scan drift introduced by
    the new column-MATCH SQL (e.g. dropping the filter on the AND
    clauses, or projecting body_text incorrectly).
    """
    store = FakeStore([
        _conv_tool_only(),
        _conv_text_only(),
        _conv_mixed(),
    ])
    linear_results = _search_via_linear_scan(
        store, token, include_tool_calls=include_tool_calls,
    )
    linear_uuids = {r.conversation_uuid for r in linear_results}
    fast_response = _search_via_index_fast(
        store, fixture_idx, token,
        source="all", sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None, bookmarks=None,
        include_tool_calls=include_tool_calls,
    )
    fast_uuids = {r.conversation_uuid for r in fast_response.results}
    assert linear_uuids == expected_conv_uuids, (
        f"linear scan returned {linear_uuids}; expected {expected_conv_uuids}"
    )
    assert fast_uuids == expected_conv_uuids, (
        f"fast path returned {fast_uuids}; expected {expected_conv_uuids}"
    )


# ----- 8. Phrase + multi-word AND queries compose with column qualifier -


def test_column_qualifier_composes_with_phrase_query(fresh_index, tmp_path) -> None:
    """A phrase query (``"deltagamma omega"``) goes through translate_query
    which emits an FTS5 phrase expression. The body_text column-qualifier
    prefix MUST compose cleanly — no syntax errors, the phrase matches
    inside body_text but not inside body-only content.

    Bug it would surface: column-qualifier prefix breaking phrase
    grammar (e.g. ``{body_text} : ("foo bar")`` failing to parse) —
    would raise sqlite3.OperationalError or silently fall back.
    """
    conv = _conv("conv-phrase", "Phrase conv", [
        _msg("m-phrase",
             text=f"prefix {TOKEN_PHRASE} omega suffix",
             content=[
                 {"type": "text",
                  "text": f"prefix {TOKEN_PHRASE} omega suffix"},
             ]),
    ])
    fresh_index.upsert_conversation(
        conv, tmp_path / f"{conv['uuid']}.json", 1.0,
    )
    fresh_index.mark_ready()
    # Phrase query, both modes, must find the message.
    for flag in (True, False):
        rows = fresh_index.query_with_snippets(
            f'"{TOKEN_PHRASE} omega"',
            include_tool_calls=flag,
        )
        uuids = {r["conv_uuid"] for r in rows}
        assert "conv-phrase" in uuids, (
            f"phrase query with include_tool_calls={flag} must find "
            f"conv-phrase; got {uuids}"
        )


def test_column_qualifier_composes_with_multiword_and(fresh_index, tmp_path) -> None:
    """A multi-word query (``alpha bravo``) is translated to
    ``"alpha" AND "bravo" *`` by translate_query. The column-qualifier
    prefix MUST compose cleanly with this AND expression.

    Bug it would surface: precedence error in the column-qualifier
    grouping that turns AND-of-tokens into something else.
    """
    conv = _conv("conv-multi", "Multi-word conv", [
        _msg("m-multi",
             text="alphacat sits next to bravodog",
             content=[
                 {"type": "text",
                  "text": "alphacat sits next to bravodog"},
             ]),
    ])
    fresh_index.upsert_conversation(
        conv, tmp_path / f"{conv['uuid']}.json", 1.0,
    )
    fresh_index.mark_ready()
    for flag in (True, False):
        rows = fresh_index.query_with_snippets(
            "alphacat bravodog",
            include_tool_calls=flag,
        )
        uuids = {r["conv_uuid"] for r in rows}
        assert "conv-multi" in uuids, (
            f"multi-word AND with include_tool_calls={flag} must find "
            f"conv-multi; got {uuids}"
        )
