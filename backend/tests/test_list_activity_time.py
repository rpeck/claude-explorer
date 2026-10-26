"""The list's "last activity" is the last entry's time, not the file's mtime.

Claude Code writes rows without a timestamp (for example ``atis-latch``)
into old session files, which moves the file's mtime to today. The fast
summary readers used that mtime as ``updated_at``, so a session whose last
message was two weeks old sorted first under "Last Activity" and passed
the "Last: 8 hours" filter (user report, 2026-10-26). The detail view
already used the last timestamp, so the list and the detail disagreed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from backend.cc_agent_reader import read_agent_summary, read_agent_summary_fast
from backend.claude_code_reader import read_conversation_summary_fast

FIRST = "2026-01-01T10:00:00.000Z"
LAST = "2026-01-02T03:04:05.000Z"


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    # Claude Code touched the file today, with no new activity.
    now = time.time()
    os.utime(path, (now, now))
    return path


def _session_rows(session_id: str, **extra) -> list[dict]:
    base = {"sessionId": session_id, "cwd": "/w/proj", "gitBranch": "main", **extra}
    return [
        {"type": "user", "timestamp": FIRST, "uuid": "u1",
         "message": {"role": "user", "content": "hello"}, **base},
        {"type": "assistant", "timestamp": "2026-01-01T10:00:05.000Z", "uuid": "a1",
         "message": {"id": "m1", "role": "assistant", "model": "claude", "content": [{"type": "text", "text": "hi"}]}, **base},
        {"type": "system", "subtype": "turn_duration", "timestamp": LAST, "uuid": "s1", **base},
        # A later row with no timestamp, as Claude Code appends to old files.
        {"type": "atis-latch", "sessionId": session_id, "atis": {}},
    ]


def test_session_summary_uses_the_last_timestamp(tmp_path: Path) -> None:
    sid = "11111111-2222-3333-4444-555555555555"
    path = _write(tmp_path / f"{sid}.jsonl", _session_rows(sid))
    summary = read_conversation_summary_fast(path)
    assert summary is not None
    assert summary["created_at"].startswith("2026-01-01T10:00:00")
    assert summary["updated_at"].startswith("2026-01-02T03:04:05")


def test_session_without_timestamps_falls_back_to_created(tmp_path: Path) -> None:
    sid = "11111111-2222-3333-4444-666666666666"
    rows = [{"type": "user", "sessionId": sid, "cwd": "/w", "message": {"role": "user", "content": "x"}}]
    summary = read_conversation_summary_fast(_write(tmp_path / f"{sid}.jsonl", rows))
    assert summary is not None
    assert summary["updated_at"] == summary["created_at"]


def test_agent_summary_fast_matches_the_full_reader(tmp_path: Path) -> None:
    """The fast agent reader reads only the first lines; its updated_at
    must still be the last entry's time, as the full reader gives."""
    sid = "11111111-2222-3333-4444-777777777777"
    rows = _session_rows(sid, agentId="abc123")
    # Pad the middle, so the last timestamp is past the first 20 lines.
    filler = [
        {"type": "assistant", "timestamp": "2026-01-01T12:00:00.000Z", "uuid": f"f{i}", "sessionId": sid,
         "agentId": "abc123", "message": {"id": f"mf{i}", "role": "assistant", "content": []}}
        for i in range(40)
    ]
    path = _write(tmp_path / "agent-abc123.jsonl", rows[:2] + filler + rows[2:])
    _, fast = read_agent_summary_fast(path)
    full = read_agent_summary(path)
    assert fast is not None and full is not None
    fast_updated = fast["updated_at"]
    fast_updated = fast_updated.isoformat() if hasattr(fast_updated, "isoformat") else fast_updated
    assert fast_updated.startswith("2026-01-02T03:04:05")
    full_updated = full["updated_at"]
    full_updated = full_updated.isoformat() if hasattr(full_updated, "isoformat") else full_updated
    assert full_updated.startswith("2026-01-02T03:04:05")
