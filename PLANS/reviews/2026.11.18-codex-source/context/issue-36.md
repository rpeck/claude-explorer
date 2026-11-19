# #36 Feature request: support Hermes Agent sessions in the web UI and MCP server

## Summary

Add **Hermes Agent** as a local conversation source, so its saved sessions can be browsed, searched, exported, and queried through Claude Explorer's existing MCP server alongside Claude Desktop, Claude Code, Cowork, and Pi.

This is another fit for Claude Explorer's local-first archive and the workflows in Raymond Peck's [web UI quickstart](https://medium.com/@raymondpeck/unlocking-your-claude-history-part-2-quickstart-claude-explorer-in-five-minutes-41526bcce2a9) (one searchable cross-source archive) and [MCP quickstart](https://medium.com/@raymondpeck/unlocking-your-claude-history-part-3-quickstart-query-your-claude-history-in-five-minutes-b12094c9c690) (outline first, then read only the relevant messages).

## Required surfaces: web UI AND MCP

Both are required for completion; an MCP-only implementation does not satisfy this request.

- **Web UI:** browse sessions in the conversation list, filter by source/project, search and jump to matching messages, read transcripts with the existing tool/compaction controls and lineage presentation, and copy/export through the existing Markdown/PDF workflows (PDF when its dependencies are installed). Search and exports must honor the selected scope and display controls.
- **`claude-sessions` MCP server:** list/search sessions and projects, fetch outlines and selected messages, and export Markdown through the existing five tools.
- Both surfaces must reuse the same normalized reader/store and agree on session identity, transcript content, and applicable filtering. No separate UI-only or MCP-only parser.

## Hermes storage contract (researched from a local Hermes installation and source)

Hermes's canonical session archive is SQLite, not the JSON files under `sessions/`:

- Default home is `~/.hermes`; `HERMES_HOME` overrides it (and Hermes also has a context-local home override).
- The main profile's archive is `$HERMES_HOME/state.db`. Named profiles have their own `state.db` under the root's `profiles/<profile>/` tree. Discovery needs a configured Hermes-home root and should scan the root store plus profile stores that exist.
- `state.db` is commonly live and uses SQLite WAL. Its `-wal` and `-shm` sidecars are part of the live database view. Do **not** treat `state-snapshots/`, request dumps, or `sessions/sessions.json` as additional conversation archives.
- The public schema lives in [`hermes_state_common.py`](https://github.com/NousResearch/hermes-agent/blob/bf0203a794/hermes_state_common.py#L461-L570). `sessions` supplies the stable `id`, `source`, `title`, `model`, `started_at`/`ended_at`, `cwd`, Git metadata, `parent_session_id`, `profile_name`, archive/pin/hidden state, and aggregate token/cost metadata. Timestamps are Unix seconds.
- `messages` holds the transcript. Important fields are `id` (the display order; timestamps are not necessarily monotonic), `session_id`, `role`, `content`, `tool_call_id`, `tool_calls`, `tool_name`, `timestamp`, `reasoning`/reasoning-detail columns, `api_content`, `display_*`, `active`, `compacted`, and `_compressed_summary`.
- Observed roles include `user`, `assistant`, `tool`, and `session_meta`. `tool_calls` and several metadata fields are JSON text; tool-result content can be large and sensitive.
- `parent_session_id` is not a general "merge every parent and child" relationship. Hermes uses it for compression continuations, branches, delegation/subagents, and resets. Hermes's own display projection follows only verified compression lineage and keeps a branched/forked session separate; see [`get_messages_as_conversation`](https://github.com/NousResearch/hermes-agent/blob/bf0203a794/hermes_state_messages.py#L1491-L1512) and its compression-lineage rules.
- Compaction and rewinds make a naive `WHERE active = 1` transcript incomplete for a human reader, while indiscriminately including inactive rows can duplicate removed/replaced turns. Hermes's display read uses display identities and compaction-aware de-duplication; this needs to be explicitly replicated or conservatively surfaced rather than silently flattened.

## Requested behavior

- Add a distinct `HERMES` source to the web UI source filter, session list/search results, project grouping, and MCP results. Namespace identities by canonical database path/profile plus Hermes session ID, so IDs from separate profiles cannot collide.
- Add a configurable Hermes-home root. Default to `~/.hermes`, but do not require it to exist and do not depend on the current process's `HERMES_HOME` value.
- Read the root `state.db` and discovered profile databases using SQLite's read-only mode. Do not use `immutable=1` (it can miss a live WAL), open write-capable connections, run migrations/PRAGMAs that alter state, rebuild Hermes FTS, copy/replace the database, or touch `-wal`/`-shm` files. A busy/I/O/corrupt/missing database must leave the rest of the archive available and report a per-store warning.
- Use `sessions` for title, source subtype, model, timestamps, project grouping (`cwd`, falling back to Git repo metadata), and archive state. Default to interactive/user-facing sessions; let users opt into `subagent`, `tool`, platform/chat, or hidden/archived sessions rather than quietly mixing them into normal conversations.
- Render user and assistant messages cleanly by default. Preserve tool calls/results, reasoning, compaction summaries, and provenance behind the equivalent existing detail controls. Order turns by `messages.id`, not timestamp, and do not expose `session_meta` as a normal chat turn.
- Apply Hermes's display/lineage semantics: show a compression continuation as one intelligible conversation without duplicate carried-forward messages; keep explicit branches/delegates/resets distinct and link them as related sessions when metadata permits. If a schema/version cannot be projected safely, show the raw session as unavailable or degraded rather than inventing a flat transcript.
- Search imported Hermes user/assistant content through Claude Explorer's own index; do not rely on or mutate Hermes's internal FTS tables. Expose supported Hermes sessions through `list_sessions`, `list_projects`, `get_session_outline`, `get_messages`, and `export_session`, retaining the MCP server's explicit-only, outline-first behavior.
- Keep the integration local and read-only. Hermes data may include system prompts, reasoning, tool inputs/results, command output, working directories, Git metadata, and platform identifiers. No transcript content should be uploaded or mutated.

## Acceptance criteria

1. With a configured Hermes root containing a main `state.db` and a profile `state.db`, Claude Explorer lists valid sessions from both, labels/group them by profile and project, and full-text search finds their user/assistant content.
2. The reader opens a live WAL-mode store read-only and observes committed WAL data without creating or modifying any Hermes database, sidecar, schema, or FTS object.
3. A selected session displays title/model/time metadata and a message-ID-ordered, compaction-aware transcript; tool and reasoning material is available on demand.
4. Compression continuations do not produce duplicate turns; explicit branch/delegate/reset children remain separate and discoverably related.
5. The MCP tools support Hermes sessions via the existing outline-first flow.
6. Fixture-based tests cover: a basic session; tool calls/results; a live WAL fixture; a compacted/rewound session; a compression continuation; an explicit branch; two profile databases with the same session ID; unknown extra columns; and missing, locked, or malformed stores.
7. The implementation is verified read-only against the fixture database (no SQLite write journal/sidecar/schema changes) and works when no Hermes installation exists.
8. A fixture-backed UI end-to-end test exercises source/project filtering → search → jump to a matching message → transcript detail controls → copy/Markdown export; PDF export is covered with its dependencies installed. Equivalent MCP reads/exports agree on the underlying transcript. Update `UX.md` and user documentation for the new UI source.

## Non-goals for the first slice

No Hermes installation, daemon/gateway integration, session capture, import into Hermes, or filesystem watcher is required. Reading already-persisted local SQLite archives on Claude Explorer startup/refresh is sufficient; live-update watching can follow after the reader's WAL and display-projection behavior is proven.

