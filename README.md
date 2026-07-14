# Claude Explorer

A local tool to browse, full-text search, and export your entire Claude conversation history — Claude Desktop, Claude Code, and Claude Cowork in one unified, searchable place — and to query it programmatically from Claude itself via a built-in MCP server. It also rescues conversations from accounts whose login email you've lost access to.

> **Disclaimer**: This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Anthropic, PBC. "Claude" and "Claude Code" are trademarks of Anthropic, PBC. This project consumes Anthropic's products as a user would — via the same APIs and on-disk file formats the official clients use — but nothing here represents an Anthropic-sanctioned interface, and the formats this project depends on may change without notice.

## Quick Start

```bash
# install uv if needed: https://docs.astral.sh/uv/getting-started/installation/

# One-time: install the Chromium build Playwright drives during credential capture.
# The sidebar Refresh button uses this to log you into Claude; without it the
# in-app login flow fails on first run.
uvx --from claude-explorer playwright install chromium

# Start the web app:
uvx claude-explorer serve

# In another terminal, install the always-on image-cache watcher
# (strongly recommended — see "Continuous Image-Cache Watcher" below):
uvx claude-explorer install-watcher
```

```bash
# Optional: install the system libraries WeasyPrint needs for PDF export
# (skip if you only care about Markdown export). Pick ONE block:
#
#   macOS:
brew install pango cairo libffi
#
#   Linux (Ubuntu/Debian):
# apt-get install libpango-1.0-0 libpangocairo-1.0-0 libcairo2
#
#   Windows: install MSYS2 (https://www.msys2.org), then in its shell run
#            `pacman -S mingw-w64-x86_64-pango`. Or grab the standalone
#            WeasyPrint .exe from the GitHub releases to skip the
#            system-library dance entirely.
```

### Windows ARM64 (Copilot+ PCs / Surface Pro X / Snapdragon-X laptops)

On Windows ARM64, install claude-explorer against an **x86_64 Python** rather than the default ARM64-native Python. The ARM64 Python on Windows lacks prebuilt wheels for several upstream dependencies (`cryptography`, `httptools`, `Brotli`); x86_64 Python runs under Microsoft Prism translation at near-native speed and has full wheel coverage. This matches Microsoft's own recommendation for Python development on Windows-on-ARM.

`uv` handles the cross-architecture install in a single command:

```powershell
# Install uv if you don't have it:
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Then install claude-explorer against an x86_64 Python (uv fetches it on demand):
uv tool install claude-explorer --python cpython-3.13.14-windows-x86_64-none
```

Everything else in this README (`uvx ...` commands, watcher install, MCP setup) works the same on Win ARM64 as on other platforms.

That's it. Open `http://localhost:8765` in your browser and your Claude Code and Claude Cowork sessions are visible immediately. Click **Refresh** in the sidebar to capture credentials and fetch your Claude Desktop history (the UI handles capture via in-process Playwright on first run; no terminal commands needed).

The watcher is a one-time install that registers a tiny background job with your OS supervisor (launchd / systemd / Task Scheduler) so Claude Code can't quietly rotate your screenshots and pasted images off disk before they get mirrored. Without it, you only have image protection while `claude-explorer serve` is running.

If you'd rather hack on the project than install it, see [From source (for contributors)](#from-source-for-contributors) below.

## Install in Claude Desktop (one-click MCP)

If you only want to query your archive from Claude (no web UI), grab the
`.mcpb` bundle from the latest [GitHub Release](https://github.com/rpeck/claude-explorer/releases/latest)
and drag it into Claude Desktop → **Settings** → **Extensions**. Five
tools — `list_sessions`, `list_projects`, `get_session_outline`,
`get_messages`, `export_session` — light up inside Claude Desktop and
Claude Code without you ever touching `claude_desktop_config.json`.

Two non-obvious things to know up front:

- **The extension is read-only.** It only reads what's already in
  `~/.claude-explorer/conversations/`. It never writes, deletes, or
  modifies your archive.
- **You still need the CLI to capture conversations.** The extension
  does not fetch from Claude. Run `uvx claude-explorer capture` once
  to grab credentials and `uvx claude-explorer fetch` to download your
  archive. After that, this extension does the read side.

First launch is a little slow (Claude Desktop's [UV runtime](https://github.com/anthropics/dxt)
resolves and installs the bundle's Python deps on first run; ~10–30 s
depending on your network). Subsequent launches are instant.

## Features

- **Browse conversations** from all three sources in one unified list: Claude Desktop (fetched), Claude Code, and Claude Cowork (both read live from local disk)
- **Full-text search** across all messages with instant results — multi-word queries AND tokens (all words must appear in the same message, any order); wrap in `"double quotes"` to require an exact phrase. Both the title-search and full-text-search honor the sidebar's active scope (source dropdown, workspace, and any active filter), so what you can't see in the sidebar can't appear in search results either.
- **Export** to Markdown or PDF
- **Dark mode** with automatic system preference detection
- **Keyboard navigation** with Emacs and Vim modes
- **Message tree visualization** for branched conversations
- **Bookmarks** (per-message): hover any message and click the star to save it; the **Bookmarks** tab in the right pane lists every saved message, with editable notes and Markdown export
- **Command palette** (Cmd+K) for quick navigation
- **Claude Code integration** with project grouping and subagent display
- **MCP server** exposing your saved sessions to Claude Desktop and Claude Code

---

## Performance

Claude Explorer keeps interaction latency under the human-perception threshold across the corpus sizes most users actually have (hundreds to low thousands of conversations, single-digit GB on disk). Numbers below come from a ~1,000-conversation / ~2.5 GB corpus measured with [`hyperfine`](https://github.com/sharkdp/hyperfine) on macOS / M3 Pro / local SSD. The "Before" column is the pre-optimization baseline shipped in early V1 betas; "After" is what V1 ships.

| Metric | Before | After | Improvement |
|---|---|---|---|
| Sidebar list (`/api/conversations`), warm cache | 4,518 ms | **72 ms** | ~63× |
| Sidebar list, cold SQLite cache, warm filesystem | 11,168 ms | **134 ms** | ~83× |
| Sidebar list, first install, cold everything | ~6,000 ms | **135 ms** | ~44× |
| Search query, narrow term (`q=foobar`) | ≈1,400 ms (linear) | **≈317 ms** (FTS5) | ~4.4× |
| Search query, broad term (`q=python`, ~770 KB results) | ≈1,400 ms (linear) | **≈750 ms** (FTS5) | ~1.9× |
| Search query cold (first call after restart) | ≈20,850 ms | **≈780 ms** | ~27× |
| Conversation detail, 288 MB CC JSONL (warm) | 1,474 ms | **≈230 ms** | ~6.4× |
| Markdown export of same conversation (warm) | 1,460 ms | **≈230 ms** | ~6.4× |
| Search-ready time after server restart | ~15 s | **<1 s** | ~15× |
| Startup time-to-image-protection | ~15 s | **~1 s** | ~15× |
| In-flight search freshness (CC session updated while running) | up to 600 s | **~2–3 s** | ~200× |
| Sidebar wire payload | 650,640 B | **459,555 B** | −29% |
| Sidebar DOM rows rendered (334-conv corpus) | 334 | **13** | −96% |

What's behind those numbers:

- **A persistent SQLite metadata cache** at `~/.claude-explorer/search-index.sqlite` (`conversation_summaries` table) means the sidebar payload comes from cached rows, not from re-parsing every JSONL on every request.
- **A drift-first FTS5 build** at startup queries the `indexed_files` table for current mtimes before loading any conversation content; only files that actually changed get re-indexed. Search becomes queryable in under a second after restart instead of ~15 s.
- **FTS5 `snippet()` for the search-result fragments** replaces the post-MATCH corpus walk that had to read every matched conversation from disk. The schema carries a two-column projection (`body` for the full text, `body_text` for the text-only view) so the **Tools** toggle behaves the same on the fast path as on the linear-scan path: column-scoped MATCH excludes tool-only hits at MATCH time, not after BM25 ranks. `snippet()` runs entirely in SQLite, and the cold-search path drops from ~16 s to under 1 s on a 1,000-conversation corpus.
- **A truncation envelope on every search response** discloses the FTS5 row cap. `/api/search` returns `{results, total_messages_matched, returned_messages, truncated}` instead of a bare list; the total comes from a sub-10 ms `COUNT(*)` under the same WHERE clauses as the snippet query. The SearchPanel renders a small footer ("Showing first 1,000 of 12,400 message matches. Refine your query to see the rest.") when the cap clipped the response. The MCP `list_sessions` tool uses a higher cap (5,000) so LLM and script consumers can reason about broader queries.
- **An LRU FileCache + cache-routed conversation-detail lookups** mean opening a 288 MB Claude Code session is a dict lookup, not a JSONL re-parse. Markdown / PDF / JSON exports get the same payoff for free.
- **The CC image-warm walk piggybacks on the FTS5 build** instead of running as a separate startup pass: every JSONL the FTS5 build reads also warms its image markers via `cache_all_markers`. Image protection now becomes available within ~1 s of restart instead of ~15 s, and a duplicate disk walk goes away entirely.
- **Event-driven `watchdog` observers** on `~/.claude/image-cache/` and `~/.claude/projects/` catch new images and CC session edits within sub-second latency; a debounced drift pass keeps the search index fresh while the explorer is running.
- **`ORJSONResponse`** plus a skinny `ConversationListItem` Pydantic model (a strict subset of `ConversationSummary`) cuts the wire payload ~29% without breaking MCP, the detail page, or the server-side `?search=` filter.
- **Frontend virtualization** (`@tanstack/react-virtual`) on the flat sidebar list mounts ~13 rows instead of all ~1,000, taking the linear-in-N React reconciliation cost with it.
- **A linear-scan fallback** kept in the codebase covers every "fast path unavailable" case (FTS5 missing on a stock Linux Python, index still warming on first install, summary cache disabled). Search and sidebar never go "down" — slow-but-correct beats fast-but-broken.

### Running the benchmarks yourself

The repo ships a `make bench` target that drives the canonical suite (sidebar list, search warm and cold, conversation-detail at small / medium / large size percentiles auto-picked from the live corpus, Markdown export) against a running backend on `:8765`.

```bash
# 1. Start the backend in one terminal:
DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run uvicorn backend.main:app --port 8765
# Wait for "search index build complete" on stdout (warm restart: <1 s;
# first run after a schema bump can take ~25 s on a large corpus).

# 2. In another terminal, from the repo root:
make bench           # full suite, ~30–60 s, human-readable table
make bench-quick     # same suite with fewer runs per measurement
make bench-json      # structured JSON, suitable for paste into a PR body
make cold-search-instructions  # prints the steps for cold-restart measurement

# Override the target host (defaults to localhost:8765):
make bench BASE=http://localhost:8766
```

`make bench` is **not** a CI gate. It's a developer-loop convenience for "did I regress anything" diffs and PR-body numbers. The harness auto-picks fixture UUIDs from your live corpus and prints them so reruns are reproducible. Full usage notes live in [`benchmarks/README.md`](./benchmarks/README.md).

Two focused scripts also live in `benchmarks/`: `bench_perf.py` covers two endpoints with custom stats, and `bench_search_paths.py` compares the FTS5 and linear-scan search paths in-process so you can confirm both produce the same `(conv_uuid, message_uuid)` set on the same corpus. For ad-hoc one-off measurements, [`hyperfine`](https://github.com/sharkdp/hyperfine) is still the right tool (`brew install hyperfine` on macOS, `apt install hyperfine` on Debian / Ubuntu, `cargo install hyperfine` anywhere with a Rust toolchain).

For the full narrative of how these pieces fit together and what was tried-but-rejected along the way, see ["Building Claude Explorer: Part 2 — The Web App"](./articles/part_2_web_app.md) "Performance (FTS5 index)" section.

---

## Background

### Where Claude Desktop Stores Your Data

Claude Desktop is an Electron app that wraps `claude.ai`. Your conversation history is **stored server-side only** — there is no meaningful local copy. If you go looking on your Mac, here's what you'll find (and won't find):

| Path | What's There |
|------|-------------|
| `~/Library/Application Support/Claude/IndexedDB/https_claude.ai_0.indexeddb.leveldb` | **Chat drafts only** — unsent text in the input box. No conversation history. |
| `~/Library/Caches/com.anthropic.claudefordesktop/Cache.db` | **App update URLs only** — not conversation data. |

The official export path (Settings → Privacy → Export Data) sends a download link to your account email. If you've lost access to that email address, you're stuck.

### The Workaround

This tool offers two ways to capture your session credentials:

**Method A: Browser Login (Default)**
1. Open a Playwright-controlled browser to claude.ai
2. Let you log in normally (SSO, email, etc.)
3. Extract the **session cookie** (`sessionKey`) after authentication
4. Use that session cookie to **bulk-fetch all conversations** directly from the claude.ai API

**Method B: Proxy Interception (--proxy)**

If you've lost access to the login (e.g., work SSO disabled) but Claude Desktop is still authenticated:

1. Run **mitmproxy** as a local proxy on port 8080
2. Launch Claude Desktop through the proxy with `--proxy-server=127.0.0.1:8080 --ignore-certificate-errors`
3. Watch the intercepted traffic to capture the **session cookie** (`sessionKey`)
4. Use that session cookie to **bulk-fetch all conversations** directly from the claude.ai API

Claude Desktop does **not** do SSL certificate pinning, which makes the proxy method possible.

---

## The claude.ai API (Unofficial, Observed)

All endpoints authenticate via a `Cookie` header:

```
Cookie: sessionKey=sk-ant-sid01-...
```

### Endpoints

```
GET /api/organizations/{org_id}/chat_conversations
    ?limit=30&starred=false&offset=0
    → Paginated list of conversation summaries

GET /api/organizations/{org_id}/chat_conversations/count_all
    → { "count": N }

GET /api/organizations/{org_id}/chat_conversations/{uuid}
    ?tree=True&rendering_mode=messages&render_all_tools=true&consistency=strong
    → Full conversation with all messages
```

### Conversation Structure

```json
{
  "uuid": "...",
  "name": "Conversation title",
  "model": "claude-sonnet-4-6",
  "created_at": "2026-02-25T19:14:43Z",
  "updated_at": "2026-02-25T20:30:51Z",
  "is_starred": false,
  "is_temporary": false,
  "current_leaf_message_uuid": "...",
  "chat_messages": [...]
}
```

### Message Structure

```json
{
  "uuid": "...",
  "sender": "human" | "assistant",
  "text": "message text",
  "content": [...],
  "index": 0,
  "created_at": "2026-02-25T19:14:43Z",
  "truncated": false,
  "parent_message_uuid": "...",
  "attachments": [],
  "files": []
}
```

### Message Trees and Branches

Messages form a **tree**, not a flat list. Each message has a `parent_message_uuid` linking it to its parent. When you edit a message and regenerate a response, Claude creates a new branch — so a conversation can have multiple alternate paths.

`current_leaf_message_uuid` on the conversation object points to the tip of the currently active branch. To reconstruct the displayed conversation, walk backward from the leaf following `parent_message_uuid` links until you reach the root, then reverse the list.

### Content Blocks

The `content` array contains typed blocks:

| Type | Description |
|------|-------------|
| `text` | Regular message text |
| `tool_use` | A tool call (name + input dict) |
| `tool_result` | The result of a tool call |
| `image` | An embedded image |

---

## From source (for contributors)

*If you want to hack on the project, run from a git checkout, or you're a contributor: install from source instead.*

### Project Structure

```
claude-explorer/
├── README.md
├── CLAUDE.md                 # Development guide
├── pyproject.toml            # Python dependencies + CLI entry point
├── PLANS/
│   ├── overview.md           # Project goals and architecture
│   ├── fetcher.md            # Fetcher design and test plan
│   ├── backend.md            # Backend API design and test plan
│   └── frontend.md           # Frontend design and test plan
├── fetcher/
│   ├── cli.py                # CLI entry point (claude-explorer command)
│   ├── playwright_capture.py # Browser-based credential capture (default)
│   ├── mitmproxy_addon.py    # Proxy-based credential capture (--proxy)
│   └── bulk_fetch.py         # Downloads all conversations to local JSON
├── backend/
│   ├── main.py               # FastAPI app
│   ├── models.py             # Pydantic models
│   ├── store.py              # Reads and indexes JSON files from disk
│   ├── search.py             # Full-text search
│   ├── export.py             # Markdown + PDF export
│   └── routers/              # conversations, search, export endpoints
├── frontend/
│   └── src/                  # React 19 + TypeScript + Tailwind + shadcn/ui
├── scripts/
│   ├── check-cleanup-period.py        # Inspect/fix Claude Code's cleanupPeriodDays
│   └── macos-restore-claude-projects.py  # Recover deleted projects from Time Machine
└── utils/
    └── restore-deleted-sessions-and-images.sh  # Recover both sessions AND image-cache PNGs
```

### Prerequisites

```bash
# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/rpeck/claude-explorer
cd claude-explorer
uv sync

# Install Playwright browsers (for browser-based credential capture)
uv run playwright install chromium

# Optional: install the system libraries WeasyPrint needs for PDF export
# (skip if you only care about Markdown export).
#   macOS:   run the brew command below
#   Linux:   use your distro's pango / cairo / libffi packages
#   Windows: install MSYS2 (https://www.msys2.org), then in its shell run
#            `pacman -S mingw-w64-x86_64-pango`. Or grab the standalone
#            WeasyPrint .exe from the GitHub releases to skip the
#            system-library dance entirely.
brew install pango cairo libffi
```

### Step 1: Capture Your Session Cookie

There are two methods to capture credentials. Choose the one that fits your situation:

#### Method A: Browser Login (Default)

The simplest approach — opens a browser window where you log into Claude normally:

```bash
uv run claude-explorer capture
```

This opens Chromium, navigates to claude.ai, and waits for you to log in. Once authenticated, credentials are automatically extracted and saved to `~/.claude-explorer/credentials.json`.

**Options:**
- `--timeout N` — Max seconds to wait for login (default: 300)

**Auditing the trust path.** Credential capture is implemented in `fetcher/playwright_capture.py` (function `capture_credentials`). It does exactly two things: read the `sessionKey` cookie out of the Playwright browser context after you log in, and write that cookie plus the org id to `~/.claude-explorer/credentials.json`. The capture step itself has no network egress beyond the browser you're already using to log into Claude.

#### Method B: Proxy Interception (--proxy)

Use this method when you **can't log into the web UI** but Claude Desktop is still authenticated. This was a lifesaver for recovering conversations from a work account after losing access to the SSO login.

```bash
# Terminal 1 — start the proxy (requires ANSI terminal)
uv run claude-explorer capture --proxy

# Terminal 2 — launch Claude Desktop through the proxy
open -a "Claude" --args --proxy-server="127.0.0.1:8080" --ignore-certificate-errors
```

Click around in Claude Desktop for a few seconds. The addon will print:

```
✅ Credentials captured! You can now quit mitmproxy (q) and close Claude Desktop.
```

**Options:**
- `--port N` — Proxy port (default: 8080)

**Platform-specific launch commands:**
```bash
# macOS
open -a "Claude" --args --proxy-server="127.0.0.1:8080" --ignore-certificate-errors

# Windows
"C:\...\Claude.exe" --proxy-server="127.0.0.1:8080" --ignore-certificate-errors

# Linux
claude --proxy-server="127.0.0.1:8080" --ignore-certificate-errors
```

**Note:** mitmproxy requires a proper ANSI terminal (Terminal.app, iTerm2, etc.). It will not work in non-TTY environments.

---

Both methods save credentials to `~/.claude-explorer/credentials.json`.

### Step 2: Download Your Conversations

```bash
uv run claude-explorer fetch
```

This downloads all your conversations as JSON files to `~/.claude-explorer/conversations/`. A rate-limited 0.3s delay between requests keeps things polite. Incremental mode (default) skips conversations you've already downloaded. Attachment bytes (images, PDFs, canvas transcripts) land alongside the JSON in a sibling `~/.claude-explorer/files/` directory keyed by conversation and file UUID.

Options:
- `--full-refresh` — Re-download all conversations
- `--limit N` — Download only N conversations
- `--verbose` — Show detailed progress

### Step 3: Browse and Export

```bash
uv run claude-explorer serve
```

Opens the web app at `http://localhost:8765`.

#### One-button Refresh (Build-9)

Once the web app is running, the **Refresh** button in the sidebar footer owns the entire pipeline. A single click:

1. Tries an incremental fetch with whatever credentials are on disk.
2. If credentials are missing, or the fetch fails with `401`/`403`/`cf-mitigated`, the backend automatically launches the same Playwright login flow as `claude-explorer capture` — a Chromium window opens, you log in, and the new `sessionKey` is written to `~/.claude-explorer/credentials.json` (`0o600`, atomically).
3. The fetch then continues automatically. Existing conversations are preserved (incremental — never `--full-refresh` automatically).

The toast walks you through each phase:

> "Opening browser to log in to Claude…" → "Waiting for you to log in (Ns elapsed)…" → "Credentials captured. Fetching…" → "Fetched +N new conversations."

If the capture window is closed or login times out (5 min default), the toast becomes sticky with a **Retry** action. You never have to drop to the CLI to re-capture.

Manual override: the **Details** modal still exposes "Full Refresh" and "Fetch New" buttons that hit the original `/fetch/start` endpoint without auto-capture.

#### Bookmarks (per-message)

Hover over any message in the conversation pane and click the star icon to bookmark it. The right pane carries a **Bookmarks** tab next to **Search**; the tab lists every saved message grouped by conversation, with a snippet, an optional inline-editable note, and the timestamp. Click any entry to jump straight to that message. A top-of-panel **Export to Markdown** button writes the whole bookmark set to a single `bookmarks-YYYY-MM-DD.md` file.

Bookmarks differ from sidebar stars: stars save a whole conversation, bookmarks save a specific message inside one. Argless command markers (`/exit`, `/clear`) intentionally do not get a bookmark affordance, since "save a meaningful message" is the underlying model.

The back end persists bookmarks atomically to `~/.claude-explorer/bookmarks.json` via the `/bookmarks` REST endpoint (`GET` / `POST` / `PATCH` / `DELETE`); set `CLAUDE_EXPLORER_BOOKMARKS_FILE` to override the location for tests.

For development with hot-reload:
```bash
# Terminal 1 — backend
DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run uvicorn backend.main:app --reload

# Terminal 2 — frontend
cd frontend && npm run dev
```
Then open `http://localhost:5173`.

#### `claude-explorer doctor`

Diagnose install and environment health. **Read-only** — reports
pass/warn/fail for each check with the exact command to fix it, and exits
non-zero if any check fails (usable in setup scripts / CI). Fixing stays
in the dedicated commands it points at.

```bash
claude-explorer doctor          # human-readable report
claude-explorer doctor --json   # machine-readable
```

Checks: credentials, data directory, config validity, CC watcher,
search/FTS5 index, uv/uvx on PATH, PDF export libraries, and whether
`claude-explorer mcp` is registered in Claude Code and Claude Desktop.

> **Note on Claude Desktop + `.mcpb`:** the Desktop MCP check only sees
> servers in `claude_desktop_config.json`. Extensions installed via the
> Desktop Extensions UI (`.mcpb` bundle) are stored in the app's internal
> database and are **not detectable from disk**, so a bundle-only install
> shows a warning, not a failure.

---

#### `claude-explorer install`

Set up integrations. Subcommands:

```bash
claude-explorer install all                 # watcher + MCP (Code & Desktop) + scheduled fetch
claude-explorer install watcher             # supervised CC image-cache watcher
claude-explorer install mcp --client all    # register `claude-explorer mcp`
claude-explorer install fetch               # hourly incremental fetch (scheduled job)
claude-explorer install fetch --interval 1800  # custom interval in seconds
claude-explorer install fetch --uninstall   # remove the scheduled fetch job
```

`install mcp` registers the MCP server (`claude-sessions`) with Claude Code
and/or Claude Desktop. `--client {all|code|desktop}` (default `all`),
`--scope {user|project}` (Claude Code only, default `user`). For Claude Code it
uses the `claude mcp add` CLI when available, otherwise writes `~/.claude.json`
directly; for Claude Desktop it merges into `claude_desktop_config.json` (restart
Desktop afterward). Re-runs are idempotent. Add `--uninstall` to any subcommand
to remove. `install-watcher` still works as a deprecated alias for
`install watcher`.

The command it registers **prefers an installed `claude-explorer` entry point
by absolute path** (e.g. `~/.local/bin/claude-explorer mcp`) when one is on your
`PATH`, falling back to `uvx claude-explorer mcp` only when it isn't installed.
The absolute path is important for GUI apps like Claude Desktop, whose launch
environment often omits `uvx` from `PATH` (you'd otherwise see `spawn uvx
ENOENT`), and it runs *your* install rather than the published PyPI package. If
you hand-edit the config instead, use the absolute path to your
`claude-explorer` for the same reason.

`install fetch` installs a cross-platform **scheduled incremental fetch** job —
hourly by default — so your Claude Desktop archive stays current without you
having to open the web UI. Each run does an incremental fetch (skips
already-saved conversations), then a drift pass on the search index. A small
status file at `~/.claude-explorer/scheduled-fetch-status.json` records the last
run time, outcome (`ok` / `auth_expired` / `error`), and conversation count.
`claude-explorer doctor` reads this file and emits a WARN when:

- the job is not installed (not-installed);
- the last run detected expired credentials (auth-expired);
- no successful run has been seen in over twice the configured interval (stale).

On the first run after credentials expire, `install fetch` sends a **best-effort
desktop notification** (`osascript` on macOS, `notify-send` on Linux,
PowerShell toast on Windows). If the notifier is unavailable, the status file
and `doctor` are the fallback. The job does **not** re-login automatically —
background jobs can't open an interactive browser. Re-authenticate with one of:

- `claude-explorer capture` in a terminal, or
- the **Refresh** button in the web UI.

Platform dispatch:

| Platform | Mechanism | Interval default |
|----------|-----------|-----------------|
| macOS    | launchd user agent (`StartInterval`) | 3600 s (1 h) |
| Linux    | systemd user `.service` + `.timer` (`OnUnitActiveSec`) | 3600 s (1 h) |
| Windows  | Task Scheduler (`/SC HOURLY`) | 3600 s (1 h) |

On Linux, run `sudo loginctl enable-linger $USER` once to keep the timer active
across logout. On macOS and Windows the job persists across logouts automatically.

> **Limitation:** `notify-send` on Linux requires a live desktop session with a
> notification daemon running. On headless servers the notification silently
> fails; `doctor` and the status file are the only signal.

`install all` now includes the scheduled fetch job (at the 3600 s default).

> Note: `.mcpb` bundle installs (Desktop Extensions UI) are managed by Claude
> Desktop's own store and are not written or detected by this command.

---

## MCP Server

The project ships with a built-in **Model Context Protocol** server that lets Claude Desktop or Claude Code query your saved conversations directly. Once configured, you can ask Claude things like *"find the session where I debugged the weasyprint install"* or *"export the ZFS conversation I had last week as markdown"* and Claude will use the tools below to answer.

### Tools exposed

| Tool | Purpose |
|------|---------|
| `list_sessions` | Full-text search / list conversation sessions, optionally filtered by source or project |
| `list_projects` | List distinct projects with session counts |
| `get_session_outline` | Lightweight per-message summaries (cached in SQLite) for a specific session |
| `get_messages` | Full message content for specific positions or UUIDs, with optional tool calls/results |
| `export_session` | Markdown export of a full or partial session |

The server runs over **stdio** (no network port) and reads the same on-disk corpus the web UI does: fetched Claude Desktop JSON under `~/.claude-explorer/conversations/`, plus live Claude Code and Claude Cowork sessions.

### Prerequisites

Make sure the project is installed and conversations have been fetched at least once.

If you installed via PyPI/uvx, you already have everything you need:

```bash
uvx claude-explorer serve   # fetch via the Refresh button, then quit
```

If you're working from a git checkout (contributor flow), the equivalent is:

```bash
cd /path/to/claude-explorer
uv sync
uv run claude-explorer capture   # one-time
uv run claude-explorer fetch
```

The MCP entry point is `claude-explorer mcp`. You can verify it works standalone:

```bash
# PyPI/uvx install:
uvx claude-explorer mcp
# (prints nothing; it's waiting for MCP JSON-RPC on stdin — Ctrl+C to exit)

# From source:
uv run --directory /path/to/claude-explorer claude-explorer mcp
```

### Claude Code setup (all platforms)

The simplest path is the `claude mcp add` CLI, which writes the config for you and runs the published package via `uvx` — nothing to clone, no path to hard-code:

```bash
claude mcp add --scope user claude-sessions -- uvx claude-explorer mcp
```

`--scope user` makes the server available in every project (written to `~/.claude.json`). Swap it for `--scope project` to scope it to the current repo instead (written to a `.mcp.json` at the repo root). A bare `claude mcp add` defaults to `local` scope (private to the current project, also stored in `~/.claude.json`). Verify with:

```bash
claude mcp list
```

To edit the config by hand instead, the `mcpServers` block is identical at both scopes; only the file differs — `~/.claude.json` for user scope, `.mcp.json` at the repo root for project scope. (MCP servers do **not** live under `.claude/`, which holds other settings like permissions.)

```json
{
  "mcpServers": {
    "claude-sessions": {
      "type": "stdio",
      "command": "uvx",
      "args": ["claude-explorer", "mcp"]
    }
  }
}
```

> **Note:** If the MCP client can't find `uvx` on the `PATH` it sees, replace `"uvx"` with its absolute path (from `which uvx` / `where uvx`). Contributors running from a checkout can instead use `"command": "uv"` with `"args": ["run", "--directory", "/path/to/claude-explorer", "claude-explorer", "mcp"]`.

### Claude Desktop setup

The easiest way to open the config file is from inside the app: **Settings → Developer → Edit Config**. (The Extensions browser is only for packaged "Desktop Extension" bundles; this is a plain stdio server, so it's a quick paste into the config, not a one-click install.) Or open the file directly — its location depends on the OS:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json` (typically `C:\Users\YOU\AppData\Roaming\Claude\claude_desktop_config.json`)
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

Add the same block you'd use for Claude Code, then **fully quit and relaunch** Claude Desktop (it only reads the config at startup):

```json
{
  "mcpServers": {
    "claude-sessions": {
      "type": "stdio",
      "command": "uvx",
      "args": ["claude-explorer", "mcp"]
    }
  }
}
```

> **Note:** A GUI app may not inherit your shell's `PATH`, so if Desktop can't find `uvx`, put its absolute path (from `which uvx` / `where uvx`) in `command`.

### Verifying it works

After restarting your client, ask it to search your history, e.g.:

> *"Use the claude-sessions MCP server to list my 5 most recent sessions."*

In Claude Code you can also run `/mcp` to see the server status and the list of tools it registered.

### Troubleshooting

- **"command not found: uvx"** — the MCP client doesn't see your shell `PATH`. Use the absolute path to `uvx` in `command`.
- **"Session not found" / empty results** — run `uvx claude-explorer fetch` first (or use the web app's Refresh button); the MCP server reads from `~/.claude-explorer/conversations/`.
- **Need to use a non-default data dir** — set `CLAUDE_EXPLORER_DATA_DIR` via an `env` block in the MCP config:
  ```json
  "env": { "CLAUDE_EXPLORER_DATA_DIR": "/path/to/conversations" }
  ```
- **Stale session outlines after a branch switch** — outlines are cached in `~/.claude-explorer/cache.db`. Delete that file to force a full rebuild.

---

## Image-Cache Watcher — Technical Details

The Quick Start above tells you to run `claude-explorer install-watcher`. This section explains what that actually does, where to look when it breaks, and how to tune it.

**Architecture: event-driven primary + backstop poll.** The watcher subscribes to OS-native filesystem events (FSEvents on macOS, inotify on Linux, ReadDirectoryChangesW on Windows, all via the [`watchdog`](https://github.com/gorakhargosh/watchdog) library) and copies new files within sub-second latency at near-zero idle CPU. A periodic backstop poll (default 600s = 10 min) re-runs the full directory walk to catch the rare event the OS dropped or coalesced. On a sandboxed Python or an unsupported filesystem (NFS, etc.) `watchdog` falls back to its `PollingObserver` automatically — strictly worse latency, same correctness — and the watcher logs which backend got selected so misconfigurations are diagnosable.

```bash
# Verify it's running (per platform):
launchctl list | grep claude-explorer                                  # macOS
systemctl --user status claude-explorer-cc-watcher.service             # Linux
schtasks /Query /TN ClaudeExplorerCCWatcher                            # Windows

# Tune the backstop poll interval (default 600s = 10min; events handle
# the latency-critical work, so smaller values do not improve normal
# capture latency):
claude-explorer install-watcher --interval 60

# Uninstall:
claude-explorer install-watcher --uninstall
```

**Where the unit lives + how it's supervised:**

| Platform | Mechanism                        | Path                                                                   |
|----------|----------------------------------|------------------------------------------------------------------------|
| macOS    | launchd user agent               | `~/Library/LaunchAgents/com.claude-explorer.cc-watcher.plist`           |
| Linux    | systemd **user** unit             | `~/.config/systemd/user/claude-explorer-cc-watcher.service`             |
| Windows  | Task Scheduler task (logon trigger)| `ClaudeExplorerCCWatcher` (launcher at `%USERPROFILE%\.claude-explorer\cc-watcher.py`) |

All three run the same Python entry point (`backend.cc_watcher.run_watcher`), which combines the `watchdog` Observer with the periodic backstop poll. Only the supervisor differs. macOS uses `KeepAlive`, Linux uses `Restart=always`, Windows uses an on-logon trigger — each restarts on crash.

**Where logs go:**

| Platform | Logs                                                                                |
|----------|-------------------------------------------------------------------------------------|
| macOS    | `~/Library/Logs/claude-explorer-cc-watcher.{out,err}`                                |
| Linux    | `journalctl --user -u claude-explorer-cc-watcher.service -f`                         |
| Windows  | Suppressed (uses `pythonw.exe` so no console pops up). For debugging, run `pythonw.exe %USERPROFILE%\.claude-explorer\cc-watcher.py` from `cmd.exe` to see output. |

**Where mirrored images live:** `~/.claude-explorer/cc-images/<sess>/<sess>--<N>.<sha8>.<ext>` — content-addressed, append-only. Safe even if Claude Code rotates the original, even if you reinstall, even if the conversation JSONL itself is deleted later by `cleanupPeriodDays`.

**Linux note — surviving logout:** systemd user units stop when you log out by default. To keep the watcher running across logout (or on a headless box), run once: `sudo loginctl enable-linger $USER`.

---

## Companion Utilities (macOS)

The `scripts/` directory ships two standalone utilities that address a Claude Code gotcha you may have hit while using this tool: **Claude Code silently auto-deletes** session files in `~/.claude/projects/` older than `cleanupPeriodDays` (default: 30). When a project subdirectory becomes empty, it is removed entirely.

### `scripts/check-cleanup-period.py` — inspect/fix the auto-cleanup

Reports the current `cleanupPeriodDays` value in `~/.claude/settings.json` and (with `--set N`) atomically updates it while preserving every other key.

```bash
# Report only
python3 scripts/check-cleanup-period.py

# Effectively disable auto-cleanup (~100 years)
python3 scripts/check-cleanup-period.py --set 36500
```

Refuses to set `0` (which silently disables conversation persistence — Claude Code [issue #23710](https://github.com/anthropics/claude-code/issues/23710)). Warns if you set anything shorter than 30 or 365 days.

### `scripts/macos-restore-claude-projects.py` — recover deleted projects from Time Machine

Walks every Time Machine snapshot under `/Volumes/.timemachine/<UUID>/`, collects the union of every `~/.claude/projects/<name>/` ever seen, diffs against the live directory, and copies the **newest backup** of each missing dir to `~/.claude/projects-recovered/`. Never overwrites anything.

```bash
# Preview what's recoverable across ALL backups
sudo python3 scripts/macos-restore-claude-projects.py --dry-run

# Recover all missing projects (default)
sudo python3 scripts/macos-restore-claude-projects.py

# Limit how far back to scan
sudo python3 scripts/macos-restore-claude-projects.py --since 2026-01-01
sudo python3 scripts/macos-restore-claude-projects.py --days 60

# Recover AND auto-move into ~/.claude/projects/ (skips any that already exist)
sudo python3 scripts/macos-restore-claude-projects.py --apply
```

**Requires:** Terminal must have **Full Disk Access** (System Settings → Privacy & Security → Full Disk Access → add Terminal). Without FDA, macOS returns "Operation not permitted" when reading TM snapshot directories. The script detects this and tells you what to do.

After recovery, **set a high `cleanupPeriodDays`** with the checker above so this doesn't happen again.

### `utils/restore-deleted-sessions-and-images.sh` — recover sessions AND image-cache PNGs

A bash superset of the Python script above that also restores files under `~/.claude/image-cache/<session-uuid>/<N>.png` (Claude Code rotates these off disk on its own schedule, separately from the `cleanupPeriodDays` setting). Walks Time Machine snapshots **newest-first**, restores anything missing from `~/.claude/projects/`, `~/.claude/image-cache/`, and `~/Library/Application Support/Claude/local-agent-mode-sessions/` (so Cowork's `audit.jsonl` trees come back too), **never overwrites** files that still exist, and supports `--dry-run` so you can review the plan before anything moves.

**Step 1 — grant Full Disk Access** (one-time):

System Settings → Privacy & Security → Full Disk Access → add (and enable) whichever terminal you'll run the script from (Terminal.app, iTerm, Ghostty, your IDE's terminal, etc.). **Quit and re-launch the terminal** so it picks up the new permission. Without FDA, even `tmutil` can't read the snapshot tree.

**Step 2 — find the canonical Time Machine path.** The user-friendly mount (e.g. `/Volumes/M3 Max Backups 5T`) is **not** what the script wants — modern macOS keeps snapshots under `/Volumes/.timemachine/<volume-uuid>/<machine-uuid>/`. Discover yours with:

```bash
tmutil destinationinfo   # shows mount points + UUIDs
tmutil latestbackup      # prints the full path to the newest snapshot
```

`tmutil latestbackup` prints something like:

```
/Volumes/.timemachine/52A1580A-…-6E0B20631733/A1B2C3D4-…/2026-05-15-153000.backup/Macintosh HD - Data
```

Pass the **parent of the dated directory** (the `<machine-uuid>` level) as `--tm-disk`.

**Step 3 — dry-run, then apply:**

```bash
# Preview what would be restored (no writes):
./utils/restore-deleted-sessions-and-images.sh \
    --tm-disk /Volumes/.timemachine/52A1580A-…/A1B2C3D4-… \
    --dry-run

# If the plan looks right, drop --dry-run to apply:
./utils/restore-deleted-sessions-and-images.sh \
    --tm-disk /Volumes/.timemachine/52A1580A-…/A1B2C3D4-…
```

**Safety contract:**
- **No-overwrite, three layers deep**: precheck `[ -e dest ]`, then `cp -n`, then a postcheck. Any file that still exists on the live filesystem is left alone.
- **Latest-copy-wins**: snapshots are walked newest-first; the first snapshot that contains a missing file is the one we restore from.
- **Idempotent**: a second `apply` run after the first is a no-op (everything's already there → all skipped).
- **Per-file errors are tallied, not fatal**: one unreadable file in one snapshot doesn't abort the run.

---

## Known Limitations

- **Session key expiry:** `sessionKey` eventually expires. Click **Refresh** in the sidebar to re-capture it — the UI launches the same Playwright login flow automatically. The CLI `claude-explorer capture` is the equivalent fallback.
- **Cloudflare cookie:** The `cf_clearance` cookie may be required on some networks. The fetcher attempts to capture it alongside `sessionKey`.
- **Truncated messages:** The API returns `truncated: true` for very long messages. A per-message full-content endpoint has not yet been identified.
- **Import:** There is no known write API for creating conversations. Migration to a new account is not currently possible programmatically.
- **Proxy capture commands:** The proxy-method launch command varies per OS. See [Method B](#method-b-proxy-interception---proxy) above for the macOS, Windows, and Linux equivalents.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Credential Capture | Playwright (browser login), mitmproxy (proxy interception) |
| Fetcher | httpx, curl_cffi |
| Backend | FastAPI, uvicorn, uv, weasyprint |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS v4, shadcn/ui, TanStack Query |
| Search | SQLite FTS5 (primary), orjson + FileCache + ThreadPoolExecutor linear-scan fallback |
| MCP server | FastMCP (5 tools: list_sessions, list_projects, get_session_outline, get_messages, export_session) |
| Export | Markdown (built-in), PDF (weasyprint) |
| Packaging | hatchling (PyPI wheel includes pre-built React bundle) |

---

## Security

For vulnerability reporting and the running log of supply-chain audits against this project's dependency tree (most recently a check against the 2026-05-11 Mini Shai-Hulud worm that hit the `@tanstack/*` ecosystem), see [SECURITY.md](./SECURITY.md).

---

## Contributing

PRs welcome — see [CONTRIBUTING.md](./CONTRIBUTING.md) for prerequisites, dev workflow, code style, and the CLA process.

## License

This project is licensed under the [Apache License 2.0](./LICENSE). Contributors agree to the [Contributor License Agreement](./CLA.md) (via [CLA Assistant](https://cla-assistant.io)) on their first pull request; the signature applies to all subsequent contributions.

See the disclaimer at the top of this README regarding Anthropic trademarks and the unaffiliated nature of this project.
