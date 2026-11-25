# `mcp_server/` — MCP Server Specification

> Canonical reference for `claude-explorer`'s built-in Model Context Protocol server. Every claim in this document is grounded in the code at `mcp_server/server.py` (checked 2026-11-09; references name functions, not line numbers, so that they stay valid when code moves). When the code and this spec disagree, **the code wins** and this document needs a follow-up fix.

## 1. Purpose and design principles

The MCP server exposes a saved conversation archive (Claude Desktop, Claude Code, Cowork, and OpenAI Codex) as five tools an LLM client (Claude Desktop, Claude Code, or any MCP-compatible client) can call. It is a **read-only facade** over the same on-disk store the FastAPI/React app uses. Its one write path is the archive copy (§8): before each tool call it copies new source files into the Explorer archive, so an MCPB-only user without `serve` or the watcher also keeps their history.

Design rules, in priority order:

1. **Explicit-only.** Every tool description includes an *"only when the user explicitly asks"* clause, and the server-level instructions repeat it. This is durable engineering: an MCP client that fans out to read history on every vague request can burn thousands of tokens before you notice. The wording is the safeguard.
2. **Outline-first, messages-on-demand.** Long sessions (thousands of messages) must not be loaded into a client's context wholesale. `get_session_outline` returns lightweight per-message summaries (cached); `get_messages` pulls full content for specific positions or UUIDs.
3. **Append-only caching.** Session files only grow (or branch); the outline cache keys summaries by `message_uuid` and is incremental — `O(new messages)` work on the common case.
4. **Same substrate as the REST API.** Every tool implementation reuses `backend.store`, `backend.search`, `backend.export`. The MCP server and the web UI agree on what a "session" means, what filters apply, and what an export looks like; there is no separate parallel codebase.
5. **stdio transport, local-only.** No network listener. The client launches the server as a subprocess and talks JSON-RPC over stdin/stdout.

## 2. Transport, identity, and entry points

- **Transport.** stdio (the only MCP transport supported by Claude Desktop and the only one this server registers). See `main()` in `mcp_server/server.py` (`mcp.run()`).
- **Server name.** `Claude Session Explorer` (the `FastMCP(...)` call in `mcp_server/server.py`).
- **Server-level instructions** (passed to the client at handshake):

  > These tools query saved conversation history (Claude and Codex). ONLY use them when the user EXPLICITLY asks to search, browse, analyze, or export past conversation sessions. Never call these tools proactively or speculatively.

  Source: the `instructions=` argument of `FastMCP(...)` in `mcp_server/server.py`.

- **Entry points.**
  - CLI: `claude-explorer mcp` (the `mcp()` command in `cli/main.py`).
  - Module: `python -m mcp_server.server` (via the `if __name__ == "__main__"` block).
  - Programmatic: `from mcp_server.server import main as mcp_main; mcp_main()`.
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

Having the server attached injects the five tool definitions (plus the server-level instructions) into the client's context on every conversation, whether or not any tool is called. Measured on 2026-11-09: **5,216 characters, about 1,300 to 1,700 tokens**, for the 5 tool definitions.

- **Method:** `len(json.dumps({"name", "description", "inputSchema"}))` for each tool, as a FastMCP `Client` receives it from `list_tools()`.
- **The earlier table** (4,681 characters, Phase 20) came from older code and an unrecorded method, so its numbers do not compare with these.

| Tool | Definition chars |
|---|---|
| `list_sessions` | 1,418 |
| `list_projects` | 424 |
| `get_session_outline` | 1,143 |
| `get_messages` | 1,275 |
| `export_session` | 956 |
| **Total** | **5,216** |

The 3 per-session tools each carry 174 characters for the `conversation_id` description, which names the former `session_id` (§6.6). The outline's page arguments and their description add 412 characters.

This is the price paid even on conversations that never invoke a tool. The *"only when explicitly asked"* hardening keeps it from compounding.

## 5. Storage backing

The server reads from the same locations the FastAPI app reads:

- **Claude Desktop conversations:** JSON files under `~/.claude-explorer/conversations/<uuid>.json` (canonical) or `~/.claude-exporter/conversations/` (legacy; transparently migrated by `backend.config.migrate_legacy_data_dir`). Override via `CLAUDE_EXPLORER_DATA_DIR`.
- **Claude Code sessions:** live-read from `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. Override via `CLAUDE_DIR`.
- **Cowork sessions:** local sessions live-read from `<Claude Desktop app dir>/local-agent-mode-sessions/<deployment>/<org>/local_<uuid>/audit.jsonl` (override via `CLAUDE_DESKTOP_APP_DIR`), and remote tasks from `<data_dir>/cowork-remote/<org>/remote_<id>/audit.jsonl`, which the fetch saves.
- **Codex sessions:** read from the archive copy at `<data_dir>/codex/<home_key>/threads/<thread_id>.json` and `rollouts/<stem>.jsonl`. The archive copy (§8) copies them from the Codex home (`CODEX_HOME`, then `codex_home` in `config.json`, then `~/.codex`) and never writes there. The conversation `uuid` is the Codex thread id, so `codex resume <uuid>` works.
- **Outline cache:** SQLite at `<data_dir>.parent / "cache.db"` — i.e. `~/.claude-explorer/cache.db` by default. Created on first call to `get_session_outline`.

`ConversationStore` (instantiated as a module singleton by `_get_store()`) wraps every source. Each source has one adapter in `backend/sources/`; `backend.sources.registry` lists them, and `backend.sources.ids` defines the `SourceId` and `SourceFilter` values that the `source` arguments accept. The outline cache rebuilds a session in full when its source descriptor sets `messages_change_in_place` (Codex does: a later snapshot of an item replaces the earlier one). The store is **lazily initialized** on first tool call.

## 6. Tool reference

All tools are registered via the `@mcp.tool()` decorator. Argument types are inferred from Python type hints; FastMCP generates JSON schema and validation from those.

---

### 6.1 `list_sessions`

**Source:** `list_sessions()` in `mcp_server/server.py`.

**Description (verbatim, from the tool docstring):**

> Search and list saved Claude conversation sessions.
> Only call when the user explicitly asks to search or browse past sessions.

**Arguments:**

| Name | Type | Default | Description |
|---|---|---|---|
| `query` | `str \| None` | `None` | Full-text search across session names and message content. Omit to list all sessions. |
| `source` | `str \| None` | `None` | Filter by source: `"CLAUDE_AI"`, `"CLAUDE_CODE"`, `"CLAUDE_COWORK"`, or `"CODEX"`, in any case. `"all"` (any case) or `None` means every source. Any other value is a tool error. |
| `project` | `str \| None` | `None` | Filter by project name (case-insensitive substring match against `ConversationSummary.project_name`). |
| `organization_id` | `str \| None` | `None` | Workspace UUID. Only conversations whose `organization_id` matches exactly. Claude Code sessions have none, so they never match. |
| `limit` | `int` | `20` | Max results to return. Clamped to `[1, 100]`. |
| `offset` | `int` | `0` | Skip this many results for pagination. Clamped to `[0, ∞)`. |
| `include_code_mode` | `bool` | `False` | Also search Code Mode runs: each script, the tool calls it made, and what it returned (issue #90). Independent of `include_tool_calls`. Ignored when `query` is omitted. |

**Return shape:**

```json
{
  "sessions": [
    {
      "uuid": "<session-uuid>",
      "name": "<title>",
      "source": "CLAUDE_AI" | "CLAUDE_CODE" | "CLAUDE_COWORK" | "CODEX",
      "project": "<project-name-or-null>",
      "message_count": <int>,
      "human_message_count": <int>,
      "model": "<model-or-empty>",
      "created_at": "<iso8601>",
      "updated_at": "<iso8601>",
      "match_count": <int>,  // ONLY present when `query` was provided
      "agent_id": "<id>" | null,            // ONLY with `query`: set on a hit inside a subagent transcript
      "agent_description": "<text>" | null, // ONLY with `query`: that agent's description, if any
      "source_name": "<Codex session name>" | null,  // Codex only
      "source_home": "~/<Codex home>",               // Codex only; never the absolute path
      "notices": ["<text>"]  // ONLY present when not empty, e.g. "incomplete: compacted"
    },
    ...
  ],
  "total": <int>,   // Total matches BEFORE limit/offset slicing
  "notices": ["<text>"],   // ONLY present when not empty: e.g. the cloud-sync gate is closed (names the provider)
  "sync_pending": true     // ONLY present when true: another writer held the archive lock (§8)
}
```

**Behavior:**

- With `query`: dispatches to `backend.search.search_conversations(store, query, source=src)`. Each `SearchResult.conversation_uuid` is joined against the full conversation list to produce the row; the `match_count` field is added (length of `matching_messages`). Search dispatches internally to FTS5 (fast path) or linear scan (fallback) — see `backend/search.py:265`.
- Subagent transcripts (issue #96): a hit inside an agent transcript of a Claude Code session is its own entry. `uuid` is the session uuid, `agent_id` and `agent_description` name the agent, `match_count` counts the agent's matching messages, and the other fields are the session's. A session's own entry has `agent_id: null`. Pass the `agent_id` on to `get_session_outline`, `get_messages`, or `export_session`.
- Without `query`: lists all conversations from the store via `list_conversations(source=...)`. No `match_count` field is added.
- `project` filter applies *after* the query/list step, in Python, on `entry["project"]`. Case-insensitive substring.
- `total` is the size of the filtered list *before* `[offset : offset + limit]` slicing. Pagination consumers should use `total` to detect end-of-list.
- Source matching: the value loses its outer spaces, and a known source id then matches in any case (`" Codex "` means `"CODEX"`). An empty value means `"all"`. An unknown value raises a `ToolError` that names the value and lists every valid value from `backend.sources.registry`, for example `Unknown source "codx". Use one of: all, CLAUDE_AI, CLAUDE_CODE, CLAUDE_COWORK, CODEX.` Both tools use `_source_filter`, and it runs before the archive copy (issue #73).

**Errors:** an unknown `source` (above). Other bad inputs produce empty results or are clamped.

---

### 6.2 `list_projects`

**Source:** `list_projects()` in `mcp_server/server.py`.

**Description (verbatim):**

> List distinct projects that have saved conversation sessions.
> Only call when the user explicitly asks to list or browse projects.

**Arguments:**

| Name | Type | Default | Description |
|---|---|---|---|
| `source` | `str \| None` | `None` | Filter by source: `"CLAUDE_AI"`, `"CLAUDE_CODE"`, `"CLAUDE_COWORK"`, or `"CODEX"`, in any case. `"all"` (any case) or `None` means every source. Any other value is a tool error. |

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
- `source` matching is the same as in `list_sessions` (same helper).

**Errors:** an unknown `source`, as in `list_sessions`.

---

### 6.3 `get_session_outline`

**Source:** `get_session_outline()` in `mcp_server/server.py`.

**Description (verbatim):**

> Get lightweight per-message summaries for a session's active branch.
> Only call when the user explicitly asks to examine a specific session.
>
> Each entry has: position, message_uuid, sender, summary (first 200 chars), char_count, tool_count, code_run_count, timestamp. Use positions from the outline to fetch full content with get_messages.
>
> The outline comes in pages. message_count is the size of the whole session. If next_position is not null, call again with start_position=next_position to get the next page.

**Arguments:**

| Name | Type | Default | Description |
|---|---|---|---|
| `conversation_id` | `str` | (required) | Session UUID (matches `ConversationSummary.uuid`). See §6.6 for why it is not `session_id`. |
| `start_position` | `int` | `0` | First position of the page. A negative value counts as `0`. |
| `limit` | `int` | `100` (`OUTLINE_PAGE_DEFAULT`) | Max entries in the page. Clamped to `[1, 500]` (`OUTLINE_PAGE_MAX`). |
| `agent_id` | `str \| None` | `None` | Give the outline of this subagent transcript of the session (issue #96). The outline cache keys it as `<conversation_id>::agent::<agent_id>`. |

**Why pages:** on 2026-11-09 a Cowork client refused the whole outline of a 579-message session: 126,470 characters, over its size limit for one tool result. One entry is about 350 characters of JSON, so the default page of 100 is about 35,000 characters. `mcp_server/tests/test_outline_pages.py` holds a page of the default size to 40,000 characters.

**Return shape:**

```json
{
  "conversation_id": "<uuid>",
  "session_id": "<uuid>",     // same value; kept for existing readers
  "agent_id": "<id>" | null,  // the subagent transcript, when `agent_id` was given
  "agent_description": "<text>" | null,
  "name": "<title>",
  "model": "<model-or-empty>",
  "source": "CLAUDE_AI" | "CLAUDE_CODE" | "CLAUDE_COWORK" | "CODEX",
  "project": "<project-name-or-null>",
  "message_count": <int>,
  "created_at": "<iso8601>",
  "updated_at": "<iso8601>",
  "start_position": <int>,    // first position of this page, after clamping
  "next_position": <int> | null,  // start of the next page; null on the last page
  "messages": [               // this page only: positions start_position .. start_position + limit - 1
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
  ],
  "source_name": "<Codex session name>" | null,  // Codex only
  "source_home": "~/<Codex home>",               // Codex only; never the absolute path
  "notices": ["<text>"],      // ONLY present when not empty: the session's notices
  "sync_pending": true        // ONLY present when true: another writer held the archive lock (§8)
}
```

**Summary semantics:**

- Text-only: only `text` blocks contribute. `tool_use` and `tool_result` blocks are not summarized.
- Whitespace and newlines collapse to single spaces (`" ".join(raw.split())`).
- `filter_tool_placeholders` strips Claude Code's `"This block is not supported on your current device yet"` strings before truncation.
- Truncates at 200 characters at a word boundary; if no space exists (single long token), hard-truncates and appends `"..."`.

**Caching (`_build_outline`):**

The SQLite outline cache (see §7) is keyed by `session_id`. Cache states:

1. **No row in `session_files`** → full build (all messages summarized, both tables populated).
2. **Cache row exists and `(file_mtime, message_count, leaf_message_uuid)` match the live conversation** → return cached rows unchanged (no work done).
3. **Cache row exists but `leaf_message_uuid` differs** → full regen (DELETE both tables, rebuild all).
4. **Cache row exists, leaf matches, but live `message_count` < cached** → full regen (deleted messages or branch shrinkage).
5. **Cache row exists, leaf matches, live `message_count` >= cached** → **append-only**: regenerate summaries only for messages at positions `[cached.message_count, len(live.messages))`. `INSERT OR REPLACE` is used so a partial overlap is safe.

The `session_files` parent row is upserted (`INSERT OR REPLACE`) on every non-trivially-fresh path to keep `(file_mtime, message_count, leaf_message_uuid)` accurate. `conn.commit()` runs once per call.

**Errors:**

- `ValueError("Session '<id>' not found.")` if `store.get_conversation(conversation_id)` returns `None`.
- With `agent_id`: `ValueError("Invalid agent_id '<id>'.")` when it does not match `^[A-Za-z0-9_-]{1,64}$`, and `ValueError("Subagent '<id>' of session '<uuid>' not found.")` when the session's discovery does not list it. `get_messages` and `export_session` raise the same errors.

---

### 6.4 `get_messages`

**Source:** `get_messages()` in `mcp_server/server.py`.

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
| `include_code_mode` | `bool` | `False` | Give each Code Mode run in full in `code_runs`. Default: a stub per run (`id`, `name`, `status`, `line_count`, `call_count`), with no script, call, or output text. |
| `agent_id` | `str \| None` | `None` | Read this subagent transcript of the session instead (issue #96). |

A message that holds a Code Mode run (issue #90) has a `code_runs` list in both modes. With `include_code_mode=True` each entry is the whole `CodeRun`: `code`, `status`, `result`, `stdout`, `stderr`, `error`, `refused`, `returned_from` (the calls whose output went back to the model), and `inner_calls` (each with `name`, `input`, `result`, `is_error`, `refused`, `code_run_parent`, `link_source`, and `inputs_from`, the earlier calls whose output is in its input). `get_session_outline` gives `code_run_count` per entry; it is counted from the messages, not kept in the outline cache.

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
  "label": "<sender name>",
  "model": "<model id>" | null,
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
  "label": "<sender name>",
  "model": "<model id>" | null,
  "timestamp": "<iso8601>",
  "content": [
    {"type": "text", "text": "..."},
    {"type": "tool_use", "id": "toolu_01ABC...", "name": "...", "input": {...}},
    {"type": "tool_use", "id": "toolu_01DEF...", "name": "...", "input_preview": "<json>..."},  // if input JSON > 200 chars and include_tool_results=False
    {"type": "tool_result", "tool_use_id": "toolu_01ABC...", "content": [...]}  // only when include_tool_results=True
  ]
}
```

Fields of both modes (issue #52):

- `label`: the sender name that the viewer and the exports show.
  - A reply: the source's assistant label plus the model name, for example `"Claude Opus 5.5"` or `"Codex gpt-5.6-codex"`.
  - A row that Claude Code made itself (model `<synthetic>`): `"Claude Code"`. A reply with no model: the assistant label alone.
  - A user message: `"You"`. A `!` command: `"Shell"`. A notice that Claude Code wrote: its kind, for example `"Task notification"`.
- `model`: the raw model id of a reply, or `null`. Claude Desktop and Codex record one model for the conversation, so their replies carry that model.
- `command_output`: only on the marker of a local slash command that printed output, for example the text of `/model`, without tags or ANSI codes.

Content-block filtering (`_filter_content_blocks`):

- `text` blocks: included if `text.strip()` non-empty. Tool-placeholder strings stripped when `include_tool_calls=False`.
- `tool_use` blocks: only included if `include_tool_calls=True`. The full `input` dict is included if `include_tool_results=True` *or* the JSON-serialized input is ≤200 chars; otherwise, `input_preview` carries the first 200 chars + `"..."`. The Anthropic `id` (e.g. `"toolu_01ABC..."`) is emitted when present so callers can pair parallel calls to their results.
- `tool_result` blocks: only included if `include_tool_results=True`. Their nested `content` is recursively filtered with the same flags. The `tool_use_id` back-reference is emitted when present — match it to a `tool_use.id` from the previous (or any nearby) message to reconstruct the call/result pair. Positional adjacency is NOT a reliable pairing signal: parallel calls may return out of order, and some results may be missing.
- `image` blocks: **currently not emitted** by `_filter_content_blocks` (the function has no `image` branch). This is a known gap.

**Errors:**

- `ValueError("Session '<id>' not found.")` if `store.get_conversation` returns `None`.

---

### 6.5 `export_session`

**Source:** `export_session()` in `mcp_server/server.py`.

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
| `include_code_mode` | `bool` | `False` | Include Code Mode runs: each script in a `javascript` block, its numbered calls, and what it returned. |
| `agent_id` | `str \| None` | `None` | Export this subagent transcript of the session instead (issue #96). |

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

**The bridge removes `session_id`.** The Claude desktop remote-devices bridge removes every tool argument whose key is `session_id`. The bridge carries calls from a Cowork cloud session to a local MCP server (tool names `mcp__remote-devices__<server name>__*`: `Claude_Explorer__Conversation_Archive_` for the Desktop extension, `claude-sessions` for a server with that name). Before the rename, `get_session_outline`, `get_messages`, and `export_session` always failed on that path with `session_id Missing required argument` (GitHub issue #33).

**No alias, but a clear error.** The tools do not accept `session_id`. A stdio client starts the server and then reads the tool list, so after an upgrade it sees only `conversation_id`. The description of `conversation_id` says that it was formerly named `session_id`. Old prompts still say `session_id`, so the middleware `_ExplainSessionIdRename` in `mcp_server/server.py` replaces the generic pydantic errors with messages that tell the client model what to send:

| Arguments sent | Result |
|---|---|
| `conversation_id` only | The tool runs. |
| `session_id`, alone or with `conversation_id` | `ToolError`: `'session_id' was renamed to 'conversation_id'. Send the session UUID as 'conversation_id', and do not send 'session_id'.` |
| Neither | `ToolError`: `Missing required argument 'conversation_id'` ..., `formerly named 'session_id'`, and some MCP bridges remove an argument named `session_id`. Through the bridge, a call that sent `session_id` arrives like this. |

The middleware applies only to these 3 tools. The other tools keep the generic "Unexpected keyword argument" error.

**The output has both keys.** `get_session_outline` returns the session UUID as `"conversation_id"`, the same name as the argument, so a model can pass it on to `get_messages` unchanged. It also keeps `"session_id"` with the same value for existing readers of the output. The bridge changes only the arguments of a call, so the output key is safe.

**Invariants and tests:**

- **No tool has an input property named `session_id`, at any depth.** `mcp_server/tests/test_conversation_id_param.py` reads the schemas that a client receives, through a FastMCP `Client`, and fails if one does.
- **Every tool works through a simulated bridge.** `mcp_server/tests/test_bridge_smoke.py` starts the server over stdio, builds the required arguments of each tool from its schema, removes the arguments in `BRIDGE_REMOVED_ARGUMENTS`, and requires each call to succeed. Run against the code before the rename, it fails with the message from issue #33.
- **The real bridge** cannot run in CI. [testing/manual-checks.md](../testing/manual-checks.md), Step 3, has the manual smoke prompt for a real Cowork session.

---

## 7. Outline cache schema

The SQLite file at `<data_dir>.parent / "cache.db"` carries two tables and one index. The schema is recreated idempotently on every `get_session_outline` call via `_ensure_schema`.

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

- **Read-only, except the archive copy.** No tool mutates the conversation store, the SQLite cache (other than its own outline rows), or anything else outside the cache table writes. The one exception is the archive copy:
  - **When:** at the start of each tool call, at most once per debounce interval (`CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC`, default 2 s), inline, with no thread and no timer (`_sync_archive`).
  - **What:** `backend.write_gate.run_archive_syncs(store, "mcp", 0)` copies each source's new files into the archive under its `ArchiveLock`. It reads a source home and never writes into it. Today only Codex copies: its rollouts, names, and `state_5` metadata go to `<data_dir>/codex/<home_key>/`.
  - **Codex home path:** outputs show it as `~/...` (`source_home`). The absolute path (`source_home_abs`) is for the web UI copy button only; no tool returns it (plan D14).
  - **Lock busy:** the tool reads the archive as it is, and `list_sessions` and `get_session_outline` return `sync_pending: true` (plan D15). The next call tries again at once.
  - **Gates:** a corrupt config skips the copy. A data folder in a cloud-sync folder skips it until the user opts in (plan D13), and `list_sessions` returns a `notices` item that names the provider. While that gate is closed, `get_session_outline` builds its outline in an in-memory database, so `cache.db` gets no rows.
- **No network egress.** The server speaks JSON-RPC over stdin/stdout. There is no HTTP listener, no outbound socket, no auth header construction. The substrate (`backend.store`) reads local files; the export path renders Markdown without fetching anything remote.
- **stdio-only transport.** A client must spawn the server as a subprocess to talk to it. There is no `--port` flag and no `--host` flag.
- **No credentials handled.** Credentials live in `~/.claude-explorer/credentials.json` for fetcher use only; the MCP server does not read them.
- **Path traversal:** all file lookups go through `backend.store` and its source adapters, which resolve session UUIDs against the configured data directory; UUIDs supplied to `get_session_outline`, `get_messages`, and `export_session` cannot escape the data root via `..` or symlinks because the store enumerates files first and matches by UUID, not by user-supplied path.

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
- **No pagination on `get_session_outline`:** sessions with tens of thousands of messages return the full outline in one payload. The summaries are small (200 chars × `N`), so the practical ceiling is high, but a `limit`/`offset` pair would let very long sessions stream.

## 11. Testing

Tests live under `mcp_server/tests/`. They use the `isolated_data_dir` fixture from `backend/tests/conftest.py` to plant minimal fixture conversations, then call the tool functions directly (FastMCP's `@mcp.tool()` decorator leaves the underlying function callable in-process).

Run with:

```bash
uv run pytest mcp_server/tests/ -q
```

The test suite is the executable form of this spec; whenever this document is updated, the tests should be updated in lockstep, and vice versa.
