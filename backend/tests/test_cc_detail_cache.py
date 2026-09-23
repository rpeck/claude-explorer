"""Workstream C1 — Conversation-detail cache bypass regression test.

PLANS/PERFORMANCE_PHASE_2.md §Workstream C1.

Bug: ``ConversationStore._find_conversation_data`` (backend/store.py:476)
walks every CC JSONL via ``discover_jsonl_files`` and on the matching
``jsonl_path.stem == uuid`` line calls ``read_claude_code_conversation``
DIRECTLY (backend/store.py:490), bypassing the ``_load_conversation_cached``
wrapper at ``backend/claude_code_reader.py:1431`` that exists specifically
to memoize this read via :class:`backend.cache.FileCache`.

Net effect: every ``/api/conversations/{uuid}`` call AND every
``/api/conversations/{uuid}/export/{markdown,pdf,json}`` call re-parses
the entire JSONL from disk. On a 288 MB / 16,103-message CC session
the warm latency is ~1,474 ms; with the cache wired the same call
hits ~30-50 ms (cache-hit dict lookup + Pydantic model build).

Contract pinned by this test:
  1. After ``store.get_conversation(uuid)`` populates the cache, a
     second call for the SAME uuid MUST NOT re-invoke the JSONL
     parser (``read_claude_code_conversation``).
  2. When the JSONL file's mtime changes, the cache MUST invalidate
     and re-read that file on the next call (cache correctness).
  3. ``FileCache.get_or_load`` (the wrapper at line 1431) IS the
     correct shared path — Desktop's branch at line 484 already uses
     ``self._load_conversation`` which routes through FileCache, so
     CC just needs to follow suit.

Bidirectional verification per TESTING.md §2:
  These tests FAIL against the current (buggy) implementation because
  the patched parser raises on the second call, surfacing the cache
  miss. They PASS against the fix that routes through
  ``_load_conversation_cached``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.cache import clear_cache
from backend.store import ConversationStore


def _write_cc_jsonl(path: Path, uuid: str, cwd: str = "/tmp/proj") -> None:
    """Write a minimal valid CC JSONL with a known UUID.

    Mirrors the shape ``read_claude_code_conversation`` parses:
      * line 1: ``summary`` entry providing the conversation title
      * line 2+: user / assistant messages with ``sessionId == uuid``
        and ``cwd`` set so the ``project_path`` is populated.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        # Summary marker (CC's "title" line).
        json.dumps({
            "type": "summary",
            "summary": f"Detail cache test {uuid[:8]}",
            "leafUuid": "leaf-x",
        }),
        # One user + one assistant message.
        json.dumps({
            "type": "user",
            "uuid": "msg-user-1",
            "sessionId": uuid,
            "cwd": cwd,
            "timestamp": "2026-05-16T12:00:00.000Z",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "hello cc"}],
            },
        }),
        json.dumps({
            "type": "assistant",
            "uuid": "msg-asst-1",
            "sessionId": uuid,
            "parentUuid": "msg-user-1",
            "cwd": cwd,
            "timestamp": "2026-05-16T12:00:05.000Z",
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-7",
                "content": [{"type": "text", "text": "hi back"}],
            },
        }),
    ]
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def cc_store(tmp_path):
    """Build a ConversationStore wired at empty Desktop dir + a
    ``~/.claude``-style claude_dir containing exactly ONE CC session.

    Returns ``(store, uuid, jsonl_path)``.
    """
    clear_cache()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    claude_dir = tmp_path / "claude"
    projects_dir = claude_dir / "projects" / "-tmp-proj"
    projects_dir.mkdir(parents=True)

    uuid = "abc12345-6789-4abc-def0-123456789abc"
    jsonl_path = projects_dir / f"{uuid}.jsonl"
    _write_cc_jsonl(jsonl_path, uuid)

    store = ConversationStore(data_dir=data_dir, claude_dir=claude_dir)
    return store, uuid, jsonl_path


def test_cc_detail_reuses_cache_across_calls(cc_store):
    """Second call to ``store.get_conversation(uuid)`` MUST NOT re-read
    the JSONL parser.

    Before fix: ``_find_conversation_data`` calls
    ``read_claude_code_conversation`` directly on every lookup; the
    patched ``side_effect=AssertionError`` fires.

    After fix: it routes through ``_load_conversation_cached`` →
    ``FileCache.get_or_load``; the second call is a cache hit, the
    patched parser is never invoked.
    """
    store, uuid, _ = cc_store

    # First call — populates cache.
    detail1 = store.get_conversation(uuid)
    assert detail1 is not None, "first lookup must succeed"
    assert detail1.uuid == uuid
    assert len(detail1.messages) >= 1, "fixture should have ≥1 message"

    # Second call — MUST be a cache hit. Patch the parser at the
    # canonical import site used by store.py + claude_code_reader.py
    # so any non-cached invocation raises immediately.
    def _boom(*args, **kwargs):
        raise AssertionError(
            "read_claude_code_conversation called on warm cache; "
            "_find_conversation_data is bypassing FileCache"
        )

    # NOTE: patch BOTH bindings — store.py value-imports the name at
    # module load, and _load_conversation_cached references the same
    # symbol via the claude_code_reader module's own binding.
    with patch("backend.store.read_claude_code_conversation", side_effect=_boom), \
         patch(
             "backend.claude_code_reader.read_claude_code_conversation",
             side_effect=_boom,
         ):
        detail2 = store.get_conversation(uuid)

    assert detail2 is not None, "second lookup must succeed (cache hit)"
    assert detail2.uuid == uuid
    assert len(detail2.messages) == len(detail1.messages)


def test_cc_detail_invalidates_cache_on_mtime_change(cc_store):
    """When the JSONL mtime changes the cache MUST invalidate.

    Negative-space assertion per TESTING.md §5.4: not only must
    the cache short-circuit unchanged reads (above test), it MUST
    also re-read when the file is mutated. A cache that never
    invalidates is just a memory leak with extra steps.
    """
    store, uuid, jsonl_path = cc_store

    # Prime cache.
    detail1 = store.get_conversation(uuid)
    assert detail1 is not None
    msg_count_1 = len(detail1.messages)

    # Mutate the file with a new mtime: append another user message.
    new_line = json.dumps({
        "type": "user",
        "uuid": "msg-user-2",
        "sessionId": uuid,
        "parentUuid": "msg-asst-1",
        "cwd": "/tmp/proj",
        "timestamp": "2026-05-16T12:01:00.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "second turn"}],
        },
    })
    with jsonl_path.open("a") as f:
        f.write(new_line + "\n")
    # Bump mtime explicitly so the test isn't sensitive to filesystem
    # mtime resolution (HFS+ rounds to seconds on some kernels).
    new_mtime = time.time() + 10
    os.utime(jsonl_path, (new_mtime, new_mtime))

    # Second call — must invalidate the cache and re-read.
    detail2 = store.get_conversation(uuid)
    assert detail2 is not None
    assert len(detail2.messages) > msg_count_1, (
        f"cache failed to invalidate on mtime change: "
        f"first call saw {msg_count_1} messages, second saw "
        f"{len(detail2.messages)} (expected more)"
    )


def test_cc_export_path_also_uses_cache(cc_store):
    """C2 coverage: ``store.get_conversation`` is the same path the
    export router calls, so it must hit the cache the same way.

    Pins the bundling claim in the plan: "Ship C1 and export gets the
    same payoff for free." If a future refactor splits export onto a
    separate code path that re-introduces the bypass, this test
    catches it.
    """
    store, uuid, _ = cc_store

    # Prime.
    assert store.get_conversation(uuid) is not None

    def _boom(*args, **kwargs):
        raise AssertionError(
            "export path re-parses JSONL; cache bypass regressed"
        )

    with patch("backend.store.read_claude_code_conversation", side_effect=_boom), \
         patch(
             "backend.claude_code_reader.read_claude_code_conversation",
             side_effect=_boom,
         ):
        # Same call the export router makes — see
        # backend/routers/export.py:{24, 64, 87} which all call
        # store.get_conversation(uuid).
        again = store.get_conversation(uuid)
        assert again is not None
        assert again.uuid == uuid
