# `mcp_server/` — MCP Server Specification

> Canonical reference for `claude-explorer`'s built-in Model Context Protocol server. Every claim in this document is grounded in the code at `mcp_server/server.py` (commit current as of 2026-05-13). When the code and this spec disagree, **the code wins** and this document needs a follow-up fix.

## 1. Purpose and design principles

The MCP server exposes a saved Claude conversation archive (Claude Desktop + Claude Code) as five tools an LLM client (Claude Desktop, Claude Code, or any MCP-compatible client) can call. It is a **read-only facade** over the same on-disk store the FastAPI/React app uses; it ships no write paths.

Design rules, in priority order:

1. **Explicit-only.** Every tool description includes an *"only when the user explicitly asks"* clause, and the server-level instructions repeat it. This is durable engineering: an MCP client that fans out to read history on every vague request can burn thousands of tokens before you notice. The wording is the safeguard.
2. **Outline-first, messages-on-demand.** Long sessions (thousands of messages) must not be loaded into a client's context wholesale. `get_session_outline` returns lightweight per-message summaries (cached); `get_messages` pulls full content for specific positions or UUIDs.
3. **Append-only caching.** Session files only grow (or branch); the outline cache keys summaries by `message_uuid` and is incremental — `O(new messages)` work on the common case.
4. **Same substrate as the REST API.** Every tool implementation reuses `backend.store`, `backend.search`, `backend.export`. The MCP server and the web UI agree on what a "session" means, what filters apply, and what an export looks like; there is no separate parallel codebase.
5. **stdio transport, local-only.** No network listener. The client launches the server as a subprocess and talks JSON-RPC over stdin/stdout.

## 2. Transport, identity, and entry points

- **Transport.** stdio (the only MCP transport supported by Claude Desktop and the only one this server registers). See `mcp_server/server.py:626` (`mcp.run()`).
- **Server name.** `Claude Session Explorer` (`mcp_server/server.py:40`).
- **Server-level instructions** (passed to the client at handshake):

  > These tools query saved Claude conversation history. ONLY use them when the user EXPLICITLY asks to search, browse, analyze, or export past conversation sessions. Never call these tools proactively or speculatively.

  Source: `mcp_server/server.py:41-46`.

- **Entry points.**
  - CLI: `claude-explorer mcp` (defined at `fetcher/cli.py:281-292`).
  - Module: `python -m mcp_server.server` (via the `if __name__ == "__main__"` block at line 629).
  - Programmatic: `from mcp_server.server import main as mcp_main; mcp_main()` (line 624).
- **Framework.** Built on `fastmcp>=3.0` (declared in `pyproject.toml`). FastMCP handles JSON-RPC, schema generation from Python type hints, and the tool registry.

## 3. Configuration paths (clients)

The same JSON block works in both Claude Code and Claude Desktop. Use `uv run --directory` (not `uvx`) when running from a local clone, because `mcp_server` is not published as a standalone PyPI package; it ships inside `claude-explorer`. If you've installed `claude-explorer` via `uvx claude-explorer`, the `uvx claude-explorer mcp` form also works.

### Claude Code

- **User scope** (`~/.claude.json`, root-level key `mcpServers`) — available in every project.
- **Project scope** (`.mcp.json` in the repo root) — scoped to the current project.
- **CLI helper:** `claude mcp add claude-sessions -- uv run --directory /absolute/path/to/claude-explorer claude-explorer mcp`.

```json
{
  "mcpServers": {
    "claude-sessions": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/claude-explorer", "claude-explorer", "mcp"]
    }
  }
}
```

### Claude Desktop

Config path varies by OS:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` (typically `C:\Users\<you>\AppData\Roaming\Claude\claude_desktop_config.json`) |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Same JSON block as Claude Code. After editing, fully quit and relaunch Claude Desktop — the config is read once at startup. MCP servers in Claude Code do **not** live in `~/.claude/settings.json`; that's the most common newcomer trap.

## 4. Fixed context cost

Having the server attached injects the five tool definitions (plus the server-level instructions) into the client's context on every conversation, whether or not any tool is called. Measured cost: **~4,681 characters / ~1,200–1,600 tokens** across the 5 tool definitions, per the Phase 20 measurement on the live server. Per-tool breakdown:

| Tool | Definition chars |
|---|---|
| `list_sessions` | 1,054 |
| `list_projects` | 657 |
| `get_session_outline` | 695 |
| `get_messages` | 1,317 |
| `export_session` | 958 |
| **Total** | **4,681** |

This is the price paid even on conversations that never invoke a tool. The *"only when explicitly asked"* hardening keeps it from compounding.

## 5. Storage backing

The server reads from the same locations the FastAPI app reads:

- **Claude Desktop conversations:** JSON files under `~/.claude-explorer/conversations/<uuid>.json` (canonical) or `~/.claude-exporter/conversations/` (legacy; transparently migrated by `backend.config.migrate_legacy_data_dir`). Override via `CLAUDE_EXPLORER_DATA_DIR`.
- **Claude Code sessions:** live-read from `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. Override via `CLAUDE_DIR`.
- **Outline cache:** SQLite at `<data_dir>.parent / "cache.db"` — i.e. `~/.claude-explorer/cache.db` by default. Created on first call to `get_session_outline`.

`ConversationStore` (instantiated as a module singleton at `_get_store()`, line 64-68) wraps both sources. The store is **lazily initialized** on first tool call.

## 6. Tool reference

All tools are registered via the `@mcp.tool()` decorator. Argument types are inferred from Python type hints; FastMCP generates JSON schema and validation from those.

---

### 6.1 `list_sessions`

**Source:** `mcp_server/server.py:358-435`.

**Description (verbatim, from the tool docstring):**

> Search and list saved Claude conversation sessions.
> Only call when the user explicitly asks to search or browse past sessions.

**Arguments:**

| Name | Type | Default | Description |
|---|---|---|---|
| `query` | `str \| None` | `None` | Full-text search across session names and message content. Omit to list all sessions. |
| `source` | `str \| None` | `None` | Filter by source: `"CLAUDE_AI"` or `"CLAUDE_CODE"`. Any other value (or `None`) means "all". |
| `project` | `str \| None` | `None` | Filter by project name (case-insensitive substring match against `ConversationSummary.project_name`). |
| `limit` | `int` | `20` | Max results to return. Clamped to `[1, 100]`. |
| `offset` | `int` | `0` | Skip this many results for pagination. Clamped to `[0, ∞)`. |

**Return shape:**

```json
{
  "sessions": [
    {
      "uuid": "<session-uuid>",
      "name": "<title>",
      "source": "CLAUDE_AI" | "CLAUDE_CODE",
      "project": "<project-name-or-null>",
      "message_count": <int>,
      "human_message_count": <int>,
      "model": "<model-or-empty>",
      "created_at": "<iso8601>",
      "updated_at": "<iso8601>",
      "match_count": <int>   // ONLY present when `query` was provided
    },
    ...
  ],
  "total": <int>   // Total matches BEFORE limit/offset slicing
}
```

**Behavior:**

- With `query`: dispatches to `backend.search.search_conversations(store, query, source=src)`. Each `SearchResult.conversation_uuid` is joined against the full conversation list to produce the row; the `match_count` field is added (length of `matching_messages`). Search dispatches internally to FTS5 (fast path) or linear scan (fallback) — see `backend/search.py:265`.
- Without `query`: lists all conversations from the store via `list_conversations(source=...)`. No `match_count` field is added.
- `project` filter applies *after* the query/list step, in Python, on `entry["project"]`. Case-insensitive substring.
- `total` is the size of the filtered list *before* `[offset : offset + limit]` slicing. Pagination consumers should use `total` to detect end-of-list.
- Invalid `source` values (anything not in `{"CLAUDE_AI", "CLAUDE_CODE"}`) silently fall back to `"all"`.

**Errors:** none raised; bad inputs produce empty results or silently broaden the search.

---

### 6.2 `list_projects`

**Source:** `mcp_server/server.py:438-464`.

**Description (verbatim):**

> List distinct projects that have saved conversation sessions.
> Only call when the user explicitly asks to list or browse projects.

**Arguments:**

| Name | Type | Default | Description |
|---|---|---|---|
| `source` | `str \| None` | `None` | Filter by source: `"CLAUDE_AI"` or `"CLAUDE_CODE"`. |

**Return shape:**

```json
[
  {"project": "<name>", "session_count": <int>},
  ...
]
```

**Behavior:**

- Walks `store.list_conversations(source=src)`.
- Aggregates by `ConversationSummary.project_name`. Conversations with `project_name == None` are excluded from the count entirely.
- Returns entries sorted by `session_count` descending (`Counter.most_common()`).
- Invalid `source` values silently fall back to `"all"`.

**Errors:** none raised.

---

### 6.3 `get_session_outline`

**Source:** `mcp_server/server.py:467-502`.

**Description (verbatim):**

> Get lightweight per-message summaries for a session's active branch.
> Only call when the user explicitly asks to examine a specific session.
>
> Each entry has: position, message_uuid, sender, summary (first 200 chars), char_count, tool_count, timestamp. Use positions from the outline to fetch full content with get_messages.

**Arguments:**

| Name | Type | Default | Description |
|---|---|---|---|
| `conversation_id` | `str` | (required) | Session UUID (matches `ConversationSummary.uuid`). See §6.6 for why it is not `session_id`. |

**Return shape:**

```json
{
  "session_id": "<uuid>",
  "name": "<title>",
  "model": "<model-or-empty>",
  "source": "CLAUDE_AI" | "CLAUDE_CODE",
  "project": "<project-name-or-null>",
  "message_count": <int>,
  "created_at": "<iso8601>",
  "updated_at": "<iso8601>",
  "messages": [
    {
      "message_uuid": "<uuid>",
      "position": <int>,        // 0-indexed, monotonically increasing
      "sender": "human" | "assistant",
      "summary": "<first 200 chars at word boundary, with '...' if truncated>",
      "char_count": <int>,      // Total text-block characters in the message
      "tool_count": <int>,      // Count of tool_use blocks
      "timestamp": "<iso8601>"
    },
    ...
  ]
}
```

**Summary semantics:**

- Text-only: only `text` blocks contribute. `tool_use` and `tool_result` blocks are not summarized.
- Whitespace and newlines collapse to single spaces (`" ".join(raw.split())`).
- `filter_tool_placeholders` strips Claude Code's `"This block is not supported on your current device yet"` strings before truncation.
- Truncates at 200 characters at a word boundary; if no space exists (single long token), hard-truncates and appends `"..."`.

**Caching (`_build_outline`, lines 170-275):**

The SQLite outline cache (see §7) is keyed by `session_id`. Cache states:

1. **No row in `session_files`** → full build (all messages summarized, both tables populated).
2. **Cache row exists and `(file_mtime, message_count, leaf_message_uuid)` match the live conversation** → return cached rows unchanged (no work done).
3. **Cache row exists but `leaf_message_uuid` differs** → full regen (DELETE both tables, rebuild all).
4. **Cache row exists, leaf matches, but live `message_count` < cached** → full regen (deleted messages or branch shrinkage).
5. **Cache row exists, leaf matches, live `message_count` >= cached** → **append-only**: regenerate summaries only for messages at positions `[cached.message_count, len(live.messages))`. `INSERT OR REPLACE` is used so a partial overlap is safe.

The `session_files` parent row is upserted (`INSERT OR REPLACE`) on every non-trivially-fresh path to keep `(file_mtime, message_count, leaf_message_uuid)` accurate. `conn.commit()` runs once per call.

**Errors:**

- `ValueError("Session '<id>' not found.")` if `store.get_conversation(conversation_id)` returns `None`.

---

### 6.4 `get_messages`

**Source:** `mcp_server/server.py:505-567`.

**Description (verbatim):**

> Get full message content for specific messages in a session.
> Only call when the user explicitly asks to read specific messages.
>
> Address messages by position (from get_session_outline) or by UUID. If neither is provided, returns all messages (caution: can be very large).

**Arguments:**

| Name | Type | Default | Description |
|---|---|---|---|
| `conversation_id` | `str` | (required) | Session UUID. See §6.6. |
| `positions` | `list[int] \| None` | `None` | 0-indexed positions from the outline. |
| `message_uuids` | `list[str] \| None` | `None` | Message UUIDs to fetch. |
| `include_tool_calls` | `bool` | `False` | Include tool call names and (truncated) inputs. |
| `include_tool_results` | `bool` | `False` | Include full tool results. Implies `include_tool_calls=True`. |

**Selector resolution:**

1. If `positions` is not `None` → iterate `positions`, append message if `0 <= pos < len(messages)`. **Out-of-range positions are silently dropped** (no error, no placeholder).
2. Else if `message_uuids` is not `None` → filter the messages list by UUID set (order: as found in the messages list, not in the input list).
3. Else → return **all messages** (the "caution: can be very large" path).

Note that `positions` takes precedence; `message_uuids` is ignored when `positions` is also provided.

**Return shape:**

The function returns `list[dict]`. Each entry's shape depends on the verbosity flags:

**Text-only mode** (`include_tool_calls=False` and `include_tool_results=False`):

```json
{
  "position": <int>,
  "uuid": "<message-uuid>",
  "sender": "human" | "assistant",
  "timestamp": "<iso8601>",
  "text": "<text content with tool-placeholder strings filtered out>"
}
```

**Structured-content mode** (either flag true):

```json
{
  "position": <int>,
  "uuid": "<message-uuid>",
  "sender": "human" | "assistant",
  "timestamp": "<iso8601>",
  "content": [
    {"type": "text", "text": "..."},
    {"type": "tool_use", "id": "toolu_01ABC...", "name": "...", "input": {...}},
    {"type": "tool_use", "id": "toolu_01DEF...", "name": "...", "input_preview": "<json>..."},  // if input JSON > 200 chars and include_tool_results=False
    {"type": "tool_result", "tool_use_id": "toolu_01ABC...", "content": [...]}  // only when include_tool_results=True
  ]
}
```

Content-block filtering (`_filter_content_blocks`, lines 278-316):

- `text` blocks: included if `text.strip()` non-empty. Tool-placeholder strings stripped when `include_tool_calls=False`.
- `tool_use` blocks: only included if `include_tool_calls=True`. The full `input` dict is included if `include_tool_results=True` *or* the JSON-serialized input is ≤200 chars; otherwise, `input_preview` carries the first 200 chars + `"..."`. The Anthropic `id` (e.g. `"toolu_01ABC..."`) is emitted when present so callers can pair parallel calls to their results.
- `tool_result` blocks: only included if `include_tool_results=True`. Their nested `content` is recursively filtered with the same flags. The `tool_use_id` back-reference is emitted when present — match it to a `tool_use.id` from the previous (or any nearby) message to reconstruct the call/result pair. Positional adjacency is NOT a reliable pairing signal: parallel calls may return out of order, and some results may be missing.
- `image` blocks: **currently not emitted** by `_filter_content_blocks` (the function has no `image` branch). This is a known gap.

**Errors:**

- `ValueError("Session '<id>' not found.")` if `store.get_conversation` returns `None`.

---

### 6.5 `export_session`

**Source:** `mcp_server/server.py:570-617`.

**Description (verbatim):**

> Export a session (or portion) as Markdown text.
> Only call when the user explicitly asks to export a session.

**Arguments:**

| Name | Type | Default | Description |
|---|---|---|---|
| `conversation_id` | `str` | (required) | Session UUID. See §6.6. |
| `start_position` | `int \| None` | `None` | 0-indexed start position (inclusive). Omit for beginning. |
| `end_position` | `int \| None` | `None` | 0-indexed end position (inclusive). Omit for end. |
| `include_tools` | `bool` | `True` | Include tool calls and results in output (note: opposite default from `get_messages`). |

**Return shape:**

`str` — Markdown text produced by `backend.export.conversation_to_markdown`. The output is the same format the web UI's *Markdown export* button produces (Inline variant — no zip bundle).

**Slicing semantics:**

- If both `start_position` and `end_position` are `None`: full conversation.
- Otherwise: slice `messages[start : end+1]` (so `end_position` is **inclusive**).
- `start` defaults to `0`, `end` defaults to `len(messages)` (treated as exclusive index after `+1` and clamping).
- `start = max(0, start)`, `end = min(len(messages), end)` — out-of-range positions clamp rather than error.
- A new `ConversationDetail` is constructed with the sliced messages; all other fields are copied through.

**Errors:**

- `ValueError("Session '<id>' not found.")` if `store.get_conversation` returns `None`.

---

### 6.6 Why the argument is `conversation_id`

**The bridge removes `session_id`.** The Claude desktop remote-devices bridge removes every tool argument whose key is `session_id`. The bridge carries calls from a Cowork cloud session to a local MCP server (tool names `mcp__remote-devices__claude-sessions__*`). Before the rename, `get_session_outline`, `get_messages`, and `export_session` always failed on that path with `session_id Missing required argument` (GitHub issue #33).

**No alias.** The tools do not accept `session_id`. A stdio client starts the server and then reads the tool list, so after an upgrade it sees only `conversation_id`. The description of `conversation_id` says that it was formerly named `session_id`, so a model that reads `session_id` in an old prompt sends `conversation_id`. A script that still sends `session_id` gets a pydantic error that names both keys: `conversation_id` is missing, and `session_id` is an unexpected argument.

**The output key does not change.** `get_session_outline` still returns the session UUID as `"session_id"`. The bridge changes only the arguments of a call, and existing readers of the output use that key.

**Invariant:** no registered tool may have an input property named `session_id`. `mcp_server/tests/test_conversation_id_param.py` reads the schemas from the FastMCP registry and fails if one does.

---

## 7. Outline cache schema

The SQLite file at `<data_dir>.parent / "cache.db"` carries two tables and one index. The schema is recreated idempotently on every `get_session_outline` call via `_ensure_schema` (line 84).

```sql
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
```

- WAL mode (`PRAGMA journal_mode=WAL`) enables concurrent reads.
- Foreign keys ON (`PRAGMA foreign_keys=ON`) plus `ON DELETE CASCADE` keep the two tables consistent: a `session_files` deletion drops all child `message_summaries`. The full-regen path deletes the child rows first, then the parent row, then re-inserts both — `INSERT OR REPLACE` is used for both so an idempotent re-call is safe.
- The `session_id` column is text (Pydantic-wrapped UUID strings, not SQLite UUIDs).
- Connection is opened **per-call** (`_get_db()` returns a new connection each time); the singleton is the store, not the DB connection. This avoids thread-safety issues with `sqlite3.Connection`.

## 8. Security boundaries

- **Read-only.** No tool mutates the conversation store, the SQLite cache (other than its own outline rows), or anything else outside the cache table writes.
- **No network egress.** The server speaks JSON-RPC over stdin/stdout. There is no HTTP listener, no outbound socket, no auth header construction. The substrate (`backend.store`) reads local files; the export path renders Markdown without fetching anything remote.
- **stdio-only transport.** A client must spawn the server as a subprocess to talk to it. There is no `--port` flag and no `--host` flag.
- **No credentials handled.** Credentials live in `~/.claude-explorer/credentials.json` for fetcher use only; the MCP server does not read them.
- **Path traversal:** all file lookups go through `backend.store`, which resolves session UUIDs against the configured data directory; UUIDs supplied to `get_session_outline`, `get_messages`, and `export_session` cannot escape the data root via `..` or symlinks because the store enumerates files first and matches by UUID, not by user-supplied path.

## 9. Performance notes

- **`list_sessions`:** dominated by `store.list_conversations` (orjson + `FileCache` + `ThreadPoolExecutor`; ~0.07 s warm for ~800 conversations on the dev machine). With `query`, dispatches to FTS5 (~50 ms warm typical).
- **`list_projects`:** same listing cost; aggregation is `O(N)` Python.
- **`get_session_outline`:** first call on a fresh cache regenerates all summaries (linear in `len(messages)`). Subsequent calls with no file change are a single SQLite SELECT. Append-only path is `O(new messages)`.
- **`get_messages`:** dominated by `store.get_conversation` (one file load); content filtering is `O(blocks)`.
- **`export_session`:** dominated by `conversation_to_markdown`; linear in conversation size.

## 10. Known gaps and follow-ups

These deliberately ship as-is in V1; flagged for future work, not blockers for the MCP article:

- **`image` content blocks in `get_messages`:** `_filter_content_blocks` has no `image` branch. Image blocks are silently dropped from the structured-content mode output. Text-mode unchanged. Filed because the bundle Markdown export does include images; consumers of the MCP path get a less faithful rendering.
- **`positions` precedence over `message_uuids`:** when both are supplied, `message_uuids` is ignored without warning. Could be tightened to raise `ValueError`.
- **Order of `message_uuids` results:** results return in the order they appear in the conversation, not in the order supplied. Document or fix to match input order.
- **`source` validation:** invalid values silently fall back to `"all"`. A loud failure (or strict typing via FastMCP enum) would be cleaner.
- **No pagination on `get_session_outline`:** sessions with tens of thousands of messages return the full outline in one payload. The summaries are small (200 chars × `N`), so the practical ceiling is high, but a `limit`/`offset` pair would let very long sessions stream.

## 11. Testing

Tests live under `mcp_server/tests/`. They use the `isolated_data_dir` fixture from `backend/tests/conftest.py` to plant minimal fixture conversations, then call the tool functions directly (FastMCP's `@mcp.tool()` decorator leaves the underlying function callable in-process).

Run with:

```bash
uv run pytest mcp_server/tests/ -q
```

The test suite is the executable form of this spec; whenever this document is updated, the tests should be updated in lockstep, and vice versa.
