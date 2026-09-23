"""``include_tool_calls`` filter — backend pinning.

Architectural fix for the search-match-focus-mismatch bug (2026-05-11):
when the UI has ``showToolCalls=false`` (the default), search must NOT
return matches inside tool_use / tool_result / thinking blocks. Those
hits have no visible owning message in the conversation pane, so a click
on the sidebar snippet either fails to focus or rings the wrong message.

The fix is server-side: ``search_conversations(..., include_tool_calls=False)``
ignores tool/thinking blocks in BOTH the FTS5 fast path and the
linear-scan fallback. The FTS5 index itself still stores the full text
(no schema bump, no rebuild for existing installs) — the filter is
applied at scatter/snippet time.

Bidirectional verification per TESTING.md §2: every assertion
that a result is *excluded* under ``include_tool_calls=False`` also
asserts the same result *appears* when the flag is flipped to True.
Without the inversion check, a buggy implementation that always returns
empty would pass.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend import search_index as si
from backend.search import (
    _extract_searchable_text,
    _search_via_index,
    _search_via_linear_scan,
    search_conversations,
)


# ----- helpers --------------------------------------------------------


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
    created_at: str = "2026-05-11T12:00:00Z",
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
        "created_at": "2026-05-11T12:00:00Z",
        "updated_at": "2026-05-11T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else "",
        "project_path": project_path,
        "source": source,
        "chat_messages": messages,
    }


# Fixture conversations covering every block-type contribution to the
# searchable text. Each conversation's token is unique so search results
# point to exactly one message.

TOKEN_PLAIN_TEXT = "alpha-plain-text-token"
TOKEN_TOOL_USE = "alpha-tool-use-token"
TOKEN_TOOL_RESULT = "alpha-tool-result-token"
TOKEN_TOOL_RESULT_STR = "alpha-tool-result-str-token"
TOKEN_THINKING = "alpha-thinking-token"
TOKEN_BOTH = "alpha-both-token"
TOKEN_TITLE = "alpha-title-token"


def _conv_plain() -> dict[str, Any]:
    return _conv("conv-plain", "Plain conv", [
        _msg("m-plain", text=f"Here is {TOKEN_PLAIN_TEXT} in a plain message.",
             content=[{"type": "text",
                       "text": f"Here is {TOKEN_PLAIN_TEXT} in a plain message."}]),
    ])


def _conv_tool_use() -> dict[str, Any]:
    return _conv("conv-tu", "Tool-use conv", [
        _msg("m-tu", text="",
             content=[{
                 "type": "tool_use",
                 "id": "tu-1",
                 "name": "Bash",
                 "input": {"command": f"echo {TOKEN_TOOL_USE}"},
             }]),
    ])


def _conv_tool_result_list() -> dict[str, Any]:
    return _conv("conv-tr", "Tool-result conv", [
        _msg("m-tr", sender="user", text="",
             content=[{
                 "type": "tool_result",
                 "tool_use_id": "tu-2",
                 "content": [{"type": "text", "text": f"Output: {TOKEN_TOOL_RESULT}"}],
             }]),
    ])


def _conv_tool_result_str() -> dict[str, Any]:
    return _conv("conv-tr-str", "Tool-result string conv", [
        _msg("m-tr-str", sender="user", text="",
             content=[{
                 "type": "tool_result",
                 "tool_use_id": "tu-3",
                 "content": f"Plain string: {TOKEN_TOOL_RESULT_STR}",
             }]),
    ])


def _conv_thinking() -> dict[str, Any]:
    return _conv("conv-think", "Thinking conv", [
        _msg("m-think", text="",
             content=[{"type": "thinking", "thinking": f"Hmm, {TOKEN_THINKING}…"}]),
    ])


def _conv_both() -> dict[str, Any]:
    """A message with the token in BOTH a text block AND a tool block.

    With include_tool_calls=False we MUST still find it (the text block
    contains the token). The snippet must come from the text block, not
    the tool block.
    """
    return _conv("conv-both", "Both-blocks conv", [
        _msg("m-both", text="",
             content=[
                 {"type": "text", "text": f"Text has {TOKEN_BOTH} here."},
                 {"type": "tool_use", "id": "tu-4", "name": "Bash",
                  "input": {"command": f"echo {TOKEN_BOTH}"}},
             ]),
    ])


def _conv_title_tool_only() -> dict[str, Any]:
    """A conversation where the query matches the title AND a tool-only
    body block. With include_tool_calls=False the title match must
    still emit a title pseudo-message; the body match must NOT appear.
    """
    return _conv(f"conv-title-{TOKEN_TITLE}", f"Title carries {TOKEN_TITLE}", [
        _msg("m-title-tu", text="",
             content=[{"type": "tool_use", "id": "tu-5", "name": "Bash",
                       "input": {"command": f"echo {TOKEN_TITLE}"}}]),
    ])


# ----- 1. _extract_searchable_text — projection semantics ---------------


def test_extract_full_projection_includes_all_block_types() -> None:
    """Default include_tool_calls=True includes text + tool_use + tool_result.

    V1 polish (2026-05-13, Fix 4): `thinking` blocks are NEVER indexed
    regardless of `include_tool_calls` (the frontend has no renderer
    for `thinking` in V1 — indexing it produces search ghosts where
    the matching text is invisible to the user). The full projection
    is "everything visible-or-toggle-able to the user"; thinking is
    neither.

    Doubled-snippet fix (2026-05-18, SCHEMA_VERSION v8): the indexer
    skips ``message['text']`` when ``content`` already has text
    blocks, because the canonical text-block content is what the
    frontend renders. We therefore use DISTINCT strings for the
    `text` field and the text block here — `text` is a bare-text
    fallback marker, the block carries the user-visible content
    that must appear in the projection.
    """
    msg = _msg("m1", text="bare-text fallback (skipped when text block present)",
               content=[
                   {"type": "text", "text": "text block content"},
                   {"type": "tool_use", "id": "tu", "name": "Bash",
                    "input": {"command": "echo hi"}},
                   {"type": "tool_result", "tool_use_id": "tu",
                    "content": [{"type": "text", "text": "result content"}]},
                   {"type": "thinking", "thinking": "secret reasoning"},
               ])
    out = _extract_searchable_text(msg)
    # v8 dedupe: `message['text']` is skipped because content has a
    # text block. Pre-v8 the indexer appended both, producing
    # doubled-snippet bug.
    assert "bare-text fallback" not in out, (
        "v8 contract: when content has text blocks, message['text'] "
        "must not also be indexed (it would double-index the prose)"
    )
    assert "text block content" in out
    assert "echo hi" in out
    assert "result content" in out
    # Spec invariant (V1 polish 2026-05-13): thinking is NEVER indexed.
    assert "secret reasoning" not in out, (
        "thinking content MUST NOT appear in the search projection — "
        "the viewer has no renderer for thinking blocks, so indexing "
        "them produces search ghosts"
    )


def test_extract_textonly_excludes_tool_blocks_and_thinking() -> None:
    """include_tool_calls=False drops tool_use / tool_result.

    V1 polish (2026-05-13, Fix 4): `thinking` is excluded from BOTH
    projections regardless of the toggle (see
    test_extract_full_projection_includes_all_block_types). Pinning
    that here too so a future re-enable of thinking indexing must
    update both tests.

    Doubled-snippet fix (2026-05-18, SCHEMA_VERSION v8): same dedupe
    contract — when content has text blocks, message['text'] is the
    redundant projection and gets dropped.
    """
    msg = _msg("m1", text="bare-text fallback (skipped when text block present)",
               content=[
                   {"type": "text", "text": "text block content"},
                   {"type": "tool_use", "id": "tu", "name": "Bash",
                    "input": {"command": "echo hi"}},
                   {"type": "tool_result", "tool_use_id": "tu",
                    "content": [{"type": "text", "text": "result content"}]},
                   {"type": "thinking", "thinking": "secret reasoning"},
               ])
    out = _extract_searchable_text(msg, include_tool_calls=False)
    assert "bare-text fallback" not in out, (
        "v8 contract: message['text'] dropped when text blocks exist"
    )
    assert "text block content" in out, "text-type blocks are user-visible content"
    assert "echo hi" not in out, "tool_use input must be excluded"
    assert "result content" not in out, "tool_result must be excluded"
    assert "secret reasoning" not in out, "thinking must be excluded"


def test_extract_strips_desktop_placeholder_when_filtering() -> None:
    """A message whose `text` field is only the Desktop tool placeholder
    yields the empty string under include_tool_calls=False (mirrors the
    frontend's filterToolPlaceholders / messageHasVisibleContent
    semantics).

    Bidirectional check: under include_tool_calls=True the placeholder
    text is included verbatim (the index keeps everything).
    """
    placeholder = (
        "```\nThis block is not supported on your current device yet.\n```"
    )
    msg = _msg("m1", text=placeholder, content=[])

    # Filter ON: drop the placeholder, end up empty.
    assert _extract_searchable_text(msg, include_tool_calls=False) == ""
    # Filter OFF (default): keep it verbatim.
    assert placeholder in _extract_searchable_text(msg, include_tool_calls=True)


def test_extract_keeps_real_text_around_placeholder() -> None:
    """A `text` field that contains the placeholder PLUS real prose
    keeps the prose under include_tool_calls=False.

    Bug it would surface: regex too greedy, eating the surrounding prose.
    """
    msg = _msg("m1",
               text=("Before-prose-marker\n"
                     "```\nThis block is not supported on your current device yet.\n```\n"
                     "after-prose-marker"),
               content=[])
    out = _extract_searchable_text(msg, include_tool_calls=False)
    assert "Before-prose-marker" in out
    assert "after-prose-marker" in out
    assert "This block is not supported" not in out


def test_extract_empty_message_returns_empty_string() -> None:
    """No text, no content blocks → empty projection in both modes."""
    msg = _msg("m1", text="", content=[])
    assert _extract_searchable_text(msg, include_tool_calls=True) == ""
    assert _extract_searchable_text(msg, include_tool_calls=False) == ""


# ----- 2. search_conversations — linear-scan path (no index) --------------


def test_search_skips_tool_only_match_when_filter_on() -> None:
    """A token that only appears in a tool_use block must NOT show up
    under include_tool_calls=False.

    Bidirectional: same query with include_tool_calls=True returns the
    match. If we got the wrong sign of the predicate this assertion
    would fail because both calls would return empty (or both full).
    """
    store = FakeStore([_conv_tool_use()])

    excluded = search_conversations(
        store, TOKEN_TOOL_USE, include_tool_calls=False,
    ).results
    assert excluded == [], (
        "Tool-only match must be filtered out when include_tool_calls=False"
    )

    included = search_conversations(
        store, TOKEN_TOOL_USE, include_tool_calls=True,
    ).results
    assert len(included) == 1
    assert included[0].conversation_uuid == "conv-tu"


@pytest.mark.parametrize("conv_factory,token,conv_uuid", [
    (_conv_tool_result_list, TOKEN_TOOL_RESULT, "conv-tr"),
    (_conv_tool_result_str, TOKEN_TOOL_RESULT_STR, "conv-tr-str"),
])
def test_search_filters_each_tool_block_type(conv_factory, token, conv_uuid):
    """Parametrize over every tool-ish block type: tool_result (list and
    string forms). Each is included when the filter is off and excluded
    when it's on.

    V1 polish (2026-05-13, Fix 4): `thinking` is NOT parametrized here
    because thinking blocks are never indexed regardless of the toggle.
    See test_search_excludes_thinking_in_both_modes for the dedicated
    contract test.
    """
    store = FakeStore([conv_factory()])
    assert search_conversations(store, token, include_tool_calls=False).results == []
    included = search_conversations(store, token, include_tool_calls=True).results
    assert len(included) == 1
    assert included[0].conversation_uuid == conv_uuid


def test_search_excludes_thinking_in_both_modes() -> None:
    """V1 polish (2026-05-13, Fix 4): tokens that ONLY appear inside a
    `thinking` content block MUST NOT be returned by search regardless
    of the `include_tool_calls` toggle.

    Bidirectional contract: Fix 4 strips thinking from
    `_extract_searchable_text` for both projections. A user can't see
    thinking content in the V1 viewer (no `case 'thinking':` renderer
    in MessageBubble.tsx), so search must never return a hit the user
    can't navigate to. If a future "Show thinking" affordance ships,
    both this test and the projection helper must update together.
    """
    store = FakeStore([_conv_thinking()])
    # Toggle OFF: not returned (already true pre-Fix-4 via tool gate).
    assert search_conversations(store, TOKEN_THINKING, include_tool_calls=False).results == []
    # Toggle ON: STILL not returned (new contract — Fix 4).
    assert search_conversations(store, TOKEN_THINKING, include_tool_calls=True).results == [], (
        "thinking-only matches MUST NOT appear in search results "
        "regardless of the include_tool_calls toggle (V1 polish Fix 4)"
    )


def test_search_returns_text_match_even_when_filter_on() -> None:
    """A token that appears in a text block (with or without a co-located
    tool block) must STILL be found under include_tool_calls=False.
    """
    store = FakeStore([_conv_both()])
    # With filter ON: text block still has the token.
    results = search_conversations(store, TOKEN_BOTH, include_tool_calls=False).results
    assert len(results) == 1
    assert results[0].conversation_uuid == "conv-both"
    # Snippet must come from the text block (the tool projection isn't visible).
    snippet = results[0].matching_messages[0].snippet
    assert "Text has" in snippet, (
        f"Snippet should be from text block; got {snippet!r}"
    )
    assert "echo" not in snippet, (
        "Snippet must not leak the tool_use input under filter ON"
    )

    # With filter OFF: also one result. Snippet may come from either
    # block; the regex-finditer / "first match wins" loop picks one
    # deterministically.
    results_full = search_conversations(store, TOKEN_BOTH, include_tool_calls=True).results
    assert len(results_full) == 1
    assert results_full[0].conversation_uuid == "conv-both"


def test_search_emits_title_match_when_body_is_filtered_tool() -> None:
    """If the query hits the title AND a tool-only body, the body match
    must be dropped under filter ON, but the title pseudo-message must
    still emit. The conv appears in results with one matching_message
    whose message_uuid is 'title'.
    """
    store = FakeStore([_conv_title_tool_only()])
    results = search_conversations(store, TOKEN_TITLE, include_tool_calls=False).results
    assert len(results) == 1
    msnips = results[0].matching_messages
    msg_uuids = [m.message_uuid for m in msnips]
    assert "title" in msg_uuids, (
        "Title match must emit even when body is filtered"
    )
    assert "m-title-tu" not in msg_uuids, (
        "Tool-only body match must be dropped"
    )

    # Inversion: filter OFF surfaces both the title and the body match.
    results_full = search_conversations(store, TOKEN_TITLE, include_tool_calls=True).results
    assert len(results_full) == 1
    full_uuids = [m.message_uuid for m in results_full[0].matching_messages]
    assert "title" in full_uuids
    assert "m-title-tu" in full_uuids


def test_search_plain_text_token_unaffected_by_filter() -> None:
    """A pure-text-block match must behave identically in both modes.

    This is a regression guard: a bug in the filter that accidentally
    nuked text-block matches would surface here.
    """
    store = FakeStore([_conv_plain()])
    on = search_conversations(store, TOKEN_PLAIN_TEXT, include_tool_calls=False).results
    off = search_conversations(store, TOKEN_PLAIN_TEXT, include_tool_calls=True).results
    assert len(on) == 1 and len(off) == 1
    assert on[0].conversation_uuid == off[0].conversation_uuid == "conv-plain"
    assert on[0].matching_messages[0].snippet == off[0].matching_messages[0].snippet


# ----- 3. FTS5 path equivalence ---------------------------------------


@pytest.fixture
def fts5_store_and_idx(tmp_path, monkeypatch):
    """Build a real on-disk FTS5 index over the same fixture conversations.

    Returns (FakeStore, SearchIndex). The module-level singleton is
    redirected to this test's index so search_conversations() dispatch
    uses our isolated index instead of the user's real one.
    """
    convs = [
        _conv_plain(),
        _conv_tool_use(),
        _conv_tool_result_list(),
        _conv_thinking(),
        _conv_both(),
        _conv_title_tool_only(),
    ]
    store = FakeStore(convs)

    si.reset_search_index_for_tests()
    idx = si.SearchIndex(tmp_path / "fixture-index.sqlite")
    for c in convs:
        idx.upsert_conversation(c, tmp_path / f"{c['uuid']}.json", 1.0)
    idx.mark_ready()

    # Redirect the singleton so search_conversations() picks up THIS index.
    monkeypatch.setattr(si, "_search_index", idx)
    yield store, idx
    si.reset_search_index_for_tests()


def test_fts5_and_linear_byte_equivalent_under_filter(fts5_store_and_idx):
    """The two paths must produce byte-for-byte identical SearchResult
    objects under include_tool_calls=False.

    Bug it would surface: the FTS5 scatter step forgetting to thread
    the flag → tool snippets leak through one path but not the other.
    """
    store, idx = fts5_store_and_idx

    for token, expected_conv in [
        (TOKEN_PLAIN_TEXT, "conv-plain"),
        (TOKEN_BOTH, "conv-both"),
    ]:
        linear = _search_via_linear_scan(
            store, token, include_tool_calls=False,
        )
        fts = _search_via_index(
            store, idx, token,
            source="all", context_size="snippet",
            sort="updated_at", sort_order="desc",
            conversation_uuid=None, project_path=None, bookmarks=None,
            include_tool_calls=False,
        )
        assert len(linear) == 1
        assert len(fts) == 1
        assert linear[0].conversation_uuid == fts[0].conversation_uuid == expected_conv
        # Snippet byte-for-byte equivalence.
        l_snippets = [m.snippet for m in linear[0].matching_messages]
        f_snippets = [m.snippet for m in fts[0].matching_messages]
        assert l_snippets == f_snippets, (
            f"Byte-for-byte mismatch for {token!r}: linear={l_snippets!r} "
            f"vs fts5={f_snippets!r}"
        )


def test_fts5_path_drops_tool_only_match_under_filter(fts5_store_and_idx):
    """FTS5 INDEX still has the tool_use token, so query() returns the
    message. The scatter step must then drop it because the text-only
    projection has no match.

    Bug it would surface: scatter step using the FULL projection cache
    key while the filter is on → tool snippets leak via FTS5 even
    though linear scan correctly excludes them.
    """
    store, idx = fts5_store_and_idx

    # FTS5 index DOES find the row (it stores full text).
    raw_index_hits = idx.query(TOKEN_TOOL_USE)
    assert any(r["conv_uuid"] == "conv-tu" for r in raw_index_hits), (
        "Index must store full text including tool_use input"
    )

    # But the public search path with the filter on returns nothing.
    filtered = _search_via_index(
        store, idx, TOKEN_TOOL_USE,
        source="all", context_size="snippet",
        sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None, bookmarks=None,
        include_tool_calls=False,
    )
    assert filtered == []

    # Inversion: filter off finds it.
    unfiltered = _search_via_index(
        store, idx, TOKEN_TOOL_USE,
        source="all", context_size="snippet",
        sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None, bookmarks=None,
        include_tool_calls=True,
    )
    assert len(unfiltered) == 1
    assert unfiltered[0].conversation_uuid == "conv-tu"


def test_fts5_cache_keys_do_not_poison_each_other(fts5_store_and_idx):
    """Toggling include_tool_calls between two consecutive queries must
    NOT cause the second query to return the first projection.

    Regression guard for the dynamic cache key. A static key would mean
    one of the two projections wins permanently.
    """
    store, idx = fts5_store_and_idx

    # Query 1 (filter ON) on conv-both. Caches text-only projection.
    r_on = _search_via_index(
        store, idx, TOKEN_BOTH,
        source="all", context_size="snippet",
        sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None, bookmarks=None,
        include_tool_calls=False,
    )
    # Query 2 (filter OFF) on conv-tu. Caches full projection on m-tu.
    r_off = _search_via_index(
        store, idx, TOKEN_TOOL_USE,
        source="all", context_size="snippet",
        sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None, bookmarks=None,
        include_tool_calls=True,
    )

    assert len(r_on) == 1 and r_on[0].conversation_uuid == "conv-both"
    assert len(r_off) == 1 and r_off[0].conversation_uuid == "conv-tu"

    # And the reverse order: re-query conv-both with filter ON now that
    # the conv-tu message has the full projection cached. Must still
    # find conv-both (and not bleed conv-tu's tool projection).
    r_on_again = _search_via_index(
        store, idx, TOKEN_BOTH,
        source="all", context_size="snippet",
        sort="updated_at", sort_order="desc",
        conversation_uuid=None, project_path=None, bookmarks=None,
        include_tool_calls=False,
    )
    assert r_on_again[0].conversation_uuid == "conv-both"
    assert r_on_again[0].matching_messages == r_on[0].matching_messages


def test_default_include_tool_calls_true_preserves_legacy_behavior():
    """Existing callers (FakeStore-based unit tests, /api/search clients)
    that don't pass include_tool_calls must keep finding tool matches.

    Bug it would surface: flipping the parameter default to False would
    silently break every existing call site that relied on the legacy
    behavior.
    """
    store = FakeStore([_conv_tool_use()])
    # No flag passed at all.
    results = search_conversations(store, TOKEN_TOOL_USE).results
    assert len(results) == 1
    assert results[0].conversation_uuid == "conv-tu"
