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

### Empty results limited by a sidebar filter

The sidebar filters limit the search too, and the control wins: with
results, nothing changes. When the query returns zero matches, the empty
state names each sidebar filter that limits the search (issue #80).
Before 1.2.0 it named only the pin. A search under the source "Codex" for
a word in a Claude Code session then looked like a broken search.

- **The line:** `Search limited by <parts>.`, directly below "No matches
  for ..." and above every button.
  The parts are joined with " and ", like the Conversation List empty
  state. Each part shows only when its filter is on:
  1. `source "<label>"`: the source filter has a value other than all.
     The label comes from the source registry, for example "Codex".
  2. `workspace "<name>"`: a workspace is selected. A workspace with no
     name shows `Workspace (<first 8 chars of the id>)`, like the sidebar.
  3. `filter "<name>"`: the active named filter removes at least one
     conversation. A filter that passes every conversation is not named.
  4. `hidden tool calls`: Show Tools is off.
  5. `hidden Code Mode runs`: Show Code Mode is off, and the archive holds
     at least one Code Mode run (issue #90).
     - The user then knows that hidden content might match and can widen
       the search, even when no run holds the word.
     - A user who never ran Code Mode never sees this part.
     - The search response carries `has_code_runs`, for the whole archive,
       not the query or its scope. The index answers it with one check of
       its `conversations` table. Before the index is ready, a scan of the
       conversations stops at the first run.
- **The buttons:** one per part, in the same order, after the pin button.
  - "Search all agents" sets the source filter to all.
  - "Search all workspaces" sets the workspace to all.
  - "Clear active filter" clears the active named filter.
  - "Search tool calls too" turns Show Tools on.
  - "Search Code Mode runs too" turns Show Code Mode on.
- Each button changes the same state as the sidebar control. The sidebar
  shows the change, and the search runs again.
- The pin keeps its own text and button. The line does not repeat it.
- With no filter on, the empty state shows only "No matches for ...".

### Live region

An `aria-live="polite"` region announces "Match N of M" each time the
active match changes (Cmd+G, Cmd+Shift+G, Enter, click on a result). The
region is visually `sr-only` but reachable to screen readers.

### A hit inside a tool call or a tool result

Every search hit is navigable, from the user or from Claude. Claude Code
stores tool results as user rows, so a "You" hit can be a tool result.

- **Show Tools controls the search.** With Show Tools off (the default),
  the search looks only at the text of user and assistant messages and at
  titles. A word that is only in a tool call or a tool result is not a hit.
- With Show Tools on, the search also looks inside tool calls and tool
  results.
- Turning Show Tools on or off runs the search again.
- Thinking blocks are never searched.
- With Show Tools off, no hit reveals a tool block. With Show Tools on, the
  tool blocks that hold the match expand, and the others stay as they are.
- History: from 2026-10-21 to 2026-11-20, the search always included tool
  content, and Show Tools off revealed only the matching block (issue #84
  reversed this).
- With Show Tools on, if no tool block holds the query text (the server's
  search stems words, so "running" can match "run"), all the tool blocks
  of the bubble expand.
- Code: `frontend/src/contexts/SearchPanelContext.tsx`,
  `frontend/src/components/search/toolReveal.ts`.

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

Each bookmark stores `{conversation_id, message_uuid, source, note,
snippet, created_at}`, and for a message of a subagent transcript also
`agent_id` and `agent_description` (issue #96; the description is a
snapshot, like the snippet).

- A set `agent_id` must match `^[A-Za-z0-9_-]{1,64}$`, else 422. The route
  does not look the agent up: a bookmark can outlive its file.
- Identity is `(conversation_id, agent_id, message_uuid)`. A bookmark from
  before #96 has no `agent_id` and is a bookmark on the session.

### UI

- A **star icon** lives on each message bubble (visible on hover, always
  visible when bookmarked). Clicking toggles the bookmark for that
  message.
- The right-pane tab strip in the SearchPanel has a **Bookmarks** tab
  next to **Search**. Selecting it renders `BookmarksPanel` instead of
  the search UI. Selection is persisted via `rightPaneTab` in settings.
- Bookmarked messages get a small star badge inline so the user can spot
  them while scrolling the conversation.
- **On an agent page** (§19), the star and the `b` key bookmark the message
  with the agent. The same message uuid on the session and on an agent are
  two bookmarks.
- In the Bookmarks tab, a bookmark on an agent shows the agent's
  description, else "Agent <id>". A click opens the agent page at the
  message (`/conversations/<session>/agents/<agent>?m=<message>`). The
  Markdown export names the agent the same way and links to the agent page.

---

## 16. Conversation header: copy buttons

You often need a session's name or one of its paths somewhere else: in a
terminal, a note, or a message. Every value in the Conversation Pane
header has a one-click copy button, so you never select a long path by
hand. A control works the same for every item in its scope, so all the
buttons follow the same rules.

| Value | Shows when | Where | Tooltip |
|---|---|---|---|
| Session name | The session has a name | Icon only, after the title, before the pin button | "Click to copy session name" |
| Session UUID | Every source but Codex | **Details**, after "Session ID:" | "Click to copy UUID" |
| Thread ID | Codex sessions (the session UUID; `codex resume <id>` takes it) | **Details**, after "Thread ID:" | "Click to copy thread ID" |
| Codex name | Codex sessions that Codex named; else the text "none" and no button | **Details**, after "Codex name:" | "Click to copy Codex name" |
| Codex home | Codex sessions | **Details**, after "Codex home:" | "Click to copy Codex home" |
| File path | The API gives one | **Details**, after "File:" | "Click to copy file path" |
| Project path | Claude Code sessions | **Details**, after the folder icon and "Project:" | "Click to copy project path" |
| Git branch | Claude Code sessions with a branch | **Details**, after the branch icon and "Branch:" | "Click to copy git branch" |
| Sandbox path | Cowork sessions | **Details**, after "Sandbox path:" | "Click to copy sandbox path" |

Rules:

- A button MUST copy the exact value from the API, with nothing added or
  removed. A path that the header truncates is still copied in full; a
  Windows path is copied as the API returns it.
- The Codex home shows as `~/...`, but its button copies the absolute
  path (plan D14).
- An unnamed session MUST NOT get a title copy button. Its title shows
  "Untitled", which is not its name.
- The title copy button shows only the icon, and its accessible name is
  "Copy session name". The title itself stays plain, selectable text: a
  heading that copies on click would surprise the user.
- The other buttons show the copy icon, then the value in a monospace
  font. Every value in **Details** has a label in front of it, for
  example "Project:", because the label says what the value is. The
  folder icon and the branch icon stay in front of their labels.
- A button copies the value only, never its label.
- After a successful copy, the copy icon changes to a green check mark
  for 2 seconds. A second click restarts the 2 seconds.
- If the clipboard write fails, an error toast says so, for example
  "Failed to copy project path to clipboard.", and no check mark shows.
- Each button has its own check mark; a click on one button never clears
  another.
- The check mark belongs to the value that was copied. The Conversation
  Pane stays mounted when you open another conversation, so:
  - opening another conversation MUST clear the check mark at once;
  - a clipboard write that finishes after you opened another
    conversation MUST NOT show the check mark on the new value.

Implementation: `CopyValueButton`
(`frontend/src/components/conversation/CopyValueButton.tsx`) owns each
button's state. It reads no context (CLAUDE.md, "Performance Work",
rule 1). Plan: `PLANS/2026.10.28-header-copy-buttons.md`.

**Notices.** Below the **Details**, above the messages, the header lists
the conversation's `notices` (`NoticeList.tsx`), one line each, in the
order of the API. A Codex session can have notices such as
"incomplete: <reason>" or "Imported from Claude Code": they tell you that
the viewer may not show the whole history. A session with no notices
shows nothing.

**Codex history.** A Codex fork or subagent continues another thread.
To follow the work across threads, the **Details** of a Codex session
link it to its parent and to its children
(`CodexHistoryLinks.tsx`):

- **"Parent:"** shows when the session has a `parent_uuid`: the fork
  parent, else the subagent parent. It holds one link.
- **"Forks and subagents:"** shows when the session has `child_uuids`:
  one link per child, oldest first.
- A link shows the title of that session, or its id when the title is
  empty. A click opens that session in the Conversation Pane.
- The titles come from one list of every Codex session, archived ones
  included. The list does not obey the Conversation List filters, so a
  filter never removes a link.
- A parent that is not in the archive shows its id as text, with no
  link.
- Other sources show neither row.

When Explorer can rebuild the history of a compaction, a rollback, or a
history base, the session shows no "incomplete:" notice for it. A
history base that is not in the archive keeps the notice
"incomplete: history_base missing".

A Codex tool call that has no item (for example a failed `apply_patch`)
shows as a tool call with its output, at its place in the turn. Show Tools
hides and shows it like any other tool call. Before 1.2.0 the viewer and
search left out such a call. The outer code-mode `exec` is a Code Mode run
(§18), not a tool call.

---

## 17. Commands, Claude Code's own rows, and the model of each reply

You read a session to know what happened, and to judge each reply. The
Conversation Pane shows what each command printed, which rows Claude
Code wrote itself, and which model wrote each reply (issue #52).

**The output of a slash command.**
- The output shows under its command, in a monospace block labelled
  "Claude Code". The program answered, not a model, so the label is not
  "Claude".
- The output is part of the command's marker (`command_output`), so the
  list's message count does not change.
- ANSI escape codes are removed; the text stays.
- Output longer than 20 lines shows the first 20 lines and a **Show all**
  button.
- stderr output shows in the same block, marked as an error ("Claude Code
  (error)").
- Older Claude Code writes the command as user rows; newer Claude Code
  writes `system` rows of subtype `local_command`. Both give the same
  marker.
- `/compact` keeps its own compact marker.
- The Markdown, PDF, and bundle exports and "Copy as Markdown" show the
  command and its output. An argless marker with no output (for example
  `/clear`) stays out of the exports, as before.

**Rows that Claude Code wrote.**
- A `!` shell command and its output show as one block labelled "Shell",
  in monospace, with no "You" label.
- A task notification, a bash notification, a scheduled task, or a system
  reminder shows as a muted, folded row labelled with its kind, for
  example "Task notification". Click the label to open it. The XML tags
  are removed.

**The model of each reply.**
- A reply is labelled with the source's assistant name and the model
  name, for example "Claude Opus 5.5", "Claude Fable 5.1", or
  "Codex gpt-5.6-codex".
- A model id that is not a Claude id shows as it is.
- A row that Claude Code made itself (model `<synthetic>`, for example an
  API error) is labelled "Claude Code".
- A reply with no model shows the assistant name alone.
- Claude Code and Cowork record the model of each reply. When a session
  switches models with `/model`, the replies before the switch name the
  old model, and the replies after it name the new one.
- Claude Desktop and Codex record one model for the conversation. Their
  replies use it, and the label's tooltip says "From the conversation's
  model setting. Claude Desktop does not record the model of each reply."
  (or "Codex").
- The header badge shows the model name; its tooltip is the raw model id.
- The same name shows in the tree view, the exports, "Copy as Markdown",
  and the MCP server's `get_messages`.

Implementation: `backend/model_names.py` and its twin
`frontend/src/lib/modelName.ts`; `CommandOutputBlock.tsx`,
`ShellBlock.tsx`, and `NoticeRow.tsx` in
`frontend/src/components/message/`.

---

## 18. Code Mode runs (issue #90)

In Code Mode, Claude writes a script that makes many tool calls in one
step. Explorer shows each run as one block, so the reader sees the code,
each call with its input and output, and what went back to the model.

- **Sources:**
  - Claude Code: the `REPL` tool (CC 2.1.323 and later,
    `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`). Cowork sessions fold their runs
    the same way.
  - Codex: the `exec` tool, and the `wait` calls that continue its cell.
    `notify()` outputs join the run; a failed script's "Script error:" text
    is its error, not a value it returned.
- **Show Code Mode:** a checkbox in the conversation toolbar, next to Show
  Tools.
  - It shows only when the conversation has a run.
  - Off by default, and not persisted, like Show Tools.
  - It is independent of Show Tools: Show Tools never shows or hides a
    run, and Show Code Mode never shows or hides a tool call.
- **The block:** a dark terminal bubble.
  - The title bar: "Code Mode", the language, the status (completed,
    failed, terminated, running, unknown), the line count, "background"
    for a background script, Copy (the script), and Expand.
  - The script, highlighted (`rehype-highlight`, `github-dark`), with line
    numbers. The bubble cuts it after 15 lines; "Show all N lines" shows
    the rest.
  - The calls, numbered in order: name, input, and output. A refused call
    and a call that failed are marked.
  - "Returned to the model", "Cut output", stdout, stderr, the error, and
    the refused calls, each only when present.
    - "Cut output" is Claude Code's note when it cut a long output. It
      names the file with the uncut text. It is not part of stdout.
  - "Media returned to the model": the images and documents that inner
    calls read and that went back to the model with the run.
    - Each image shows. Each document is a download link, "Download
      document N (<media type>)".
    - A foreground run has them in its result. A background run has them
      in the event that ends it.
    - One `Poll` result can end several Claude Code scripts. Each run gets
      the media of its own event (`toolUseResult.media`, one list per
      event). Without that list, each run gets the media that its event marks
      ("image bytes moved to the REPL result"), in order. If the marks do not
      match the media, no run of that result shows media (issue #103).
    - Codex: an `exec` script that returns an image (for example from
      `view_image`) has it as a `data:` URL in its output. That URL is
      data inside the record, not a reference, so the image shows.
    - Any other URL is never fetched; its image stays the "[Image]" label.
  - A background script with no result says "Still running".
    - A background script ends with a `poll_events` record or with the
      result of a `Poll` call. Only an event with a nonce from the "authentic
      event nonces" list counts; any other `<event>` text is quoted text.
- **The overlay:** Expand opens a dialog with the tabs Script (every
  line), Calls, Data flow, and Returned. Esc closes it.
  - Data flow is a sequence diagram: one lane for the model, one for the
    script, and one per tool. Time goes down.
  - The usual reason for Code Mode is a pipe: one tool's output goes into
    another tool, and only a small result goes back to the model. Neither
    transcript records the flow, but both record every input and output.
    So when an earlier call's output text is in a later call's input, the
    diagram draws "output of call 1 → call 2" from tool to tool, and the
    call list marks "input from call 1". Only outputs of 8 or more
    characters count (`backend/code_run_flow.py`).
  - Above the diagram: "Tool output: N characters. Returned to the model:
    M characters.", and which call outputs went back to the model.
- **Links, exact or none:**
  - Claude Code names the parent run of each inner call
    (`ranByToolUseId`), so every inner call is in its run.
  - Codex names no parent. A nested call links to the one cell that is
    alive at the time. With two or more cells alive it links to none: it
    stays a tool call, labeled "made by a Code Mode script (one of N
    running)".
  - A Claude Code call with a Code Mode id and no parent field is a tool
    call, labeled "made by a Code Mode script". In 2.1.331 these are the
    script helpers (`searchTools`, id `repl_…`) and the error record that
    Claude Code adds for a failed script (id `repl-error-…`).
- **Tested on real sessions:** `backend/tests/fixtures/cc_code_mode/` and
  `backend/tests/fixtures/codex_code_mode/` hold recorded sessions (synthetic
  prompts and files, cleaned by `scripts/scrub_fixture.py`).
- **Search:** with Show Code Mode off, the search does not look inside
  runs. With it on, the script, the calls, and what the run returned are
  searchable. Turning it on or off runs the search again. A hit in a run
  shows the whole script and marks the match.
  - Cmd+G and a click on a result center the first match in the bubble,
    not the bubble. A run with a hit is often taller than the pane, and a
    centered bubble hid its match (`scrollBubbleIntoView`).
- **Exports** (Markdown, the bundle, PDF, Copy as Markdown, and Copy on a
  bubble) follow Show Code Mode: on, each run is a `javascript` block,
  its numbered calls, and what it returned; off, no run text.
  - Markdown names the media by kind ("1 image (image/png), 1 document
    (application/pdf)"). PDF shows the images and names the documents.
  - MCP `get_messages` gives each media item as a stub (type, media type,
    and size in bytes), with no bytes, as for other image blocks.
- Code: `backend/cc_code_mode.py`, `backend/codex/projection.py`
  (`_CodeMode`), `frontend/src/components/message/blocks/CodeRunBlock.tsx`,
  `frontend/src/components/diagram/SequenceDiagram.tsx`.

## 19. Subagent transcripts (issue #96)

A Claude Code session can start subagents. Each agent writes its own
transcript, and the answer you need is often in one of them. Explorer opens
and searches these transcripts like a session.

- **Which transcripts:** for one session, the agent files in
  `<project>/<session>/subagents/agent-<id>.jsonl` and the older
  `<project>/agent-<id>.jsonl` whose first `sessionId` is the session.
  - Compaction runs (`acompact-*`) and side questions (`aside_question-*`)
    are Claude Code's own runs, so they are left out.
  - A file whose real path is outside the session's project folder is left
    out (a symlink that points elsewhere).
- **The agent page:** `/conversations/<session>/agents/<agent>` shows the
  transcript in the Conversation Pane.
  - Above the title, a breadcrumb: "Session › <the agent's description, or
    its id>". "Session" opens the session.
  - The title is the description from the agent's `.meta.json`, else
    "Agent <id>". The Details add an "Agent ID:" row.
  - Show Tools, Show Code Mode, Show Compactions, Copy as Markdown, the
    Markdown export, the bundle, and the PDF work as for a session. The
    export file name holds the agent id.
- **Ways in:**
  - The session's Details have a "Subagents:" row, with one link per agent
    (only when the session has one).
  - A search hit inside an agent.
  - The URL.
  - The Conversation List does not change: it shows sessions only.
- **Search:** each agent transcript is its own result, under its session's
  name, with the agent's description (or id) on the card. A click or Cmd+G
  opens the agent page at the message.
  - The scope pin, the bookmarks scope, the active filter, the project, and
    the source apply to an agent through its session.
  - Show Tools, Show Code Mode, and Show Compactions apply as for a session.
  - An agent's description gives no title hit.
- **MCP:** a `list_sessions` hit inside an agent carries `agent_id`;
  `get_session_outline`, `get_messages`, and `export_session` take it.
- **What #46 adds later:** the Agent call block in the parent session, links
  from each call to its agent, nested agents, workflow agents, agents whose
  transcript is gone, and the Activity panel.
- **Bookmarks:** a bookmark made on an agent page holds the agent id and
  opens the agent page (§15).
- Code: `backend/cc_subagents.py`, the route in
  `backend/routers/conversations.py`,
  `frontend/src/components/conversation/SubagentLinks.tsx`.

---

## 20. Messages between sessions (issue #91)

Sessions can message each other: Claude Code with `SendMessage`, and Codex
agents within one thread tree. When you read one side of an exchange, the
other side is one click away, and a message that arrived during a turn shows
where it arrived.

- **A received message** shows as a row: "Message from @<name>" and the body.
  - The envelope does not show: no prefix line, no tags, no safety paragraph.
  - A message that arrived during a turn (a `queued_command` attachment)
    shows at its place in the turn, not at the end.
  - One row per message: a second copy with the same `msg_id` does not show.
  - The row shows also with Show Tools off. Its sender label in the exports
    and in MCP is "Message from @<name>".
  - A mode chip shows the sender's mode when it is not "prompting".
- **Links, both ways:** "From <title> →" on the received row, and "Sent to
  <title> →" on the send block (with Show Tools on). A link opens the other
  session at the message.
  - Claude Code links by `msg_id` only: the sender's result and the
    receiver's `origin` have the same id. Never by name, socket, or time.
  - "not in the archive": the other side is not in the archive.
  - No link and no such text: the search index cannot answer (no index, or
    the cloud-sync gate is closed).
- **"Sent because of: <first line>"** on a received row: the sender's prompt
  that led to the send (the nearest earlier prompt or received message in the
  sender's session). It opens the sender's session at that prompt.
- **Idle notice** (`notify_when_idle`): the row keeps its text, links to
  your own request in this session ("Your request →"), and to the peer.
- **Codex agent messages** show as `agent_message` tool calls, with the
  payload as the output. An encrypted payload shows "[Encrypted agent
  message]"; the ciphertext never shows.
  - Exact link: the sender's `spawn_agent` or `send_message` call with the
    same payload, within one thread tree.
  - Else a link to the sender's thread only (for example a worker's final
    answer to the root).
  - A worker's copy of its parent's turns is never a link target.
- **Details: "Messages with:"** lists the other sessions, in the order of the
  first message. A session in the archive is a link; another shows its name.
- **MCP:** `get_messages` gives `peer_ref` on each such message (and on the
  tool call, with `include_tool_calls`); follow it with `get_messages`.
- **Not yet:** the Messages tab, session state, and the link across
  machines (`bridge-session`).
- Code: `backend/cc_peer_messages.py`, the `peer_messages` table in
  `backend/search_index.py`, the join in `backend/store.py`,
  `frontend/src/components/message/PeerMessageRow.tsx`,
  `frontend/src/components/conversation/PeerSessionsRow.tsx`.

## 21. Tickets (issue #93)

You start sessions on one ticket in many ways: in a terminal, in an editor,
in the Desktop app, on another machine. The ticket page goes from the ticket
to every session and every turn that worked on it, and the PRs that came
out. Explorer reads only the transcripts. It calls no GitHub, Linear, or
Jira API and assumes no launcher.

- **References:** `backend/tickets.py` finds them in the drift pass.
  - `owner/repo#84`, and the URLs `github.com/owner/repo/issues/84`,
    `.../pull/84`, and `app.graphite.dev/github/pr/owner/repo/84`, in the
    title and in the text of user and assistant messages. Tool calls, tool
    output, and thinking do not count.
  - A bare `#84` needs a boundary: `(#84)` and `PR #84,` count;
    `abc#84`, `#84a`, `#0`, and `# Heading` do not.
  - A bare `#84` in message text counts only when the session's repo is
    known (see below). In a title or a branch it always counts; with no repo,
    it is the ticket "#84 (unknown repo)". The index keeps the other refs, so
    a later PR in the same folder makes them count.
  - Linear and Jira keys such as `ENG-123`: see "Linear and Jira".
  - The git branch: a path segment that starts with digits and then "-" or
    the end (`fix/84-search-tools`, `fix/84`).
  - Claude Code `pr-link` records: an exact link to a PR.
- **Limits:** at most 200 references per message and 2000 tickets per
  session; Explorer drops the rest in text order. The scan is linear, so a
  huge message cannot stall the index.
- **The ticket key** is `owner/repo#84` (lower case), `#84` when the repo
  is not known, or `ENG-123` for Linear and Jira. The same number in two
  repos is two tickets.
- **Linear and Jira** (`config.json`, no Settings UI yet). List the key
  prefixes in upper case; the default is empty, and then only URLs count:

  ```json
  {"ticket_keys": {"linear": ["ENG", "WEB"], "jira": ["OPS"]},
   "ticket_hosts": {"linear": "example-team", "jira": "example.atlassian.net"}}
  ```

  - `ENG-123` in text counts only when `ENG` is listed, with the
    boundaries of `#84`. `UTF-8` and `SHA-256` never count unless listed.
  - A branch counts when a segment holds a listed key in any case
    (`me/eng-123-fix-search`).
  - URLs always count: `linear.app/<workspace>/issue/ENG-123[/slug]` and
    `<site>.atlassian.net/browse/OPS-7`.
  - After a change to `ticket_keys`, restart Explorer: the index then reads
    every file again.
- **The repo of a bare #84** is the session's repo, found at query time:
  - a github.com Codex remote (`session_meta.git.repository_url`);
  - else the repo of the session's pr-link records, when there is one;
  - else the one repo that other sessions in the same project folder have;
  - else "unknown repo". Explorer never reads `.git/config`.
- **The ticket page** `/tickets/<owner>/<repo>/<n>` (`/tickets/_/_/<n>` for
  the unknown repo), or `/tickets/key/ENG-123`:
  - the heading `owner/repo#84`, `#84 (unknown repo)`, or `ENG-123`;
  - "Open in Linear (acme)" or "Open in Jira (acme.atlassian.net)". Explorer
    builds the link from a host; it never copies a URL from a message:
    - the host in `config.json` `"ticket_hosts"` (a Jira host, a Linear
      workspace), when it is set;
    - else the host that most references to the key name (a tie goes to the
      earliest reference);
    - else, for Linear, `linear.app/issue/ENG-123`; Jira has no link.
    - The system (Linear or Jira) comes from `ticket_keys` first, then from
      the chosen URL.
  - the PRs from the pr-link records of the listed sessions (open in a new
    tab). Only a `https://github.com/<owner>/<repo>/pull/<n>` URL shows;
    Explorer drops any other `prUrl`;
  - one row per session, newest first: the name (a link), the source, the
    entrypoint chip, the state and the machine when the archive has them;
  - under each session, every message (turn) that names the ticket, and the
    title, branch, and PR references. A message reference opens the session
    at that message.
  - "No sessions refer to this ticket." when there are none. "The ticket
    index is not ready yet." while the search index builds.
- **Details: "Tickets:"** in the conversation header links to each ticket of
  the session.
- **The entrypoint chip** (list row and header) names the launcher: CLI,
  Desktop, VS Code, SDK, GitHub Action, Codex CLI, Codex exec, Codex VS
  Code. An unknown value shows as it is. The tooltip is the raw value.
- **Filters:** a named filter can match on Ticket or Entrypoint (see
  "Composable filters"). MCP: `list_sessions(ticket="owner/repo#84")`, or
  `ticket="ENG-123"` when the prefix is listed or a URL in the archive
  names it.
- **"Copy start command":** Settings → "Start a Session" holds an optional
  command template, empty by default. With a template, the ticket page shows
  the button. It fills the template and copies it to the clipboard. Explorer
  never runs it.
  - Placeholders: `{repo}`, `{issue}`, `{title}` (the newest session's
    name), `{cwd}` (the newest session's project folder).
  - `{repo}` and `{issue}` are checked (`owner/repo`, digits).
  - **Put `{title}` and `{cwd}` outside quotes.** Explorer fills each as
    one single-quoted shell word, so no value can run a command. Double
    quotes are not enough: interactive bash and zsh still run history
    expansion (`!#`) inside them.
    - A newline becomes a space. A title that starts with `-` gets a
      leading space, so no program reads it as a flag.
    - A `{cwd}` that is not an absolute path fills as `''`.
    - When the template has `{title}` or `{cwd}` inside quotes, Settings
      shows "Put {title} and {cwd} outside quotes: Explorer quotes them.",
      and the ticket page shows no button.
  - Examples:
    - `claude --bg "Fix {repo}#{issue}: "{title}`
    - `claude --cloud "Fix {repo}#{issue}"`
    - `cd {cwd} && claude "Fix {repo}#{issue}: "{title}`
    - Herdr: the Herdr command that starts an agent in a folder, with
      `{cwd}` outside quotes (see the Herdr docs).
- **Not yet:** the Messages view scoped to a ticket (#91, the Messages tab);
  GitLab, custom patterns, and a Settings UI for `ticket_keys`.
- Code: `backend/tickets.py`, the `ticket_refs`, `ticket_prs`, and
  `ticket_sessions` tables in `backend/search_index.py`, the join in
  `backend/store.py`, `backend/routers/tickets.py`,
  `frontend/src/components/tickets/`.

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
- **Every fetch first copies the local sources into the archive** (the
  Codex sessions, from slice 1 of the Codex plan; the Claude Code and local
  Cowork sessions, from #92). The copy runs before the capture, so it runs
  also without credentials.
  - Refresh copies for at most 20 s; the watcher copies the rest.
  - It waits up to 60 s for another writer (the watcher, the CLI, an MCP
    server). If the lock stays busy, the toast shows "Another Claude
    Explorer process is copying sessions into the archive. This
    fetch skipped the copy; the next one does it." (`phase: "codex"`), and
    the claude.ai fetch continues.
- **A Claude Code or local Cowork session stays in the list after Claude
  deletes its file.** The watcher, the backstop, and Refresh copy each
  session file (also the subagent files and the Cowork sidecar) into
  `<data folder>/machines/<machine id>/`. The copy only grows.
  - While the live file exists, the list, the viewer, search, and MCP show
    the live file. When it is gone, they show the archive copy, with no
    notice. MCP reads the copies, but does not copy.
  - The first copy writes about as much as `~/.claude/projects` holds (for
    example 5.8 GB). If the data folder is cloud-synced, the cloud-sync
    consent (below) also sends these sessions to that cloud service.
- **The cloud-sync gate refuses a fetch** (see "Cloud-sync gate" below).
  Refresh, **Fetch New**, **Full Refresh**, and **Re-download** get HTTP
  409 before any network call. The message names the folder and the
  provider.

### Cloud-sync gate

Work transcripts must not reach a cloud service by accident. So Explorer
writes nothing into a folder that a cloud client syncs, until the user
allows it (plan D13).

- **What counts as cloud-synced:** the data folder, `search-index.sqlite`,
  or `cc-images/` is under iCloud Drive, a Dropbox root, a OneDrive root, a
  Google Drive root, or `~/Library/CloudStorage/`. Symlinks are followed.
  When detection cannot decide, the folder counts as cloud-synced.
  - Detection is best effort: a sync client that is not in this list is
    not detected.
- **While the gate is closed:**
  - Refresh and the other fetch actions are refused (HTTP 409).
  - A settings change is not saved on the server (`PUT` and `PATCH
    /api/preferences` get HTTP 409). The page keeps it in the browser's
    local storage.
  - The archive copy of the local sources is skipped.
  - The search index and the list-summary cache write nothing. Search
    uses its linear-scan fallback, and the list reads each session.
  - Startup does not create the data folder or run the layout migration.
  - New Claude Code images go to `~/.claude-explorer/cc-images-pending/`
    (the `cc-images/` layout), and the viewer shows them from there. If
    that folder is cloud-synced too, they go to the platform cache folder.
    No image is skipped.
- **The cloud-sync gate banner:** while the gate is closed, a persistent
  banner (`CloudSyncGateBanner.tsx`) shows at the top of the app, below the
  corrupt-config banner. It reads `cloud_sync_gate` (`provider`, `folder`,
  `data_dir`) from `GET /api/config`, which is `null` while the gate is
  open. It names the provider and the folder. It has no
  close button, because the refused writes need an explanation. It shows
  also when the data was in a cloud folder before the upgrade. Its
  **Allow writes…** button opens the consent dialog.
- **The consent dialog** (`CloudSyncConsentDialog.tsx`) has three steps:
  1. A warning that names the provider and the folder. **Continue**.
  2. A check box: "I understand that <provider> uploads my conversation
     data to its cloud." **Continue** stays off until it is checked.
  3. A text field for the typed provider name. **Allow writes** stays
     off until the text matches the name (spaces at the ends and case do
     not matter).
  - **Cancel** at any step sends nothing. The dialog opens again at
    step 1.
  - An error (for example 409, when the folder or the provider changed)
    shows in the dialog, and the dialog stays open.
  - On success the dialog closes, the page reads `/api/config` again, and
    the banner goes away.
- **To allow the writes:** step 3 of the consent dialog calls
  `POST /api/write-consent/cloud-sync` with the folder and the provider
  that it showed. The route needs the header
  `X-Claude-Explorer-Action: allow-cloud-sync-writes` and a same-origin or
  loopback `Origin`. It writes `allow_writes_in_cloud_sync` (the resolved
  data folder) into `~/.claude-explorer/config.json`.
  - A moved data folder no longer matches the key, so the gate closes
    again.
- **After the opt-in:** the next watcher scan moves each pending image
  into `cc-images/` (copy, `fsync`, size check, then delete the pending
  copy).
- **To revoke:** delete the key. The data that is already there stays.

### Which sessions the list shows

The user may want to see and search any session that had an effect on
their work (AGENTS.md, "Guiding Principles").

- The list shows every session that has a turn: a user message or an
  assistant message.
- A session with only an assistant turn shows too. Before 2026-11-14 the
  list hid a Claude Code or Cowork session that had no user message, but
  search still found the Claude Code one.
- The list hides only a session with no turns. Examples:
  - a 0-byte Claude Code file;
  - a file with only metadata rows;
  - a remote task that was opened but never got a prompt.
- The list, the viewer, and search agree: each one shows the same sessions.

### Emoji in titles

A title from the API can start with an emoji, for example "⚡ Weekly
report". In color, that emoji looked like one of the app's colored source
icons, so users did not read it as part of the title (issue #44).

- **Every emoji in a title renders in grayscale.** `TitleText`
  (`frontend/src/components/TitleText.tsx`) puts each emoji in a
  `span.title-emoji` with Tailwind's `grayscale` class.
- **The title text does not change.** Copy, export, search, and filters
  use the exact API title, emoji included.
- **A hover shows the exact title** on the list row and on the header.
- **Places that use `TitleText`:**
  - the Conversation List row, and the subagent row;
  - the conversation header;
  - the search result card: the name line, and the snippet of a title match;
  - the pin chip and the "No matches … in <name>" line of the Search Pane;
  - the items of the pin menu.
- **A new place that shows a title MUST use `TitleText`.**
- **Places without the style:**
  - Message text, also a message snippet in the Search Pane. An emoji
    there is message content.
  - Exports (Markdown, PDF, MCP) and file names. They keep the exact
    title, and no app icon is near it.
  - Native tooltips, and the fetch progress text (toast and Fetch dialog).
    These are plain text, with no source icon beside the title.
- **Why not `font-variant-emoji: text`:** a test on macOS (2026-11-12)
  showed that it does not work.
  - Chromium 156 drew a text glyph only for ⚡.
  - WebKit (Playwright 27.2) does not support it.
  - Firefox 157 drew empty "missing glyph" boxes. After that, it also drew
    boxes for the same emoji in other elements of the page.
  - `filter: grayscale(1)` works in all three.
  - Do not add `font-variant-emoji` anywhere in the app.

### Conversation list dim states

In addition to the scope-pin dim (Section 5), the list dims rows for:

- **Phantom sessions** (empty Claude Code sessions): hidden by default,
  toggled by the "Empty" checkbox in the sidebar header.
- **Group-by-project** mode: only available when the source filter is
  "All Agents", "Claude Code", or "Codex". See "Conversation List
  filters".

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
  shown for the sources "All Agents", "Claude Code", and "Codex"
  (each source whose registry flag `has_projects` is true).
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
  `show-only`), a mode (`glob` / `regex`), and a **Match on** target
  (default Title). Ticket matches the session's ticket keys
  (`owner/repo#84`, `#84`); Entrypoint matches the raw launcher value
  (`cli`, `codex_cli_rs`). A session with no value for the target
  matches no pattern, so Hide keeps it and Show only drops it (issue #93). The Behavior controls
  what happens to a conversation that matches at least one of the
  atom's patterns: *Hide matches* drops it; *Show only matches* keeps
  it (and drops everything else).
  - Patterns match the exact title text, case-insensitively. A title
    such as `⚡ Weekly report` starts with the emoji, so `^Weekly`
    does not match it. Write `Weekly report$` or `^⚡ Weekly report$`.
    The list draws that emoji in gray, but it is still part of the
    title (see "Emoji in titles").
  - **Glob:** `*`, `?`, and `[abc]` work as in a shell, and a pattern
    with them must match the whole title. A pattern without them
    matches anywhere in the title.
  - **Glob anchors:** a leading `^` or a trailing `$` anchors the
    pattern at that end, as in a regex. `^Daily digest$` matches only
    the title "Daily digest".
    - Everywhere else, `^` and `$` are literal: `$5 ^up` matches titles
      that contain "$5 ^up".
    - A pattern of anchors only (`^`, `$`, `^$`) is literal.
    - A backslash escapes nothing in glob mode. To match a literal `^`
      at the start or `$` at the end, write it twice: `^^caret`,
      `costs $$`.
    - With `*`, `?`, or `[`, the anchors change nothing:
      `^Weekly*report$` is the same as `Weekly*report`.
  - **Regex:** a JavaScript regular expression. An invalid regex
    matches nothing.
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
  - A help line under the patterns gives the rules of the current mode.
    It changes when the user picks the other mode.
  - The summary line under it repeats the Behavior and the patterns.
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
