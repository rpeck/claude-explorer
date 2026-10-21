"""Regression: CC ``--resume`` sessions write the same internal
``sessionId`` across MULTIPLE JSONL files (original session +
continuation file with a different filename stem). The store's
``list_claude_code_conversations`` must collapse these into a single
conversation per ``uuid``, keeping the one with the LATEST
``updated_at`` so the user sees the most-recent state (and the
sidebar / search emit one row per logical session, not two).

Bug surfaced 2026-05-22 by user testing on a 1062-file corpus: 38
sessionIds appeared twice, causing duplicate React keys in
``context_size='full'`` search results (the slow path walks every
conv dict via ``get_all_conversations_raw`` and emits one
``SearchResult`` per dict — duplicates collapse into duplicate-key
warnings in the frontend). FTS5 fast path was already last-writer-
wins per ``conv_uuid`` at INSERT time, so Snippet-mode results were
clean — this masked the bug until Full mode was exercised.

Bidirectional pair:

  * ``test_resume_files_stay_two_conversations`` — since 2026-10-21 a
    conversation's uuid is its file name, so the two files are two
    conversations. The old collapse hid the older file's messages.

  * ``test_two_distinct_session_ids_remain_two_convs`` — the
    must-not-match pair: distinct sessionIds across two files must
    NOT collapse. Pins that the dedup keys on ``uuid``, not on
    ``project_path`` or any other proxy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.claude_code_reader import list_claude_code_conversations


def _cc_entry(
    *,
    uuid: str,
    session_id: str,
    text: str,
    timestamp: str,
    role: str = "user",
    cwd: str = "/tmp/p",
) -> dict:
    """One JSONL entry in CC's wire format. Minimal but complete enough
    for ``_extract_conversation_metadata`` to extract uuid + updated_at."""
    if role == "user":
        return {
            "type": "user",
            "uuid": uuid,
            "parentUuid": None,
            "sessionId": session_id,
            "cwd": cwd,
            "gitBranch": "main",
            "version": "test",
            "timestamp": timestamp,
            "message": {"role": "user", "content": text},
        }
    return {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": None,
        "sessionId": session_id,
        "timestamp": timestamp,
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "id": f"msg_{uuid}",
            "content": [{"type": "text", "text": text}],
        },
    }


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


@pytest.fixture(autouse=True)
def _reset_conversation_cache():
    """Each test gets a fresh FileCache so prior tests' JSONLs don't bleed in."""
    from backend.cache import _conversation_cache
    if _conversation_cache is not None:
        _conversation_cache.clear()
    yield
    if _conversation_cache is not None:
        _conversation_cache.clear()


def test_resume_files_stay_two_conversations(tmp_path):
    """Two JSONL files share sessionId=S (the old CC resume pattern).

    Until 2026-10-21 the list collapsed them into ONE conversation that
    showed only the newer file, so the older file's messages were hidden.
    On one real archive such a hidden file had 6,790 rows, and only 21 of
    them were in the newer file. A conversation's uuid is now its file name
    (``cc_message_transforms.cc_session_uuid``), so both files stay visible,
    each with its own messages, and no uuid appears twice.
    """

    claude_dir = tmp_path / "claude"
    project_dir = claude_dir / "projects" / "test-project"

    SHARED_SESSION = "11111111-1111-1111-1111-111111111111"
    OTHER_STEM = "22222222-2222-2222-2222-222222222222"

    # File 1: stem == sessionId. Original session, earlier timestamps.
    _write_jsonl(
        project_dir / f"{SHARED_SESSION}.jsonl",
        [
            _cc_entry(
                uuid="m1",
                session_id=SHARED_SESSION,
                text="original message",
                timestamp="2025-01-01T10:00:00.000Z",
            ),
            _cc_entry(
                uuid="m2",
                session_id=SHARED_SESSION,
                text="original reply",
                timestamp="2025-01-01T10:01:00.000Z",
                role="assistant",
            ),
        ],
    )

    # File 2: stem != sessionId. Resume continuation, LATER timestamps.
    # Replicates CC's `claude --resume` flow exactly.
    _write_jsonl(
        project_dir / f"{OTHER_STEM}.jsonl",
        [
            _cc_entry(
                uuid="m3",
                session_id=SHARED_SESSION,
                text="resumed message",
                timestamp="2025-01-02T10:00:00.000Z",
            ),
            _cc_entry(
                uuid="m4",
                session_id=SHARED_SESSION,
                text="resumed reply",
                timestamp="2025-01-02T10:01:00.000Z",
                role="assistant",
            ),
            _cc_entry(
                uuid="m5",
                session_id=SHARED_SESSION,
                text="more later",
                timestamp="2025-01-03T15:00:00.000Z",
            ),
        ],
    )

    convs = list_claude_code_conversations(claude_dir, full_content=True)

    by_uuid = {c["uuid"]: c for c in convs}
    assert sorted(by_uuid) == sorted([SHARED_SESSION, OTHER_STEM])
    assert len(convs) == 2, "no uuid may appear twice (duplicate React keys)"
    original = {m["uuid"] for m in by_uuid[SHARED_SESSION]["chat_messages"]}
    resumed = {m["uuid"] for m in by_uuid[OTHER_STEM]["chat_messages"]}
    assert original == {"m1", "m2"}
    assert resumed == {"m3", "m4", "m5"}


def test_two_distinct_session_ids_remain_two_convs(tmp_path):
    """Same project dir, DIFFERENT sessionIds in two files. Dedup MUST
    NOT collapse these — keys on uuid only."""
    claude_dir = tmp_path / "claude"
    project_dir = claude_dir / "projects" / "test-project"

    SESSION_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    SESSION_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    _write_jsonl(
        project_dir / f"{SESSION_A}.jsonl",
        [
            _cc_entry(
                uuid="m1",
                session_id=SESSION_A,
                text="A message",
                timestamp="2025-01-01T10:00:00.000Z",
            ),
        ],
    )

    _write_jsonl(
        project_dir / f"{SESSION_B}.jsonl",
        [
            _cc_entry(
                uuid="m2",
                session_id=SESSION_B,
                text="B message",
                timestamp="2025-01-02T10:00:00.000Z",
            ),
        ],
    )

    convs = list_claude_code_conversations(claude_dir, full_content=True)
    uuids = {c.get("uuid") for c in convs}
    assert SESSION_A in uuids, f"Session A missing: {uuids}"
    assert SESSION_B in uuids, f"Session B missing: {uuids}"
    # Bidirectional pin: don't return more rows than distinct sessions.
    assert len(convs) == 2, (
        f"Expected exactly 2 convs for 2 distinct sessions; got {len(convs)} "
        f"uuids={[c.get('uuid') for c in convs]}"
    )
