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
import logging
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

from backend.cc_subagents import is_valid_agent_id
from backend.config import Settings, get_settings, read_env
from backend.export import (
    conversation_to_markdown,
    filter_tool_placeholders,
    media_bytes,
    without_code_runs,
)
from backend.model_names import sender_label
from backend.models import (
    ContentBlock,
    ConversationDetail,
    ConversationSummary,
    Message,
)
from backend.search import search_conversations
from backend.sources.ids import SourceFilter
from backend.sources.registry import (
    get_adapter_or_none,
    get_descriptor,
    get_descriptors,
)
from backend.store import ConversationStore
from backend.tickets import key_system, parse_ticket_key, ticket_key
from backend.write_gate import cloud_sync_block, run_archive_syncs, sync_busy
from mcp_server import __version__

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "Claude Session Explorer",
    instructions=(
        "These tools query saved conversation history (Claude and Codex). "
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

# get_session_outline returns the outline in pages. A Cowork client refused
# the whole outline of a 579-message session (126,470 characters) on
# 2026-11-09. One entry is about 350 characters of JSON, so a page of 100 is
# about 35,000 characters.
OUTLINE_PAGE_DEFAULT = 100
OUTLINE_PAGE_MAX = 500

# The per-session tools name their session argument ``conversation_id``,
# never ``session_id``. The Claude desktop remote-devices bridge removes
# every tool argument named ``session_id`` when it forwards a call from
# a Cowork cloud session, so a tool that needs that name cannot be
# called from Cowork (GitHub issue #33). A test pins this:
# mcp_server/tests/test_conversation_id_param.py.
#
# The middleware below does not accept ``session_id``. It replaces the
# generic pydantic errors with messages that tell the client model what
# to send instead, because old prompts still say ``session_id``.
CONVERSATION_ID_TOOLS = frozenset(
    {"get_session_outline", "get_messages", "export_session"}
)

SESSION_ID_RENAMED = (
    "'session_id' was renamed to 'conversation_id'. Send the session UUID "
    "as 'conversation_id', and do not send 'session_id'."
)
CONVERSATION_ID_MISSING = (
    "Missing required argument 'conversation_id': the session UUID (the "
    "'uuid' field from list_sessions). It was formerly named 'session_id'. "
    "Some MCP bridges remove an argument named 'session_id', so send "
    "'conversation_id'."
)


class _ExplainSessionIdRename(Middleware):
    async def on_call_tool(
        self,
        # The message is an ``mcp.types.CallToolRequestParams``. Importing
        # ``mcp`` directly adds a dependency that the MCPB closure canary
        # refuses (mcp_server/tests/test_mcpb_closure.py).
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        if context.message.name in CONVERSATION_ID_TOOLS:
            arguments = context.message.arguments or {}
            if "session_id" in arguments:
                raise ToolError(SESSION_ID_RENAMED)
            if arguments.get("conversation_id") is None:
                raise ToolError(CONVERSATION_ID_MISSING)
        return await call_next(context)


mcp.add_middleware(_ExplainSessionIdRename())

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


# Plan §4.3, D15: each tool call runs one archive copy inline, with no wait
# for the lock and no thread or timer, at most once per debounce interval.
_last_sync_at: float | None = None
_sync_pending = False
_sync_notices: list[str] = []


def _sync_interval() -> float:
    """The watcher's debounce (``CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC``)."""
    raw = read_env("CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC")
    try:
        return max(0.05, float(raw)) if raw else 2.0
    except ValueError:
        return 2.0


def _sync_archive() -> None:
    """Copy each source into the archive before a tool reads it.

    A busy lock leaves the archive as it is and sets ``sync_pending``; the
    next call tries again at once. A closed cloud-sync gate becomes a notice.
    """
    global _last_sync_at, _sync_pending, _sync_notices
    now = time.monotonic()
    if _last_sync_at is not None and now - _last_sync_at < _sync_interval():
        return
    try:
        reports = run_archive_syncs(_get_store(), "mcp", 0)
    except Exception:
        log.exception("archive copy before an MCP tool call failed")
        return
    _sync_pending = sync_busy(reports)
    if not _sync_pending:
        _last_sync_at = now
    _sync_notices = [
        w for r in reports if r.skipped_reason == "cloud_sync_gate" for w in r.warnings
    ]


def _sync_fields(*, notices: bool = False) -> dict[str, Any]:
    """``sync_pending`` only when true, ``notices`` only when not empty: an
    empty field costs the client model tokens on every call."""
    out: dict[str, Any] = {}
    if notices and _sync_notices:
        out["notices"] = list(_sync_notices)
    if _sync_pending:
        out["sync_pending"] = True
    return out


def _source_filter(source: str | None) -> SourceFilter:
    """The ``source`` argument as a filter. Raises ``ToolError`` for an unknown value.

    A silent "all" for a typo gave the caller every source with no sign of
    the mistake (issue #73). The web API refuses it too (HTTP 422).
    """
    value = (source or "").strip().lower()
    if value in ("", "all"):
        return "all"
    ids = [d.id for d in get_descriptors()]
    for source_id in ids:
        if value == source_id.lower():
            return source_id  # type: ignore[return-value]
    raise ToolError(f'Unknown source "{source}". Use one of: {", ".join(["all", *ids])}.')


def _ticket_filter(ticket: str | None) -> str | None:
    """The ticket key of the ``ticket`` argument (issue #93). Raises ``ToolError`` for an invalid value."""
    if ticket is None or not ticket.strip():
        return None
    parsed = parse_ticket_key(ticket)
    if parsed is None or (parsed[0] and "/" not in parsed[0] and not _known_ticket_prefix(parsed[0])):
        raise ToolError("ticket must be owner/repo#N, #N, a configured KEY-N, or a ticket URL")
    return ticket_key(*parsed)


def _known_ticket_prefix(prefix: str) -> bool:
    """A Linear or Jira prefix in config.json "ticket_keys", or one that a URL in the archive names."""
    if key_system(prefix, get_settings().ticket_keys):
        return True
    from backend.search_index import get_search_index

    idx = get_search_index()
    try:
        return idx is not None and prefix in idx.ticket_prefixes_seen()
    except sqlite3.Error:
        return False


def _source_fields(conv: ConversationSummary) -> dict[str, Any]:
    """The Codex name and home (``~/...``), and the notices when not empty.

    Never the absolute home (D14): the user name must not reach a model.
    """
    out: dict[str, Any] = {}
    if conv.source_home is not None:
        out["source_name"] = conv.source_name
        out["source_home"] = conv.source_home
    if conv.notices:
        out["notices"] = list(conv.notices)
    return out


def _history_links(store: ConversationStore, conv: ConversationSummary) -> dict[str, Any]:
    """The parent and the forks and subagents, for a source that links them (Codex)."""
    adapter = get_adapter_or_none(conv.source, store)
    if not hasattr(adapter, "child_uuids"):
        return {}
    return {"parent_uuid": conv.parent_uuid, "child_uuids": adapter.child_uuids(conv.uuid)}


def _load_conversation(
    store: ConversationStore, conversation_id: str, agent_id: str | None
) -> ConversationDetail:
    """A session, or one of its subagent transcripts (issue #96); else ValueError."""
    if agent_id is not None and not is_valid_agent_id(agent_id):
        raise ValueError(f"Invalid agent_id '{agent_id}'.")
    conversation = (
        store.get_conversation(conversation_id)
        if agent_id is None
        else store.get_agent_conversation(conversation_id, agent_id)
    )
    if conversation is None:
        if agent_id is not None:
            raise ValueError(
                f"Subagent '{agent_id}' of session '{conversation_id}' not found."
            )
        raise ValueError(f"Session '{conversation_id}' not found.")
    return conversation


def reset_archive_sync_for_tests() -> None:
    """Test hook: forget the last copy time and the ``sync_pending`` state."""
    global _last_sync_at, _sync_pending, _sync_notices
    _last_sync_at = None
    _sync_pending = False
    _sync_notices = []


def _db_path() -> Path:
    return _get_settings().data_dir.parent / "cache.db"


def _get_db() -> sqlite3.Connection:
    """Open a new SQLite connection (per-call for thread safety).

    While the cloud-sync gate is closed (plan D13), the outline is built in
    an in-memory database, so ``cache.db`` next to the data folder gets no
    message summaries.
    """
    gate_closed = cloud_sync_block(get_settings()) is not None
    conn = sqlite3.connect(":memory:" if gate_closed else str(_db_path()))
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
    elif (d := get_descriptor(conversation.source)) is not None and d.messages_change_in_place:
        # An old message can have changed; the append path would keep its stale summary.
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

    # Upsert session_files record first (FK parent must exist before children).
    # Not INSERT OR REPLACE: REPLACE deletes the old row, and ON DELETE CASCADE
    # then deletes every summary that the append path keeps.
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO session_files "
        "(session_id, file_path, file_mtime, leaf_message_uuid, "
        "message_count, cached_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET file_path = excluded.file_path, "
        "file_mtime = excluded.file_mtime, leaf_message_uuid = excluded.leaf_message_uuid, "
        "message_count = excluded.message_count, cached_at = excluded.cached_at",
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
                # Issue #91: the other side of a message between sessions.
                if block.peer_ref is not None:
                    entry["peer_ref"] = block.peer_ref.model_dump()
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
        elif block.type == "tool_result" and include_tool_results and block.content:
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
    assistant_label: str = "Claude",
    include_code_mode: bool = False,
) -> dict[str, Any]:
    """Convert a Message to a dict with the requested verbosity."""
    d: dict[str, Any] = {
        "position": position,
        "uuid": msg.uuid,
        "sender": msg.sender,
        # Issue #52: the same sender name as the viewer and the exports.
        "label": sender_label(msg, assistant_label),
        "model": msg.model,
        "timestamp": _format_dt(msg.created_at),
    }
    if msg.command_output:
        d["command_output"] = msg.command_output
    # Issue #91: follow it with get_messages(conversation_uuid, ...).
    if msg.peer_ref is not None:
        d["peer_ref"] = msg.peer_ref.model_dump()

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

    # Issue #90: the runs, in full or as a stub with no script or output text.
    # Media is a stub in both: MCP leaves out image bytes, as for other blocks.
    runs = [b for b in msg.content if b.type == "code_run" and b.code_run is not None]
    if runs:
        d["code_runs"] = [
            {
                "id": b.id, "name": b.name, **b.code_run.model_dump(exclude={"media"}),
                "media": [
                    {"type": m.type, "media_type": (m.source or {}).get("media_type"), "bytes": media_bytes(m)}
                    for m in b.code_run.media
                ],
            } if include_code_mode else {
                "id": b.id, "name": b.name, "status": b.code_run.status,
                "line_count": b.code_run.line_count, "call_count": len(b.code_run.inner_calls),
            }
            for b in runs
        ]
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
    include_tool_calls: bool = False,
    include_code_mode: bool = False,
    ticket: str | None = None,
) -> dict[str, Any]:
    """Search and list saved Claude conversation sessions.
    Only call when the user explicitly asks to search or browse past sessions.
    match_count: the number of matching messages; 0 when only the name matches.

    Args:
        query: Full-text search across session names and message content.
               Omit to list all sessions.
        source: Filter by source: "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK",
                or "CODEX", in any case. "all" or no value means every
                source. Any other value is an error that lists the
                valid values.
        project: Filter by project name (substring match, case-insensitive).
        organization_id: Workspace UUID — restrict to conversations whose
                         organization_id matches exactly. Mirrors the
                         sidebar's Workspace dropdown. Note that Claude
                         Code sessions have no organization_id, so this
                         filter only matches Claude Desktop conversations
                         tagged with that workspace.
        limit: Max results to return (default 20, max 100).
        offset: Skip this many results for pagination.
        include_tool_calls: Also search tool calls and tool output: shell
               commands and their output, file reads and edits, MCP tool
               calls and their results, and web searches. Default false:
               the search matches only session names and the text of user
               and assistant messages. Set it to true when the words may be
               only in a command's output, a file, or a tool result (for
               example an error message, a file path, or a function name),
               or when a search with false finds nothing. Thinking and
               reasoning are never searched. Ignored when query is omitted.
        include_code_mode: Also search Code Mode runs: each script, the tool
               calls it made, and what it returned. Default false.
               Independent of include_tool_calls. Ignored when query is omitted.
        ticket: Only sessions that refer to this ticket: "owner/repo#N",
               "#N" (a number whose repo is not known), a GitHub issue or
               pull URL, a Linear or Jira key such as "ENG-123" (its prefix
               must be configured or named by a URL in the archive), or a
               Linear or Jira URL. Exact: "#84" matches only the
               unknown-repo ticket 84. Works with and without query.

    Note (spec §3, 2026-05-14): there is intentionally NO ``active_filter``
    or ``conversation_uuids`` parameter. The sidebar's active-filter
    graph (atoms/groups under frontend/src/lib/filterEngine.ts) is a
    UI-only convenience over the user's private preferences blob; MCP
    callers don't know the user's filter names. The "one truth, three
    surfaces" invariant is preserved by intersection on the common
    subset (source, project, organization_id) — see
    PLANS/2026.05.14-search-scope-propagation-spec.md.
    """
    src = _source_filter(source)
    ticket_filter = _ticket_filter(ticket)
    _sync_archive()
    store = _get_store()
    limit = min(max(1, limit), 100)
    offset = max(0, offset)

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
            include_tool_calls=include_tool_calls,
            include_code_mode=include_code_mode,
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
                    # Issue #84: the title entry is not a message match.
                    "match_count": sum(
                        1 for m in sr.matching_messages if m.message_uuid != "title"
                    ),
                    # Issue #96: a hit inside a subagent transcript of the session.
                    "agent_id": sr.agent_id,
                    "agent_description": sr.agent_description,
                    **_source_fields(s),
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
                **_source_fields(s),
            })

    if ticket_filter is not None:
        in_ticket = {s.uuid for s in store.list_conversations(source=src, organization_id=organization_id)
                     if ticket_filter in s.tickets}
        sessions = [s for s in sessions if s["uuid"] in in_ticket]

    # Apply project filter
    if project:
        project_lower = project.lower()
        sessions = [
            s for s in sessions
            if s.get("project") and project_lower in s["project"].lower()
        ]

    total = len(sessions)
    sessions = sessions[offset : offset + limit]

    return {"sessions": sessions, "total": total, **_sync_fields(notices=True)}


@mcp.tool()
def list_projects(
    source: str | None = None,
) -> list[dict[str, Any]]:
    """List distinct projects that have saved conversation sessions.
    Only call when the user explicitly asks to list or browse projects.

    Args:
        source: Filter by source: "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK",
                or "CODEX", in any case. "all" or no value means every
                source. Any other value is an error that lists the
                valid values.
    """
    src = _source_filter(source)
    _sync_archive()
    store = _get_store()

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
    conversation_id: str,
    start_position: int = 0,
    limit: int = OUTLINE_PAGE_DEFAULT,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Get lightweight per-message summaries for a session's active branch.
    Only call when the user explicitly asks to examine a specific session.

    Each entry has: position, message_uuid, sender, summary (first 200 chars),
    char_count, tool_count, code_run_count, timestamp. Use positions from the outline to
    fetch full content with get_messages.

    The outline comes in pages. message_count is the size of the whole
    session. If next_position is not null, call again with
    start_position=next_position to get the next page.

    Args:
        conversation_id: The session UUID (the "uuid" field from list_sessions).
                         This argument was formerly named session_id. If a
                         prompt says session_id, send conversation_id.
        start_position: First position of the page (0-indexed, default 0).
        limit: Max entries in the page (default 100, max 500).
        agent_id: Give the outline of this subagent transcript of the session
                  (the "agent_id" of a list_sessions hit) instead.
    """
    _sync_archive()
    store = _get_store()
    conversation = _load_conversation(store, conversation_id, agent_id)

    # An agent transcript is cached under its own key.
    cache_key = f"{conversation_id}::agent::{agent_id}" if agent_id else conversation_id
    conn = _get_db()
    try:
        summaries = _build_outline(conn, cache_key, conversation)
    finally:
        conn.close()

    start = max(0, start_position)
    end = start + min(max(1, limit), OUTLINE_PAGE_MAX)
    next_position = end if end < len(summaries) else None

    return {
        # Same name as the argument, so it passes on to get_messages as-is.
        "conversation_id": conversation.uuid,
        # Kept for existing readers of the output.
        "session_id": conversation.uuid,
        "agent_id": conversation.agent_id,
        "agent_description": conversation.agent_description,
        "name": conversation.name,
        "model": conversation.model,
        "source": conversation.source,
        "project": conversation.project_name,
        "message_count": len(conversation.messages),
        "created_at": _format_dt(conversation.created_at),
        "updated_at": _format_dt(conversation.updated_at),
        "start_position": start,
        "next_position": next_position,
        # Issue #90: not in the outline cache; counted from the messages.
        "messages": [
            {**e, "code_run_count": sum(b.type == "code_run" for b in conversation.messages[e["position"]].content)}
            for e in summaries[start:end]
        ],
        **_source_fields(conversation),
        **_history_links(store, conversation),
        **_sync_fields(),
    }


@mcp.tool()
def get_messages(
    conversation_id: str,
    positions: list[int] | None = None,
    message_uuids: list[str] | None = None,
    include_tool_calls: bool = False,
    include_tool_results: bool = False,
    include_code_mode: bool = False,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Get full message content for specific messages in a session.
    Only call when the user explicitly asks to read specific messages.

    Address messages by position (from get_session_outline) or by UUID.
    If neither is provided, returns all messages (caution: can be very large).

    Args:
        conversation_id: The session UUID (the "uuid" field from list_sessions).
                         This argument was formerly named session_id. If a
                         prompt says session_id, send conversation_id.
        positions: List of 0-indexed positions from the outline.
        message_uuids: List of message UUIDs.
        include_tool_calls: Include tool call names and inputs (default false).
        include_tool_results: Include full tool results (default false, implies tool calls).
        include_code_mode: Give each Code Mode run in full: the script, the
               tool calls it made with their input and output, and what it
               returned (default false: a short stub per run in code_runs).
        agent_id: Read this subagent transcript of the session instead.

    A message from another session, or a message tool call (with
    include_tool_calls), has peer_ref. When its conversation_uuid is set, read
    the other side with get_messages(conversation_id=conversation_uuid,
    message_uuids=[message_uuid]).
    """
    _sync_archive()
    store = _get_store()
    conversation = _load_conversation(store, conversation_id, agent_id)

    # include_tool_results implies include_tool_calls
    if include_tool_results:
        include_tool_calls = True

    messages = conversation.messages
    results: list[dict[str, Any]] = []
    assistant_label = getattr(get_descriptor(conversation.source), "assistant_label", "Claude")

    if positions is not None:
        for pos in positions:
            if 0 <= pos < len(messages):
                results.append(
                    _message_to_dict(
                        messages[pos], pos,
                        include_tool_calls, include_tool_results, assistant_label,
                        include_code_mode,
                    )
                )
    elif message_uuids is not None:
        uuid_set = set(message_uuids)
        for pos, msg in enumerate(messages):
            if msg.uuid in uuid_set:
                results.append(
                    _message_to_dict(
                        msg, pos,
                        include_tool_calls, include_tool_results, assistant_label,
                        include_code_mode,
                    )
                )
    else:
        # Return all messages
        for pos, msg in enumerate(messages):
            results.append(
                _message_to_dict(
                    msg, pos,
                    include_tool_calls, include_tool_results, assistant_label,
                    include_code_mode,
                )
            )

    return results


@mcp.tool()
def export_session(
    conversation_id: str,
    start_position: int | None = None,
    end_position: int | None = None,
    include_tools: bool = True,
    include_code_mode: bool = False,
    agent_id: str | None = None,
) -> str:
    """Export a session (or portion) as Markdown text.
    Only call when the user explicitly asks to export a session.

    Args:
        conversation_id: The session UUID (the "uuid" field from list_sessions).
                         This argument was formerly named session_id. If a
                         prompt says session_id, send conversation_id.
        start_position: Start position (0-indexed, inclusive). Omit for beginning.
        end_position: End position (0-indexed, inclusive). Omit for end.
        include_tools: Include tool calls and results in output (default true).
        include_code_mode: Include Code Mode runs: each script, the tool calls
               it made, and what it returned (default false).
        agent_id: Export this subagent transcript of the session instead.
    """
    _sync_archive()
    store = _get_store()
    conversation = _load_conversation(store, conversation_id, agent_id)

    # Slice messages if positions are specified
    if start_position is not None or end_position is not None:
        msgs = conversation.messages
        start = start_position if start_position is not None else 0
        end = (end_position + 1) if end_position is not None else len(msgs)
        start = max(0, start)
        end = min(len(msgs), end)
        # model_copy keeps every field; a field-by-field rebuild dropped new ones.
        conversation = conversation.model_copy(update={"messages": msgs[start:end]})

    if not include_code_mode:
        conversation = without_code_runs(conversation)
    return conversation_to_markdown(conversation, include_tools=include_tools)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server with stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()
