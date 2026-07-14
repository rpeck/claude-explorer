# Claude Explorer

## UX Rules

All UX flows and rules are documented in [UX.md](./UX.md). Code changes that affect UI behavior MUST keep that document accurate; failing-test-first applies (see CLAUDE.md "Code Style" rule on TDD).

## Testing Rules

When writing or reviewing tests (Playwright, pytest, vitest), read [CLAUDE-TESTING.md](./CLAUDE-TESTING.md). It codifies black-box / spec-driven discipline, bidirectional verification, Playwright-specific gotchas (overflow-clipping, shadcn `<Select>`, Radix `<ScrollArea>`, Radix `<RadioGroup>` `.check()` race), fixture-design rules, and a pre-flight checklist. Other agents (pure feature work, refactors, deployments) can skip it.

**Test-execution integrity (HARD invariant — earned 2026-06-01, when a confident "the suite passes" was reported while 13 spec files weren't even running).** A run is not "green" until you have verified it actually executed, using the runner's *real* exit code:

1. **Never pipe a test / type-check / lint command through `tail` / `head` / `grep` when pass/fail matters.** A shell pipeline's exit status is the LAST stage's, so `pytest … | tail` (or a backgrounded `playwright test … | tail`) returns `tail`'s `0` even when the runner failed. Run it bare and read the output, redirect full output to a file and read the file, or force the status to survive (`set -o pipefail`, `${PIPESTATUS[0]}` in bash, `$pipestatus[1]` in zsh).
2. **"0 failed" is not "green" — verify the COUNT.** A parse/import/collection error or an empty filter makes a suite "succeed" while testing nothing (on 2026-06-01, 13 Playwright specs threw `SyntaxError` at parse time and the run still "completed"). Confirm the runner's pass count is at/above the known baseline — backend pytest **1139 passed / 1 skipped**, vitest **538 passed / 67 files**, Playwright **~441 tests** — and grep the output for `SyntaxError` / `Error:` / `no tests ran` / `collected 0` before reporting green. A count that *dropped* is a failure signal, not "tests were removed." **Then run the baseline-free file-count check: independently count the test files on disk and confirm the runner collected exactly that many.** A parse/collection error drops a whole file silently, so disk-file-count > collected is the direct tell. Examples (numbers current 2026-06-01) — vitest: `find frontend/src \( -name '*.test.ts' -o -name '*.test.tsx' \) | wc -l` (= 67) must equal the `Test Files N passed (N)` count; Playwright: `find frontend/e2e -name '*.spec.ts' | wc -l` (= 113) must equal the `M` in `npx playwright test --list`'s `Total: N tests in M files` footer (`--list` errors outright on a parse-broken file); pytest: `find backend fetcher -name 'test_*.py' | wc -l` (= 142) vs the unique files from `uv run pytest --collect-only -q` (= 141), where the **expected** delta is the deliberately-deselected serial benchmark (`-m 'not serial'` drops `backend/tests/test_search_index_benchmark.py`). Investigate every mismatch and confirm each excluded file is intentional; an unexplained drop means files never ran — not green.
3. **Report only verified results.** Never tell the user the suite passes from a background "exit 0" or a truncated tail; read the summary line and the real exit code first. A green claim that turns out false is a falsification event — correct it loudly and immediately. Full detail: [CLAUDE-TESTING.md §5.16](./CLAUDE-TESTING.md).

## Performance Work

Three project-specific invariants the 2026-05-22 → 2026-05-23 search-perf hunt earned. Full walk: [PLANS/POSTMORTEM-search-typing-lag-2026-05-22.md](./PLANS/POSTMORTEM-search-typing-lag-2026-05-22.md). Testing protocol: [CLAUDE-TESTING.md §5.14](./CLAUDE-TESTING.md). Council-driven perf workflow: `~/.claude/agents/llm-council-coding.md` Rules P0–P11.

1. **No `useContext()` of a churning provider in any list-rendered component (N ≥ 100 rows).** Known churning providers in this codebase: `SettingsContext`, `SearchPanelContext`, `BookmarksContext` — their value identity changes on every keystroke, toggle, or navigation. `useContext` bypasses `React.memo` (Fiber resolves context deps in `beginWork` before the bailout check), so subscribing from a row component re-renders every row on every context flip. The list-owning parent (`ConversationPage`) calls `useContext` once and threads relevant fields as props. Carve-outs: dispatch-only contexts with stable function identity, and `useMemo([])`-stabilized config contexts.

2. **Memoize every `<Provider value={{...}}>` with `useMemo` + explicit deps list.** Inline object literals rebuild value identity every render and fire the entire subscriber graph. Pattern lives in `SearchPanelContext.tsx` and `SettingsContext.tsx`.

3. **For any user-reported "feels slow", the first commit on the branch is a measurement commit.** Output: one number from `PerformanceObserver` Long Task total OR cProfile wall time on the real corpus (not a 3-row synthetic). Every subsequent commit must move that number, or revert. A user re-reporting the same symptom after a fix shipped is a falsification event for the diagnosis — re-instrument, don't stack a second fix in the same suspected layer. Instrumentation snippet in `CLAUDE-TESTING.md §5.14`.

## Project Structure

```
├── backend/          # FastAPI backend (Python)
├── frontend/         # React frontend (TypeScript)
├── fetcher/          # mitmproxy addon for fetching conversations (Python)
├── mcp_server/       # stdio MCP server (5 tools, FastMCP)
├── scripts/          # build-mcpb.py, closure analyzer, format checks
├── assets/           # mcpb-icon.png, mcpb-README.md (Extensions panel)
├── PLANS/            # Implementation plans
└── pyproject.toml    # Python dependencies (version is dynamic, read from
                     #   mcp_server/__init__.py:__version__ — single source
                     #   of truth for pip + MCP serverInfo + MCPB manifest)
```

### MCPB bundle (Claude Desktop extension)

`scripts/build-mcpb.py` produces `dist/claude-explorer-${VERSION}.mcpb` —
the drag-drop Claude Desktop extension that wraps `mcp_server.server`'s
5 tools. Pipeline:

1. `scripts/mcpb_import_closure.py` walks the eager-import graph of
   `mcp_server.server` (skips function bodies — that's the supported
   escape hatch for lazy-importing heavy deps like `weasyprint` inside
   `create_pdf`).
2. Build script copies only the closed-over `.py` files into the
   bundle, manually appends a small `DYNAMIC_IMPORT_MODULES` list for
   modules `backend.store` / `backend.search` lazy-import at call time
   (`backend.cowork_reader`, `backend.summary_cache`,
   `backend.search_index`).
3. Writes a stripped `pyproject.toml` with ONLY the 4 deps the MCP
   path uses (`fastmcp`, `pydantic`, `orjson`, `platformdirs`).
4. Writes `manifest.json` with `manifest_version: "0.4"` +
   `server.type: "uv"` — Claude Desktop ships Node but not Python,
   so UV resolves + installs on the user's machine on first launch.
5. Runs `mcpb pack` (`npm install -g @anthropic-ai/mcpb` if missing).

**Closure-canary invariant.** `mcp_server/tests/test_mcpb_closure.py`
fails the suite if a future PR pulls FastAPI / weasyprint / mitmproxy
/ watchdog into the MCP code path. If the canary fires, DO NOT loosen
the test — find the import and either move it inside a function body
(lazy) or remove it from the MCP code path. The bundle is supposed to
stay around ~500 KB; pulling in even one of those would balloon it
past 30 MB.

CI release wiring (`.github/workflows/release.yml` `build-mcpb` job)
runs the same script on every `v*` tag push and attaches the artifact
to the GitHub Release alongside the wheel.

## CLI Usage

After installing (`uv sync`), use the `claude-explorer` command:

```bash
# Step 1: Capture credentials from Claude Desktop
claude-explorer capture

# In another terminal, launch Claude Desktop through the proxy:
open -a "Claude" --args --proxy-server="127.0.0.1:8080" --ignore-certificate-errors

# Step 2: Fetch all conversations
claude-explorer fetch

# Step 3: Start the web server to browse
claude-explorer serve
# Then open http://localhost:8765

# Diagnose install + environment health (read-only)
claude-explorer doctor

# Install integrations (watcher + MCP registration for Code/Desktop + scheduled fetch)
claude-explorer install all

# Install hourly incremental fetch job (included in `install all`)
claude-explorer install fetch
```

### Command Reference

#### `claude-explorer capture`

Start mitmproxy to intercept Claude Desktop session credentials.

```
Options:
  --port INTEGER    Proxy port (default: 8080)
```

**How it works:**
1. Starts a local HTTPS proxy using mitmproxy
2. You launch Claude Desktop through the proxy
3. The addon extracts `sessionKey` and `org_id` from API requests
4. Credentials are saved to `~/.claude-explorer/credentials.json`

**Platform-specific launch commands:**
```bash
# macOS
open -a "Claude" --args --proxy-server="127.0.0.1:8080" --ignore-certificate-errors

# Windows
"C:\...\Claude.exe" --proxy-server="127.0.0.1:8080" --ignore-certificate-errors

# Linux
claude --proxy-server="127.0.0.1:8080" --ignore-certificate-errors
```

#### `claude-explorer fetch`

Download all conversations from Claude using captured credentials.

```
Options:
  --output-dir PATH               Where to save JSON files
                                  (default: ~/.claude-explorer/conversations)
  --credentials PATH              Path to credentials file
                                  (default: ~/.claude-explorer/credentials.json)
  --session-key TEXT              Session key (overrides credentials file)
  --org-id TEXT                   Org ID (overrides credentials file)
  --incremental / --full-refresh  Skip already-saved conversations (default: incremental)
  --delay FLOAT                   Seconds between requests (default: 0.3)
  --limit INTEGER                 Max conversations to fetch
  --verbose                       Show detailed output
```

**Examples:**
```bash
# Fetch all new conversations
claude-explorer fetch

# Re-fetch everything
claude-explorer fetch --full-refresh

# Fetch only 10 conversations with verbose output
claude-explorer fetch --limit 10 --verbose

# Use custom credentials
claude-explorer fetch --session-key "sk-ant-..." --org-id "uuid-..."
```

#### `claude-explorer serve`

Start the web server to browse and export conversations.

```
Options:
  --host TEXT       Host to bind to (default: 127.0.0.1)
  --port INTEGER    Port to bind to (default: 8765)
  --reload          Enable auto-reload for development
```

**Examples:**
```bash
# Start server
claude-explorer serve

# Start on different port
claude-explorer serve --port 9000

# Development mode with auto-reload
claude-explorer serve --reload
```

#### `claude-explorer install-watcher` (cross-platform — strongly recommended)

Install a supervised job that runs the CC image-cache watcher
continuously, independent of `claude-explorer serve`. **Without this,
the watcher only runs while the dev server is up — Claude Code can
rotate images off disk during downtime, causing permanent data loss.**

The CLI dispatches by `sys.platform`:

  * macOS  → launchd user agent (`~/Library/LaunchAgents/com.claude-explorer.cc-watcher.plist`)
  * Linux  → systemd user unit (`~/.config/systemd/user/claude-explorer-cc-watcher.service`); also run `sudo loginctl enable-linger $USER` to survive logout
  * Windows → Task Scheduler task `ClaudeExplorerCCWatcher` (logon-triggered, runs the launcher at `%USERPROFILE%\.claude-explorer\cc-watcher.py` via `pythonw.exe`)

The watcher uses **`watchdog` for event-driven capture** (FSEvents on
macOS, inotify on Linux, ReadDirectoryChangesW on Windows) — sub-
second latency, near-zero idle CPU. A periodic backstop poll
(default 600s = 10 min, overridable via `--interval` or env var
`CLAUDE_EXPLORER_CC_WATCHER_INTERVAL_SEC`) catches the rare event the
OS dropped or coalesced.

```bash
uv run claude-explorer install-watcher

# Verify (per platform):
launchctl list | grep claude-explorer                                 # macOS
systemctl --user status claude-explorer-cc-watcher.service            # Linux
schtasks /Query /TN ClaudeExplorerCCWatcher                           # Windows

# Logs (macOS):
tail -f ~/Library/Logs/claude-explorer-cc-watcher.{out,err}

# Tune the backstop poll interval (default 600s):
uv run claude-explorer install-watcher --interval 60

# Uninstall:
uv run claude-explorer install-watcher --uninstall
```

#### `claude-explorer reindex-search` (manual override only)

Force a rebuild of the SQLite FTS5 search index at
`~/.claude-explorer/search-index.sqlite`. **You should not need this in
normal operation:** the index is built automatically at backend startup
(non-blocking lifespan task) and kept in sync by the same watcher
loop that handles CC images (event-driven via `watchdog`, with a
600s backstop poll). Use only when:

- the index file got corrupted (delete it, then run this);
- you want a known-fresh full rebuild;
- a future schema bump requires manual rebuild without restarting `serve`.

```bash
# Default: full DROP + rebuild from scratch.
uv run claude-explorer reindex-search

# Drift-only pass (re-index only files whose mtime changed since last index).
uv run claude-explorer reindex-search --drift
```

**How search works in the running server:**

- `backend/search_index.py` owns the SQLite FTS5 schema, lifecycle,
  and queries. Singleton via `get_search_index()`; returns `None` on
  sqlite3 builds without FTS5.
- `backend/search.py:search_conversations` is a dispatcher: prefer
  the FTS5 fast path when `idx.is_ready()`, fall back to the
  linear-scan code on any failure (initial build still running, FTS5
  unavailable, sqlite3 error). Search never goes "down".
- Architecture is **Scatter-Gather**: FTS5 returns `(conv_uuid,
  message_uuid)` pairs; the existing Python `create_snippet`/sort
  code runs on the matched conversations only (warm via FileCache).
  Result: byte-for-byte identical `SearchResult` shape to the linear
  path for whole-word queries.
- The CC watcher (`backend/cc_watcher.py:scan_once`) runs
  the search-index drift pass once per backstop scan (600s default).
  Image-cache events fire instantly via `watchdog` but do NOT trigger
  a drift pass — search picks up new sessions on the next backstop
  poll. Failures in either pass are isolated.

If you change the schema, bump `backend/search_index.SCHEMA_VERSION`
and the next process startup will drop+rebuild on its own.

#### Web UI Refresh button (Build-9)

The sidebar **Refresh** button owns the full pipeline — capture + fetch — so the user never has to drop to the CLI to re-capture credentials.

- **Endpoint:** `GET /api/fetch/refresh?incremental=true` (SSE).
- **Behavior:** if `~/.claude-explorer/credentials.json` is missing OR the fetch returns `401`/`403`/`cf-mitigated`, the backend invokes `fetcher.playwright_capture.capture_credentials` in-process. On success it persists creds (atomic write, `0o600`) and continues with an incremental fetch automatically.
- **Capture is run at most once per request.** A post-capture fetch that still 401s emits a final `error` event — no retry loop.
- **Concurrency:** module-level `_refresh_in_progress` flag plus `asyncio.Lock`. A second concurrent request returns `409 Conflict`. Frontend disables the button while running, so 409 is defense-in-depth.
- **SSE event types:** `capture_start`, `capture_waiting_login` (heartbeat every 25s during the 5-min login wait), `capture_done`, `capture_error`, plus the existing `start`, `progress`, `complete`, `error`.
- **Manual override:** the Details modal's "Full Refresh" and "Fetch New" buttons still hit `/fetch/start` directly with no auto-capture.

If you change capture or fetch logic, edit `backend/routers/fetch.py` (the `_capture_phase_stream`, `_fetch_phase_stream`, and `refresh_pipeline_stream` async generators) and `frontend/src/components/fetch/FetchToast.tsx` (the `useRefreshPipeline` hook) together — the SSE event schema is shared.

## Development Setup

### Python (Backend & Fetcher)

Use `uv` to manage the Python virtual environment:

```bash
# Create/sync the virtual environment
uv sync

# Run backend server
uv run uvicorn backend.main:app --reload --port 8765

# Run with dev dependencies
uv sync --extra dev
uv run pytest
```

The `.venv` directory is local to the project and managed by `uv`.

### Frontend

```bash
cd frontend
npm install
npm run dev    # Development server on http://localhost:5173
npm run build  # Production build
```

## Running the Full Stack

1. Start the backend:
   ```bash
   # On macOS with Homebrew, set library path for WeasyPrint PDF support:
   DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run uvicorn backend.main:app --reload --port 8765
   ```

2. Start the frontend (in another terminal):
   ```bash
   cd frontend && npm run dev
   ```

The frontend proxies `/api` requests to the backend.

## Data Directory

Conversations are stored in `~/.claude-explorer/conversations/` as JSON files.

Set `CLAUDE_EXPLORER_DATA_DIR` to override, or create `~/.claude-explorer/config.json`:
```json
{"data_dir": "/path/to/conversations"}
```

## Corrupt-config safe mode

When `~/.claude-explorer/config.json` fails to parse (editor crash mid-save, truncated JSON, non-dict root, permission flip, non-UTF-8 contents), the app does NOT crash and does NOT silently fall back to defaults. Instead it boots in **read-only safe mode**:

1. `backend/config.py:Settings` catches `(JSONDecodeError, OSError, TypeError, ValueError)` and populates `config_corrupt_reason: str | None` with a one-line `<path>: <ExceptionType>: <message>` summary. The reader continues to the next candidate (canonical → legacy) instead of `break`ing — a valid legacy config next to a corrupt canonical still wins.
2. Every writer route checks the flag via `_refuse_if_config_corrupt(settings)` (defined in `backend/deps.py`) and returns **HTTP 503** with a recovery message instead of writing. Currently gated: `POST/PATCH/DELETE /api/bookmarks/*`, `PUT/PATCH /api/preferences`, `GET /api/fetch/{start,refresh,conversation/{uuid}}`, and the `claude-explorer fetch` CLI.
3. `GET /api/config` exposes `config_corrupt_reason` to the frontend. The lru_cache on `get_settings()` is cleared on every `/api/config` call so the user fixing the file mid-session is detected without a server restart.
4. The frontend renders a persistent (non-dismissible) `ConfigCorruptionBanner` at the top of the app shell. Dismissing it would re-enable the data-orphaning failure mode it exists to prevent, so the only way to remove the banner is to fix the underlying file.
5. Reads remain unconditional — `/api/conversations`, `/api/search`, `/api/conversations/{uuid}` all stay at 200 even when corrupt. The user can still browse what's already on disk while they recover.

**When adding a new writer route**, wire it through `_refuse_if_config_corrupt(settings)` (or `Depends(refuse_if_config_corrupt)` if you prefer the FastAPI-dep form). Otherwise a corrupt-config user can silently write to the wrong data_dir and orphan their archive.

**`install-watcher` is intentionally EXEMPT** from the writer gate. The launcher template lives under `~/Library/LaunchAgents/` (or systemd/Task Scheduler equivalents), NOT under `data_dir`, AND the supervised watcher IS the recovery path the user needs to reach when config is corrupt. Gating it would lock the user out of recovery. The CLI test `test_install_watcher_runs_when_config_corrupt` pins this exemption as a HARD invariant — do not remove.

**`cc_watcher.scan_once` is also NOT gated.** Blocking the background CC image-cache watcher during corrupt-config would cause the data loss it exists to prevent (Claude Code rotates images off disk while the watcher is blocked). Future enhancement: log a WARNING when scan_once runs against a corrupt config so the failure is visible in supervised-job logs.

## PDF Export Dependencies

WeasyPrint requires system libraries for PDF generation:

```bash
# macOS
brew install pango cairo libffi

# Ubuntu/Debian
apt-get install libpango-1.0-0 libpangocairo-1.0-0 libcairo2

# Windows (via MSYS2 — https://www.msys2.org)
# In the MSYS2 shell:
pacman -S mingw-w64-x86_64-pango
# Or skip the system-library install entirely by using the standalone
# WeasyPrint .exe from the WeasyPrint GitHub releases page.
```

See: https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation

### macOS DYLD bootstrap (tests + dev server)

macOS Sonoma+ strips `DYLD_*` env vars from subprocess invocations
(SIP), so prefixing `uv run pytest` with `DYLD_FALLBACK_LIBRARY_PATH=...`
silently no-ops. The tests bootstrap this from `backend/tests/conftest.py`
at import time (sets `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`
before WeasyPrint imports), so `uv run pytest backend/tests` Just Works
with no `--ignore` flags or env-var prefixes.

For the live dev server (`claude-explorer serve` or
`uvicorn backend.main:app`), set `DYLD_LIBRARY_PATH` directly on the
command line as shown above — the server doesn't go through conftest.

## Code Style

- Python: Follow PEP 8, use type hints
- TypeScript: Strict mode, prefer functional components
- Commits: Conventional commit messages, no AI attribution lines

## Static-analysis tooling

Three layers run alongside the manual pre-push checklist. Neither replaces it — they cover different failure modes (greps find leaked credentials; LLM/AST tools find logic bugs).

### React Doctor (`millionco/react-doctor`)

Oxlint-based AST scanner for React (~250 rules across architecture, state-and-effects, performance, a11y, correctness). Catches things `eslint-plugin-react-hooks` doesn't, e.g. inline `<Provider value={{...}}>` literals via `jsx-no-constructed-context-values` — directly relevant to the [[Performance Work]] invariant #2.

Two npm scripts:

```bash
cd frontend
npm run lint:react        # full-codebase informational scan; never fails CI
npm run lint:react:diff   # pre-push gate: scan files changed vs main, fail on errors
```

**Baseline as of 2026-05-27**: 71/100, 346 issues (4 architecture errors + 2 state-and-effects errors + 340 warnings across a11y/perf/correctness). Do not block a push on pre-existing baseline. Pre-push gate is `lint:react:diff` — fails only if YOUR change introduces a new error.

Known gap: React Doctor does NOT catch [[Performance Work]] invariant #1 (`useContext` of churning provider in list-rendered components). No public linter does — that one stays a human-review / postmortem rule.

### `security-guidance` plugin (Anthropic, user-scope)

Real-time `PreToolUse` hook that intercepts `Write`/`Edit`/`MultiEdit` and warns about `eval`, `pickle`, `dangerouslySetInnerHTML`, `child_process.exec`, GHA injection, command injection patterns. Free; runs at the harness level (~0 token cost). Installed via `claude plugin install security-guidance@claude-plugins-official`. Verify with `claude plugin list`.

If a hook fires during normal editing, READ the warning — don't paper over it with a config override. The hook fires on a curated list of known-bad patterns, not on style preferences.

### `/security-review` slash command (built-in)

Diff-based LLM security review of pending changes on the current branch. Same engine as the `anthropics/claude-code-security-review` GitHub Action. Covers SQLi, XSS, authn/authz, IDOR, SSRF, weak crypto, RCE/deserialization, hardcoded secrets, supply-chain. Manual invocation only — see the pre-push checklist.

## Pre-push checklist (runs before every push that affects the public repo)

Public-flip is a one-way door: once a commit is on `origin/main` and the repo is public, secrets, personal paths, and AI attribution become permanently public-cached (search-indexed, mirrored by archivers, scraped by training-data crawlers). Run this scan before pushing to a public repo. **Every step MUST return clean (or only known-OK matches the maintainer has eyeballed) before push.**

```bash
# 1. Secrets in unpushed commit diffs
git log -p origin/main..HEAD | grep -nE 'sk-ant-[A-Za-z0-9_-]{10,}|Bearer [A-Za-z0-9_.-]{20,}|"password"\s*:\s*"[^"]+"'

# 2. Personal /Users/<name>/ paths in committed files (excluding docs/PLANS)
git grep -nE '/Users/[a-zA-Z]+/|/home/[a-zA-Z]+/' -- ':!PLANS/' ':!*.md'

# 3. Real session keys / cookies in test fixtures (only "fake-test-key" literals should match)
git grep -nE 'sessionKey.{0,5}[A-Za-z0-9+/_-]{30,}' -- 'backend/tests/' 'fetcher/tests/'

# 4. AI attribution across ALL unpushed commits (NOT just the most recent — check the whole batch)
git log origin/main..HEAD --format='%H %B' | grep -inE 'co-authored-by:.*claude|🤖 generated'

# 5. Accidentally-tracked credential / cache / local files
git ls-files | grep -E '(^|/)\.env$|\.env\.[^.]+$|credentials\.json$|\.sqlite$|\.local$|\.local\.json$'

# 6. Real email addresses outside known-OK list
git grep -nE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' -- ':!*.md' ':!PROCESS/' | \
  grep -v -E 'raymondpeckiii@gmail\.com|noreply@|@example\.(com|org)|user@host'

# 7. ~/.claude or /Users/rpeck/ in files shipped to PyPI sdist
uv build --sdist 2>&1 | tail -3
mkdir -p /tmp/sdist-check && tar xzf dist/*.tar.gz -C /tmp/sdist-check
grep -rnE '/Users/rpeck|~/\.claude(?!-explorer)' /tmp/sdist-check | grep -v PKG-INFO  # PKG-INFO ~/.claude refs are legitimate README content
rm -rf /tmp/sdist-check

# 8. IP addresses outside known-OK list (127.0.0.1, RFC1918, link-local)
git grep -nE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' -- ':!*.md' ':!PROCESS/' | \
  grep -v -E '127\.0\.0\.1|0\.0\.0\.0|169\.254\.|192\.168\.|10\.[0-9]+\.|255\.255'

# 9. Private-infra URLs (internal., grafana, slack channels, linear, etc.)
git grep -nE '(internal\.|\.local/|linear\.app/|slack\.com/archives|grafana\.|datadog\.)' -- ':!PROCESS/'

# 10. TODO/FIXME/XXX in user-facing code (samples below should be intentional documented tech debt only)
git grep -nE 'TODO|FIXME|XXX' -- 'frontend/src/' 'backend/' ':!**/tests/**'

# 11. React Doctor diff-gate — fails on NEW errors in files changed vs main
(cd frontend && npm run lint:react:diff)

# 12. LLM security review of the diff (built-in slash command; run inside Claude Code)
#     /security-review

# 13. Article image/link formats must render on GitHub (co-equal surface with Medium)
#     Fails on Obsidian ![[...]] / [[...]] (literal text on GitHub), spaces or %20 in
#     local image/link paths (GitHub's blob renderer mangles %20), and referenced
#     images that aren't git-committed (404). Earned 2026-06-01 when a public push
#     shipped broken images on every Part 2 screenshot.
python3 scripts/check-article-formats.py
```

**Known-OK matches** (won't fail the scan but worth re-eyeballing):

- `fake-test-key` and `sk-ant-sid01-fake-test-key` in `backend/tests/`, `fetcher/tests/` — deliberate fake fixtures.
- `/Users/rpeck/` in test fixtures under `frontend/e2e/` — deliberate test data shape mirroring real CC session paths.
- `~/.claude` in `PKG-INFO` (README copy) and source code — describing the actual home-directory paths the app reads from.
- `claude-exporter` (the legacy pre-V1 name) in `.gitignore` (backwards-compat) and `PROCESS/a70251a5/outline.jsonl` (frozen historical conversation snapshots).
- `http://claude-explorer.local/` in `backend/export.py` — base URL for relative-resource resolution in PDF export; not a real infrastructure reference.

**Things this checklist does NOT cover** (and that you should still think about before flipping visibility):

- The `PROCESS/` directory exposes development-session conversation snapshots. Some maintainers want this; some don't. Decide before flip.
- The `.github/workflows/*.yml` reference org-scoped secrets (`PYPI_API_TOKEN`, OIDC trusted publishing config). On the public repo, these need to be configured under repo Settings → Secrets and variables → Actions BEFORE the first release workflow run.
