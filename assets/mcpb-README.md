# Claude Explorer (Conversation Archive)

Search and read your saved conversation history (Claude and Codex)
from inside Claude: Claude Desktop, Claude Code, Cowork, and OpenAI Codex
sessions.

This extension exposes the local conversation archive at
`~/.claude-explorer/conversations/` as 5 MCP tools that Claude can call
directly:

| Tool | What it does |
|---|---|
| `list_projects` | List distinct projects with session counts |
| `list_sessions` | Search and list sessions, filtered by project, date, or text |
| `get_session_outline` | Lightweight per-message summaries of a session, in pages |
| `get_messages` | Full content for specific message UUIDs |
| `export_session` | Markdown export of a full or partial session |

## Read-only

The extension only reads existing conversations. It never deletes or
changes anything in `~/.claude-explorer/conversations/`. Your archive is
safe.

Its one write adds your Codex sessions to the archive: before a tool
call, it copies new rollouts from the Codex home (`CODEX_HOME`, else
`~/.codex`) into `~/.claude-explorer/conversations/codex/`. It only reads
the Codex home and never writes there.

## You still need the CLI to capture conversations

This extension does NOT fetch conversations from Claude. Run the
`claude-explorer` CLI separately to capture credentials and download
your sessions:

```bash
pip install claude-explorer
claude-explorer capture   # one-time credential capture
claude-explorer fetch     # download your archive
```

Once you have a populated `~/.claude-explorer/conversations/`, this
extension lights up the 5 tools above inside Claude. Re-run
`claude-explorer fetch` (or install the watcher) whenever you want to
refresh.

Source, docs, and issues: https://github.com/rpeck/claude-explorer
