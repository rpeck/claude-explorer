"""MCP server exposing Claude conversation sessions as queryable tools.

Provides 5 tools:
  - list_sessions: Search and list conversation sessions
  - list_projects: List distinct projects with session counts
  - get_session_outline: Lightweight per-message summaries (cached in SQLite)
  - get_messages: Full message content for specific messages
  - export_session: Markdown export of full or partial session

Usage:
  claude-explorer mcp          # via CLI
  python -m mcp_server.server  # direct
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP

from backend.config import Settings
from backend.export import (
    conversation_to_markdown,
    filter_tool_placeholders,
)
from backend.models import ContentBlock, ConversationDetail, Message
from backend.search import search_conversations
from backend.store import ConversationStore
from mcp_server import __version__

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "Claude Session Explorer",
    instructions=(
        "These tools query saved Claude conversation history. "
        "ONLY use them when the user EXPLICITLY asks to search, browse, "
        "analyze, or export past conversation sessions. "
        "Never call these tools proactively or speculatively."
    ),
    # Source-of-truth version from mcp_server/__init__.py. Read this way
    # (not via importlib.metadata) so the MCPB bundle works: the bundle
    # vendors mcp_server/ + backend/ as bare directories, so there is no
    # installed claude-explorer package metadata for UV to resolve at
    # runtime. The __version__ attribute is available in all three
    # contexts: installed wheel, dev-from-source, MCPB bundle.
    version=__version__,
)

# MCP search LIMIT (plan §C). Higher than the HTTP route's 1000 cap
# (backend.routers.search.HTTP_SEARCH_LIMIT) because programmatic /
# LLM consumers can usefully reason about broader result sets than the
# paginated sidebar UI does. The truncation envelope's
# total_messages_matched still reports the true match count regardless
# of how much was actually returned, so the LLM caller can decide
# whether to refine.
MCP_SEARCH_LIMIT = 5000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_settings: Settings | None = None
_store: ConversationStore | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def _get_store() -> ConversationStore:
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store


def _db_path() -> Path:
    return _get_settings().data_dir.parent / "cache.db"


def _get_db() -> sqlite3.Connection:
    """Open a new SQLite connection (per-call for thread safety)."""
    conn = sqlite3.connect(str(_db_path()))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create cache tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS session_files (
            session_id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            file_mtime REAL NOT NULL,
            leaf_message_uuid TEXT NOT NULL DEFAULT '',
            message_count INTEGER NOT NULL,
            cached_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS message_summaries (
            message_uuid TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            sender TEXT NOT NULL,
            summary TEXT,
            char_count INTEGER NOT NULL,
            tool_count INTEGER DEFAULT 0,
            timestamp TEXT,
            FOREIGN KEY (session_id)
                REFERENCES session_files(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_msg_session
            ON message_summaries(session_id, position);
    """)


def _make_summary_text(message: Message) -> str:
    """Generate a summary from the first 200 chars of text content.

    Excludes tool_use and tool_result blocks. Truncates at a word boundary.
    """
    parts: list[str] = []
    if message.content:
        for block in message.content:
            if block.type == "text" and block.text:
                parts.append(block.text)
    elif message.text:
        parts.append(message.text)

    raw = " ".join(parts)
    # Collapse whitespace / newlines into single spaces for a clean summary
    clean = " ".join(raw.split())
    # Filter tool placeholder text
    clean = filter_tool_placeholders(clean).strip()

    if len(clean) <= 200:
        return clean

    truncated = clean[:200]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space] + "..."
    # No space found (e.g. long URL) - hard truncate
    return truncated + "..."


def _count_tools(message: Message) -> int:
    """Count tool_use blocks in a message."""
    count = 0
    for block in message.content:
        if block.type == "tool_use":
            count += 1
    return count


def _message_char_count(message: Message) -> int:
    """Total character count of all text content in a message."""
    total = 0
    if message.content:
        for block in message.content:
            if block.type == "text" and block.text:
                total += len(block.text)
    elif message.text:
        total += len(message.text)
    return total


def _format_dt(dt: datetime) -> str:
    """Format datetime as ISO string."""
    return dt.isoformat()


def _build_outline(
    conn: sqlite3.Connection, session_id: str, conversation: ConversationDetail
) -> list[dict[str, Any]]:
    """Build or update the cached outline for a session.

    Uses append-only optimization: if the file grew (same leaf UUID,
    more messages), only generates summaries for new messages.
    If the leaf UUID changed (branch switch/edit), regenerates fully.
    """
    _ensure_schema(conn)

    file_path = conversation.file_path or ""
    try:
        mtime = Path(file_path).stat().st_mtime if file_path else 0.0
    except OSError:
        mtime = 0.0
    msg_count = len(conversation.messages)
    leaf_uuid = conversation.current_leaf_message_uuid or ""

    # Check cache state
    cached = conn.execute(
        "SELECT file_mtime, message_count, leaf_message_uuid "
        "FROM session_files WHERE session_id = ?",
        (session_id,),
    ).fetchone()

    need_full_regen = False
    append_from = 0

    if cached is None:
        # No cache at all
        need_full_regen = True
    elif (
        cached["file_mtime"] == mtime
        and cached["message_count"] == msg_count
        and cached["leaf_message_uuid"] == leaf_uuid
    ):
        # Cache is fresh - return cached summaries
        rows = conn.execute(
            "SELECT message_uuid, position, sender, summary, "
            "char_count, tool_count, timestamp "
            "FROM message_summaries WHERE session_id = ? ORDER BY position",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    elif cached["leaf_message_uuid"] != leaf_uuid:
        # Branch changed - full regen
        need_full_regen = True
    elif msg_count < cached["message_count"]:
        # Messages decreased - full regen
        need_full_regen = True
    else:
        # Same branch, more messages - append only
        append_from = cached["message_count"]

    if need_full_regen:
        conn.execute(
            "DELETE FROM message_summaries WHERE session_id = ?",
            (session_id,),
        )
        conn.execute(
            "DELETE FROM session_files WHERE session_id = ?",
            (session_id,),
        )
        append_from = 0

    # Upsert session_files record first (FK parent must exist before children)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO session_files "
        "(session_id, file_path, file_mtime, leaf_message_uuid, "
        "message_count, cached_at) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, file_path, mtime, leaf_uuid, msg_count, now),
    )

    # Generate summaries for new messages
    messages = conversation.messages
    for pos in range(append_from, len(messages)):
        msg = messages[pos]
        conn.execute(
            "INSERT OR REPLACE INTO message_summaries "
            "(message_uuid, session_id, position, sender, summary, "
            "char_count, tool_count, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                msg.uuid,
                session_id,
                pos,
                msg.sender,
                _make_summary_text(msg),
                _message_char_count(msg),
                _count_tools(msg),
                _format_dt(msg.created_at),
            ),
        )

    conn.commit()

    # Return all summaries
    rows = conn.execute(
        "SELECT message_uuid, position, sender, summary, "
        "char_count, tool_count, timestamp "
        "FROM message_summaries WHERE session_id = ? ORDER BY position",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _filter_content_blocks(
    blocks: list[ContentBlock],
    include_tool_calls: bool,
    include_tool_results: bool,
) -> list[dict[str, Any]]:
    """Filter content blocks based on verbosity flags."""
    result: list[dict[str, Any]] = []
    for block in blocks:
        if block.type == "text":
            if block.text:
                text = block.text
                if not include_tool_calls:
                    text = filter_tool_placeholders(text)
                if text.strip():
                    result.append({"type": "text", "text": text})
        elif block.type == "tool_use":
            if include_tool_calls:
                entry: dict[str, Any] = {
                    "type": "tool_use",
                    "name": block.name or "",
                }
                # Surface the Anthropic tool_use id so callers can pair
                # parallel calls to their matching tool_result blocks.
                if block.id:
                    entry["id"] = block.id
                if include_tool_results and block.input:
                    entry["input"] = block.input
                else:
                    # Truncated input summary
                    if block.input:
                        input_str = json.dumps(block.input)
                        if len(input_str) > 200:
                            entry["input_preview"] = input_str[:200] + "..."
                        else:
                            entry["input"] = block.input
                result.append(entry)
        elif block.type == "tool_result":
            if include_tool_results and block.content:
                nested = _filter_content_blocks(
                    block.content, include_tool_calls, include_tool_results
                )
                entry = {"type": "tool_result", "content": nested}
                # Back-reference to the tool_use.id this is a result for.
                if block.tool_use_id:
                    entry["tool_use_id"] = block.tool_use_id
                result.append(entry)
    return result


def _message_to_dict(
    msg: Message,
    position: int,
    include_tool_calls: bool = False,
    include_tool_results: bool = False,
) -> dict[str, Any]:
    """Convert a Message to a dict with the requested verbosity."""
    d: dict[str, Any] = {
        "position": position,
        "uuid": msg.uuid,
        "sender": msg.sender,
        "timestamp": _format_dt(msg.created_at),
    }

    if not include_tool_calls and not include_tool_results:
        # Text-only mode
        text = msg.text or ""
        if not text and msg.content:
            parts = []
            for block in msg.content:
                if block.type == "text" and block.text:
                    parts.append(block.text)
            text = "\n".join(parts)
        text = filter_tool_placeholders(text).strip()
        d["text"] = text
    else:
        # Structured content mode
        d["content"] = _filter_content_blocks(
            msg.content, include_tool_calls, include_tool_results
        )

    return d


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_sessions(
    query: str | None = None,
    source: str | None = None,
    project: str | None = None,
    organization_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Search and list saved Claude conversation sessions.
    Only call when the user explicitly asks to search or browse past sessions.

    Args:
        query: Full-text search across session names and message content.
               Omit to list all sessions.
        source: Filter by source: "CLAUDE_AI", "CLAUDE_CODE", or "CLAUDE_COWORK".
        project: Filter by project name (substring match, case-insensitive).
        organization_id: Workspace UUID — restrict to conversations whose
                         organization_id matches exactly. Mirrors the
                         sidebar's Workspace dropdown. Note that Claude
                         Code sessions have no organization_id, so this
                         filter only matches Claude Desktop conversations
                         tagged with that workspace.
        limit: Max results to return (default 20, max 100).
        offset: Skip this many results for pagination.

    Note (spec §3, 2026-05-14): there is intentionally NO ``active_filter``
    or ``conversation_uuids`` parameter. The sidebar's active-filter
    graph (atoms/groups under frontend/src/lib/filterEngine.ts) is a
    UI-only convenience over the user's private preferences blob; MCP
    callers don't know the user's filter names. The "one truth, three
    surfaces" invariant is preserved by intersection on the common
    subset (source, project, organization_id) — see
    PLANS/2026.05.14-search-scope-propagation-spec.md.
    """
    store = _get_store()
    limit = min(max(1, limit), 100)
    offset = max(0, offset)

    src: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all"
    if source in ("CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"):
        src = source  # type: ignore[assignment]

    if query:
        # Deep search across message content. organization_id is pushed
        # into search_conversations so the FTS5 path filters in SQL —
        # mirrors how source already works.
        #
        # limit=5000 (plan §C): MCP consumers (LLM agents, scripts) can
        # usefully reason about broader result sets than the HTTP UI's
        # paginated sidebar, so we pass a higher cap here. The HTTP
        # route uses 1000 via backend.routers.search.HTTP_SEARCH_LIMIT.
        search_response = search_conversations(
            store,
            query,
            source=src,
            organization_id=organization_id,
            limit=MCP_SEARCH_LIMIT,
        )
        # Build a lookup map once (not per result). Pass organization_id
        # so the summary list and the search results agree on which
        # workspace they're scoped to.
        all_summaries = store.list_conversations(
            source=src,
            organization_id=organization_id,
        )
        summary_map = {s.uuid: s for s in all_summaries}
        sessions = []
        for sr in search_response.results:
            s = summary_map.get(sr.conversation_uuid)
            if s:
                entry = {
                    "uuid": s.uuid,
                    "name": s.name,
                    "source": s.source,
                    "project": s.project_name,
                    "message_count": s.message_count,
                    "human_message_count": s.human_message_count,
                    "model": s.model,
                    "created_at": _format_dt(s.created_at),
                    "updated_at": _format_dt(s.updated_at),
                    "match_count": len(sr.matching_messages),
                }
                sessions.append(entry)
    else:
        convs = store.list_conversations(
            source=src,
            organization_id=organization_id,
        )
        sessions = []
        for s in convs:
            sessions.append({
                "uuid": s.uuid,
                "name": s.name,
                "source": s.source,
                "project": s.project_name,
                "message_count": s.message_count,
                "human_message_count": s.human_message_count,
                "model": s.model,
                "created_at": _format_dt(s.created_at),
                "updated_at": _format_dt(s.updated_at),
            })

    # Apply project filter
    if project:
        project_lower = project.lower()
        sessions = [
            s for s in sessions
            if s.get("project") and project_lower in s["project"].lower()
        ]

    total = len(sessions)
    sessions = sessions[offset : offset + limit]

    return {"sessions": sessions, "total": total}


@mcp.tool()
def list_projects(
    source: str | None = None,
) -> list[dict[str, Any]]:
    """List distinct projects that have saved conversation sessions.
    Only call when the user explicitly asks to list or browse projects.

    Args:
        source: Filter by source: "CLAUDE_AI", "CLAUDE_CODE", or "CLAUDE_COWORK".
    """
    store = _get_store()

    src: Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "all"
    if source in ("CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"):
        src = source  # type: ignore[assignment]

    convs = store.list_conversations(source=src)

    project_counts: Counter[str] = Counter()
    for c in convs:
        if c.project_name:
            project_counts[c.project_name] += 1

    return [
        {"project": name, "session_count": count}
        for name, count in project_counts.most_common()
    ]


@mcp.tool()
def get_session_outline(
    session_id: str,
) -> dict[str, Any]:
    """Get lightweight per-message summaries for a session's active branch.
    Only call when the user explicitly asks to examine a specific session.

    Each entry has: position, message_uuid, sender, summary (first 200 chars),
    char_count, tool_count, timestamp. Use positions from the outline to
    fetch full content with get_messages.

    Args:
        session_id: The session UUID.
    """
    store = _get_store()
    conversation = store.get_conversation(session_id)
    if conversation is None:
        raise ValueError(f"Session '{session_id}' not found.")

    conn = _get_db()
    try:
        summaries = _build_outline(conn, session_id, conversation)
    finally:
        conn.close()

    return {
        "session_id": conversation.uuid,
        "name": conversation.name,
        "model": conversation.model,
        "source": conversation.source,
        "project": conversation.project_name,
        "message_count": len(conversation.messages),
        "created_at": _format_dt(conversation.created_at),
        "updated_at": _format_dt(conversation.updated_at),
        "messages": summaries,
    }


@mcp.tool()
def get_messages(
    session_id: str,
    positions: list[int] | None = None,
    message_uuids: list[str] | None = None,
    include_tool_calls: bool = False,
    include_tool_results: bool = False,
) -> list[dict[str, Any]]:
    """Get full message content for specific messages in a session.
    Only call when the user explicitly asks to read specific messages.

    Address messages by position (from get_session_outline) or by UUID.
    If neither is provided, returns all messages (caution: can be very large).

    Args:
        session_id: The session UUID.
        positions: List of 0-indexed positions from the outline.
        message_uuids: List of message UUIDs.
        include_tool_calls: Include tool call names and inputs (default false).
        include_tool_results: Include full tool results (default false, implies tool calls).
    """
    store = _get_store()
    conversation = store.get_conversation(session_id)
    if conversation is None:
        raise ValueError(f"Session '{session_id}' not found.")

    # include_tool_results implies include_tool_calls
    if include_tool_results:
        include_tool_calls = True

    messages = conversation.messages
    results: list[dict[str, Any]] = []

    if positions is not None:
        for pos in positions:
            if 0 <= pos < len(messages):
                results.append(
                    _message_to_dict(
                        messages[pos], pos,
                        include_tool_calls, include_tool_results,
                    )
                )
    elif message_uuids is not None:
        uuid_set = set(message_uuids)
        for pos, msg in enumerate(messages):
            if msg.uuid in uuid_set:
                results.append(
                    _message_to_dict(
                        msg, pos,
                        include_tool_calls, include_tool_results,
                    )
                )
    else:
        # Return all messages
        for pos, msg in enumerate(messages):
            results.append(
                _message_to_dict(
                    msg, pos,
                    include_tool_calls, include_tool_results,
                )
            )

    return results


@mcp.tool()
def export_session(
    session_id: str,
    start_position: int | None = None,
    end_position: int | None = None,
    include_tools: bool = True,
) -> str:
    """Export a session (or portion) as Markdown text.
    Only call when the user explicitly asks to export a session.

    Args:
        session_id: The session UUID.
        start_position: Start position (0-indexed, inclusive). Omit for beginning.
        end_position: End position (0-indexed, inclusive). Omit for end.
        include_tools: Include tool calls and results in output (default true).
    """
    store = _get_store()
    conversation = store.get_conversation(session_id)
    if conversation is None:
        raise ValueError(f"Session '{session_id}' not found.")

    # Slice messages if positions are specified
    if start_position is not None or end_position is not None:
        msgs = conversation.messages
        start = start_position if start_position is not None else 0
        end = (end_position + 1) if end_position is not None else len(msgs)
        start = max(0, start)
        end = min(len(msgs), end)
        # Create a copy with sliced messages
        conversation = ConversationDetail(
            uuid=conversation.uuid,
            name=conversation.name,
            summary=conversation.summary,
            model=conversation.model,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            is_starred=conversation.is_starred,
            message_count=conversation.message_count,
            human_message_count=conversation.human_message_count,
            has_branches=conversation.has_branches,
            source=conversation.source,
            project_path=conversation.project_path,
            messages=msgs[start:end],
            current_leaf_message_uuid=conversation.current_leaf_message_uuid,
            file_path=conversation.file_path,
        )

    return conversation_to_markdown(conversation, include_tools=include_tools)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server with stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()
