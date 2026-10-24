"""Title-resolution rule for Claude Code JSONL conversations.

Build-2 — see PLANS/explorer-improvements-build.md.

Rule (validated against six real sessions in Inv-2):
  1. Last entry with type=='summary' wins (its 'summary' field is the title).
  2. Else: first non-system user message's first meaningful line.
  3. Else: 'Untitled — <iso-date>'.
"""

from __future__ import annotations

from pathlib import Path

from backend.claude_code_reader import (
    read_claude_code_conversation,
    read_conversation_summary_fast,
)

FIXTURES = Path(__file__).parent / "fixtures" / "jsonl"


def test_multi_summary_uses_last_summary_fast() -> None:
    meta = read_conversation_summary_fast(FIXTURES / "multi_summary.jsonl")
    assert meta is not None
    assert meta["name"] == "Final Summary - Latest Topic"


def test_multi_summary_uses_last_summary_full() -> None:
    conv = read_claude_code_conversation(FIXTURES / "multi_summary.jsonl")
    assert conv is not None
    assert conv["name"] == "Final Summary - Latest Topic"


def test_single_summary_used_as_title_fast() -> None:
    meta = read_conversation_summary_fast(FIXTURES / "single_summary.jsonl")
    assert meta is not None
    assert meta["name"] == "Building LinkedIn Tab Title Userscripts with Git"


def test_single_summary_used_as_title_full() -> None:
    conv = read_claude_code_conversation(FIXTURES / "single_summary.jsonl")
    assert conv is not None
    assert conv["name"] == "Building LinkedIn Tab Title Userscripts with Git"


def test_six_summaries_uses_last_one() -> None:
    meta = read_conversation_summary_fast(FIXTURES / "six_summaries.jsonl")
    assert meta is not None
    assert meta["name"] == "Claude Desktop Message Exporter Polish Features"


def test_no_summary_falls_back_to_first_user_message_fast() -> None:
    meta = read_conversation_summary_fast(FIXTURES / "no_summary.jsonl")
    assert meta is not None
    assert "Quick question about Python decorators" in meta["name"]


def test_no_summary_falls_back_to_first_user_message_full() -> None:
    conv = read_claude_code_conversation(FIXTURES / "no_summary.jsonl")
    assert conv is not None
    assert "Quick question about Python decorators" in conv["name"]


def test_system_only_users_skipped_in_fallback() -> None:
    meta = read_conversation_summary_fast(FIXTURES / "system_only_user.jsonl")
    assert meta is not None
    assert "Tell me about your favorite color" in meta["name"]
    assert "Caveat" not in meta["name"]
    assert "<command-name>" not in meta["name"]
