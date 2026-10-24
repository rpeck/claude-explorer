"""V1 polish cleanup (2026-05-13): the search projection MUST exclude
argless command markers (``is_command_marker=True``), matching the
viewer's hidden-by-default behavior and the export surfaces' exclusion
in ``backend.export._is_excludable_marker``.

Spec invariant "one truth, three surfaces" (viewer + search + export):
typing ``exit`` in the search box should NOT produce noisy hits on
``Session: /exit`` chrome rows. If the viewer hides a bubble behind
``SessionPreludeAffordance`` / ``SlashCommandBadge`` and the export
drops it from Markdown/PDF, then a search hit on it would navigate the
user to invisible chrome — exactly the failure mode we want to prevent.

Bidirectional contract:

  * NEGATIVE (the new behavior — these tests):
      - argless ``/exit`` marker  -> projection is ``""``
      - argless ``/clear`` marker -> projection is ``""``
      - prelude marker (which is always argless) -> projection is ``""``
      - exclusion holds in BOTH projection variants
        (include_tool_calls=True AND False)

  * POSITIVE (the inverse — pre-existing behavior must still work):
      - argful ``/coding <prose>`` marker (is_command_marker=False)
        IS searchable; both the user's prose AND the ``/coding``
        token appear in the projection.
      - argful ``/plan <prose>`` marker IS searchable on the prose body.
      - regular user message whose body contains the literal substring
        ``/exit`` somewhere (is_command_marker=False) IS searchable.
      - regular assistant reply IS searchable.

  * INDEX-LEVEL VERIFICATION:
      - The FTS5 indexer (``upsert_conversation``) writes an empty
        ``body`` for argless-marker rows, so an FTS5 ``MATCH`` on
        ``exit`` returns 0 rows for a conversation whose only "exit"
        content lives inside a ``Session: /exit`` argless marker.
      - Bidirectional inverse at the index level: the SAME conversation
        with the marker replaced by a regular user message containing
        ``"please exit the program"`` DOES return a body-match row.
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

# ----- _extract_searchable_text: NEGATIVE (argless excluded) ----------------


def test_argless_exit_marker_returns_empty_projection() -> None:
    """An argless ``/exit`` marker (the post-collapser synthetic shape)
    must produce an empty projection. Otherwise its body ``"Session: /exit"``
    and its ``/exit`` slash_command token would both leak into FTS5 and a
    user searching for ``exit`` would hit chrome the viewer hides."""
    marker = {
        "uuid": "u1",
        "sender": "human",
        "text": "Session: /exit",
        "content": [{"type": "text", "text": "Session: /exit"}],
        "is_command_marker": True,
        "slash_command": "/exit",
    }
    assert _extract_searchable_text(marker) == ""


def test_argless_clear_marker_returns_empty_projection() -> None:
    """``/clear`` is the other common argless marker. Same exclusion."""
    marker = {
        "uuid": "u1",
        "sender": "human",
        "text": "Session: /clear",
        "content": [{"type": "text", "text": "Session: /clear"}],
        "is_command_marker": True,
        "slash_command": "/clear",
    }
    assert _extract_searchable_text(marker) == ""


def test_prelude_marker_returns_empty_projection() -> None:
    """Leading prelude markers (``is_prelude=True``) are always argless
    per the invariant enforced at
    ``claude_code_reader._flag_leading_prelude_markers``: ``is_prelude=True``
    implies ``is_command_marker=True``. Same exclusion path."""
    marker = {
        "uuid": "u1",
        "sender": "human",
        "text": "Session: /exit",
        "content": [{"type": "text", "text": "Session: /exit"}],
        "is_command_marker": True,
        "is_prelude": True,
        "slash_command": "/exit",
    }
    assert _extract_searchable_text(marker) == ""


def test_argless_marker_exclusion_holds_with_include_tool_calls_false() -> None:
    """The early-return guard MUST fire BEFORE the include_tool_calls
    branching, so toggling tool visibility doesn't accidentally re-include
    chrome. Both projection variants must return ``""``."""
    marker = {
        "uuid": "u1",
        "sender": "human",
        "text": "Session: /exit",
        "content": [{"type": "text", "text": "Session: /exit"}],
        "is_command_marker": True,
        "slash_command": "/exit",
    }
    assert _extract_searchable_text(marker, include_tool_calls=True) == ""
    assert _extract_searchable_text(marker, include_tool_calls=False) == ""


def test_argless_marker_with_sibling_tool_blocks_still_excluded() -> None:
    """Defensive: even if some unexpected pipeline injects tool blocks
    alongside an argless marker, the early-return drops them all. The
    chrome row must be inert regardless of payload shape."""
    marker = {
        "uuid": "u1",
        "sender": "human",
        "text": "Session: /exit",
        "content": [
            {"type": "text", "text": "Session: /exit"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "echo hello"}},
            {"type": "tool_result", "content": "hello"},
        ],
        "is_command_marker": True,
        "slash_command": "/exit",
    }
    assert _extract_searchable_text(marker) == ""


# ----- _extract_searchable_text: POSITIVE (argful + regular still indexed) ---


def test_argful_coding_marker_still_searchable() -> None:
    """Argful slash markers carry ``is_command_marker=False`` post-Fix-2
    (claude_code_reader.py:454: argful triplets get ``is_command_marker=False``
    via the ``cur.get('is_command_marker') is not True`` branch). The user's
    prose AND the ``/coding`` slash_command token must remain searchable."""
    marker = {
        "uuid": "u1",
        "sender": "human",
        "text": "Double-check your plan with the LLM council.",
        "content": [
            {"type": "text", "text": "Double-check your plan with the LLM council."}
        ],
        "is_command_marker": False,
        "slash_command": "/coding",
    }
    projection = _extract_searchable_text(marker)
    assert "Double-check your plan with the LLM council." in projection
    assert "/coding" in projection


def test_argful_plan_marker_still_searchable() -> None:
    """Same bidirectional inverse for ``/plan``: the prose must be
    searchable; the slash command token must be searchable."""
    marker = {
        "uuid": "u1",
        "sender": "human",
        "text": "Plan a migration from FTS5 to Tantivy.",
        "content": [
            {"type": "text", "text": "Plan a migration from FTS5 to Tantivy."}
        ],
        "is_command_marker": False,
        "slash_command": "/plan",
    }
    projection = _extract_searchable_text(marker)
    assert "Plan a migration from FTS5 to Tantivy." in projection
    assert "/plan" in projection


def test_regular_user_message_containing_slash_exit_is_searchable() -> None:
    """The strongest bidirectional inverse: a real user message whose
    body literally contains ``/exit`` (e.g. the user is discussing the
    command itself) MUST remain searchable. The exclusion is keyed on
    ``is_command_marker``, not on textual heuristics."""
    msg = {
        "uuid": "u1",
        "sender": "human",
        "text": "What does /exit do in Claude Code?",
        "content": [
            {"type": "text", "text": "What does /exit do in Claude Code?"}
        ],
        # The defining trait of a "real" user message vs a synthetic marker
        # is is_command_marker=False. (slash_command may be absent here —
        # there's no triplet to collapse.)
        "is_command_marker": False,
    }
    projection = _extract_searchable_text(msg)
    assert "What does /exit do in Claude Code?" in projection
    assert "/exit" in projection


def test_assistant_reply_with_no_marker_fields_is_searchable() -> None:
    """Defensive: assistant replies have no marker fields at all. They
    must pass through unchanged (the guard only fires on truthy
    ``is_command_marker``)."""
    msg = {
        "uuid": "a1",
        "sender": "assistant",
        "text": "Sure, here's the plan.",
        "content": [{"type": "text", "text": "Sure, here's the plan."}],
    }
    projection = _extract_searchable_text(msg)
    assert "Sure, here's the plan." in projection


def test_message_missing_is_command_marker_field_is_searchable() -> None:
    """Defensive: Desktop messages have no ``is_command_marker`` key at all.
    ``message.get("is_command_marker")`` returns None, and ``None is True``
    is False, so the guard correctly does NOT fire on these."""
    msg = {
        "uuid": "u1",
        "sender": "human",
        "text": "Hello from Desktop.",
        "content": [{"type": "text", "text": "Hello from Desktop."}],
        # No is_command_marker, no slash_command, no is_prelude — pure
        # Desktop shape.
    }
    projection = _extract_searchable_text(msg)
    assert "Hello from Desktop." in projection


# ----- Strict ``is True`` check: defensive type guards ----------------------


def test_non_bool_truthy_value_does_not_trigger_exclusion() -> None:
    """The guard uses ``is True``, not truthiness. A string ``"true"`` or
    integer ``1`` is truthy but NOT identical to ``True`` — it must
    pass through. This pins the strict identity check so a future
    refactor to ``if message.get("is_command_marker"):`` would fail.

    Rationale: production data is always real bool per the Pydantic
    Message model, but the function signature is ``dict[str, Any]`` and
    test fixtures or third-party data could inject odd types. Better to
    fail open (include in search) than fail closed (silently drop
    legitimate messages).
    """
    # String "true" — common JSON-serialization artifact.
    msg_str = {
        "uuid": "u1",
        "sender": "human",
        "text": "Body text.",
        "content": [{"type": "text", "text": "Body text."}],
        "is_command_marker": "true",  # not the bool True
    }
    assert _extract_searchable_text(msg_str) != ""
    assert "Body text." in _extract_searchable_text(msg_str)

    # Integer 1 — defensive against int-vs-bool coercion drift.
    msg_int = {
        "uuid": "u2",
        "sender": "human",
        "text": "Body text.",
        "content": [{"type": "text", "text": "Body text."}],
        "is_command_marker": 1,
    }
    assert _extract_searchable_text(msg_int) != ""


def test_is_command_marker_false_does_not_trigger_exclusion() -> None:
    """Symmetric: an explicit ``False`` must NOT trigger the guard. This
    is the "argful marker" production case."""
    msg = {
        "uuid": "u1",
        "sender": "human",
        "text": "Body text.",
        "content": [{"type": "text", "text": "Body text."}],
        "is_command_marker": False,
    }
    assert "Body text." in _extract_searchable_text(msg)


# ----- Schema-version verification ----------------------------------------


def test_schema_version_bumped_to_4() -> None:
    """SCHEMA_VERSION MUST be at least 4 — the bump that forces an
    existing on-disk index to drop+rebuild on next backend start so
    pre-cleanup argless-marker body rows get cleared. If a refactor
    accidentally lowers this, existing user installs would serve stale
    chrome hits until manual reindex."""
    assert SCHEMA_VERSION >= 4, (
        "SCHEMA_VERSION must be >=4 to force a rebuild that clears "
        "pre-cleanup argless-marker body rows. Got: %d" % SCHEMA_VERSION
    )


# ----- FTS5-level: end-to-end bidirectional contract ---------------------


@pytest.mark.skipif(not fts5_available(), reason="FTS5 not available")
def test_fts5_does_not_match_exit_inside_argless_marker(tmp_path) -> None:
    """End-to-end FTS5 contract.

    Setup: a conversation whose ONLY "exit" content lives inside an
    argless ``/exit`` marker row. After upsert with the new projection,
    an FTS5 MATCH for ``exit`` must return 0 rows for this conversation.

    Bidirectional inverse: the same conversation with the marker
    REPLACED by a regular user message containing ``"please exit the
    program"`` DOES produce a match.
    """
    reset_search_index_for_tests()
    index_path = tmp_path / "idx.sqlite"
    idx = SearchIndex(index_path)

    # ----- NEGATIVE: argless marker -> 0 body matches -----
    marker_conv = {
        "uuid": "conv-marker",
        "name": "Some innocuous title",  # does NOT contain "exit"
        "source": "CLAUDE_CODE",
        "project_path": "/tmp/test",
        "chat_messages": [
            {
                "uuid": "m1",
                "sender": "human",
                "text": "Session: /exit",
                "content": [{"type": "text", "text": "Session: /exit"}],
                "is_command_marker": True,
                "slash_command": "/exit",
                "created_at": "2026-05-13T00:00:00Z",
            },
        ],
    }
    idx.upsert_conversation(marker_conv, index_path, mtime=1.0)

    # FTS5 MATCH for "exit" — should return 0 rows.
    matches = idx.query("exit", source="all")
    matching_for_marker_conv = [m for m in matches if m["conv_uuid"] == "conv-marker"]
    assert matching_for_marker_conv == [], (
        "Argless marker conversation should produce 0 FTS5 matches for 'exit'; "
        f"got: {matching_for_marker_conv!r}"
    )

    # ----- POSITIVE: real user message body containing "exit" -> 1+ matches -----
    real_conv = {
        "uuid": "conv-real",
        "name": "Some innocuous title 2",  # does NOT contain "exit"
        "source": "CLAUDE_CODE",
        "project_path": "/tmp/test",
        "chat_messages": [
            {
                "uuid": "m2",
                "sender": "human",
                "text": "please exit the program",
                "content": [{"type": "text", "text": "please exit the program"}],
                "is_command_marker": False,
                "created_at": "2026-05-13T00:00:00Z",
            },
        ],
    }
    idx.upsert_conversation(real_conv, index_path, mtime=1.0)

    matches = idx.query("exit", source="all")
    matching_for_real_conv = [m for m in matches if m["conv_uuid"] == "conv-real"]
    assert len(matching_for_real_conv) >= 1, (
        "Real user message containing 'exit' should produce at least 1 FTS5 "
        f"match; got: {matching_for_real_conv!r}"
    )

    idx.close()
    reset_search_index_for_tests()


@pytest.mark.skipif(not fts5_available(), reason="FTS5 not available")
def test_fts5_title_match_still_works_for_conv_with_only_marker_rows(tmp_path) -> None:
    """Subtle correctness check (raised by GPT-5.2 review).

    With argless markers producing empty bodies, the FTS5 ``title`` column
    still carries the conversation title on the marker row. A query that
    matches the title MUST still return the conversation (the existing
    title-sweep + title pseudo-message machinery in _search_via_index
    depends on this).

    Setup: a conversation whose only content is an argless marker BUT
    whose title contains the query token.
    Expected: FTS5 MATCH on the title token returns the row (via
    unqualified MATCH which searches both title and body).
    """
    reset_search_index_for_tests()
    index_path = tmp_path / "idx_title.sqlite"
    idx = SearchIndex(index_path)

    conv = {
        "uuid": "conv-title",
        "name": "Migration retrospective",  # title carries the query word
        "source": "CLAUDE_CODE",
        "project_path": "/tmp/test",
        "chat_messages": [
            {
                "uuid": "m1",
                "sender": "human",
                "text": "Session: /exit",
                "content": [{"type": "text", "text": "Session: /exit"}],
                "is_command_marker": True,
                "slash_command": "/exit",
                "created_at": "2026-05-13T00:00:00Z",
            },
        ],
    }
    idx.upsert_conversation(conv, index_path, mtime=1.0)

    # Title-token query "Migration" — should hit via title column even
    # though body is empty.
    matches = idx.query("Migration", source="all")
    assert any(m["conv_uuid"] == "conv-title" for m in matches), (
        "Title-only match must still work after argless-marker bodies are "
        f"emptied; got: {matches!r}"
    )

    idx.close()
    reset_search_index_for_tests()
