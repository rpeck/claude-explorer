"""Agent sub-conversation discovery and metadata for Claude Code.

Claude Code stores each invocation of an in-conversation sub-agent
(launched via Task / agent_use) as a separate
``~/.claude/projects/<proj>/agent-<id>.jsonl`` file alongside the main
session JSONL. This module owns:

  * ``read_agent_summary_fast`` — early-exit reader that scans the
    first ~20 lines of an agent JSONL to grab name/model/timestamp
    without paying for a full parse.
  * ``read_agent_summary`` + ``_extract_agent_metadata`` — full-parse
    metadata variant for callers that already have the entry list.
  * ``build_agent_index_with_summaries`` — walks every project dir
    once and returns a ``sessionId -> [agent_summary]`` map, used by
    the listing path so subagents can be hung off their parent
    conversation without a per-session glob.
  * ``discover_agent_files`` — lookup helper for one specific session.

Layering:
  * Imports ``parse_jsonl_file`` from :mod:`backend.cc_jsonl_io` for
    full-parse paths.
  * Imports ``_parse_datetime`` from :mod:`backend.parsing` (re-exported
    via :mod:`backend.cc_jsonl_io` but importing the canonical name
    directly avoids a needless hop).
  * Imported by the facade :mod:`backend.claude_code_reader`.

History (refactor B5, 2026-05-18): extracted from the 1540-line
``backend.claude_code_reader`` monolith. The facade re-exports every
public function from this module so existing imports
(``from backend.claude_code_reader import read_agent_summary_fast``)
continue to resolve.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from .cc_jsonl_io import DEFAULT_CLAUDE_DIR, parse_jsonl_file
from .parsing import _parse_iso_opt
from .parsing import parse_datetime as _parse_datetime

_TAIL_BYTES = 64 * 1024


def _last_timestamp(path: Path) -> str | None:
    """The latest ``timestamp`` in the last 64 KB of a JSONL file, or None."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            tail = f.read()
    except OSError:
        return None
    lines = tail.splitlines()
    if size > _TAIL_BYTES and lines:
        lines = lines[1:]  # the first line can be cut in two
    latest = None
    for line in lines:
        try:
            ts = orjson.loads(line).get("timestamp")
        except (orjson.JSONDecodeError, AttributeError):
            continue
        if isinstance(ts, str) and (latest is None or ts > latest):
            latest = ts
    return latest


def read_agent_summary_fast(agent_path: Path) -> tuple[str | None, dict | None]:
    """Read agent metadata quickly without full parsing.

    Returns (session_id, summary_dict) or (None, None) if invalid.
    Only reads first ~20 lines for speed.
    """
    first_user = None
    first_assistant = None
    first_timestamp = None
    agent_id = None
    lines_read = 0
    max_lines = 20

    try:
        with open(agent_path, "rb") as f:  # Binary mode for orjson
            for line in f:
                lines_read += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = orjson.loads(line)
                    entry_type = entry.get("type")

                    if agent_id is None:
                        agent_id = entry.get("agentId")

                    ts = entry.get("timestamp")
                    if ts and first_timestamp is None:
                        first_timestamp = ts

                    if entry_type == "user" and not first_user:
                        first_user = entry
                    elif entry_type == "assistant" and not first_assistant:
                        first_assistant = entry

                    # Stop once we have what we need
                    if first_user and first_assistant:
                        break

                except orjson.JSONDecodeError:
                    pass

                if lines_read >= max_lines:
                    break
    except OSError:
        return None, None

    if not first_user:
        return None, None

    session_id = first_user.get("sessionId")
    if not session_id:
        return None, None

    if not agent_id:
        agent_id = agent_path.stem.replace("agent-", "")

    # Get name from first user message
    name = f"Agent {agent_id}"
    first_msg = first_user.get("message", {})
    content = first_msg.get("content", "")
    if isinstance(content, str) and content.strip():
        name = content[:80].strip()
    elif isinstance(content, list):
        text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
        text = " ".join(text_parts)[:80].strip()
        if text:
            name = text

    model = ""
    if first_assistant:
        msg = first_assistant.get("message", {})
        model = msg.get("model", "")

    # updated_at is the last row's time, not the file's mtime (see
    # cc_jsonl_io.read_conversation_summary_fast). This reader stops after
    # the first lines, so it reads the end of the file once for it.
    created_at = _parse_datetime(first_timestamp)
    last_timestamp = _last_timestamp(agent_path)
    updated_at = _parse_datetime(last_timestamp) if last_timestamp else created_at

    summary = {
        "uuid": agent_id,
        "agent_id": agent_id,
        "name": name,
        "model": model,
        "created_at": created_at,
        "updated_at": updated_at,
        "message_count": 0,  # Not counted for speed
    }

    return session_id, summary


def build_agent_index_with_summaries(claude_dir: Path = DEFAULT_CLAUDE_DIR) -> dict[str, list[dict]]:
    """Build a mapping of sessionId -> list of agent summaries.

    This scans all agent files once and extracts both sessionId and summary data,
    avoiding repeated file reads when listing conversations.
    """
    index: dict[str, list[dict]] = {}
    projects_dir = claude_dir / "projects"

    if not projects_dir.exists():
        return index

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        # Find all agent files in this project directory
        for agent_file in project_dir.glob("agent-*.jsonl"):
            session_id, summary = read_agent_summary_fast(agent_file)
            if session_id and summary:
                if session_id not in index:
                    index[session_id] = []
                index[session_id].append(summary)

    return index


def discover_agent_files(project_dir: Path, session_id: str) -> list[Path]:
    """Find all agent JSONL files belonging to a session.

    Note: This function reads each agent file to verify ownership.
    For batch operations, use build_agent_index() instead.
    """
    agent_files = []

    # Agent files can be directly in project dir or in session subdirectory
    for pattern in [
        project_dir / "agent-*.jsonl",
        project_dir / session_id / "**" / "agent-*.jsonl",
    ]:
        for agent_file in project_dir.glob(pattern.name if pattern.parent == project_dir else str(pattern.relative_to(project_dir))):
            # Verify this agent belongs to the session by checking sessionId in file
            entries = parse_jsonl_file(agent_file)
            if entries:
                first_user = next((e for e in entries if e.get("type") == "user"), None)
                if first_user and first_user.get("sessionId") == session_id:
                    agent_files.append(agent_file)

    return agent_files


def _extract_agent_metadata(entries: list[dict], agent_path: Path) -> dict:
    """Extract metadata from agent JSONL entries."""
    user_entries = [e for e in entries if e.get("type") == "user"]
    assistant_entries = [e for e in entries if e.get("type") == "assistant"]

    # Get agent ID from first entry
    first_entry = entries[0] if entries else {}
    agent_id = first_entry.get("agentId", agent_path.stem.replace("agent-", ""))

    # Timestamps. See `cc_message_transforms._extract_conversation_metadata`
    # for the Hunt #7 rationale: use the `None`-on-failure primitive
    # `_parse_iso_opt` and filter, rather than `_parse_datetime` whose
    # `now(utc)` fallback would inflate `max()` for corrupt-row sessions.
    all_timestamps = [
        parsed
        for e in entries
        if (parsed := _parse_iso_opt(e.get("timestamp"))) is not None
    ]

    created_at = min(all_timestamps) if all_timestamps else datetime.now(UTC)
    updated_at = max(all_timestamps) if all_timestamps else datetime.now(UTC)

    # Get name from first user message
    name = f"Agent {agent_id}"
    if user_entries:
        first_msg = user_entries[0].get("message", {})
        content = first_msg.get("content", "")
        if isinstance(content, str):
            name = content[:80].strip() or name
        elif isinstance(content, list):
            text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
            name = " ".join(text_parts)[:80].strip() or name

    # Get model
    model = ""
    if assistant_entries:
        msg = assistant_entries[0].get("message", {})
        model = msg.get("model", "")

    # Count messages
    message_count = len(user_entries) + len(assistant_entries)

    return {
        "uuid": agent_id,
        "agent_id": agent_id,
        "name": name,
        "model": model,
        "created_at": created_at,
        "updated_at": updated_at,
        "message_count": message_count,
    }


def read_agent_summary(agent_path: Path) -> dict[str, Any] | None:
    """Read summary metadata for an agent conversation."""
    entries = parse_jsonl_file(agent_path)
    if not entries:
        return None

    return _extract_agent_metadata(entries, agent_path)
