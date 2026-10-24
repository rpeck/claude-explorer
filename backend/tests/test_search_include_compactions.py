"""Compaction-aware FTS5 projection — RED phase tests (2026-05-26).

User-observable contract this pins:

  When the "Show Compactions" checkbox in the conversation header is OFF
  (``hideCompactMarkers=True`` → ``include_compactions=False`` on the wire),
  the search endpoint MUST NOT return hits whose match falls inside a
  compaction-summary message body (``isCompactSummary: true`` rows).

  The contract mirrors ``include_tool_calls`` (the "Show Tools" checkbox),
  documented in ``articles/part_2_web_app.md`` line 252:

    "Search results also respect the Tools toggle in the conversation
    header, so a hit you couldn't see in the viewer never shows up in
    the result list either."

  Same rule must apply to compactions.

Architecture (per Council decision 2026-05-26):

  * NEW UNINDEXED FTS5 column ``is_compaction_summary`` (boolean, stored
    as INTEGER 0/1). The indexer sets it from a UUID set built from
    ``conv['compact_markers']`` — same approach the exporters use
    (``_is_compact_summary_message`` in backend/exporters/_shared.py).
  * ``_build_match_where_clause`` adds ``AND is_compaction_summary = 0``
    when ``include_compactions=False``, so the filter applies BEFORE
    bm25 ranking + LIMIT.
  * Linear-scan fallback applies the same filter at the message-loop
    emit site.

Bidirectional verification per CLAUDE-TESTING §2: every "absent" or
"present" assertion is paired with its opposite under the flipped
toggle.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

import pytest

from backend import search_index as si
from backend.compact_prefixes import COMPACTION_TITLE_PREFIX
from backend.search import (
    _search_via_index,
    _search_via_index_fast,
    _search_via_index_fast_full,
    _search_via_linear_scan,
    search_conversations,
)

# ----- fixtures --------------------------------------------------------


class FakeStore:
    """Stand-in for ConversationStore.get_all_conversations_raw()."""

    def __init__(self, conversations: list[dict[str, Any]]):
        self._conversations = conversations

    def get_all_conversations_raw(self, source: str = "all") -> list[dict[str, Any]]:
        return self._conversations


# Token-unique fixtures so search results map to exactly one conv.
TOKEN_COMPACT_ONLY = "zebraquark"  # only inside an isCompactSummary body
TOKEN_REGULAR_ONLY = "alphacheck"  # only inside a regular text block
TOKEN_BOTH = "betacheck"  # in compaction summary AND a regular text message


def _msg(
    uuid: str,
    *,
    sender: str = "human",
    text: str = "",
    content: list[dict[str, Any]] | None = None,
    is_compact_summary: bool = False,
    created_at: str = "2026-05-26T12:00:00Z",
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "uuid": uuid,
        "sender": sender,
        "text": text,
        "content": content if content is not None else (
            [{"type": "text", "text": text}] if text else []
        ),
        "created_at": created_at,
        "updated_at": created_at,
        "parent_message_uuid": None,
    }
    if is_compact_summary:
        out["isCompactSummary"] = True
    return out


def _conv(
    uuid: str,
    name: str,
    messages: list[dict[str, Any]],
    *,
    source: str = "CLAUDE_CODE",
    project_path: str | None = "/tmp/proj",
) -> dict[str, Any]:
    # Derive compact_markers from any isCompactSummary message — same
    # shape backend.cc_image_markers.extract_compact_markers produces.
    compact_markers = [
        {
            "message_uuid": m["uuid"],
            "summary_text": m.get("text", ""),
            "timestamp": m.get("created_at", ""),
            "kind": "auto",
            "user_prompt": None,
        }
        for m in messages
        if m.get("isCompactSummary") is True
    ]
    return {
        "uuid": uuid,
        "name": name,
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-26T12:00:00Z",
        "updated_at": "2026-05-26T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else "",
        "project_path": project_path,
        "source": source,
        "chat_messages": messages,
        "compact_markers": compact_markers,
    }


def _conv_compact_only() -> dict[str, Any]:
    """A conv whose ONLY hit on TOKEN_COMPACT_ONLY is inside an
    isCompactSummary row."""
    return _conv(
        "conv-compact-only",
        "Compact-only conv",
        [
            _msg("m-pre", sender="human", text="Start a long task."),
            _msg(
                "m-summary",
                sender="human",  # CC marks the synthetic row as human
                text=f"Summary mentioning {TOKEN_COMPACT_ONLY} keyword.",
                is_compact_summary=True,
            ),
            _msg("m-post", sender="assistant", text="Continuing after compaction."),
        ],
    )


def _conv_regular_only() -> dict[str, Any]:
    """A conv with a hit on TOKEN_REGULAR_ONLY in a REGULAR text block —
    no compaction summary at all."""
    return _conv(
        "conv-regular-only",
        "Regular conv",
        [
            _msg("r-1", sender="human", text=f"Hello {TOKEN_REGULAR_ONLY} world."),
        ],
    )


def _conv_both() -> dict[str, Any]:
    """A conv with TOKEN_BOTH in BOTH a regular text block AND an
    isCompactSummary row. Under include_compactions=False the regular
    text still matches → conv still appears, but the compaction row's
    snippet must NOT be in the results."""
    return _conv(
        "conv-both",
        "Both conv",
        [
            _msg("b-text", sender="human", text=f"Regular {TOKEN_BOTH} content here."),
            _msg(
                "b-summary",
                sender="human",
                text=f"Compact summary also mentioning {TOKEN_BOTH}.",
                is_compact_summary=True,
            ),
        ],
    )


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
    """SearchIndex pre-populated with the compaction fixtures."""
    idx = si.SearchIndex(tmp_path / "compaction-awareness.sqlite")
    for c in [
        _conv_compact_only(),
        _conv_regular_only(),
        _conv_both(),
    ]:
        idx.upsert_conversation(c, tmp_path / f"{c['uuid']}.json", 1.0)
    idx.mark_ready()
    yield idx
    idx.close()


# ----- 1. Schema has is_compaction_summary column ----------------------


def test_schema_has_is_compaction_summary_column(fresh_index) -> None:
    """``messages`` virtual table includes ``is_compaction_summary`` as an
    UNINDEXED column.

    Bug it would surface: forgetting to add the column to SCHEMA_SQL or
    to _EXPECTED_MESSAGES_COLS — would cause the column-drift detector
    to either rebuild every time (no-op via SCHEMA_VERSION but bad
    smell) or worse, accept a stale table and produce "no such column"
    errors at MATCH time.
    """
    conn = fresh_index._get_read_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    assert "is_compaction_summary" in cols, (
        "compaction-aware filtering requires an is_compaction_summary "
        "column on the messages FTS5 table (Council decision 2026-05-26)"
    )


# ----- 2. SCHEMA_VERSION bumped to force rebuild ------------------------


def test_schema_version_bumped_for_is_compaction_summary() -> None:
    """SCHEMA_VERSION must be >=13 so existing v12 indexes drop+rebuild
    with the new column populated.

    Bug it would surface: bumping the column set but forgetting the
    version bump. Existing on-disk indexes would keep the v12 schema
    (no is_compaction_summary column) and the column-drift detector
    would fire — but only if column-set check is also updated. Belt-
    and-suspenders.
    """
    assert si.SCHEMA_VERSION >= 13, (
        f"SCHEMA_VERSION must be >=13 for is_compaction_summary rollout; "
        f"got {si.SCHEMA_VERSION}"
    )


# ----- 3. Upsert populates is_compaction_summary correctly --------------


def test_upsert_marks_compaction_summary_rows(fresh_index, tmp_path) -> None:
    """``upsert_conversation`` reads ``conv['compact_markers']`` and sets
    is_compaction_summary=1 for messages whose uuid is in the marker
    set, 0 for all other messages.

    Bug it would surface: indexer hardcoding 0 (the filter would have
    nothing to filter) OR hardcoding 1 (everything filtered).
    """
    conv = _conv_compact_only()
    fresh_index.upsert_conversation(conv, tmp_path / f"{conv['uuid']}.json", 1.0)
    conn = fresh_index._get_read_conn()
    rows = conn.execute(
        "SELECT message_uuid, is_compaction_summary FROM messages "
        "WHERE conv_uuid = ? ORDER BY message_uuid",
        ("conv-compact-only",),
    ).fetchall()
    by_uuid = dict(rows)
    assert by_uuid["m-pre"] == 0, (
        "regular message must have is_compaction_summary=0"
    )
    assert by_uuid["m-summary"] == 1, (
        "isCompactSummary row must have is_compaction_summary=1 — "
        "the indexer must consult conv['compact_markers']"
    )
    assert by_uuid["m-post"] == 0, (
        "post-compaction regular message must have is_compaction_summary=0"
    )


# ----- 4. FTS5 fast path respects include_compactions -------------------


def test_fast_path_includes_compact_hit_when_flag_true(fixture_idx) -> None:
    """``include_compactions=True`` (default): hits on compaction-only
    tokens MUST surface — preserves backward-compat for external API
    callers and the in-app default."""
    response = _search_via_index_fast(
        FakeStore([_conv_compact_only(), _conv_regular_only(), _conv_both()]),
        fixture_idx,
        TOKEN_COMPACT_ONLY,
        source="all",
        sort="updated_at",
        sort_order="desc",
        conversation_uuid=None,
        project_path=None,
        bookmarks=None,
        include_tool_calls=True,
        include_compactions=True,
    )
    conv_uuids = {r.conversation_uuid for r in response.results}
    assert "conv-compact-only" in conv_uuids, (
        f"include_compactions=True must find compaction-only token; "
        f"got {conv_uuids}"
    )


def test_fast_path_excludes_compact_hit_when_flag_false(fixture_idx) -> None:
    """``include_compactions=False``: hits inside isCompactSummary rows
    MUST be dropped at MATCH time — same architectural treatment as
    include_tool_calls=False on body_text."""
    response = _search_via_index_fast(
        FakeStore([_conv_compact_only(), _conv_regular_only(), _conv_both()]),
        fixture_idx,
        TOKEN_COMPACT_ONLY,
        source="all",
        sort="updated_at",
        sort_order="desc",
        conversation_uuid=None,
        project_path=None,
        bookmarks=None,
        include_tool_calls=True,
        include_compactions=False,
    )
    conv_uuids = {r.conversation_uuid for r in response.results}
    assert "conv-compact-only" not in conv_uuids, (
        "compaction-only match must vanish with include_compactions=False; "
        f"got {conv_uuids}"
    )


def test_fast_path_both_conv_keeps_regular_hit_when_flag_false(
    fixture_idx,
) -> None:
    """A conv with TOKEN_BOTH in BOTH a regular block AND a compaction
    summary: under include_compactions=False the regular block still
    matches → conv still appears, BUT the compaction-row snippet must
    NOT be in matching_messages.

    Bug it would surface: filter too aggressive (drops the whole conv
    even when a non-compaction message ALSO matches the query).
    """
    response = _search_via_index_fast(
        FakeStore([_conv_compact_only(), _conv_regular_only(), _conv_both()]),
        fixture_idx,
        TOKEN_BOTH,
        source="all",
        sort="updated_at",
        sort_order="desc",
        conversation_uuid=None,
        project_path=None,
        bookmarks=None,
        include_tool_calls=True,
        include_compactions=False,
    )
    by_conv = {r.conversation_uuid: r for r in response.results}
    assert "conv-both" in by_conv, (
        "conv-both must still appear (regular block matches); "
        f"got {list(by_conv.keys())}"
    )
    msg_uuids = {m.message_uuid for m in by_conv["conv-both"].matching_messages}
    assert "b-text" in msg_uuids, (
        "regular-text hit must survive include_compactions=False"
    )
    assert "b-summary" not in msg_uuids, (
        "compaction-summary hit must NOT appear; got message uuids "
        f"{msg_uuids}"
    )


# ----- 5. count_matches honors the same filter --------------------------


def test_count_matches_honors_include_compactions(fixture_idx) -> None:
    """``count_matches`` must apply the same is_compaction_summary filter
    so the truncation envelope's ``total_messages_matched`` is accurate.

    Bug it would surface: ``include_compactions=False`` filter applied
    in query_with_snippets but not in count_matches → user sees "1 of
    5" matches when reality is "1 of 1" (the other 4 were compaction
    hits dropped at scatter time). This was the exact concern that
    drove the Council to choose the SQL-column approach over post-
    filter.
    """
    n_all = fixture_idx.count_matches(
        TOKEN_COMPACT_ONLY, include_tool_calls=True, include_compactions=True,
    )
    n_no_compact = fixture_idx.count_matches(
        TOKEN_COMPACT_ONLY, include_tool_calls=True, include_compactions=False,
    )
    assert n_all >= 1, (
        f"include_compactions=True must count the compaction hit; got {n_all}"
    )
    assert n_no_compact == 0, (
        "include_compactions=False must NOT count the compaction-only hit; "
        f"got {n_no_compact}"
    )


# ----- 6. Linear-scan fallback honors the same filter -------------------


def test_linear_scan_excludes_compact_hit_when_flag_false() -> None:
    """The linear-scan path (FTS5 unavailable) must apply the same
    filter — otherwise sqlite3 builds without FTS5 (some Linux distros)
    would still leak compaction hits."""
    store = FakeStore([_conv_compact_only(), _conv_regular_only(), _conv_both()])
    results = _search_via_linear_scan(
        store,
        TOKEN_COMPACT_ONLY,
        include_tool_calls=True,
        include_compactions=False,
    )
    conv_uuids = {r.conversation_uuid for r in results}
    assert "conv-compact-only" not in conv_uuids, (
        "linear-scan path must drop compaction-only hit when "
        f"include_compactions=False; got {conv_uuids}"
    )


def test_linear_scan_includes_compact_hit_when_flag_true() -> None:
    """Bidirectional pair: include_compactions=True keeps the hit."""
    store = FakeStore([_conv_compact_only(), _conv_regular_only(), _conv_both()])
    results = _search_via_linear_scan(
        store,
        TOKEN_COMPACT_ONLY,
        include_tool_calls=True,
        include_compactions=True,
    )
    conv_uuids = {r.conversation_uuid for r in results}
    assert "conv-compact-only" in conv_uuids, (
        "linear-scan path must keep compaction hit when "
        f"include_compactions=True; got {conv_uuids}"
    )


# ----- 7. Equivalence FTS5 fast path vs linear scan ---------------------


@pytest.mark.parametrize(
    "token,include_compactions,expected_conv_uuids",
    [
        # Compact-only hit: BOTH paths drop under filter ON.
        (TOKEN_COMPACT_ONLY, False, set()),
        (TOKEN_COMPACT_ONLY, True, {"conv-compact-only"}),
        # Regular-only hit: present in both modes.
        (TOKEN_REGULAR_ONLY, False, {"conv-regular-only"}),
        (TOKEN_REGULAR_ONLY, True, {"conv-regular-only"}),
        # Both: present in both modes (regular half carries it).
        (TOKEN_BOTH, False, {"conv-both"}),
        (TOKEN_BOTH, True, {"conv-both"}),
    ],
)
def test_fast_path_matches_linear_under_compaction_toggle(
    fixture_idx, token, include_compactions, expected_conv_uuids,
) -> None:
    """For each (token, toggle) pair, the FTS5 fast path's conv-uuid
    set MUST match the linear-scan path's set.

    Bug it would surface: silent drift between the column-MATCH SQL
    and the linear-scan Python guard.
    """
    store = FakeStore([
        _conv_compact_only(),
        _conv_regular_only(),
        _conv_both(),
    ])
    linear_results = _search_via_linear_scan(
        store, token,
        include_tool_calls=True,
        include_compactions=include_compactions,
    )
    linear_uuids = {r.conversation_uuid for r in linear_results}
    fast_response = _search_via_index_fast(
        store, fixture_idx, token,
        source="all", sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None, bookmarks=None,
        include_tool_calls=True,
        include_compactions=include_compactions,
    )
    fast_uuids = {r.conversation_uuid for r in fast_response.results}
    assert linear_uuids == expected_conv_uuids, (
        f"linear scan returned {linear_uuids}; expected {expected_conv_uuids}"
    )
    assert fast_uuids == expected_conv_uuids, (
        f"fast path returned {fast_uuids}; expected {expected_conv_uuids}"
    )


# ----- 8. Full-mode fast path also honors the filter --------------------


def test_full_mode_fast_path_excludes_compact_hit_when_flag_false(
    fixture_idx,
) -> None:
    """``context_size='full'`` fast path (_search_via_index_fast_full) must
    also apply the filter — otherwise the "full body" UX would surface
    compaction summaries the user just hid."""
    response = _search_via_index_fast_full(
        FakeStore([_conv_compact_only(), _conv_regular_only(), _conv_both()]),
        fixture_idx,
        TOKEN_COMPACT_ONLY,
        source="all",
        sort="updated_at",
        sort_order="desc",
        conversation_uuid=None,
        project_path=None,
        bookmarks=None,
        include_tool_calls=True,
        include_compactions=False,
    )
    conv_uuids = {r.conversation_uuid for r in response.results}
    assert "conv-compact-only" not in conv_uuids, (
        "full-mode fast path must drop compaction-only hit when "
        f"include_compactions=False; got {conv_uuids}"
    )


# ----- 9. search_conversations dispatcher plumbs the flag ---------------


def test_search_conversations_plumbs_include_compactions(
    fixture_idx, tmp_path, monkeypatch,
) -> None:
    """End-to-end: ``search_conversations(query, include_compactions=False)``
    must drop compaction-only hits regardless of which underlying path
    (FTS5 fast, FTS5 slow, linear) runs.

    Bug it would surface: dispatcher accepts the kwarg but doesn't
    pass it down — easy bug because all three downstream signatures
    must be updated.
    """
    # Force the FTS5 fast path by injecting our fixture index. The
    # search.py dispatcher calls ``get_search_index()`` lazily, so
    # patching the module-level symbol AND the singleton variable
    # together guarantees the fixture index wins regardless of which
    # codepath runs first.
    monkeypatch.setattr(si, "_search_index", fixture_idx)
    monkeypatch.setattr(si, "get_search_index", lambda: fixture_idx)

    store = FakeStore([
        _conv_compact_only(), _conv_regular_only(), _conv_both(),
    ])
    response = search_conversations(
        store,
        TOKEN_COMPACT_ONLY,
        include_compactions=False,
    )
    conv_uuids = {r.conversation_uuid for r in response.results}
    assert "conv-compact-only" not in conv_uuids, (
        "search_conversations(include_compactions=False) must drop "
        f"compaction-only hits; got {conv_uuids}"
    )


# ----- 10. SCHEMA migration: v12 → v13 forces rebuild -------------------


def test_schema_v12_to_current_triggers_rebuild(tmp_path) -> None:
    """An on-disk v12 messages table (no is_compaction_summary) MUST
    drop+rebuild on open so the new column is populated.

    Bug it would surface: version check passing on a v12 file — the
    new column would never appear and column-MATCH SQL would raise
    "no such column: is_compaction_summary".
    """
    db_path = tmp_path / "legacy-v12.sqlite"
    # Hand-craft a v12 messages table (no is_compaction_summary).
    with closing(sqlite3.connect(str(db_path))) as raw, raw:
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
                body_text,
                tokenize = "porter unicode61 remove_diacritics 1"
            )
        """)
        raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        raw.execute("INSERT INTO schema_version (version) VALUES (12)")
        raw.commit()

    idx = si.SearchIndex(db_path)
    try:
        conn = idx._get_read_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        assert "is_compaction_summary" in cols, (
            "v12 → current open must drop+rebuild so is_compaction_summary "
            f"appears; got cols {cols}"
        )
        sv = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        assert sv == (si.SCHEMA_VERSION,), (
            f"schema_version row must be ({si.SCHEMA_VERSION},); got {sv}"
        )
    finally:
        idx.close()


# ----- 11. v14: title-only compaction-text leak (bug 2026-05-26 follow-up) --
#
# Same-day follow-up bug. v13 closed the per-message compaction-summary
# body leak. v14 closes the per-CONVERSATION compaction-titled title-
# sweep leak: a CC session that starts with a compaction-summary message
# and has no summary/custom-title/agent-name row gets its title fallback-
# derived from the first 100 chars of the compaction body
# ("This session is being continued from a previous conversation that
# ran out of context..."). The v13 fix correctly suppressed the message-
# body hit, but the title-substring sweep (title_match_snippets /
# title_match_uuids) had no is_compaction_titled filter, so the same
# conversation surfaced as a title-only hit with the 📄 "title" badge.
#
# Architecture: per-conversation ``is_compaction_titled`` column on the
# ``conversations`` projection table (NOT the FTS5 messages table — that
# table is 2.5GB and an FTS5 schema add requires a full rebuild). The
# title-sweep helpers add ``AND is_compaction_titled = 0`` when
# include_compactions=False. The linear-scan path and the _search_via_index
# slow path also gate their Python-side title-pseudo emission on the same
# predicate (via the shared ``is_compaction_prefix_text`` helper in
# ``backend.compact_prefixes``).


# Canonical fallback title text (matches what
# cc_message_transforms._extract_conversation_metadata would derive when
# the first non-system user message IS the compaction summary).
TITLE_COMPACTION = (
    COMPACTION_TITLE_PREFIX
    + " The conversation continued with debugging the search index."
)

# Token that appears ONLY inside the compaction-derived title (and so
# would be a title-only hit). Pick a token that's part of the canonical
# prefix.
TITLE_NEEDLE = "ran out of context"

# Token that appears ONLY in a substantive body message of a compaction-
# titled conversation. Used to verify the filter doesn't over-aggressively
# drop the whole conversation — body hits MUST still surface even when
# the title is hidden.
BODY_NEEDLE = "uniquemarker"


def _conv_compaction_titled_with_body() -> dict[str, Any]:
    """A conv whose TITLE is the canonical compaction prefix AND which
    also has a substantive non-compaction body message containing
    BODY_NEEDLE.

    This is the BIDIRECTIONAL pair fixture: it should DROP under
    a title-only search with include_compactions=False, but the body
    hit must still surface when the query matches body text.
    """
    return _conv(
        "conv-compaction-titled-with-body",
        TITLE_COMPACTION,
        [
            _msg("ct-1", sender="human", text=f"Substantive content with {BODY_NEEDLE}."),
            _msg(
                "ct-2",
                sender="human",
                text=TITLE_COMPACTION,  # the actual compaction body
                is_compact_summary=True,
            ),
        ],
    )


def _conv_normal_titled_matching() -> dict[str, Any]:
    """Control: a conv with a normal title that happens to contain
    ``TITLE_NEEDLE``. Must NOT be over-filtered (the filter applies ONLY
    to compaction-prefix titles, not to any title containing the needle).
    """
    return _conv(
        "conv-normal-title",
        "Debug session for ran out of context bug",
        [
            _msg("n-1", sender="human", text="No compaction here."),
        ],
    )


@pytest.fixture
def title_fixture_idx(tmp_path):
    """Index with the compaction-titled fixtures."""
    idx = si.SearchIndex(tmp_path / "title-leak.sqlite")
    for c in [
        _conv_compaction_titled_with_body(),
        _conv_normal_titled_matching(),
    ]:
        idx.upsert_conversation(c, tmp_path / f"{c['uuid']}.json", 1.0)
    idx.mark_ready()
    yield idx
    idx.close()


# 11a. Schema: conversations.is_compaction_titled exists.

def test_conversations_table_has_is_compaction_titled(fresh_index) -> None:
    """The v14 ``conversations`` projection MUST carry
    ``is_compaction_titled`` so the title-sweep filter has a column to
    gate on.

    Bug it would surface: forgetting the column add (the title-sweep
    SQL would error "no such column").
    """
    conn = fresh_index._get_read_conn()
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()
    }
    assert "is_compaction_titled" in cols, (
        "compaction-titled gating requires an is_compaction_titled column "
        f"on the conversations projection table; got {cols}"
    )


def test_schema_version_bumped_to_at_least_14() -> None:
    """SCHEMA_VERSION must be >=14 so a v13 on-disk index migrates to
    pick up the new column."""
    assert si.SCHEMA_VERSION >= 14, (
        f"SCHEMA_VERSION must be >=14 for is_compaction_titled rollout; "
        f"got {si.SCHEMA_VERSION}"
    )


# 11b. Upsert populates is_compaction_titled correctly.

def test_upsert_marks_compaction_titled_conversations(fresh_index, tmp_path) -> None:
    """``upsert_conversation`` reads the conv name and sets
    ``is_compaction_titled=1`` when the title starts with the canonical
    compaction prefix (after lstrip), 0 otherwise."""
    conv_titled = _conv_compaction_titled_with_body()
    conv_normal = _conv_normal_titled_matching()
    fresh_index.upsert_conversation(
        conv_titled, tmp_path / f"{conv_titled['uuid']}.json", 1.0,
    )
    fresh_index.upsert_conversation(
        conv_normal, tmp_path / f"{conv_normal['uuid']}.json", 1.0,
    )
    conn = fresh_index._get_read_conn()
    rows = dict(conn.execute(
        "SELECT conv_uuid, is_compaction_titled FROM conversations "
        "ORDER BY conv_uuid"
    ).fetchall())
    assert rows.get("conv-compaction-titled-with-body") == 1, (
        "compaction-prefix title must set is_compaction_titled=1"
    )
    assert rows.get("conv-normal-title") == 0, (
        "normal title (even one containing 'ran out of context' as part "
        "of a larger phrase) must NOT be flagged"
    )


def test_upsert_handles_leading_whitespace_in_title(fresh_index, tmp_path) -> None:
    """The detector strips leading whitespace before the prefix check
    (matches ``cowork_reader._extract_cowork_compact_markers`` semantics
    and the SQL ``ltrim()`` used in the v13→v14 migration backfill).
    Otherwise the SQL and Python paths could drift on whitespace-leading
    titles."""
    conv = _conv(
        "conv-whitespace-title",
        "  \n  " + COMPACTION_TITLE_PREFIX + " trailing text",
        [_msg("w-1", sender="human", text="body")],
    )
    fresh_index.upsert_conversation(
        conv, tmp_path / f"{conv['uuid']}.json", 1.0,
    )
    conn = fresh_index._get_read_conn()
    row = conn.execute(
        "SELECT is_compaction_titled FROM conversations WHERE conv_uuid = ?",
        (conv["uuid"],),
    ).fetchone()
    assert row == (1,), (
        "compaction prefix after leading whitespace must still set "
        f"is_compaction_titled=1; got {row}"
    )


# 11c. RED: the bug. Title-only hit with include_compactions=False must
#       NOT surface the compaction-titled conv (before the fix this was
#       leaking the 📄 "title" badge).

def test_fast_path_title_only_compaction_hit_dropped_when_flag_false(
    title_fixture_idx,
) -> None:
    """The bug 2026-05-26. ``include_compactions=False`` + a query that
    matches ONLY the compaction-derived title text MUST drop the
    conversation entirely (no body hit, no title pseudo-message)."""
    response = _search_via_index_fast(
        FakeStore([
            _conv_compaction_titled_with_body(),
            _conv_normal_titled_matching(),
        ]),
        title_fixture_idx,
        TITLE_NEEDLE,
        source="all",
        sort="updated_at",
        sort_order="desc",
        conversation_uuid=None,
        project_path=None,
        bookmarks=None,
        include_tool_calls=True,
        include_compactions=False,
    )
    conv_uuids = {r.conversation_uuid for r in response.results}
    assert "conv-compaction-titled-with-body" not in conv_uuids, (
        "Bug 2026-05-26: title-only hit on canonical compaction title "
        "MUST NOT surface with include_compactions=False. "
        f"Got conv_uuids={conv_uuids}"
    )


def test_fast_path_title_only_compaction_hit_present_when_flag_true(
    title_fixture_idx,
) -> None:
    """Bidirectional pair: include_compactions=True keeps the title hit."""
    response = _search_via_index_fast(
        FakeStore([
            _conv_compaction_titled_with_body(),
            _conv_normal_titled_matching(),
        ]),
        title_fixture_idx,
        TITLE_NEEDLE,
        source="all",
        sort="updated_at",
        sort_order="desc",
        conversation_uuid=None,
        project_path=None,
        bookmarks=None,
        include_tool_calls=True,
        include_compactions=True,
    )
    conv_uuids = {r.conversation_uuid for r in response.results}
    assert "conv-compaction-titled-with-body" in conv_uuids, (
        "include_compactions=True must surface the compaction-titled "
        f"hit; got {conv_uuids}"
    )


# 11d. Body hits in a compaction-titled conv still surface
#       (filter is not over-aggressive at the per-conv level).

def test_fast_path_body_hit_in_compaction_titled_conv_still_present(
    title_fixture_idx,
) -> None:
    """The bidirectional contract from the bug ticket: a query that
    matches substantive body text in a compaction-titled conversation
    MUST still return the body hit when include_compactions=False.
    Proves the filter is per-FEATURE (title vs body), not per-CONV."""
    response = _search_via_index_fast(
        FakeStore([
            _conv_compaction_titled_with_body(),
            _conv_normal_titled_matching(),
        ]),
        title_fixture_idx,
        BODY_NEEDLE,
        source="all",
        sort="updated_at",
        sort_order="desc",
        conversation_uuid=None,
        project_path=None,
        bookmarks=None,
        include_tool_calls=True,
        include_compactions=False,
    )
    by_conv = {r.conversation_uuid: r for r in response.results}
    assert "conv-compaction-titled-with-body" in by_conv, (
        "body hit on substantive (non-compaction) message MUST surface "
        "even when the conv's title is compaction-derived; "
        f"got conv_uuids={list(by_conv.keys())}"
    )
    msg_uuids = {
        m.message_uuid
        for m in by_conv["conv-compaction-titled-with-body"].matching_messages
    }
    assert "ct-1" in msg_uuids, (
        "non-compaction body message must be in matching_messages; "
        f"got {msg_uuids}"
    )
    # And no title pseudo-message should leak through.
    assert "title" not in msg_uuids, (
        "title pseudo-message MUST NOT appear under include_compactions=False "
        f"when the title is compaction-prefix; got {msg_uuids}"
    )


# 11e. Normal-titled conv with matching title text is NOT over-filtered.

def test_fast_path_normal_titled_conv_not_over_filtered(
    title_fixture_idx,
) -> None:
    """Control: a conv with a normal user title that HAPPENS to contain
    TITLE_NEEDLE (`"ran out of context"`) as part of a larger phrase
    MUST still surface as a title hit under include_compactions=False —
    the filter only applies to titles that LITERALLY start with the
    canonical compaction prefix."""
    response = _search_via_index_fast(
        FakeStore([
            _conv_compaction_titled_with_body(),
            _conv_normal_titled_matching(),
        ]),
        title_fixture_idx,
        TITLE_NEEDLE,
        source="all",
        sort="updated_at",
        sort_order="desc",
        conversation_uuid=None,
        project_path=None,
        bookmarks=None,
        include_tool_calls=True,
        include_compactions=False,
    )
    conv_uuids = {r.conversation_uuid for r in response.results}
    assert "conv-normal-title" in conv_uuids, (
        "Normal-titled conv whose title happens to match TITLE_NEEDLE "
        "must NOT be over-filtered; the gate is anchored to the "
        f"compaction prefix only. Got {conv_uuids}"
    )


# 11f. Linear-scan path applies the same gate (parity with FTS path).

def test_linear_scan_title_only_compaction_hit_dropped_when_flag_false() -> None:
    """Linear-scan fallback (FTS5 unavailable, index not ready, sqlite
    error) MUST apply the same gate so the bug doesn't leak in fallback
    mode."""
    store = FakeStore([
        _conv_compaction_titled_with_body(),
        _conv_normal_titled_matching(),
    ])
    results = _search_via_linear_scan(
        store, TITLE_NEEDLE,
        include_tool_calls=True,
        include_compactions=False,
    )
    conv_uuids = {r.conversation_uuid for r in results}
    assert "conv-compaction-titled-with-body" not in conv_uuids, (
        "linear-scan path must drop title-only compaction hit when "
        f"include_compactions=False; got {conv_uuids}"
    )


def test_linear_scan_title_only_compaction_hit_present_when_flag_true() -> None:
    """Bidirectional pair: include_compactions=True keeps the title hit."""
    store = FakeStore([
        _conv_compaction_titled_with_body(),
        _conv_normal_titled_matching(),
    ])
    results = _search_via_linear_scan(
        store, TITLE_NEEDLE,
        include_tool_calls=True,
        include_compactions=True,
    )
    conv_uuids = {r.conversation_uuid for r in results}
    assert "conv-compaction-titled-with-body" in conv_uuids, (
        "linear-scan path must keep title hit under "
        f"include_compactions=True; got {conv_uuids}"
    )


# 11g. _search_via_index slow path (the third leak vector the Python
#       expert identified — Python-side title pseudo-message emit).

def test_slow_index_path_title_only_compaction_hit_dropped_when_flag_false(
    title_fixture_idx,
) -> None:
    """The slow ``_search_via_index`` path does its OWN Python-side
    title pseudo-message emit (independent of title_match_uuids).
    Gating only the SQL helper would leave THIS path leaking. This test
    pins the gate on the Python emit too.

    Pinned by the Council 2026-05-26 disagreement-resolution (Python
    Expert: 3 leak vectors, not 2)."""
    store = FakeStore([
        _conv_compaction_titled_with_body(),
        _conv_normal_titled_matching(),
    ])
    results = _search_via_index(
        store,
        title_fixture_idx,
        TITLE_NEEDLE,
        source="all",
        context_size="snippet",
        sort="updated_at",
        sort_order="desc",
        conversation_uuid=None,
        project_path=None,
        bookmarks=None,
        include_tool_calls=True,
        include_compactions=False,
    )
    conv_uuids = {r.conversation_uuid for r in results}
    assert "conv-compaction-titled-with-body" not in conv_uuids, (
        "slow _search_via_index path must drop title-only compaction "
        f"hit when include_compactions=False; got {conv_uuids}"
    )


def test_slow_index_path_title_only_compaction_hit_present_when_flag_true(
    title_fixture_idx,
) -> None:
    """Bidirectional pair for the slow path."""
    store = FakeStore([
        _conv_compaction_titled_with_body(),
        _conv_normal_titled_matching(),
    ])
    results = _search_via_index(
        store,
        title_fixture_idx,
        TITLE_NEEDLE,
        source="all",
        context_size="snippet",
        sort="updated_at",
        sort_order="desc",
        conversation_uuid=None,
        project_path=None,
        bookmarks=None,
        include_tool_calls=True,
        include_compactions=True,
    )
    conv_uuids = {r.conversation_uuid for r in results}
    assert "conv-compaction-titled-with-body" in conv_uuids, (
        "slow path with include_compactions=True must surface title "
        f"hit; got {conv_uuids}"
    )


# 11h. v13 → v14 fast migration. The whole point of v14 is "don't pay
#       the reindex cost twice in one day". This test pins that the
#       migration is FAST (no DROP+rebuild of the messages FTS5 table)
#       AND that the new column is populated correctly from existing
#       title text.

def test_v13_to_v14_fast_migration_preserves_messages_and_backfills_flag(
    tmp_path,
) -> None:
    """An on-disk v13 index MUST fast-migrate to v14 by:
      (a) adding the ``is_compaction_titled`` column to
          ``conversations``;
      (b) backfilling it from ``ltrim(title) LIKE prefix%``;
      (c) leaving the v13 ``messages`` FTS5 table intact (no full
          DROP+rebuild — the user would lose ~5-15 min to reindex);
      (d) bumping ``schema_version`` to >=14.

    Pinned by the Python Expert's adversarial review (2026-05-26):
    without an explicit v13→v14 fast-migration block, the existing
    ``_init_schema`` falls through to the full DROP+rebuild branch
    (search_index.py:776+) — defeating the "ms not minutes" goal.
    """
    db_path = tmp_path / "legacy-v13.sqlite"
    # Hand-craft a v13 schema: messages FTS5 with is_compaction_summary
    # AND a conversations projection WITHOUT is_compaction_titled.
    with closing(sqlite3.connect(str(db_path))) as raw, raw:
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
                is_compaction_summary UNINDEXED,
                title,
                body,
                body_text,
                tokenize = "porter unicode61 remove_diacritics 1"
            )
        """)
        raw.execute(
            "CREATE TABLE indexed_files ("
            "path TEXT PRIMARY KEY, mtime REAL NOT NULL, "
            "indexed_at INTEGER NOT NULL, conv_uuid TEXT)"
        )
        raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        raw.execute("INSERT INTO schema_version (version) VALUES (13)")
        raw.execute("""
            CREATE TABLE conversations (
                conv_uuid TEXT PRIMARY KEY,
                title TEXT,
                conv_created_at TEXT,
                conv_updated_at TEXT,
                project_path TEXT,
                source TEXT,
                organization_id TEXT
            )
        """)
        # Seed two conversations: one with compaction-prefix title, one
        # with a normal title. Use the canonical prefix so the backfill
        # ltrim(title) LIKE check fires.
        raw.execute(
            "INSERT INTO conversations "
            "(conv_uuid, title, conv_created_at, conv_updated_at, "
            " project_path, source, organization_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("legacy-compaction",
             COMPACTION_TITLE_PREFIX + " etc.",
             "2026-05-26T12:00:00Z",
             "2026-05-26T13:00:00Z",
             "/tmp/legacy",
             "CLAUDE_CODE",
             ""),
        )
        raw.execute(
            "INSERT INTO conversations "
            "(conv_uuid, title, conv_created_at, conv_updated_at, "
            " project_path, source, organization_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("legacy-normal",
             "A normal user-given title",
             "2026-05-26T12:00:00Z",
             "2026-05-26T13:00:00Z",
             "/tmp/legacy",
             "CLAUDE_CODE",
             ""),
        )
        # Seed a sentinel row in messages so we can prove the FTS5 table
        # was NOT dropped+rebuilt (which would empty it).
        raw.execute(
            "INSERT INTO messages "
            "(conv_uuid, message_uuid, sender, created_at, source, "
            " project_path, organization_id, conv_created_at, "
            " conv_updated_at, is_compaction_summary, title, body, body_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy-normal", "sentinel-msg", "human",
             "2026-05-26T12:00:00Z", "CLAUDE_CODE", "/tmp/legacy", "",
             "2026-05-26T12:00:00Z", "2026-05-26T13:00:00Z",
             0, "A normal user-given title", "sentinel body", "sentinel body"),
        )
        raw.commit()

    idx = si.SearchIndex(db_path)
    try:
        conn = idx._get_read_conn()
        # (a) column exists.
        cols = {
            r[1] for r in conn.execute(
                "PRAGMA table_info(conversations)"
            ).fetchall()
        }
        assert "is_compaction_titled" in cols, (
            f"v13 → v14 fast migration must add is_compaction_titled; "
            f"got cols {cols}"
        )

        # (b) backfill flipped the compaction-prefix row to 1, left the
        # normal row at 0.
        rows = dict(conn.execute(
            "SELECT conv_uuid, is_compaction_titled FROM conversations"
        ).fetchall())
        assert rows.get("legacy-compaction") == 1, (
            "v14 backfill must set is_compaction_titled=1 for the "
            f"compaction-prefix title; got rows={rows}"
        )
        assert rows.get("legacy-normal") == 0, (
            "v14 backfill must leave non-compaction titles at 0; "
            f"got rows={rows}"
        )

        # (c) messages FTS5 table NOT dropped — sentinel row must survive.
        sentinel = conn.execute(
            "SELECT body FROM messages WHERE message_uuid = ?",
            ("sentinel-msg",),
        ).fetchone()
        assert sentinel == ("sentinel body",), (
            "v13 → v14 must be a FAST migration — the messages FTS5 "
            "table must NOT be dropped (the user just paid ~5-15 min "
            f"for v12→v13 today); got {sentinel}"
        )

        # (d) schema_version stamped to >=14.
        sv = conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        assert sv == (si.SCHEMA_VERSION,), (
            f"schema_version row must be ({si.SCHEMA_VERSION},); got {sv}"
        )
    finally:
        idx.close()


def test_v13_to_v14_migration_is_idempotent(tmp_path) -> None:
    """A partial migration (column added but schema_version not stamped)
    MUST survive a second open without crashing. Same idempotency
    pattern v11→v12 follows for indexed_files.conv_uuid."""
    db_path = tmp_path / "partial-v13.sqlite"
    # v13 schema as above, BUT also add the new column manually (without
    # bumping schema_version). Simulates a crash between ALTER TABLE and
    # the INSERT INTO schema_version.
    with closing(sqlite3.connect(str(db_path))) as raw, raw:
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
                is_compaction_summary UNINDEXED,
                title,
                body,
                body_text,
                tokenize = "porter unicode61 remove_diacritics 1"
            )
        """)
        raw.execute(
            "CREATE TABLE indexed_files ("
            "path TEXT PRIMARY KEY, mtime REAL NOT NULL, "
            "indexed_at INTEGER NOT NULL, conv_uuid TEXT)"
        )
        raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        raw.execute("INSERT INTO schema_version (version) VALUES (13)")
        raw.execute("""
            CREATE TABLE conversations (
                conv_uuid TEXT PRIMARY KEY,
                title TEXT,
                conv_created_at TEXT,
                conv_updated_at TEXT,
                project_path TEXT,
                source TEXT,
                organization_id TEXT,
                is_compaction_titled INTEGER NOT NULL DEFAULT 0
            )
        """)
        raw.execute(
            "INSERT INTO conversations "
            "(conv_uuid, title, conv_created_at, conv_updated_at, "
            " project_path, source, organization_id, is_compaction_titled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("partial-conv", COMPACTION_TITLE_PREFIX + " mid-migration text",
             "2026-05-26T12:00:00Z", "2026-05-26T13:00:00Z",
             "/tmp/legacy", "CLAUDE_CODE", "", 0),
        )
        raw.commit()

    idx = si.SearchIndex(db_path)
    try:
        conn = idx._get_read_conn()
        # Schema bumped.
        sv = conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        assert sv == (si.SCHEMA_VERSION,), (
            f"partial-migration recovery must still bump schema_version "
            f"to {si.SCHEMA_VERSION}; got {sv}"
        )
        # Backfill still ran and flipped the flag (the migration UPDATE
        # is unconditional — runs whether the column was pre-added or not).
        row = conn.execute(
            "SELECT is_compaction_titled FROM conversations "
            "WHERE conv_uuid = ?",
            ("partial-conv",),
        ).fetchone()
        assert row == (1,), (
            "idempotent migration backfill must flip the flag even when "
            f"the column was pre-added; got {row}"
        )
    finally:
        idx.close()


# 11i. SQL helper plumbing tests — count_matches contract unaffected by
#       title-sweep changes (title-only hits are not counted in either
#       number; this test makes that explicit so a future regression
#       doesn't silently include them).

def test_count_matches_unchanged_by_v14_title_gate(title_fixture_idx) -> None:
    """``count_matches`` counts BODY rows under the FTS5 MATCH WHERE,
    NOT title-only hits. The v14 title-sweep gate must not bleed into
    count_matches."""
    n_with_body_compactions_off = title_fixture_idx.count_matches(
        BODY_NEEDLE, include_tool_calls=True, include_compactions=False,
    )
    n_with_body_compactions_on = title_fixture_idx.count_matches(
        BODY_NEEDLE, include_tool_calls=True, include_compactions=True,
    )
    # BODY_NEEDLE lives in a regular (non-compaction) message of the
    # compaction-titled conv — present in both modes.
    assert n_with_body_compactions_off >= 1
    assert n_with_body_compactions_on >= 1


# 11j. title_match_snippets / title_match_uuids direct contract tests.

def test_title_match_snippets_respects_include_compactions(title_fixture_idx) -> None:
    """Direct unit test on the helper: the SQL-level gate works."""
    hits_off = title_fixture_idx.title_match_snippets(
        TITLE_NEEDLE, include_compactions=False,
    )
    hits_on = title_fixture_idx.title_match_snippets(
        TITLE_NEEDLE, include_compactions=True,
    )
    assert "conv-compaction-titled-with-body" not in hits_off, (
        "title_match_snippets must drop compaction-titled conv when "
        f"include_compactions=False; got {set(hits_off.keys())}"
    )
    assert "conv-compaction-titled-with-body" in hits_on, (
        "title_match_snippets must include compaction-titled conv when "
        f"include_compactions=True; got {set(hits_on.keys())}"
    )
    # Normal-titled conv whose title happens to contain the needle:
    # present in BOTH modes (not over-filtered).
    assert "conv-normal-title" in hits_off
    assert "conv-normal-title" in hits_on


def test_title_match_uuids_respects_include_compactions(title_fixture_idx) -> None:
    """Symmetry test for the sister helper."""
    uuids_off = title_fixture_idx.title_match_uuids(
        TITLE_NEEDLE, include_compactions=False,
    )
    uuids_on = title_fixture_idx.title_match_uuids(
        TITLE_NEEDLE, include_compactions=True,
    )
    assert "conv-compaction-titled-with-body" not in uuids_off
    assert "conv-compaction-titled-with-body" in uuids_on
    assert "conv-normal-title" in uuids_off
    assert "conv-normal-title" in uuids_on
