"""Regression tests for two Claude Code rendering bugs found 2026-05-12.

Bug 1 — Friendly title (`type:"custom-title"` / `type:"agent-name"`).
    CC writes these rows when the user runs `/rename`. The reader was
    only consulting `type:"summary"` rows, so renamed sessions fell back
    to the truncated first user message.

Bug 2 — Compact truncation via leaf-walk cycle.
    CC re-serializes some messages with identical UUIDs across the
    `/compact` boundary. The streaming-chunk dedupe in
    `_get_message_key` merges them; the parent-chain walk in
    `store.resolve_active_branch` then hits a synthetic cycle and drops
    every pre-compact message. CC is fundamentally an append-only
    chronological log with no edit-branches, so the fix is to skip the
    leaf-walk for CC sessions and render `chat_messages` in original
    order.

Black-box discipline (per TESTING.md):
- Bidirectional verification: each test asserts the NEW behavior AND
  proves the test would catch a regression to the OLD behavior.
- No knowledge of internal helper names is required to read what the
  test guards; only public reader / store outputs are inspected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.claude_code_reader import (
    read_claude_code_conversation,
    read_conversation_summary_fast,
)
from backend.store import ConversationStore

FIXTURES = Path(__file__).parent / "fixtures" / "jsonl"


# ---------------------------------------------------------------------------
# Bug 1 — title extraction from CC's renamed-session rows
# ---------------------------------------------------------------------------


def test_custom_title_row_wins_over_first_user_message() -> None:
    """A `type:"custom-title"` row supplies the conversation name even
    when the first user message would otherwise be truncated into a
    placeholder title."""
    path = FIXTURES / "custom_title.jsonl"
    full = read_claude_code_conversation(path)
    fast = read_conversation_summary_fast(path)
    assert full is not None
    assert fast is not None

    # NEW behavior: friendly title surfaces from custom-title row.
    assert full["name"] == "Medium post creation"
    assert fast["name"] == "Medium post creation"

    # OLD-behavior catch: the first user message starts with
    # "I don't think we've pushed this repo yet" — if the fix
    # regressed, the name would be that truncated string.
    assert not full["name"].lower().startswith("i don't think")
    assert not fast["name"].lower().startswith("i don't think")


def test_last_title_event_wins_across_rename() -> None:
    """When the user renames mid-session and CC later auto-emits a
    summary, the chronologically-last title event should win. This
    mirrors the existing "last summary wins" rule."""
    path = FIXTURES / "custom_title_last_wins.jsonl"
    full = read_claude_code_conversation(path)
    fast = read_conversation_summary_fast(path)
    assert full is not None
    assert fast is not None

    assert full["name"] == "Renamed Later"
    assert fast["name"] == "Renamed Later"

    # Bidirectional: prove we're not just picking the first one or the
    # `summary` field.
    assert full["name"] != "First name"
    assert full["name"] != "Auto generated summary that should NOT win"


def test_empty_custom_title_falls_through_to_message() -> None:
    """A blank `customTitle` (whitespace-only) must NOT blank out the
    title. Falls through to first-user-message truncation."""
    path = FIXTURES / "custom_title_empty.jsonl"
    full = read_claude_code_conversation(path)
    fast = read_conversation_summary_fast(path)
    assert full is not None
    assert fast is not None

    # Must use the first user message as fallback (not empty string).
    assert full["name"].startswith("real first message")
    assert fast["name"].startswith("real first message")


def test_no_title_rows_falls_back_to_truncated_message() -> None:
    """JSONLs with no title rows at all should keep the existing
    first-user-message-truncated behavior — guards against
    over-eagerly rewriting `name` to the session UUID."""
    # Reuse the existing no_summary.jsonl which has no summary,
    # no custom-title, and no agent-name rows.
    path = FIXTURES / "no_summary.jsonl"
    full = read_claude_code_conversation(path)
    fast = read_conversation_summary_fast(path)
    assert full is not None
    assert fast is not None

    assert "Python decorators" in full["name"]
    assert "Python decorators" in fast["name"]
    # Bidirectional: not the bare stem.
    assert full["name"] != path.stem
    assert fast["name"] != path.stem


def test_summary_only_still_works_for_legacy_sessions() -> None:
    """Older CC versions only emit `type:"summary"` rows; that path
    must still produce the title."""
    path = FIXTURES / "single_summary.jsonl"
    full = read_claude_code_conversation(path)
    fast = read_conversation_summary_fast(path)
    assert full is not None
    assert fast is not None

    assert full["name"] == "Building LinkedIn Tab Title Userscripts with Git"
    assert fast["name"] == "Building LinkedIn Tab Title Userscripts with Git"


# ---------------------------------------------------------------------------
# Bug 2 — compact-aware rendering: pre-compact messages must survive
# ---------------------------------------------------------------------------


def test_cc_compact_preserves_pre_compact_messages(tmp_path) -> None:
    """A CC session with messages BEFORE and AFTER a compact marker
    must surface every message at the store layer — the parent-chain
    walk that drops pre-compact rows on duplicate-UUID collisions
    must NOT be applied to CC."""
    src = FIXTURES / "cc_compact_with_dup_uuids.jsonl"

    # Wire the store to a temp .claude-explorer-style layout: the store
    # treats `~/.claude/projects/<encoded>/<session>.jsonl` as a live CC
    # session. We patch the discovery root.
    proj_dir = tmp_path / ".claude" / "projects" / "-tmp-fake"
    proj_dir.mkdir(parents=True)
    (proj_dir / "sess-compact.jsonl").write_bytes(src.read_bytes())

    store = ConversationStore(
        data_dir=tmp_path / "empty-data",
        claude_dir=tmp_path / ".claude",
    )
    detail = store.get_conversation("sess-compact")

    assert detail is not None, "store failed to find the CC session"
    assert detail.source == "CLAUDE_CODE"

    # NEW behavior: every chronological message renders, including the
    # 4 pre-compact rows. The fixture has 4 pre-compact user/assistant
    # rows, 1 compact-summary row, then 2 post-compact rows = 7.
    # (The duplicated `pre-a` collapses via msg.id dedupe so 4 pre +
    # 1 compact + 2 post = 7.)
    assert len(detail.messages) == 7, (
        f"expected 7 chronological messages; got {len(detail.messages)}"
    )

    # OLD-behavior catch: under the bug, leaf-walking from `post-a`
    # would terminate at `compact-summary` (whose parent `pre-a` is
    # already in the visited set via the duplicate), so the first
    # rendered message was the compact summary. Assert the first
    # rendered message is the actual first user message.
    assert detail.messages[0].uuid == "pre-1"
    assert detail.messages[0].sender == "human"
    assert "first pre-compact" in detail.messages[0].text

    # Compact marker is preserved at its chronological position
    # (index 4 in the 0..6 list) so the UI can render it inline.
    assert detail.messages[4].uuid == "compact-summary"
    assert "previous conversation" in detail.messages[4].text.lower()

    # And the compact-marker side-channel still surfaces it for the
    # frontend's CompactMarker overlay.
    marker_uuids = [m.message_uuid for m in detail.compact_markers]
    assert "compact-summary" in marker_uuids

    # Bug 2 also poisoned has_branches() for CC sessions — the
    # duplicate-UUID parent-children map looked like a true fork.
    # CC has no edit-branch UI; the flag must be False.
    assert detail.has_branches is False


def test_cc_compact_message_count_matches_chat_messages(tmp_path) -> None:
    """The `message_count` field must equal the rendered list length
    for CC. Catches regression where one side counts pre-compact rows
    but the other does not."""
    src = FIXTURES / "cc_compact_with_dup_uuids.jsonl"
    proj_dir = tmp_path / ".claude" / "projects" / "-tmp-fake"
    proj_dir.mkdir(parents=True)
    (proj_dir / "sess-compact.jsonl").write_bytes(src.read_bytes())

    store = ConversationStore(
        data_dir=tmp_path / "empty-data",
        claude_dir=tmp_path / ".claude",
    )
    detail = store.get_conversation("sess-compact")

    assert detail is not None
    assert detail.message_count == len(detail.messages)
    # Bidirectional: a regression where message_count counts the raw
    # JSONL entries (including non-user/assistant rows) or counts only
    # the post-compact branch would fail one side or the other.
    assert detail.message_count == 7


def test_cc_leaf_override_is_ignored(tmp_path) -> None:
    """CC has no edit-branches; `leaf_override` (UI branch-switcher)
    should be a no-op rather than truncating the chronological view."""
    src = FIXTURES / "cc_compact_with_dup_uuids.jsonl"
    proj_dir = tmp_path / ".claude" / "projects" / "-tmp-fake"
    proj_dir.mkdir(parents=True)
    (proj_dir / "sess-compact.jsonl").write_bytes(src.read_bytes())

    store = ConversationStore(
        data_dir=tmp_path / "empty-data",
        claude_dir=tmp_path / ".claude",
    )
    # Override to a pre-compact leaf — under old logic this would
    # collapse the rendered list to just the pre-compact branch.
    detail = store.get_conversation("sess-compact", leaf_override="pre-2")

    assert detail is not None
    # All 7 messages still render — leaf_override is ignored for CC.
    assert len(detail.messages) == 7
    assert detail.messages[0].uuid == "pre-1"


# ---------------------------------------------------------------------------
# Integration — the real user-reported JSONL, when present locally
# ---------------------------------------------------------------------------

REAL_PATH = Path(
    "/Users/rpeck/.claude/projects/-Users-rpeck-Source-claude-desktop-message-exporter/76fe578b-7872-4263-bc24-f911c7f2efcc.jsonl"
)


@pytest.mark.skipif(not REAL_PATH.exists(), reason="user-reported JSONL not present")
def test_user_reported_session_renders_correctly() -> None:
    """Smoke test against the actual JSONL the user reported the bugs
    in. Skipped on CI (file is dev-machine specific).

    History (2026-05-12):

    * Bug 1 — title rendered as truncated first-message string instead of
      the user's /rename title. Fix: `_TITLE_FIELD_BY_TYPE` in the reader.
      Pinned by ``conv["name"] == "Medium post creation"``.
    * Bug 2 — only 1143/1410 messages rendered. Fix: streaming-chunk
      merge respects all entries. Pinned (then) by ``len == 1410``.
    * Bug 3 — first three "messages" were raw `<local-command-caveat>`,
      `<command-name>/exit`, `<local-command-stdout>` XML. Fix: collapse
      adjacent local-command boilerplate into one marker
      (`_collapse_local_command_triplets`). This shrinks the rendered count
      below 1410 (every triplet becomes 1 row, every doublet becomes 1 row),
      so we now pin a count RANGE plus assert the head of the stream is a
      clean marker, not raw XML.
    """
    conv = read_claude_code_conversation(REAL_PATH)
    assert conv is not None
    # Title comes from custom-title row, not the truncated first-msg.
    assert conv["name"] == "Medium post creation"

    messages = conv["chat_messages"]
    # Bug-2 contract: streaming-chunk merge must still surface every
    # logical message. The triplet collapse removes ~30-50 boilerplate
    # rows; the floor is conservative.
    #
    # No upper bound: this is the live session the developer keeps
    # working in (the conversation about this very project), so the
    # message count only grows over time. A hardcoded ceiling becomes
    # stale within days and produces false-positive regressions.
    # The "did the collapse do anything?" half of the invariant is
    # checked below — first message must be a synthetic marker, not
    # raw boilerplate XML.
    assert len(messages) >= 1300, (
        f"expected at least 1300 messages after triplet collapse "
        f"(streaming-chunk merge regressed if this fires); got {len(messages)}"
    )

    # Bug-3 contract: the first message must NOT be raw local-command XML.
    first = messages[0]
    first_text = first.get("text", "")
    assert "<local-command-caveat>" not in first_text, (
        "first message must not be raw caveat XML; "
        "triplet collapse should have replaced it with a synthetic marker"
    )
    assert "<command-name>" not in first_text, (
        "first message must not be raw command-name XML"
    )
    assert "<local-command-stdout>" not in first_text, (
        "first message must not be raw stdout XML"
    )
    # Either the marker (synthetic) or a real user prompt is fine — what
    # matters is no raw boilerplate at the head. The reported session
    # opens with /exit boilerplate, so we expect the marker specifically.
    assert first.get("is_command_marker") is True, (
        f"expected synthetic command marker at index 0; got: "
        f"text={first_text[:80]!r}, is_command_marker={first.get('is_command_marker')!r}"
    )
    assert first_text == "Session: /exit", (
        f"first marker should label as /exit; got: {first_text!r}"
    )
