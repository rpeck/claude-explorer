# #35 Feature request: support Pi Coding Agent sessions in the web UI and MCP server

## Summary

Add **Pi Coding Agent** as a local conversation source, so Pi session history can be browsed, searched, exported, and queried through the existing MCP server alongside Claude Desktop, Claude Code, and Cowork.

This fits Claude Explorer's stated local-first archive model and the workflows in Raymond Peck's [web UI quickstart](https://medium.com/@raymondpeck/unlocking-your-claude-history-part-2-quickstart-claude-explorer-in-five-minutes-41526bcce2a9) (one searchable cross-source archive) and [MCP quickstart](https://medium.com/@raymondpeck/unlocking-your-claude-history-part-3-quickstart-query-your-claude-history-in-five-minutes-b12094c9c690) (outline first, then read only the messages that matter).

## Required surfaces: web UI AND MCP

Both are required for completion; an MCP-only implementation does not satisfy this request.

- **Web UI:** browse sessions in the conversation list, filter by source/project, search and jump to matching messages, read transcripts with the existing tool/compaction controls and branch presentation, and copy/export through the existing Markdown/PDF workflows (PDF when its dependencies are installed). Search and exports must honor the selected scope and display controls.
- **`claude-sessions` MCP server:** list/search sessions and projects, fetch outlines and selected messages, and export Markdown through the existing five tools.
- Both surfaces must reuse the same normalized reader/store and agree on session identity, transcript content, and applicable filtering. No separate UI-only or MCP-only parser.

## Pi sessions to read

Pi persists sessions as JSONL by default under:

```text
~/.pi/agent/sessions/--<encoded-cwd>--/<timestamp>_<session-id>.jsonl
```

The storage root is configurable via `--session-dir`, `PI_CODING_AGENT_SESSION_DIR`, or Pi's `sessionDir` setting, so discovery should permit an override rather than assuming the default.

Each file is an append-only JSONL stream:

- The first record is `{"type":"session", "version": ..., "id": ..., "timestamp": ..., "cwd": ...}`. Use the header's `cwd` for project grouping; do not derive it from the encoded directory name.
- Current files use version 3. Version 1 was linear; versions 2+ represent a tree through entry-level `id` and `parentId` fields. A reader needs a branch/active-path strategy rather than treating every record as one flat chat.
- `message` records wrap an `AgentMessage`. Relevant roles are `user`, `assistant`, `toolResult`, and `system`. Assistant messages carry provider/model, usage, stop reason, and sometimes `thinkingLevel`; their content is an array of blocks such as `text`, `thinking`, and `toolCall`.
- Session records can also be `model_change`, `thinking_level_change`, `compaction`, `context_edit`, `branch_summary`, `custom`, `custom_message`, `label`, `session_info`, and `usage`. In particular, a `compaction` is a summary plus retained-boundary metadata, not an ordinary user/assistant turn; `context_edit` changes only future model context and does not rewrite raw history.
- `session_info.name`, when present, is the user-assigned display name. Otherwise, a first meaningful user message is a reasonable fallback title.

Pi's session-format reference: <https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/session-format.md>.

## Requested behavior

- Add a distinct `PI` source in the web UI's source filter, list/search results, and project grouping. Namespace stable IDs by Pi session identity/path so they cannot collide with other sources.
- Read Pi JSONL files locally and defensively: one malformed/truncated session or record should be reported/skipped without blocking the rest of the archive.
- Render normal user/assistant turns cleanly by default; preserve tool calls/results, thinking, system/context records, compactions, and branch information behind the existing equivalent detail controls rather than silently losing them.
- Make Pi sessions searchable through the same store/index as the UI, and expose them through `list_sessions`, `list_projects`, `get_session_outline`, `get_messages`, and `export_session`. Keep the MCP server's existing explicit-only, outline-first behavior.
- Keep this source read-only and local-only. Pi transcripts can contain system prompts, command arguments/output, file contents, and other sensitive project material; no session content should be uploaded or mutated.
- Document the default path, supported override(s), current format/version assumptions, branch behavior, and any unsupported entry/content types.

## Acceptance criteria

1. With a configurable Pi session root containing multiple JSONL files, the UI lists Pi sessions, groups them by header `cwd`, and full-text search finds Pi user/assistant content.
2. A selected session shows its title, timestamps, model metadata when available, and an intelligible branch-aware transcript; compacted/edited sessions do not duplicate or misrepresent turns.
3. The MCP tools can list and retrieve Pi sessions using the same outline-first workflow as existing sources.
4. Fixture-based tests cover a basic v3 session, a branched session, a compacted session, a malformed/truncated JSONL record, and ID collisions with another source.
5. The implementation never writes to Pi's session directory and works when the default directory is absent.
6. A fixture-backed UI end-to-end test exercises source/project filtering → search → jump to a matching message → transcript detail controls → copy/Markdown export; PDF export is covered with its dependencies installed. Equivalent MCP reads/exports agree on the underlying transcript. Update `UX.md` and user documentation for the new UI source.

## Non-goal for the first slice

No Pi runtime/plugin integration, session capture, or always-on watcher is required. This does not exclude Claude Explorer's web UI, which is required above. Reading existing local session files on refresh/startup is enough; live filesystem watching can be a follow-up once the reader and normalized conversation model are proven.

