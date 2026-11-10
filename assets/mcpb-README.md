# Claude Explorer (Conversation Archive)

Search and read your saved Claude Desktop and Claude Code conversations
from inside Claude.

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

The extension only reads existing conversation JSON. It never writes,
deletes, or modifies anything in `~/.claude-explorer/conversations/`.
Your archive is safe.

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
