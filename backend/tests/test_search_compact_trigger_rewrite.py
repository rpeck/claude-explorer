"""Compact-marker auto-expand backend contract (Option C, 2026-05-23).

When the user types a manual `/compact` in Claude Code, three artifacts are
produced in the JSONL:

  1. an ``isCompactSummary: true`` synthetic user row (the LLM's compaction
     summary; this is the row the frontend's CompactMarker auto-expand chain
     keys off);
  2. a SECOND user row with ``<command-name>/compact</command-name>`` +
     ``<command-args>{user_prompt}</command-args>`` (the user's typed slash
     command itself — what the runtime saw); and
  3. the verbatim user prose, which lives ONLY inside the trigger row's
     ``<command-args>`` block.

Pre-2026-05-23, the FTS5 indexer treated artifact (2) like any other user
message and indexed its full body — including the verbatim ``user_prompt``
text. A user searching for words they typed in their own compact prompt
would land on the trigger row's UUID, NOT the marker's UUID, so the
frontend's auto-expand could not fire (it keys on
``compact_marker.message_uuid``).

Option C: BOTH (a) exclude the trigger row from the index entirely, AND
(b) rewrite any latent trigger-row hits at scatter-gather time to point at
the corresponding marker. (a) is the cleanest UX fix; (b) is belt-and-
suspenders for the linear-scan fallback and stale indices.

Bidirectional contract:

  * NEGATIVE (new behavior, these tests):
      - ``_extract_searchable_text`` returns ``""`` for a /compact trigger
        row (any of the three projection variants).
      - End-to-end FTS5: searching for text that lives ONLY inside the
        trigger row's ``<command-args>`` returns ZERO matches.
      - Scatter-gather: if a hit somehow lands on a trigger row UUID
        (stale index / linear-scan path), it gets REWRITTEN to the
        corresponding compact marker UUID.

  * POSITIVE (inverse / must-not-break):
      - The isCompactSummary message body remains searchable.
      - A search for summary text returns the MARKER UUID (unchanged
        behavior).
      - Regular user messages whose body contains the literal substring
        ``/compact`` (e.g. the user is discussing the slash command) remain
        searchable. The predicate keys on the ``<command-name>`` envelope,
        not on the word ``compact``.

  * SCHEMA-VERSION:
      - ``SCHEMA_VERSION`` MUST be >= 11. The bump forces existing on-disk
        indices to drop+rebuild so pre-fix trigger-row tokens are cleared.
"""

from __future__ import annotations

import pytest

from backend.search import _extract_searchable_text
from backend.search_index import (
    SCHEMA_VERSION,
    SearchIndex,
    fts5_available,
    reset_search_index_for_tests,
)
from backend.search_text import _is_compact_trigger_message

# ---------------------------------------------------------------------------
# Realistic /compact trigger row shape (mirrors fixtures/jsonl/compact_manual_only.jsonl)
# ---------------------------------------------------------------------------

# Verbatim CC envelope (post-parse, after the JSONL `message.content` string
# becomes the message's `text` field). The two ``<command-*>`` tags + the
# ``<command-message>`` preamble exactly mirror what ``backend/cc_jsonl_io``
# emits when it parses CC's manual /compact replay row.
_TRIGGER_TEXT = (
    "<command-message>compact</command-message>\n"
    "<command-name>/compact</command-name>\n"
    "<command-args>Make special note of the improvements to the test suite "
    "and the new search-compact-auto-expand spec.</command-args>"
)

# The ``user_prompt`` substring that ONLY lives in the trigger row's
# <command-args>. Pre-fix, a search for this finds the trigger row.
_USER_PROMPT_NEEDLE = "Make special note of the improvements"

# The summary text — lives in the isCompactSummary marker message, NOT in
# the trigger row. Pre-existing behavior: search finds the marker.
_SUMMARY_NEEDLE = "ZEBRAQUARK summary keyword that lives only in the summary"


def _make_trigger_row(uuid: str = "trigger-uuid") -> dict:
    """A faithful representation of the post-parse /compact trigger row.

    Matches what `store._parse_message` produces from the JSONL row:
      - sender: "human"
      - is_command_marker: False (the collapser short-circuits /compact —
        see cc_message_transforms.py:259, `is_compact_run = True` branch)
      - text: the verbatim <command-*> envelope
    """
    return {
        "uuid": uuid,
        "sender": "human",
        "text": _TRIGGER_TEXT,
        "content": [{"type": "text", "text": _TRIGGER_TEXT}],
        "is_command_marker": False,
        "created_at": "2026-04-01T11:00:01Z",
    }


def _make_marker_row(uuid: str = "marker-uuid") -> dict:
    """The isCompactSummary synthetic message — what the frontend renders
    as the compact-marker pill and what auto-expand keys off."""
    summary_text = (
        "Manual compact summary: " + _SUMMARY_NEEDLE + " — focus on "
        "tests and refactor the auth module."
    )
    return {
        "uuid": uuid,
        "sender": "human",
        "text": summary_text,
        "content": [{"type": "text", "text": summary_text}],
        "is_command_marker": False,
        "isCompactSummary": True,
        "created_at": "2026-04-01T11:00:00Z",
    }


# ---------------------------------------------------------------------------
# Predicate: _is_compact_trigger_message
# ---------------------------------------------------------------------------


def test_predicate_fires_on_real_compact_trigger_row() -> None:
    """The predicate MUST identify the post-parse /compact trigger row by
    its ``<command-name>/compact</command-name>`` envelope (the same literal
    `extract_compact_markers` already uses for lookahead classification)."""
    assert _is_compact_trigger_message(_make_trigger_row()) is True


def test_predicate_does_not_fire_on_marker_row() -> None:
    """The isCompactSummary marker row carries the SUMMARY text; the
    ``<command-name>/compact</command-name>`` envelope NEVER appears in it.
    The predicate must NOT fire on it — otherwise we'd exclude the very
    rows the user CAN search to land the auto-expand."""
    assert _is_compact_trigger_message(_make_marker_row()) is False


def test_predicate_does_not_fire_on_regular_user_message_mentioning_compact() -> None:
    """The strongest bidirectional inverse: a user discussing the slash
    command itself (e.g. ``"How do I use /compact in CC?"``) MUST remain
    searchable. The predicate keys on the ``<command-name>`` envelope,
    not on the word ``compact``."""
    msg = {
        "uuid": "u1",
        "sender": "human",
        "text": "How do I use /compact in Claude Code?",
        "content": [{"type": "text", "text": "How do I use /compact in Claude Code?"}],
        "is_command_marker": False,
    }
    assert _is_compact_trigger_message(msg) is False


def test_predicate_does_not_fire_on_assistant_message() -> None:
    """Assistant messages never carry a slash-command envelope; the predicate
    only checks text content but is meaningful only on user messages — the
    indexer guard must not accidentally suppress assistant replies."""
    msg = {
        "uuid": "a1",
        "sender": "assistant",
        "text": "Here's the /compact docs you asked for.",
        "content": [{"type": "text", "text": "Here's the /compact docs you asked for."}],
    }
    assert _is_compact_trigger_message(msg) is False


def test_predicate_handles_missing_text_gracefully() -> None:
    """Defensive: empty / missing text MUST not raise. Predicate is
    consulted from the hot indexing path and from the scatter-gather
    rewrite — any TypeError would crash search."""
    assert _is_compact_trigger_message({"uuid": "x", "sender": "human"}) is False
    assert _is_compact_trigger_message({"uuid": "x", "text": None}) is False
    assert _is_compact_trigger_message({"uuid": "x", "text": ""}) is False


def test_predicate_finds_envelope_inside_content_blocks() -> None:
    """CC sometimes emits the trigger row as a content-block list instead
    of a flat string (see test_extract_compact_markers_list_content_blocks).
    The predicate must catch BOTH shapes; the indexer reads both surfaces
    via _extract_searchable_text."""
    msg = {
        "uuid": "u1",
        "sender": "human",
        "text": "",  # flat text is empty; envelope only in blocks
        "content": [{"type": "text", "text": _TRIGGER_TEXT}],
        "is_command_marker": False,
    }
    assert _is_compact_trigger_message(msg) is True


# ---------------------------------------------------------------------------
# _extract_searchable_text: trigger row excluded from projection
# ---------------------------------------------------------------------------


def test_extract_searchable_text_returns_empty_for_trigger_row() -> None:
    """The indexing-time predicate fires before any body assembly, so the
    projection MUST be ``""`` — matching how is_command_marker=True markers
    are handled today."""
    assert _extract_searchable_text(_make_trigger_row()) == ""


def test_extract_searchable_text_exclusion_holds_in_both_projection_modes() -> None:
    """The Tools-toggle projection (include_tool_calls=False) must apply
    the same exclusion — otherwise a user with Tools off would still get
    trigger-row hits via the body_text column."""
    trigger = _make_trigger_row()
    assert _extract_searchable_text(trigger, include_tool_calls=True) == ""
    assert _extract_searchable_text(trigger, include_tool_calls=False) == ""


def test_extract_searchable_text_keeps_marker_row_searchable() -> None:
    """Bidirectional inverse: the marker (isCompactSummary) row must
    remain searchable — the user MUST be able to search for summary
    text and find the marker."""
    projection = _extract_searchable_text(_make_marker_row())
    assert projection != ""
    assert _SUMMARY_NEEDLE in projection


def test_extract_searchable_text_user_prompt_text_not_in_trigger_projection() -> None:
    """The hard contract: the verbatim user_prompt that lives inside the
    <command-args> block MUST NOT appear in the trigger row's indexed
    projection. (This is the bug that produced the wrong-UUID search
    hit; the fix is to drop the projection entirely.)"""
    projection = _extract_searchable_text(_make_trigger_row())
    assert _USER_PROMPT_NEEDLE not in projection
    assert projection == ""


# ---------------------------------------------------------------------------
# Schema version pin
# ---------------------------------------------------------------------------


def test_schema_version_bumped_to_11_or_higher() -> None:
    """SCHEMA_VERSION must be >= 11 so existing on-disk indices DROP+rebuild
    on next backend start, clearing pre-fix trigger-row body tokens. Without
    the bump, deployed users would serve stale hits and the fix would
    appear not to work."""
    assert SCHEMA_VERSION >= 11, (
        f"SCHEMA_VERSION must be >=11 to force a rebuild that clears pre-fix "
        f"/compact trigger-row body tokens. Got: {SCHEMA_VERSION}"
    )


# ---------------------------------------------------------------------------
# End-to-end FTS5: trigger row produces zero matches; marker matches
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not fts5_available(), reason="FTS5 not available")
def test_fts5_user_prompt_text_does_not_match_via_trigger_row(tmp_path) -> None:
    """The bug repro at the FTS5 layer.

    Setup: a conversation with both the marker (summary text) and the
    trigger row (user_prompt inside <command-args>). The marker carries
    the summary needle; the trigger row carries the user-prompt needle.

    Bidirectional contract:
      * Searching for the user-prompt needle returns ZERO body matches
        (trigger row excluded from index).
      * Searching for the summary needle returns the MARKER UUID.
    """
    reset_search_index_for_tests()
    index_path = tmp_path / "idx.sqlite"
    idx = SearchIndex(index_path)

    conv = {
        "uuid": "conv-compact",
        "name": "Manual compact session",
        "source": "CLAUDE_CODE",
        "project_path": "/tmp/proj",
        "chat_messages": [
            {
                "uuid": "u1",
                "sender": "human",
                "text": "Start a long task.",
                "content": [{"type": "text", "text": "Start a long task."}],
                "is_command_marker": False,
                "created_at": "2026-04-01T10:00:00Z",
            },
            _make_marker_row(uuid="marker-uuid"),
            _make_trigger_row(uuid="trigger-uuid"),
        ],
    }
    idx.upsert_conversation(conv, index_path, mtime=1.0)

    # ----- NEGATIVE: user_prompt token "improvements" only lives in the
    # trigger row's <command-args>; index must NOT hit it.
    # Tightened (per code review): assert ZERO rows from this conversation,
    # not just absence of trigger-uuid. A future title/body drift could
    # otherwise false-green this assertion.
    matches = idx.query("improvements", source="all")
    matching_for_conv = [m for m in matches if m["conv_uuid"] == "conv-compact"]
    assert matching_for_conv == [], (
        "Conversation must produce ZERO matches on the user_prompt-only "
        f"token 'improvements'; got: {matching_for_conv!r}"
    )

    # ----- Porter-stem variant: the FTS5 tokenizer is
    # `porter unicode61` so "improvements" stems to "improv"; querying the
    # singular form must ALSO not hit the trigger row body.
    matches = idx.query("improvement", source="all")
    matching_for_conv = [m for m in matches if m["conv_uuid"] == "conv-compact"]
    assert matching_for_conv == [], (
        "Porter-stem variant 'improvement' must also produce zero matches; "
        f"got: {matching_for_conv!r}"
    )

    # ----- POSITIVE: summary needle still matches the marker.
    matches = idx.query("ZEBRAQUARK", source="all")
    matching_uuids = {m["message_uuid"] for m in matches}
    assert "marker-uuid" in matching_uuids, (
        f"Marker row must remain searchable on summary text; got: {matches!r}"
    )

    idx.close()
    reset_search_index_for_tests()


@pytest.mark.skipif(not fts5_available(), reason="FTS5 not available")
def test_fts5_regular_message_mentioning_compact_still_searchable(tmp_path) -> None:
    """Bidirectional inverse: a regular user message whose body literally
    contains ``/compact`` (e.g. the user discussing the slash command in
    prose) MUST remain searchable. The predicate keys on the
    ``<command-name>/compact</command-name>`` envelope, not on the word
    ``compact``."""
    reset_search_index_for_tests()
    index_path = tmp_path / "idx_inverse.sqlite"
    idx = SearchIndex(index_path)

    conv = {
        "uuid": "conv-inverse",
        "name": "Discussing /compact",
        "source": "CLAUDE_CODE",
        "project_path": "/tmp/proj",
        "chat_messages": [
            {
                "uuid": "u-real",
                "sender": "human",
                "text": "How does /compact behave with subagents?",
                "content": [{"type": "text", "text": "How does /compact behave with subagents?"}],
                "is_command_marker": False,
                "created_at": "2026-04-01T10:00:00Z",
            },
        ],
    }
    idx.upsert_conversation(conv, index_path, mtime=1.0)

    matches = idx.query("subagents", source="all")
    matching_uuids = {m["message_uuid"] for m in matches}
    assert "u-real" in matching_uuids, (
        f"Regular user message mentioning /compact must remain searchable; "
        f"got: {matches!r}"
    )

    idx.close()
    reset_search_index_for_tests()


# ---------------------------------------------------------------------------
# Scatter-gather UUID rewrite (belt-and-suspenders for stale indices /
# linear-scan path)
# ---------------------------------------------------------------------------


def test_build_compact_trigger_uuid_map_realistic_layout() -> None:
    """The mapping helper takes a conversation dict and returns
    ``{trigger_row_uuid: marker_uuid}`` for every (marker, trigger) pair.

    Layout mirrors the post-parse production shape: the marker row
    appears FIRST (the isCompactSummary message is injected at the
    compaction moment), the trigger row follows within a small lookahead
    window (the runtime's replay of the user's slash command)."""
    from backend.search import _build_compact_trigger_uuid_map

    conv = {
        "uuid": "conv-compact",
        "chat_messages": [
            {"uuid": "u1", "sender": "human", "text": "Start.", "is_command_marker": False},
            _make_marker_row(uuid="marker-1"),
            _make_trigger_row(uuid="trigger-1"),
            {"uuid": "u4", "sender": "human", "text": "Continue.", "is_command_marker": False},
        ],
        "compact_markers": [
            {
                "message_uuid": "marker-1",
                "summary_text": "summary",
                "timestamp": "2026-04-01T11:00:00Z",
                "kind": "manual",
                "user_prompt": "Make special note of the improvements...",
            },
        ],
    }

    mapping = _build_compact_trigger_uuid_map(conv)
    assert mapping == {"trigger-1": "marker-1"}


def test_build_compact_trigger_uuid_map_no_compact_markers_returns_empty() -> None:
    """Conversations without compact markers (the common case) must
    return an empty mapping cheaply — this helper runs on every
    scatter-gather conversation walk."""
    from backend.search import _build_compact_trigger_uuid_map

    conv = {
        "uuid": "conv-plain",
        "chat_messages": [
            {"uuid": "u1", "sender": "human", "text": "Hello.", "is_command_marker": False},
        ],
        "compact_markers": [],
    }
    assert _build_compact_trigger_uuid_map(conv) == {}


def test_build_compact_trigger_uuid_map_auto_compact_has_no_trigger() -> None:
    """Auto compactions (isCompactSummary with NO replayed /compact within
    the lookahead window) have NO trigger row; the mapping must skip
    them silently rather than crash or guess."""
    from backend.search import _build_compact_trigger_uuid_map

    conv = {
        "uuid": "conv-auto",
        "chat_messages": [
            _make_marker_row(uuid="marker-auto"),
            # No trigger row — auto compaction. Next is a regular user msg.
            {
                "uuid": "u-next",
                "sender": "human",
                "text": "Carry on.",
                "is_command_marker": False,
            },
        ],
        "compact_markers": [
            {
                "message_uuid": "marker-auto",
                "summary_text": "summary",
                "timestamp": "2026-04-01T11:00:00Z",
                "kind": "auto",
                "user_prompt": None,
            },
        ],
    }
    assert _build_compact_trigger_uuid_map(conv) == {}


def test_build_compact_trigger_uuid_map_multiple_compacts() -> None:
    """A long session with multiple /compact runs must produce one
    mapping entry per (marker, trigger) pair without cross-binding."""
    from backend.search import _build_compact_trigger_uuid_map

    conv = {
        "uuid": "conv-multi",
        "chat_messages": [
            _make_marker_row(uuid="marker-A"),
            _make_trigger_row(uuid="trigger-A"),
            {"uuid": "u-mid", "sender": "human", "text": "More work.", "is_command_marker": False},
            _make_marker_row(uuid="marker-B"),
            _make_trigger_row(uuid="trigger-B"),
        ],
        "compact_markers": [
            {"message_uuid": "marker-A", "kind": "manual", "user_prompt": "X"},
            {"message_uuid": "marker-B", "kind": "manual", "user_prompt": "Y"},
        ],
    }
    mapping = _build_compact_trigger_uuid_map(conv)
    assert mapping == {"trigger-A": "marker-A", "trigger-B": "marker-B"}


def test_build_compact_trigger_uuid_map_handles_missing_compact_markers_key() -> None:
    """Defensive: Desktop conversations and pre-CC-image-cache CC convs
    may have no ``compact_markers`` key at all. The helper must default
    to empty — not crash."""
    from backend.search import _build_compact_trigger_uuid_map

    conv = {"uuid": "conv-x", "chat_messages": []}
    assert _build_compact_trigger_uuid_map(conv) == {}


def test_build_compact_trigger_uuid_map_respects_lookahead_window() -> None:
    """Behavioral coupling test (no shared constant — keeps layering clean):
    the rewrite mapper uses the SAME lookahead window as
    ``extract_compact_markers`` (``_COMPACT_LOOKAHEAD = 8`` in
    backend/cc_image_markers.py). If they drift, a manual /compact whose
    trigger row sits 9+ messages after the marker would be classified
    ``auto`` by ``extract_compact_markers`` but still get mapped by the
    rewrite (or vice versa).

    Contract: a trigger row within 8 messages of its marker IS mapped;
    a trigger row exactly 9 messages away is NOT. The numeric "8" lives
    once each in two places by design — testing them in lockstep here
    prevents silent drift without forcing a cross-module import edge."""
    from backend.search import _build_compact_trigger_uuid_map

    def _filler(uuid: str) -> dict:
        return {
            "uuid": uuid,
            "sender": "human",
            "text": "filler",
            "is_command_marker": False,
        }

    # Within window (offset 8: marker, then 7 fillers, then trigger).
    within_window = {
        "uuid": "conv-within",
        "chat_messages": [
            _make_marker_row(uuid="marker-w"),
            *[_filler(f"f{i}") for i in range(7)],
            _make_trigger_row(uuid="trigger-w"),  # offset 8 from marker
        ],
        "compact_markers": [
            {"message_uuid": "marker-w", "kind": "manual", "user_prompt": "X"},
        ],
    }
    assert _build_compact_trigger_uuid_map(within_window) == {"trigger-w": "marker-w"}

    # Beyond window (offset 9: marker, then 8 fillers, then trigger).
    # extract_compact_markers would classify this as auto (no trigger
    # found in window); the rewrite mapper must skip it too.
    beyond_window = {
        "uuid": "conv-beyond",
        "chat_messages": [
            _make_marker_row(uuid="marker-b"),
            *[_filler(f"f{i}") for i in range(8)],
            _make_trigger_row(uuid="trigger-b"),  # offset 9 from marker
        ],
        "compact_markers": [
            {"message_uuid": "marker-b", "kind": "auto", "user_prompt": None},
        ],
    }
    assert _build_compact_trigger_uuid_map(beyond_window) == {}


# ---------------------------------------------------------------------------
# Scatter-gather body-emit dedupe (linear-scan + slow-index paths)
# ---------------------------------------------------------------------------


def test_search_dedupe_when_both_marker_and_trigger_match(monkeypatch) -> None:
    """Belt-and-suspenders coverage: simulate the residual failure mode
    Option C still protects against. Once the v11 index rebuild lands,
    ``_is_compact_trigger_message`` returns True for the trigger row and
    ``_extract_searchable_text`` empties its body — so the trigger row
    is invisible to both the FTS5 index AND the linear-scan body match,
    and the rewrite path never fires.

    But for the rewrite + dedupe to be ROBUST against any future code
    path that re-introduces trigger-row text into search (or against a
    legacy on-disk state we haven't fully reasoned about), we need to
    pin the dedupe behavior at the rewrite site itself. This test
    forces the residual scenario by monkeypatching
    ``_is_compact_trigger_message`` to return False (simulating "stale
    index / regressed projection"), making BOTH the marker AND the
    trigger row visible to the linear-scan body match for the same
    token. The rewrite then collapses both message_uuids to the marker
    uuid; the emit-site dedupe MUST drop the second entry so
    ``matching_messages`` contains exactly one row per (rewritten)
    UUID — frontend does NOT dedupe at render time (see
    frontend/src/contexts/SearchPanelContext.tsx).
    """
    import backend.search as search_mod
    import backend.search_text as st
    from backend.search import _search_via_linear_scan

    # Two-pronged patch:
    # (1) Disable the index-time predicate in search_text so
    #     _extract_searchable_text returns the trigger row's body
    #     (simulating the stale state we're guarding against).
    # (2) Re-enable the same predicate ONLY for the rewrite mapper's
    #     consumption by leaving its dedicated binding alone — the
    #     rewrite map below is built BEFORE the body walk by directly
    #     calling _build_compact_trigger_uuid_map, which uses the
    #     search.py-local binding. Since search.py imported
    #     _is_compact_trigger_message at module-load time, its binding
    #     in search_mod survives the search_text monkeypatch.
    real_pred = search_mod._is_compact_trigger_message
    monkeypatch.setattr(st, "_is_compact_trigger_message", lambda _msg: False)
    monkeypatch.setattr(search_mod, "_is_compact_trigger_message", real_pred)

    # Build a conversation where BOTH the marker AND the trigger row
    # carry the shared token "ZEBRAQUARK" — simulates the stale-index
    # state where the trigger row's body is still in the index AND
    # is still readable from the JSONL.
    shared = "ZEBRAQUARK shared token in BOTH rows"
    marker = _make_marker_row(uuid="m1")
    marker["text"] = shared
    marker["content"] = [{"type": "text", "text": shared}]
    trigger = _make_trigger_row(uuid="t1")
    # Inject the same token into the trigger row's <command-args> so the
    # linear-scan regex would otherwise match BOTH rows.
    trigger["text"] = (
        "<command-message>compact</command-message>\n"
        "<command-name>/compact</command-name>\n"
        f"<command-args>{shared} note for fixture</command-args>"
    )
    trigger["content"] = [{"type": "text", "text": trigger["text"]}]

    conv = {
        "uuid": "conv-dedupe",
        "name": "Dedupe test",
        "source": "CLAUDE_CODE",
        "project_path": "/tmp/proj",
        "created_at": "2026-04-01T10:00:00Z",
        "updated_at": "2026-04-01T11:00:00Z",
        "chat_messages": [
            {
                "uuid": "u0",
                "sender": "human",
                "text": "Start.",
                "content": [{"type": "text", "text": "Start."}],
                "is_command_marker": False,
                "created_at": "2026-04-01T10:00:00Z",
            },
            marker,
            trigger,
        ],
        "compact_markers": [
            {
                "message_uuid": "m1",
                "summary_text": shared,
                "timestamp": "2026-04-01T11:00:00Z",
                "kind": "manual",
                "user_prompt": shared,
            },
        ],
    }

    class _FakeStore:
        def get_all_conversations_raw(self, *, source="all"):
            return [conv]

    results = _search_via_linear_scan(
        _FakeStore(),  # type: ignore[arg-type]
        "ZEBRAQUARK",
        source="all",
    )
    assert len(results) == 1, f"Expected one SearchResult, got: {results!r}"
    msgs = results[0].matching_messages
    # The contract: every emitted UUID must be unique within a result.
    # If dedupe is missing, the marker UUID appears twice — once from
    # the genuine marker row, once from the rewritten trigger row.
    emitted = [m.message_uuid for m in msgs]
    assert len(emitted) == len(set(emitted)), (
        f"Duplicate message_uuid in matching_messages: {emitted!r}"
    )
    # The single emitted body-row UUID must be the marker (NOT the trigger).
    body_uuids = [u for u in emitted if u != "title"]
    assert body_uuids == ["m1"], (
        f"Expected exactly one body row with marker uuid 'm1'; got: {body_uuids!r}"
    )
