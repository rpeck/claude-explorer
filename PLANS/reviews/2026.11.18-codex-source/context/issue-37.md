# #37 Feature request: support OpenAI Codex sessions in the web UI and MCP server

## Update 2026-11-18: decisions and corrections

The implementation plan is `PLANS/2026.11.18-codex-source.md`. It splits the work into 12 PRs in a task DAG, in three slices: foundation, Codex usable, full history.

**Decisions.** Where a decision differs from the text below, the decision wins.

- **D1. Archive copy.** Explorer copies every Codex rollout into its own archive and never deletes the copy (CLAUDE.md principle 2). Every reader (list, viewer, search, export, MCP) reads the archive copy. Only the copier reads `~/.codex`, and it never writes there.
- **D2. Visibility.** A session with any turn stays visible. Do not hide a session because `has_user_event` is false, or because a title or a first message is missing (principle 1).
- **D3. Source adapter first.** Port Desktop, Claude Code, and Cowork to a `SourceAdapter` interface with no change in behavior, before Codex. Pi (#35) and Hermes (#36) use the same interface.
- **D4. Dependency.** Add `zstandard` (only for Python < 3.14; 3.14 has `compression.zstd`). The MCPB bundle gets `zstandard` and `portalocker`.
- **D5. Watcher and manual Refresh both copy, with no collisions.** Use one pass per process (single-flight), a cross-process portalocker lock for each Codex home, idempotent append-only copies, and atomic writes (temp file, fsync, replace). This replaces the earlier non-goal "no always-on watcher".
- **D6. Source of truth.** The rollouts are the source of truth. Never read `thread_history_1.sqlite`: Codex builds it from the rollouts for its own paging. Read `state_5.sqlite` for metadata only, and keep a snapshot of that metadata in the archive.
- **D8 (changes "Namespace Explorer identities" below).** The Explorer conversation id is the Codex thread id, so a user can paste it into `codex resume` or an MCP call. The UI header and the MCP tools (`list_sessions`, `get_session_outline`, `export_session`) also show the Codex session name (`source_name`) and the Codex home folder (`source_home`). If the same thread id occurs in two homes, keep the newest copy.
- **D9.** The copier obeys the corrupt-config writer gate.

**Corrections and new facts (checked 2026-11-17 against `openai/codex` at `f2d3086`):**

- Codex compresses a plain rollout that is older than 7 days into `.jsonl.zst` and deletes the plain file. When the thread resumes, Codex decompresses it back to `.jsonl` and deletes the `.zst` file. Archiving moves the file to `archived_sessions/`. Thus the physical key is the file stem, never the path.
- No Codex code prunes old rollouts.
- `state_5.sqlite.threads` marks subagent threads with `thread_source = 'subagent'` and a `parent_thread_id` in `source`.
- `external_agent_session_imports.json` records Claude Code sessions that Codex imported, each with a new thread id. The same conversation can thus appear under Claude Code and under Codex. The Codex copy gets an "Imported from Claude Code" label.
- `mcp_server/SPEC.md` omits Cowork in several places. The adapter PR fixes it. The Codex PR changes the MCP server instructions from "Claude conversation history" to text that covers Codex too.

**Verification on real data.** The tests use only synthetic data. For real corpora, a `claude-explorer codex-report` command prints a shape report with no message text, no paths, no titles, and no raw ids. Testers paste it into a `codex-verification` issue.

---

## Summary

Add **OpenAI Codex** as a local conversation source in **both Claude Explorer's web UI and the `claude-sessions` MCP server**. An MCP-only implementation does not satisfy this request.

Companion requests: Pi #35 and Hermes #36. Reuse Claude Explorer's normalized conversation store, search, and export paths rather than implementing separate UI/MCP readers.

## Required surfaces

- **Web UI:** conversation list, Codex source filter, project grouping, full-text search and jump-to-message, readable transcripts, tool/compaction detail controls, available branch/related-session presentation, copy as Markdown, and existing Markdown/PDF export workflows (PDF with its dependencies installed). Search and exports must honor the applicable source/project scope and display controls.
- **MCP:** `list_sessions`, `list_projects`, `get_session_outline`, `get_messages`, and `export_session`, retaining the existing explicit-only, outline-first behavior.
- Both surfaces must agree on session identity, displayed transcript, and applicable filtering. Support locally persisted Codex CLI/Desktop history; cloud-only conversations are outside this first slice.

## Research: actual local data and upstream storage

Inspected local file shapes and SQLite metadata read-only, plus OpenAI's source at commit `25f4940d4affcf8c60f96ffec5e3752e1f590b8a`. No private transcript contents, account identifiers, or personal project paths are included here.

**Important: this is not just a plain JSONL reader.** When first inspected (2026-11-09), the local corpus contained 35 `.jsonl.zst` rollouts and one plain `.jsonl`. On 2026-11-17 it held 57 rollouts in `sessions/` (22 plain, 35 `.zst`) and 3 in `archived_sessions/`, for 56 threads (55 paginated, 1 legacy); the local thread index reports `history_mode = paginated`. Inspected samples include both `response_item` records and canonical `event_msg/item_completed` display records. Plain-JSONL-only or legacy-response-item-only support would miss or misrepresent this corpus.

### Locations and identities

```text
<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-<timestamp>-<id>.jsonl
<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-<timestamp>-<id>.jsonl.zst
<CODEX_HOME>/archived_sessions/
<CODEX_HOME>/session_index.jsonl
<CODEX_HOME>/state_5.sqlite       # observed locally; versioned internal metadata store
```

- Codex normally uses `~/.codex`; allow an explicit Codex-home override for installations using `CODEX_HOME` or another root. Do not read credentials to discover sessions.
- Scan supported rollouts recursively, including archived rollouts with an explicit archive label/filter. Support Zstandard decoding without decompressing back into Codex's directory. Plain/compressed representations of the same rollout must not produce duplicate conversations.
- Rollout lines contain `timestamp`, `type`, and `payload`. `session_meta` supplies thread `id`, `cwd`, creation time, `cli_version`, provider/source information, and optional Git, fork/parent, and history-reference metadata.
- Use metadata rather than filename assumptions: newer `session_id` refers to the root session, whereas `id` identifies the thread. A revert can keep the thread ID while creating a different physical rollout ID/file. Namespace Explorer identities by source/home and thread identity; preserve physical rollout provenance.
- `session_index.jsonl` is an append-only **name index** (`id`, `thread_name`, `updated_at`); the latest matching entry wins. It is not a transcript.
- Locally observed `state_5.sqlite.threads` includes `id`, `rollout_path`, title/name, timestamps, provider/model, `cwd`, archive state, Git metadata, and `history_mode`. Treat this as versioned metadata, not a table of chat messages. It can help identify the current rollout after a revert; do not blindly show every old physical rollout as a separate active thread. Validate any indexed paths against configured roots.
- `history.jsonl` contains input-history records (`session_id`, `text`, `ts`), not complete assistant/tool conversations. Do not ingest it as a second archive. Logs, credentials, shell snapshots, and application task databases are also not transcript sources.

### Transcript semantics

- **Paginated history:** follow canonical `event_msg` records with `type = item_completed`, using turn/item identity to apply repeated snapshots rather than appending duplicates. Locally observed completed-item types include `UserMessage`, `AgentMessage`, `Reasoning`, and `CommandExecution`. Preserve turn completion/interruption information where present.
- **Legacy history:** handle its response/event shapes according to the supported format. `response_item/message` carries roles and content blocks such as `input_text` and `output_text`. Function/custom tool calls and outputs use `call_id`; arguments may be JSON encoded inside a string, and outputs may be text or structured content. Match calls/results by identity, not adjacency.
- **Do not combine both representations indiscriminately.** Model context includes developer instructions, injected environment text, tool declarations, and replayed context that are not necessarily user-visible turns. In paginated history, `response_item` is not a second copy of the visible conversation to append after `item_completed`.
- Preserve recorded commentary/final phases, readable reasoning summaries, tools, and image/attachment references with appropriate controls. Encrypted reasoning is opaque: do not attempt to decrypt it or imply it contains readable text. Missing media should be labeled, not fetched automatically from arbitrary URLs or filesystem paths.
- `turn_context` records model/cwd/settings for individual turns; do not assume the session used one model throughout. Other observed records include `world_state` and `token_usage_record`; these are not ordinary chat turns.
- Compaction (`compacted` and replacement context), rollback/revert, forks/subagents, and `history_base` prefix references require explicit projection rules. Preserve original human-readable history where available without duplicating replacement context or resurrecting rolled-back turns. Keep forks/subagents distinct. Bound reference traversal and detect missing bases/cycles. If a format cannot be reconstructed safely, report a degraded/incomplete session rather than fabricate a complete transcript.

## Safety and refresh requirements

- Read-only ingestion: never launch Codex, resume a thread, migrate its database, rewrite rollouts/indexes, or modify archive state. Explorer's own cache/index writes stay in Explorer's data directory.
- Any SQLite metadata reads use `mode=ro`, short bounded reads, and normal WAL-aware SQLite access—not `immutable=1` or raw copying of an active database. No migrations, journal-mode changes, checkpoints, or repair. An unavailable metadata database must not prevent reading independently usable rollouts; ambiguity about the current revision must be surfaced.
- Tolerate active append/truncated final lines, file replacement/compression transitions, malformed records, corrupt compressed streams, unknown optional fields, and missing roots. Report per-session failures without breaking other sources; do not present a damaged transcript as complete.
- Startup/manual refresh is enough initially, but refresh must invalidate stale search/outline/export data after append, rename, archive, compaction, or revert—not just after a message-count increase.
- Keep ingestion local. Do not upload session material, read authentication files, or publish real transcripts as test fixtures. MCP-returned content may subsequently be sent to a model by the user's MCP client; local ingestion is not a guarantee against that client-side disclosure.

## Acceptance criteria

1. A synthetic corpus containing plain and Zstandard-compressed rollouts appears in the UI under **Codex**, with correct titles, project grouping, archive state, timestamps, and model metadata where available.
2. UI end-to-end coverage exercises source/project filtering → search → jump to a matching message → transcript/detail controls → copy/Markdown export. Cover PDF with its dependencies installed; update `UX.md` and user documentation.
3. All five MCP tools work for the same sessions; equivalent UI/MCP reads and exports agree on the normalized transcript.
4. Tests cover legacy and paginated formats, duplicated response/event representations, repeated item snapshots, parallel tool calls/results, compaction, rollback/revert, fork/history-prefix references, and missing/cyclic bases. No duplicated turns, merged child conversations, or falsely complete histories.
5. Tests cover both physical compression representations, absent/stale/locked SQLite metadata, name-index updates, malformed/truncated records, unknown optional records, missing media, root/path containment, and stable IDs across refreshes and different source roots.
6. Fixture-based verification proves no Codex transcript/index/database content or schema is changed. Missing installations are harmless; unsupported formats generate actionable warnings rather than silently disappearing.

## Source references

Pinned upstream references used for this research:

- [Rollout recorder and dated paths](https://github.com/openai/codex/blob/25f4940d4affcf8c60f96ffec5e3752e1f590b8a/codex-rs/rollout/src/recorder.rs)
- [Plain/Zstandard rollout reading](https://github.com/openai/codex/blob/25f4940d4affcf8c60f96ffec5e3752e1f590b8a/codex-rs/rollout/src/compression.rs)
- [Session name index](https://github.com/openai/codex/blob/25f4940d4affcf8c60f96ffec5e3752e1f590b8a/codex-rs/rollout/src/session_index.rs)
- [Session metadata, prefix references, and thread identity](https://github.com/openai/codex/blob/25f4940d4affcf8c60f96ffec5e3752e1f590b8a/codex-rs/protocol/src/protocol.rs)
- [Response item/content types](https://github.com/openai/codex/blob/25f4940d4affcf8c60f96ffec5e3752e1f590b8a/codex-rs/protocol/src/models.rs)
- [Canonical paginated display projection](https://github.com/openai/codex/blob/25f4940d4affcf8c60f96ffec5e3752e1f590b8a/codex-rs/app-server-protocol/src/protocol/thread_history_projection.rs)

## Non-goals

No Codex runtime/plugin or daemon integration, cloud fetching, write-back, session capture, or always-on watcher. These exclusions do **not** exclude Claude Explorer's web UI: UI and MCP are both required. Compressed rollouts and paginated display history are required for the first useful slice because they are already present locally.


