# Claude Explorer — UX Contract

This document is the contract for how the Claude Explorer UI behaves. It
describes every UX rule the app is expected to satisfy. Code that changes UI
behavior MUST keep this document accurate. Tests for new UI behavior should
fail first against this document, then pass when the code lines up.

The conventions used below:

- **MUST** / **MUST NOT** are normative.
- "Pane" = one of the three top-level UI surfaces (sidebar, conversation,
  search panel).
- "Cmd+X" means Cmd on macOS, Ctrl on Windows/Linux. The handler accepts
  either modifier; UI hint glyphs render the platform-correct symbol.
- Where this document says "future" or "planned", the rule is the target
  contract; the code may not yet match. New work should converge on this
  document, not the other way around.

---

## 1. Three-pane layout + focus model

The app has exactly three first-class panes:

1. **Sidebar (list)** — left rail. Conversation list, source / workspace /
   sort filters, refresh button, settings link.
2. **Conversation pane (detail)** — center. The currently-open conversation,
   its messages, and per-conversation actions (export, copy, pin).
3. **SearchPanel (search)** — right overlay. Sliding panel for full-text
   search and bookmarks. Always mounted; slides off-screen via
   `translate-x-full` when closed so React Query state and input focus
   survive open/close cycles.

### `focusArea` state machine

`focusArea` lives in `KeyboardNavigationContext` and takes one of four
values:

```
type FocusArea = 'list' | 'detail' | 'search' | 'none'
```

State transitions:

| From  | Trigger                                  | To       |
|-------|-------------------------------------------|----------|
| any   | Click anywhere in sidebar                | `list`   |
| any   | Click anywhere in conversation pane      | `detail` |
| any   | Click any message                        | `detail` |
| any   | Cmd+F (focus search input, opens panel)  | `search` |
| any   | Cmd+K when panel was closed              | `search` |
| `list`| Right arrow, Enter on a row              | `detail` |
| `detail`| Left arrow                              | `list`   |
| `detail`| Esc, `navSource === 'list'`             | `list`   |
| `detail`| Esc, `navSource === 'search'`           | `search` (panel re-opens if closed) |
| `search`| Esc when query is empty                 | panel closes; `focusArea` becomes whatever `navSource` implies for the active match message |
| `search`| Esc when query is non-empty             | clears query first; second Esc closes |
| `list`/`detail` | Tab                              | toggles between `list` ↔ `detail` (Tab never selects `search`) |
| `search`/`none` | Tab                              | jumps to `list` |

`navSource` is `'list' | 'search'` and records which pane initiated the
most recent navigation INTO the detail view, so Escape returns the user to
the right place. Navigations driven by clicking a search result MUST set
`navSource = 'search'`; navigations driven by sidebar Enter / right-arrow
MUST set `navSource = 'list'`.

### Active-pane visual

The pane whose `focusArea` is current renders a subtle inset ring
(`ring-2 ring-inset ring-blue-500/50`). All panes set their own ring
condition; nothing else paints the ring.

---

## 2. Keyboard shortcuts

All global shortcuts are owned by `useKeyboardShortcuts.ts`. Components
MUST NOT register their own global keydown handlers; lightbox is the only
exception (it owns its keys while open — see Section 14).

### Universal (both keyboard modes)

| Key                | Action                                                                 |
|--------------------|------------------------------------------------------------------------|
| Cmd+F              | Focus search input. Opens panel if closed. NEVER closes the panel.    |
| Cmd+K              | Toggle SearchPanel (open ↔ closed).                                    |
| Cmd+G              | Next match. Focus stays in the search input; conversation scrolls to and highlights the match. |
| Cmd+Shift+G        | Previous match.                                                       |
| Esc (panel open)   | First press: clears non-empty query OR closes the panel. After close, focuses the active-match message (single press from input). |
| Enter (in input)   | Opens the active match (or first result if none active). Panel stays open. |
| Cmd+R              | Refresh conversation list (invalidates the conversations query). Browser refresh is preempted. |
| Cmd+C              | Copy focused message as Markdown (detail pane only, only when no text selection exists; otherwise the OS copy runs). |
| Tab                | Rotate `list` ↔ `detail`. From `search`/`none` → `list`. Tab never selects `search`. |
| ?                  | Open the keyboard help modal.                                          |
| Arrow Up/Down      | Move selection in the active pane (list or detail).                   |
| Arrow Right (list) | Open selected conversation; focus → `detail`; selects first message.  |
| Arrow Left (detail)| Focus → `list`.                                                        |
| Enter (list)       | Same as Arrow Right.                                                  |
| Esc (detail)       | Returns to whichever sidebar initiated the navigation (`navSource`).   |
| u / a              | Detail pane: next user / next assistant message.                      |
| U / A              | Detail pane: previous user / previous assistant message.              |
| g                  | Detail or list: top of the current pane.                              |
| G                  | Detail or list: bottom of the current pane.                           |

### Vim mode (default)

In addition to the universal keys above:

| Key      | List pane                          | Detail pane                       |
|----------|------------------------------------|-----------------------------------|
| j        | Next item                          | Next message                      |
| k        | Previous item                      | Previous message                  |
| g        | First item                         | First message                     |
| G        | Last item                          | Last message                      |
| Ctrl+d   | (n/a)                              | Page down (10 messages)           |
| Ctrl+u   | (n/a)                              | Page up (10 messages)             |
| /        | Focus the sidebar title-search input | (n/a)                           |

### Emacs mode

| Key      | List pane                          | Detail pane                       |
|----------|------------------------------------|-----------------------------------|
| Ctrl+n   | Next item                          | Next message                      |
| Ctrl+p   | Previous item                      | Previous message                  |
| Alt+<    | First item                         | First message                     |
| Alt+>    | Last item                          | Last message                      |
| Alt+n    | (n/a)                              | Page down (10 messages)           |
| Alt+p    | (n/a)                              | Page up (10 messages)             |
| Ctrl+s   | Focus the sidebar title-search input | (n/a)                           |

### Input-focus rules

- Global shortcuts are suppressed when typing in any `<input>`,
  `<textarea>`, `<select>`, or `contentEditable` element.
- An input MAY opt back into specific shortcuts (Cmd+K, Cmd+F, Cmd+G,
  Cmd+Shift+G, Esc) by setting `data-allow-shortcuts` on itself or any
  ancestor. The SearchPanel's input does this so its own navigation keys
  still fire while it holds focus.
- Cmd-modified shortcuts (Cmd+R, Cmd+C, Cmd+K, Cmd+F, Cmd+G,
  Cmd+Shift+G) ALWAYS run; they're checked before the input-focus guard.

---

## 3. Search-scope pin

The user can pin search to a single conversation or project, so Cmd+G and
the SearchPanel results stay in scope.

### URL representation

The pin is encoded in the URL as a single query param:

- `?pin=conv:<conversation-uuid>` — pin one conversation.
- `?pin=project:<project-path-or-slug>` — pin a whole project.

### Persistence rules

- Pin is sticky across browser reload (URL is the source of truth).
- Pin survives switching to a different conversation in the sidebar.
- Pin survives closing and re-opening the SearchPanel.
- Pin is **per-tab**, not per-user. Two tabs may have different pins.

### Clearing the pin

The pin MUST be cleared by exactly one of:

- The user clicks **Unpin** in the conversation header pin dropdown.
- The user types into the sidebar's "Search titles..." input. (Typing
  there expresses broadening intent, so an active pin MUST be cleared as
  soon as the input value changes.)

The pin is NOT cleared by closing the panel, switching conversations, or
navigating with arrow keys.

### Pin button (conversation header)

- Lives in the conversation header next to the title.
- Opens a dropdown with three items, in this order:
  1. **Pin this conversation**
  2. **Pin this project**
  3. **Unpin** — always rendered in the dropdown (the divider + button
     are NOT conditional on an active pin). When no pin is active, the
     button is disabled (`opacity-50`, `cursor-not-allowed`, and the
     native `disabled` attribute so clicks are ignored). This keeps the
     Unpin affordance discoverable so users learn the gesture before
     they need it.

### Cmd+G honors the scope

When a pin is active, Cmd+G / Cmd+Shift+G wrap within the pinned
scope. Cross-conversation jumps are limited to the pinned conversation
or pinned project's conversations.

---

## 4. SearchPanel chip

When a pin is set, a dismissible blue pill chip appears immediately below
the SearchPanel input:

```
[ In: <Conversation Title> × ]
```

- Background: blue-100 / dark blue-900; border: blue-300.
- The chip's `×` MUST clear the pin (same effect as Unpin).
- The chip's label MUST reflect the pinned scope:
  - `In: <Conversation Title>` for `pin=conv:...`
  - `In: <Project Name>` for `pin=project:...`

### Empty results in scoped mode

If the query returns zero matches AND a pin is active, render a
call-to-action below the empty-state message:

```
Unpin and search all →
```

Clicking it MUST clear the pin and re-run the search across everything.

### Live region

An `aria-live="polite"` region announces "Match N of M" each time the
active match changes (Cmd+G, Cmd+Shift+G, Enter, click on a result). The
region is visually `sr-only` but reachable to screen readers.

### A hit inside a tool call or a tool result

Every search hit is navigable, from the user or from Claude. Claude Code
stores tool results as user rows, so a "You" hit can be a tool result.

- **The search always includes tool content**, whatever Show Tools says.
  From 2026-05-11 to 2026-10-21, Show Tools off also left tool content
  out of the search (`include_tool_calls=false`), because such hits
  could not be navigated.

- **Show Tools off (the default):** the active hit's bubble shows ONLY the
  tool blocks that hold the match, expanded, with the match highlighted.
  The other tool blocks stay hidden, and the **Show Tools** setting does
  not change. When the search is cleared, the revealed blocks hide again.
- **Show Tools on:** the tool blocks that hold the match expand. The
  others stay as they are.
- A tool-only message stays in the Conversation Pane while it holds the
  active hit, so the click can scroll to it.
- If no tool block holds the query text (the server's search stems
  words, so "running" can match "run"), the bubble shows all its tool
  blocks, so it is not empty.
- Code: `frontend/src/components/search/toolReveal.ts`.

### The user's click or scroll wins over a search jump

A jump to a search hit centers the hit, then re-checks the position at
250, 750, and 1250 ms and re-centers it if a row above changed height
(`scrollBubbleIntoView`). The user's own action ends those re-checks:

- **A click on a bubble, or a manual wheel or touch scroll**, records
  "demonstrated focus" (`markDemonstratedFocus`). It also cancels the
  pending re-checks. Before 2026-10-23, a click within about a second
  of a jump was undone: a re-check scrolled back to the hit.
- The same demonstrated focus keeps a later search refetch (for example
  a Show Compactions toggle) from jumping back to the first match.
- Cmd+G, a new query, or another conversation starts navigation again.
- **The newest search jump wins.** A jump either scrolls straight to a
  rendered bubble, or sets `?highlight=` and scrolls once the bubble
  renders. A `?highlight=` that an older jump set is dropped when a newer
  jump starts (`lib/searchNavigation.ts`). Before 2026-10-23, on a slow
  machine, two jumps 50 ms apart could end on the older hit. A pasted
  link or a bookmark is not a search jump, so it always works.

---

## 5. Sidebar dim (out-of-scope rows)

When a pin is active, conversation rows in the sidebar that are NOT in the
pinned scope render at `opacity-40` (40%).

- The dim level matters: it MUST be visually distinct enough that the user
  can tell at a glance what's in vs. out of scope. 40% is the target.
- In-scope rows render at full opacity.
- The dim is purely visual; out-of-scope rows are still clickable. Clicking
  one navigates to that conversation but does NOT clear the pin (typing in
  the search input is the only way to broaden, per Section 3).

---

## 6. Sidebar title-search

The sidebar input above the conversation list:

- Placeholder text: **"Search titles..."**.
- Filters on the conversation `name` OR the conversation `project_path` /
  `project_name`. It MUST NOT filter on message body / summary content —
  message search lives in the SearchPanel (Cmd+K).
- Typing in this input MUST clear any active scope pin (signals broadening
  intent — see Section 3).
- Stays in sync with the URL `q` parameter; deep-links and back/forward
  both reflect the current value.
- Keyboard: in Vim mode, `/` focuses this input from the list pane. In
  Emacs mode, `Ctrl+s` does the same.

---

## 7. Tool-placeholder hiding

The literal string

```
This block is not supported on your current device yet.
```

is what Claude Desktop emits in place of tool calls and artifacts that
its current renderer cannot display. We surface it consistently across
every export path and the in-app viewer — but the viewer and the
exports diverge intentionally on _how_ they handle it:

- **Conversation viewer (`frontend/src/components/message/MarkdownRenderer.tsx`)**
  - The strip is **fenced-aware**: when the placeholder is inside a
    fenced code block (the canonical Claude Desktop shape), it is
    **not** stripped. Instead the fenced-code renderer detects it and
    swaps in the friendly inline badge "Tool call or artifact not
    captured in export." The badge is shown regardless of the
    Tools-visibility toggle, because it is a breadcrumb for missing
    content, not a captured tool call.
  - Outside a fence (mid-paragraph or as a bare line) the literal
    string is stripped wherever it appears. Lines that contained
    nothing but the placeholder are dropped entirely so we don't leave
    phantom blank paragraphs; runs of 3+ newlines collapse back to a
    single paragraph break.
- **Single-file Markdown export (`backend/export.py:filter_tool_placeholders`).**
- **Markdown bundle export (CommonMark and Obsidian).**
- **PDF export.**
- **The MCP server's exported markdown.**

The frontend constant `TOOL_PLACEHOLDER` and the backend's
`filter_tool_placeholders` MUST stay textually in sync on the
placeholder string itself. The viewer's strip behavior intentionally
goes further than the backend regex (mid-paragraph hits + fenced-block
preservation for the badge); the exports are a flat regex by design.

---

## 8. Markdown export dialog

Clicking the **Markdown** button in the conversation header opens a dialog
(modal) with three radio modes:

1. **Inline single .md** — one self-contained Markdown file. Images stay
   as backend URL references (`/api/cc-image?path=...`). Default for
   first-time users.
2. **Bundle CommonMark (.zip)** — zip with `conversation.md`, an
   `images/` directory, and an `attachments/` directory. Image refs in
   the markdown use relative paths like `![alt](images/x.png)`. File
   works without the local backend running.
3. **Bundle Obsidian (.zip)** — same shape as CommonMark, but image refs
   use Obsidian wikilinks: `![[images/x.png]]`.

Dialog rules:

- The dialog MUST pre-select the user's saved preference (sticky across
  exports).
- An optional **"Save as default"** checkbox writes the choice back to
  preferences when checked.
- An **Export** button kicks off the download with the selected mode.
- Cancel / Esc closes the dialog without changing state.
- The dialog is the only entry point for export-mode choice; the
  Markdown button MUST NOT silently re-export with the last mode without
  showing the dialog.

PDF export is a SEPARATE button (Section 9), not a fourth mode.

---

## 9. PDF export

- Its own button in the conversation header, next to Markdown.
- One-click: clicking it generates the PDF and starts the download
  (no dialog).
- The PDF MUST embed images. It uses WeasyPrint's `url_fetcher` hook to
  resolve `/api/cc-image?...` and `/api/<org>/files/...` URLs from the
  local disk. Network fetches inside WeasyPrint MUST NOT be required.
- Tool placeholders are filtered (Section 7).
- Filename matches the conversation title via the same `sanitizeFilename`
  helper used by Markdown export.

---

## 10. Settings persistence

Preferences are persisted **server-side**, not in the browser, so they
are consistent across browsers / devices that point at the same backend.

### Storage location

`~/.claude-explorer/preferences.json`. Atomic writes (write to temp file
then `rename`).

### Access pattern

- Backend exposes:
  - `GET /api/preferences` — full preferences object.
  - `PATCH /api/preferences` — deep-merge update; only the keys the
    client sends are touched.
- Frontend hook: `usePreferences()` (planned). Exposes a typed view plus
  setters that PATCH on change.

### Migration from localStorage

The app historically stored prefs in `localStorage`. During the
server-side migration:

- `usePreferences()` does **dual-read** at startup: server first, then
  fall back to `localStorage` if the server returns 404 / empty.
- On first successful server read, the hook seeds the server with the
  existing localStorage values via PATCH.
- On every write, the hook does **dual-write**: PATCH to the server, and
  also write to `localStorage` so any code that hasn't migrated yet
  still sees the latest.
- A migration marker `prefs_migrated_v1=true` is set in `localStorage`
  after the first successful seed. This prevents two open tabs from
  racing each other into double-seeds.

`localStorage` is **fallback only** post-migration; the server is the
source of truth.

### Per-tab vs. per-user

- The scope **pin** is per-tab (URL state — Section 3).
- The list's project or path filter is per-tab too (URL state).
- Everything else (theme, keyboard mode, sort, group-by-project, "show
  empty sessions", the recent-sessions filter, show tool calls, markdown
  export default, search panel context size / sort, right-pane tab) is
  per-user.

---

### The About section

The last section of the Settings page.

- **Version:** "Version 1.1.6". It comes from `version` on `/api/config`,
  which is `mcp_server.__version__`. A user can see which release runs,
  for example after `uv tool upgrade`. A server from before 2026-10-21
  sends no version; then the line is not shown.
- **View on GitHub:** links to `https://github.com/rpeck/claude-explorer`.

## 11. Image fallback + auto-reload

Inline images go through one fallback layer before showing the
"image unavailable" tile.

### `<img onError>` retry

When an `<img>` fails to load:

1. The first error retries once via the **permanent-cache** endpoint for
   that image's source.
2. If the cached fetch also fails, the component shows the fallback tile.

The retry MUST be one shot per image; an infinite reload loop would
hammer the backend on broken paths.

### Fallback tile

The "image not in cache" placeholder (CC marker images and inline image
content blocks):

- Dashed border (`border-dashed border-zinc-300`).
- `ImageOff` icon from lucide-react.
- Visible label: `"Image not in cache: <file_name>"` (the filename
  segment is `font-mono`).
- Clickable (opens the lightbox at this image's index, which then shows
  "Image unavailable" — matches Section 14's behavior so the user still
  has a way to navigate siblings).
- `aria-label`: `"Image not in cache: <file_name>"`.
- `title` tooltip explains the most common root cause:
  `"Original was rotated by Claude Code; this image was not present at
  fetch time, so we couldn't cache it."`

The clearer copy + tooltip exist because users were reading the bare
filename + broken-image styling as an app bug rather than a known
limitation of CC's image-cache rotation.

### Permanent cache locations

- **Claude Code marker images** (`[Image: source: /...]` markers in chat
  text) cache to:
  ```
  ~/.claude-explorer/cc-images/<conv-uuid>/<sess>--<N>.<sha8>.png
  ```
- **Claude Desktop attachments** (uploaded files) cache to:
  ```
  ~/.claude-explorer/files/<conv-uuid>/<file-uuid>/<file_name>
  ```

Both locations are managed by the backend. The frontend never writes to
them directly; the cache is filled by the fetch pipeline and by lazy
hydration on first request.

---

## 12. Keyboard mode (Vim or Emacs)

The user picks one of two modes in **Settings → Keyboard mode**. The
choice is persisted (Section 10) and applied globally.

- **Vim** is the default for new users.
- All bindings are listed in Section 2 above. Both modes share the
  universal keys (Cmd+F/G/K, arrows, ?, Tab, Cmd+R, Cmd+C, u/a/U/A,
  g/G).
- Vim adds `j/k`, `g/G`, `Ctrl+d/Ctrl+u`, `/`.
- Emacs adds `Ctrl+n/p`, `Alt+</>`, `Alt+n/p`, `Ctrl+s`.
- The keyboard help modal (`?`) renders the bindings for the user's
  active mode.

Mode changes take effect immediately; no reload needed.

---

## 13. Click-to-focus

Mouse clicks ALWAYS update `focusArea`:

- Click anywhere on the sidebar background (including its scroll area
  but outside an interactive control) → `focusArea = 'list'`.
- Click anywhere on the conversation pane background → `focusArea =
  'detail'`.
- Click a row in the sidebar → `focusArea = 'list'`, plus selects the
  row.
- Click a message bubble → `focusArea = 'detail'`, plus selects the
  message.
- Click inside the SearchPanel (any control) → `focusArea = 'search'`.

The pane background click handler MUST NOT swallow clicks on interactive
children. Buttons, links, inputs continue to fire normally; the pane
just records its focus afterwards via event bubbling.

---

## 14. Image lightbox

Clicking an inline image opens a full-screen lightbox.

### Scope

- The lightbox shows **every image in the current conversation** in
  document order (top to bottom, message after message). Arrows walk
  the entire conversation, not just the current message.
- This applies to both Claude Desktop attachments (`ImageFile`) and
  Claude Code marker images (`[Image: source: ...]` resolved via
  `/api/cc-image`).

### Keys (lightbox-local; preempt globals while open)

| Key       | Action                                          |
|-----------|-------------------------------------------------|
| Esc       | Close.                                          |
| ←         | Previous image in the conversation.             |
| →         | Next image in the conversation.                 |
| d         | Download the current image.                     |
| o         | Open the original asset in a new browser tab.   |

While the lightbox is open it OWNS the keyboard. Cmd+G, j/k, Ctrl+n/p,
and the rest of the global bindings DO NOT fire. The lightbox closes
via Esc, the close button, or clicking the backdrop; on close, focus
returns to the thumbnail that opened it.

### No zoom/pan in v1

Fit-to-viewport via `object-contain` handles 90% of cases. For pixel-
accurate inspection the user clicks "Open original" and the browser's
native viewer takes over.

---

## 15. Bookmarks

Bookmarks are persisted **server-side** at `~/.claude-explorer/bookmarks.json`.

### Endpoint

`/api/bookmarks` (router: `backend/routers/bookmarks.py`):

- `GET /api/bookmarks` — list all bookmarks.
- `POST /api/bookmarks` — create.
- `PATCH /api/bookmarks/{id}` — update note.
- `DELETE /api/bookmarks/{id}` — remove.

Each bookmark stores `{conversation_uuid, message_uuid, note,
created_at}`.

### UI

- A **star icon** lives on each message bubble (visible on hover, always
  visible when bookmarked). Clicking toggles the bookmark for that
  message.
- The right-pane tab strip in the SearchPanel has a **Bookmarks** tab
  next to **Search**. Selecting it renders `BookmarksPanel` instead of
  the search UI. Selection is persisted via `rightPaneTab` in settings.
- Bookmarked messages get a small star badge inline so the user can spot
  them while scrolling the conversation.

---

## Cross-cutting rules

### Toasts

- Toasts use `sonner`'s `Toaster` mounted once at the App root.
- Position: **top-center**. This avoids being occluded by the
  SearchPanel (right-edge) or the sidebar.
- Loading toasts use `toast.loading(id)`; success / error transitions
  reuse the same `id` so one toast updates in place rather than stacking.
- Errors that the user can resolve themselves (auth, missing creds) get
  a **Details** action that opens the FetchDialog.
- Transient network errors (DNS, offline) are classified as transient
  and do NOT raise an error toast — the connection-status indicator
  handles them.

### Fetch / Refresh pipeline

- The header **Refresh** button (sidebar top-right, `RefreshCw` icon)
  triggers the full capture+fetch pipeline via SSE
  (`GET /api/fetch/refresh?incremental=true`). See `CLAUDE.md` →
  "Web UI Refresh button (Build-9)" for the SSE event schema.
- The button is disabled while a pipeline is running. A second concurrent
  request would receive HTTP 409; the disabled state is defense in depth.
- The Refresh icon spins (`animate-spin`) while running.
- The FetchDialog (Details modal) exposes manual **Full Refresh** and
  **Fetch New** actions that hit `/fetch/start` directly and do NOT
  auto-trigger capture.
- Only one fetch runs at a time. While a Refresh or a `/fetch/start` fetch
  runs, the server refuses a second one of either kind with HTTP 409. An
  `EventSource` cannot read that status, so the page asks
  `/fetch/status` (`fetch_in_progress`). The dialog or the toast then
  shows "Another fetch is already running. Wait for it to finish, then
  try again." instead of "Connection lost".
- While the Cowork tasks download, the toast shows "Cowork task 3/18:
  <title>". The Cowork step counts the tasks of each workspace from 1, so
  it does not continue the "Fetching N/M" count of the chat phase.
- Every fetch also archives the account's **remote Cowork tasks**:
  sessions that ran in Anthropic's cloud, listed in claude.ai under
  "Chats and tasks". `chat_conversations` does not list them, so an
  account that only runs tasks would otherwise fetch nothing. They appear
  under the **Claude Cowork** source filter, like local Cowork sessions.
  The `complete` message counts them ("Fetched 0 conversations and 18
  Cowork tasks across 2 workspace(s).").
- The count includes only the tasks that brought new messages. A task
  with only a new title gets the new title, but the count does not include
  it. An incremental fetch skips a task only when its `updated_at` and its
  `last_event_at` did not change.
- **A Cowork task that fails to download is never hidden.** The `complete`
  message then names up to three failed tasks ("2 Cowork tasks could not
  be downloaded (A, B). The next Refresh tries them again."), and the final
  toast is a **warning** with a **Details** button, shown for 15 seconds,
  instead of the green success toast.
- Incremental fetches re-download a conversation whose server
  `updated_at` is newer than the local copy, not only conversations
  missing from disk.

### Conversation list dim states

In addition to the scope-pin dim (Section 5), the list dims rows for:

- **Phantom sessions** (empty Claude Code sessions): hidden by default,
  toggled by the "Empty" checkbox in the sidebar header.
- **Group-by-project** mode: only available when the source filter is
  "All Conversations" or "Claude Code". See "Conversation List filters".

### Conversation List filters

The user often wants only the sessions that are active now, or only one
project. These controls hide the other rows. Spec and test cases:
[PLANS/2026.10.25-sidebar-list-filters.md](./PLANS/2026.10.25-sidebar-list-filters.md).

**No exceptions.** Each filter applies to every row. The open
conversation is hidden when a filter excludes it. The Conversation Pane
still shows it.

**Filters and Sorting** (round 4 of the spec,
[PLANS/2026.10.26-sidebar-filters-accordion.md](./PLANS/2026.10.26-sidebar-filters-accordion.md)):
every control below the "Search titles and projects" field sits in one
section that opens and closes from its header.

- Closed by default. The per-user preference `sidebarFiltersOpen` keeps
  the user's choice across reloads and restarts.
- The header shows "N active" when N filters differ from their defaults:
  the active filter, the source, the workspace, a project or path
  filter, a time window other than Forever, "Empty", and "Archived". The
  sort and the grouping do not count, because they hide no rows.

**Recent sessions** (the "Last:" dropdown, a sidebar row below the sort
controls; round 2 of the spec,
[PLANS/2026.10.26-list-filter-dropdowns.md](./PLANS/2026.10.26-list-filter-dropdowns.md)):

- Windows: 8 hours, 1 day, 3 days, 1 week, 2 weeks, 1 month (30 days),
  3 months (90 days), 6 months (180 days), 1 year (365 days), and Forever,
  which turns the filter off. Default: Forever (since 2026-10-26), so a
  new user sees every session; a saved window does not change.
- The list hides each row whose `updated_at` is older than now minus the
  window. The list applies the cutoff again once a minute.
- Per-user preferences `listRecentOnly` (false for Forever) and
  `listRecentHours`. A saved number of hours that is not a window (from
  the old hours field) shows as its own option, such as "48 hours".

**What a project is** (Settings → Conversation List → "Projects are";
per-user preference `listProjectKey`; round 3 of the spec,
[PLANS/2026.10.26-list-project-key.md](./PLANS/2026.10.26-list-project-key.md)):

- **Folder path** (default): each folder is its own project. An entry's
  label is the last two folders of its path, with more folders when two
  labels would be the same, so `~/Source/clientco/ai` reads
  "clientco/ai". The full path is the entry's second line.
- **Folder name**: folders with the same last name are one project, as
  before. The second line is the path, or "N folders" when the name
  covers more than one.
- Paths inside the home folder show as `~/...`; `/api/config` reports the
  home folder as `home_dir`.

**Project or path** (per tab, in the URL):

- `?project=<name>` shows the rows whose `project_name` equals `<name>`,
  ignoring case. `project_name` is the last folder of `project_path`, so
  one project can hold several paths.
- `?path=<path>` shows the rows whose `project_path` equals `<path>`.
- Only one of the two is active. Rows without a project (Claude Desktop,
  Cowork) do not match either.
- **Sidebar project picker:** a button that opens a list with a field,
  "Type to filter projects". The list has "All projects" first (also
  while the user types), then the active filter when no entry stands for
  it (such as "ai (all folders)" for an old `?project=` link in path
  mode), and then the projects of the loaded list, newest first by their
  latest session. Typing keeps the entries whose label or path contains
  the text, ignoring case. The arrow keys and Enter pick an entry;
  Escape closes the list. In path mode a pick sets `?path=`; in name
  mode, `?project=`. The button is always shown, so that it can clear a
  filter that a URL or the header set.
- **Conversation header, "List" menu** (Claude Code sessions with a
  path): "Show only this project" and "Show all projects"; name mode
  also has "Show only this folder". The label is "Filter list" when no
  project filter is on, "List: this project" (or "List: this folder" in
  name mode) when the filter shows this conversation's project, and
  "List: other project" for any other project filter.
- This filter is separate from the search scope pin (Section 3), which
  dims rows and does not hide them.

**Empty state:** when the filters hide every loaded row, the list names
each active filter ("All 12 conversations hidden by project=foo and
last 1 day."). It gives a button for each filter that it can clear:
"Clear active filter", "Show all projects", and "Show older sessions"
(which sets Forever).

**Group sessions by project** (the folder-tree checkbox):

- The list groups only when the checkbox is shown and checked. It is
  shown for the sources "All Conversations" and "Claude Code".
- Folders hold only the filtered rows. A folder with no visible row is
  not shown, and its count is its number of visible rows.
- In path mode, each folder is one path: its header shows the picker's
  label, and its tooltip shows the path. In name mode, a folder is one
  name.
- **Folder order:** a folder takes the value of its first session in the
  chosen sort order: the largest value for a descending sort, the
  smallest for an ascending sort. A tie sorts by folder name, A to Z.
  - Last Activity: `updated_at`. Start Time: `created_at`.
  - Title: `name`, ignoring case. Project: the folder's label.
- Inside a folder, starred rows come first.
- Keyboard navigation (`j`/`k`, arrows) follows the display order,
  folder by folder, and skips the rows of a collapsed folder.

**Layout:** the list keeps a minimum height of 12rem (about two rows).
On a short screen, the block of controls above it scrolls; with
"Filters and Sorting" closed, the list starts right below the search
field. Before
2026-10-25, the controls and two banners could leave the list 0 px high,
with no row shown.

### Workspace / org filter

When the captured credentials cover ≥2 organizations, the sidebar
renders a workspace `<Select>` between the source filter and sort
controls. The slot is reserved (`h-9`) even with one org so the layout
doesn't shift mid-stream when a second org appears.

- When the user selects a workspace, the list and the Search Pane show
  only the conversations of that workspace.
- A remote Cowork task belongs to the workspace that Refresh fetched it
  from. Its sidecar records that workspace as `organizationId`.
- The list groups a remote task under its workspace **name**, which the
  fetch records in the task's sidecar (`organizationName`). A task fetched
  before 2026-10-23 gets the name at the next Refresh; until then it shows
  as "Workspace (<uuid prefix>)".
- A local Cowork session has no workspace. Claude Desktop stores it under
  an internal org directory that does not match a workspace. Thus, the
  list shows local Cowork sessions only when "All workspaces" is
  selected.
- Claude Code sessions also have no workspace.

### Theme

- Three options: **light**, **dark**, **system**.
- Cycled via the icon button in the sidebar footer (Sun → Moon → Monitor
  → Sun ...).
- "System" follows `prefers-color-scheme` and reacts live to OS changes.
- Persisted per-user (Section 10).

### Signed-in account (sidebar footer)

The bottom of the sidebar shows the Anthropic account that the captured
credentials belong to, as claude.ai and Claude Desktop do. A user with a
work account and a personal account can see whose archive Refresh
fetches.

- **Where:** a row in the sidebar footer, above the Settings, theme, and
  Export buttons. It is a region named **Signed-in account**.
- **Avatar:** a circle with the account's initials: the first letter of
  the first and the last word of the name that contain a letter
  ("Ada Lovelace @ Engines.co" → "AE"). A one-word name gives one letter.
  With no name, the first letter of the email.
- **First line:** the display name, else the full name, else the email.
- **Second line:** `email · workspace`, where the workspace is the
  primary org from the credentials. A missing part and its `·` are left
  out. When the email is already the first line, the second line is the
  workspace alone.
- No credentials, or no initials to show: the avatar shows a person icon.
- Long text truncates with an ellipsis. Each line's `title` tooltip holds
  its full text.
- **No credentials:** first line "Not signed in", second line "Click
  Refresh to sign in".
- **Lookup failed** (expired session, offline): the first line is the
  workspace name if known, else "Signed in". The second line is the
  error message from `/api/account`, such as "Session expired. Click
  Refresh to sign in again."
- **Credentials file corrupt** (`/api/account` answers 503): first line
  "Account unavailable". The second line is the reason from the server,
  such as "Credentials file is corrupt: … Re-run capture to refresh
  credentials." Its `title` tooltip holds the full text. The request is
  not retried, because the file does not fix itself.
- **Freshness:** read from `GET /api/account`. The row is refetched when
  Refresh captures new credentials, and again when a Refresh pipeline
  completes, because the new credentials can belong to a different
  account. The fetch after a capture can fail, so the capture alone
  refetches the row.
- **Failed lookup retry:** while the row shows a lookup failure, the app
  asks again every 60 seconds, the interval at which the server retries
  claude.ai. A successful lookup stops this.
- **Loading / other endpoint error:** the row keeps its height and shows
  no text, so the footer does not shift.

### Quit (power button)

The user can stop the Claude Explorer server from the app itself. This
matters most when the macOS app, the Windows Start-menu shortcut, or the
Linux menu entry started the server, because no terminal is open to
press Ctrl+C in.

- **Where:** the sidebar footer, at the right end of the button row: an
  icon button with a power icon, named **Quit Claude Explorer**.
- **Confirm first:** clicking it opens a dialog titled **Quit Claude
  Explorer?** with the text "This stops the Claude Explorer server. Open
  Claude Explorer again to restart it." Buttons: **Cancel** and **Quit**.
  Cancel, Esc, or a click outside closes the dialog and changes nothing.
- **Refresh running:** the dialog adds "A Refresh is running. Quitting
  interrupts it; the next Refresh picks up where it stopped."
- **Quit:** sends `POST /api/shutdown`. On success the app closes a
  running Refresh stream, and then the whole page is replaced by a screen
  with the heading **Claude Explorer has stopped** and the text "You can
  close this tab." The stopped page sends no more requests, so no
  connection-lost warning appears.
- **Screen readers:** the stopped screen moves focus to its heading, and
  the text "You can close this tab." is in a polite status region.
- **Other tabs:** every other open Claude Explorer tab in the same
  browser and at the same address (for example
  `http://localhost:8765`) shows the same stopped screen at once, instead
  of the "Connecting to Backend" or "Connection Failed" dialog. A browser
  without `BroadcastChannel` shows the stopped screen only in the tab
  that quit.
- **Failure:** if the request fails, the dialog stays open and shows
  "Could not stop the server." with the reason. The app keeps working.
  If the server does not answer within 10 seconds, the reason is "The
  server did not answer within 10 seconds.", and Cancel works again.
  A server that `uvicorn --reload` or `--workers` started refuses with
  "This server runs under a supervisor (uvicorn --reload or --workers).
  Stop it with Ctrl+C in its terminal."
- **Launchers:** the macOS app quits itself when the server it started
  stops, and the Windows tray icon goes away. A server started from a
  terminal exits back to the prompt. The background image-cache watcher
  is a separate job and keeps running.
- **Quit during the first search-index build:** the build stops after
  the conversation in progress, so the server exits promptly. Search
  keeps working without the index until the next start, which indexes
  the rest.

### Sort controls (sidebar AND search panel)

Both panes share the same four sort fields:

- **Last Activity** (`updated_at`) — default.
- **Start Time** (`created_at`).
- **Title** (`name`).
- **Project** (`project`).

Each pane has its own sort state. The search panel's sort can differ
from the sidebar's so the user can scan, e.g., the most recent matches
without disturbing their list ordering.

### Composable filters (named title filters)

Saved title filters use a composable graph model. Two kinds of node:

- **Atoms** carry one set of patterns plus a **Behavior** (`hide` /
  `show-only`) and a mode (`glob` / `regex`). The Behavior controls
  what happens to a conversation that matches at least one of the
  atom's patterns: *Hide matches* drops it; *Show only matches* keeps
  it (and drops everything else).
- **Groups** combine other named filters. A group is either *match all
  of these* (every member must pass) or *match any of these* (at least
  one member must pass). Groups can reference atoms or other groups.
  **Groups carry no Behavior of their own** — they are pure
  combinators over their children's keep/drop decisions. To "hide a
  combination", either author a single atom whose patterns enumerate
  the combination, or build a group whose children are all hide-atoms
  (the combination semantics fall out of each child's own keep/drop).

The UI deliberately avoids AND/OR jargon — the radio labels are *"Match
all of these filters"* and *"Match any of these filters"*.

#### One active filter

At most one filter is active at a time. The sidebar's active-filter
`<Select>` (between the title-search input and the source filter) shows
every enabled named filter; selecting one sets it as the active filter.
The sentinel option **All conversations** maps to no active filter
(nothing is filtered out).

#### Enabled / disabled

Every filter has an `enabled` boolean.

- A disabled filter never appears in the active-filter `<Select>` and
  cannot be selected as active.
- Disabled members are dropped from a group's quantifier *before* the
  match runs. A `match: 'any'` group containing a single disabled
  member therefore does NOT pass for everything; the disabled member is
  removed first, and the resulting empty group passes (see "least
  surprise" rule below).
- If the active filter itself is disabled it becomes a no-op (treated as
  "no filter active") instead of throwing.

#### Least surprise: empty filters pass

- An atom with zero patterns passes for every conversation.
- A group with zero members (or a group whose members are all disabled
  / all orphans) passes for every conversation.

#### Manage Filters modal

The `Manage filters` button opens a two-pane modal: list of saved
filters on the left, editor for the selected filter on the right.

- **Atom editor**: name, Behavior (*Hide matches* / *Show only
  matches*), mode (glob/regex), patterns (one per line), enabled
  toggle. The Name input auto-fills
  from the first usable pattern (≥3 alphanumeric chars after stripping
  glob/regex meta-characters) until the user manually edits the name;
  clearing the name resumes auto-fill.
- **Group editor**: name, match radio (*all of these* / *any of these*),
  enabled toggle, member chips with an "Add member" `<Select>`. The Add
  member options exclude (a) self and (b) any node that would create a
  cycle.
- **"Used by:" line** sits directly under the name input and lists the
  groups that reference the current filter. Deletion is blocked while
  the filter is referenced; the block message names the referencing
  group(s) inline.

#### Canonical example: hiding cron-style chatter

The least-friction way to hide several unrelated patterns is one atom
with Behavior=*Hide matches* and every pattern listed (one per line).
For example: a single atom named `cron jobs` with patterns
`Scan Gmail for meeting invites*` and `*automated run of a scheduled
task*` hides every matching conversation in one node. Groups are only
needed when the user wants to combine separately-named, separately-
toggleable filters.

#### Cycle defense

- The Add-member `<Select>` hides candidates that would introduce a
  cycle, so the editor cannot save a cyclic graph.
- The runtime evaluator carries a `visited` set; a cycle introduced by
  manual edit of the prefs file short-circuits to "no-op" rather than
  blowing the stack.

#### Migration from the legacy `pinned` model

Older builds used a flat `Filter[]` plus a separate `pinned` boolean
per filter, with seeding code that copied pinned filters into a
session-only `activeFilterIds[]`. On the first load with the new code,
the app migrates legacy state once:

- Each legacy filter becomes an `AtomFilter` (drop `pinned`).
- The previously-pinned atoms become children of a single
  `GroupFilter` named **Default (migrated)**.
- The new active filter is the migrated group when at least one filter
  was pinned, otherwise null.
- The legacy `savedFilters` and `activeFilterIds` keys are explicitly
  nulled in the migration PATCH so the backend's per-key overwrite
  clears them.
- A sentinel `filters._migratedV1: true` is set so subsequent mounts
  skip migration.

A second one-time migration (`_migratedV2: true`) carries v1 atoms
forward to the Behavior model: each atom's previous `polarity:
'include'` becomes `behavior: 'show-only'`, and `polarity: 'exclude'`
becomes `behavior: 'hide'`. Groups are unchanged — they had no
polarity in v1 and gain no Behavior in v2. Saved filters survive the
migration with their previous semantics intact.

After a successful migration the sidebar renders a one-time amber
**Composable filters banner** above the conversation list:

> Filters are now composable. Your previously-pinned filters are
> grouped under **Default (migrated)** — your active filter. Click
> *Manage filters* to review.

The banner has a single dismiss control (`×`) that writes
`filters.migrationBannerDismissed: true` through the same preferences
PATCH path. Dismissed state survives reload. Fresh installs never see
the banner because `_migratedV1` is left at `false` when no legacy
state was found.

### Help modal

- `?` opens it (suppressed inside inputs).
- The modal title says "Keyboard Shortcuts" and the body shows the
  shortcuts for the user's current keyboard mode (Section 12).
- Esc dismisses it.

### Loading & empty states

- Conversation list: skeleton rows while `isLoading`; "No conversations
  yet" when truly empty; "No conversations found" when search filters
  hide everything.
- Conversation detail: `LoadingState` while fetching;
  "Conversation not found" on 404; `HintState` when the sidebar
  selection differs from the URL (so a stale conversation isn't shown
  while the user navigates with j/k).
- SearchPanel: "Type at least 2 characters to search" for query length
  &lt; 2; "Searching…" with a spinner while in flight; "No matches for
  &lt;query&gt;" only after the request settles with zero results.
