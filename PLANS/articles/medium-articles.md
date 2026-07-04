# Medium Article Series: *Unlocking Your Claude History*

> Living plan + progress tracker. Mirrors `/Users/rpeck/.claude/plans/bright-jingling-comet.md` (the approved plan from the `/plan` flow).

## Context

A Medium series on this project — not one post. Three topics are fixed: **(1)** what it is + use cases, **(2)** the web UI, **(3)** the MCP server on Claude Code + Desktop across macOS/Windows/Linux. Plus **(4–5)** a creation-story on building it with Claude Code, mined from the actual build transcript.

The raw material for (4–5) lives in the `claude-sessions` MCP server: one main build session of **5,207 total messages** — 5,006 on the active branch (201 on inactive branches), of which 2,624 are recorded as `sender=human` but only **312 are real user-authored prompts** (the rest are `tool_result` payloads that Claude Code tags as `human`). The plan's central job is a disciplined extraction pipeline that lands on disk as citable artifacts in `PROCESS/`, then drafts five article parts in the user's established Medium voice.

## Shape

```
                           ┌─────────────────────┐
  5,207-msg build session ─┤  claude-sessions    │
                           │   MCP tools         │
                           └──────────┬──────────┘
                                      │
                                      ▼
   ┌──────────────────────────────────────────────────────────────┐
   │                       PROCESS/                                │
   │  outline → phases → phase_NN_*.md (citable, msg_uuid-linked) │
   └──────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  synthesis:  themes / quotes / timeline / use_cases           │
   └──────────────────────────────────────────────────────────────┘
                                      │
        ┌──────────────┬──────────────┼──────────────┬──────────────┬──────────────┐
        ▼              ▼              ▼              ▼              ▼              ▼
     Part 1         Part 2         Part 3         Part 4         Part 5         Part 6
     Overview       Web UI         MCP server     Build story 1  Build story 2  LLM Council
     (README +      (screenshots)  (README +      (phases 1–N)   (phases N–end) (methodology +
      use_cases)                    meta demo)                                   catches)
```

Single-threaded. Subagents write files, return ≤300-word summaries. Pauses between phases let the user redirect.

## Confirmed Decisions

| Decision | Choice |
|---|---|
| Series title | *Unlocking Your Claude History: A UI and MCP Server for Your Conversations* |
| Publication | `@raymondpeck` directly on Medium |
| Voice source | User's "Best Practices for Modern REST APIs in Python" series |
| Voice artifact | `PROCESS/99_styleguide.md` — pasted into every drafting subagent |
| Part 1 hook | Real story, first person — the "lost access to my account" narrative is sketchy; it makes me seem like I'm stealing IP; rather, the narrative should be around searching and accessing full session transcripts with a UI that shows the entirety of each session, plus programmatically via Claude Code and Claude Desktop (UI first, then MCP) |
| Part count | 3 fixed (overview, UI, MCP) + 1–3 creation-story parts; decided after synthesis |
| Code in articles | Real repo code, lightly trimmed |
| Screenshots | Captured via Playwright at draft time; user approves/retakes |
| Session scope | `claude-desktop-message-exporter` project only; 7 Gmail-agent sessions skipped |
| Target length | 2,500–5000 words per part; 600–900 for series intro |
| Pause points | 7 approved (before start, after scaffold, after phase boundaries, every 5 phases during extraction, after synthesis, after each article part, before final PII pass) |
| LLM Council content | **Folded back IN as Part 6** (2026-05-22 reversal of the 2026-04-20 split-out decision). Trigger: a 2026-05-21 multi-day code-review pass driven by a new `llm-council-code-review` agent (heterogeneous GPT-5.2 + Gemini-3-Pro + Opus 4.7) produced a fresh batch of concrete, citable catches — including a shipping crash in `claude-explorer fetch` that no test covered. The cumulative receipts (Phase 19/20 catches from the build session + this 2026-05-21 sweep + the user's existing LinkedIn material) now justify a full series part rather than a forward-pointer. Detailed plan in `PLANS/articles/part6_llm_council_plan.md`. The seed doc at `PLANS/future_articles/llm_council.md` is being absorbed into the new part-plan; the future-articles file remains as historical record. |
| Part 7 (perf postmortem) | **Added 2026-05-23, second wave folded in 2026-05-25 and 2026-05-28.** A multi-day perf hunt produced a 5-belief falsification chain on search-typing lag, plus dramatic numbers across the stack (95% warm-switch reduction via virtualization; 4× cold-load; 13.6× cold-search via FTS5 projection table; 7.5s→13ms Cmd-F; gzip-event-loop trap; React.memo bypass via useContext). Standalone Part 7 — *Hunting Latency: A Performance Postmortem in Practice* — rather than fold into Part 5 (would bury the dramatic numbers) or Part 6 (would dilute Council methodology focus). **Primary receipts** (must be incorporated): `PLANS/POSTMORTEM-search-typing-lag-2026-05-22.md` (the 5-belief falsification chain, the searchable storm narrative), `PLANS/PERFORMANCE_BASELINE_2026-05-23.md` (numbers, methodology, fixtures). **~31 perf-related commits pre-session** (run `git log --grep='perf\|profile\|virtualiz\|FTS5\|warmup\|cold-' --before=2026-05-27 main -- backend/ frontend/` to enumerate; the consolidated commit `51f9def perf(search,api,ui): typing-lag hunt + virtualization + lifespan warmups + v11→v12 Cowork-search recovery` is the main packaging). **Second-wave additions** (added after the original Part 7 plan was sketched): the 2026-05-25 perf hunt commit `51f9def` plus the schema self-healing `7832b86` (the lifespan task that recovers Cowork rows orphaned by an earlier schema bump). **Third-wave addition (2026-05-28, F53)**: virtualizer-scroll-recovery in `ConversationPage.tsx:680-704` — the auto-scroll effect silently no-op'd when the target message was off-screen (the virtualizer hadn't mounted it; `messageRefs.current.get` returned `undefined`). Fix: fall back to `virtualizer.scrollToIndex(visIdx, { align: 'center' })`. TDD pin: `frontend/e2e/keyboard-nav-alt-jump-virtualizer-recovery.spec.ts` uses a 600-message fixture (forces real virtualizer mount/unmount; the existing short-fixture keyboard-nav specs never exercised this path). F53's article-worthy beat: **automated tests can pass while UX is broken when the fixture isn't shaped like the production case** — when adding perf-driven indirection (virtualization, lazy mounting, suspense), every UX flow that touches the indirection needs at least one test against a fixture large enough to exercise it. F53 lives in `PLANS/articles/2026.05.27-react-doctor-notes.md`. **Detailed plan TODO** at `PLANS/articles/part7_perf_postmortem_plan.md` (next planning session). |
| Part 6 / Part 7 ordering | **Open question.** "Build → Reflect → Hunt" (current 1-7 order) tells story of evolving project. "Build → Hunt → Reflect" (swap 6 and 7) is stronger pedagogically since Part 7's perf work was itself Council-driven. Decide at Part 6 draft time. |
| Show Compactions toggle in Part 2 | Documented as a rendering-only filter (the conversation stays intact on disk), with multi-source detection (CC `isCompactSummary`, CC text-prefix, Cowork text-prefix) and title-leak filter. Schema-bump narrative reserved for Part 7. |
| Demonstrated-focus arbitration in Part 2 | Documented as a UX commitment: clicks and manual scrolls protect viewer position; Cmd+G / Enter / card click / bookmark click are explicit nav. Implementation reserved for Part 6 / Part 7. |
| Supervised CC image-cache watcher detection in Part 2 | Install-step warning sentence + caching-section banner / endpoint / log-line mention. Cross-OS detection via launchd / systemd / schtasks probes. |
| Time Machine restore script polish in Part 2 | Cowork coverage + `tmutil latestbackup` auto-detect + APFS mount-on-demand + `--continue-on-mount-failure` + fail-fast non-root. |
| Header-checkbox UX in Part 2 | Word-level: "toggle" prose replaced with "checkbox" prose where the conversation-header toggles are referenced; screenshot refresh shows the new affordance. |
| Unified compact toggle + rich /compact rendering | One source of truth: viewer + copy + Markdown + PDF all honor the same Show Compactions preference; rich-summary rendering replaces the stub trigger row in exports. The previous Settings-page `export.includeCompactContent` pref + checkbox got REMOVED in favor of the conversation-header checkbox as the single source of truth; future readers should not expect a Settings entry. |
| Truncation indicator (`N+` / `1000+`) in Part 2 | One-sentence user-facing note in the search section; full wire-format / capacity story stays in the existing truncation-disclosure subsection. The UI appends `+` to the displayed match count when the total exceeds the per-query limit, so a truncated count of 1000 renders as `1000+` (per `frontend/src/components/search/SearchPanel.tsx:473`). |
| Settings-page flash sidebar | Lives in Part 7 (the misleading-green-e2e sidebar). Part 2 stays out of it. Codified rule `CLAUDE-TESTING.md §5.15`. |
| Part 7 plan trigger | The 2026-05-25 perf hunt (commit `51f9def`) plus the schema self-healing (commit `7832b86`) constitute the second wave the Part 7 plan must capture; Part 7 plan TODO escalates. |
| TOC in the article series | **Hand-maintained**, H2-only, **HTML-anchor** syntax. This is the documented standard for **every part**, not just Part 2. Above each H2 sits an explicit `<a id="kebab"></a>` anchor; a `## Contents` list after the lede links to each one with `[Section Text](#kebab)`. The `kebab` id is the heading text lowercased, with `:` `,` `(` `)` `!` dropped and spaces turned to hyphens (so `## So: Should You Run This?` becomes `so-should-you-run-this`). **Where it resolves** (empirical 4-viewer click-test, 2026-06-01): **GitHub yes** (it resolves HTML `id` anchors, internally prefixing `user-content-` and lowercasing), **MacDown yes**, **Obsidian no** (Obsidian ignores HTML anchors; its readers navigate via the Outline plugin instead), **Medium no** (it assigns random per-block ids and strips the markup on import). There is **no single anchor syntax that jumps in GitHub, Obsidian, and MacDown all at once**: GitHub-kebab and Obsidian-verbatim ids are mutually exclusive. (Two earlier notes here were wrong in turn: a 2026-05-27 council guess claimed GitHub-kebab links also resolved in Obsidian, then a 2026-06-01 over-correction switched to an Obsidian-only `%20`-encoded scheme that broke GitHub. Click-testing settled it.) HTML anchors win because **GitHub is the most critical surface** and they also cover MacDown; Obsidian is the only loss, and its Outline plugin fills the gap. **Publish step: at Medium import, strip BOTH the `## Contents` list AND every per-heading `<a id>` anchor** (Medium renders neither). Maintenance: when an H2 is added, renamed, or removed, regenerate its `<a id>` and the matching Contents line with the kebab rule above. **Medium CAN carry a clickable TOC — but only as a post-publish step, proven 2026-06-02 (Part 2 twins).** The mechanism: Medium assigns every heading a stable per-block `id` hash on its own element (`<h2 id="7e05">`), and a same-page link of the form `<full-article-url>#<hash>` jumps to it. The hash is derived from the heading text, so it survives edits **unless you rename the heading** (then re-harvest that one). Recipe: **(1)** publish/import the article first (the hashes don't exist until it's live); **(2)** harvest each H2's hash from the rendered DOM — a Playwright/browser pass running `Array.from(document.querySelectorAll('article h2')).map(h => ({id: h.id, text: h.innerText}))` dumps them all (curl/WebFetch are Cloudflare-blocked on Medium; the `name` attribute is unused, the anchor is always in `id`); **(3)** build a bulleted list of `[Section](<full-url>#<hash>)` links; **(4) paste it RENDERED, not as raw Markdown** — Medium's editor does not parse Markdown, so raw `[text](url)` pastes as literal text, but pasting from a Markdown *preview* (GitHub/VS Code/Obsidian reading view) carries the live `<a>` anchors, fragments included, through Medium's HTML-clipboard handler. Verify by clicking a link in the **published/preview** view (in-editor jumps don't always fire). The harvested fragments + paste-ready link lists live in `articles/medium-toc-part2-{user-guide,deep-dive}.md` (scratch paste-sources, not published; the in-repo `.md` Contents blocks keep their GitHub kebab anchors — never put Medium hashes in the `.md`, they'd break GitHub). |
| Relative cross-links between Part 2 twins | The two twins link each other with relative `.md` paths: the userdoc's `[longer version](part_2_web_app.md)` and the long-form's `[user-guide version](part_2_web_app_userdoc.md)`. These resolve in Obsidian, MacDown, and GitHub but break on Medium (no repo file tree behind the post). **Publish step: at Medium import, replace each relative twin link with the published Medium URL of the target twin, or strip the link and keep the anchor text** when that twin is not yet posted. Same posture as the TOC strip above; the in-repo relative links stay for Obsidian and GitHub readers. Absolute GitHub `.md` links (`CONTRIBUTING.md`, `SECURITY.md`) resolve fine and need no change. (Council finding F19, 2026-05-29.) |
| Image syntax in the article series | **Standard Markdown** `![alt](Attachments/<file>.png)`, NOT Obsidian `![[...]]` embeds. GitHub renders `![[...]]` as literal text, so the embeds broke on the GitHub surface (co-equal with Medium). Standard MD renders on **GitHub and Obsidian** and re-imports to Medium. Rules: **(1)** relative path from the article file (`Attachments/<file>`; images live in `articles/Attachments/`). **(2) NO spaces in image filenames — use dashes** (`Pasted-image-<ts>.png`). `%20`-encoding is NOT enough: the raw file resolves at the `%20` URL (HTTP 200), but **GitHub’s blob-Markdown renderer mangles `%20` in *relative* image paths and shows a broken image** (verified by a failed 2026-06-01 push-test); renaming the 25 files space→dash fixed it. **(3)** the image file MUST be git-committed or GitHub 404s. **(4)** drop Obsidian `|width` hints (GitHub and Medium size to the content column regardless; width only mattered in Obsidian). Standard MD has no width control, so a **narrow crop** uses HTML `<img src="Attachments/Pasted-image-<ts>.png" alt="..." width="N">` wrapped in `<div align="center">` to center it (GitHub allows `align` on `div`/`p` but **strips inline `style=`**; `align` on the `<img>` itself does not center). Full-window shots stay standard MD. All forms confirmed rendering + sizing + centering in Obsidian (2026-06-01). **State 2026-06-01:** Part 2 twins + quickstart hero done — full-window/hero as standard MD, 9 sized crops as centered `<img width>` (250/306/400/400/450/514, restored from the original `|width`); all 25 referenced files renamed space→dash and committed. **Part 3’s 5 embeds left as `![[...]]`** (image files not yet in `articles/Attachments/`). **Publish step: at Medium import, re-insert images** (Medium re-hosts and ignores both `![[...]]` and relative paths). |
| Medium import method (DEFERRED to Part 3 publication) | **Clipboard paste fails on image-heavy posts.** Medium's editor fetches every image synchronously on paste and chokes; Part 2's 23-image long-form had to be pasted in blocks of ≤2 images (painful, 2026-06-01). **The better path is Medium's "Import a story"** (`medium.com/p/import`): it fetches a *URL* server-side and pulls images asynchronously, so no clipboard limit. It needs a **public HTML page with absolute image URLs**. `scripts/medium-prep.py` already emits standalone HTML with absolute `raw.githubusercontent` image URLs (+ strips TOC/anchors, resolves links); the only missing piece is hosting it as real HTML. **Plan:** enable **GitHub Pages** (source `main` `/docs`, add `.nojekyll` so raw HTML is served as-is), drop the generated `<part>.html` into `docs/`, push, then import from `https://rpeck.github.io/claude-explorer/<part>.html`. **Validate on the long-form first** (the worst paste case) to check the URL-importer's fidelity on code blocks + formatting before doing the rest. Pages is currently NOT enabled (checked 2026-06-01); enabling it is an outward-facing change but only serves already-public article content scoped to `docs/`. **Part 2 shipped via chunked paste; crops left column-width on Medium (Option B — no resized copies).** Revisit and set up Pages when publishing Part 3. |

## The Series (7 parts; was 5, +1 LLM Council added 2026-05-22, +1 Perf Postmortem added 2026-05-23)

### Part 1 — *What This Thing Is and Why You'd Want It*
Hook: the full text of every Claude conversation you've ever had — Desktop and Code — unified in one searchable UI, and also queryable programmatically from Claude Code and Claude Desktop via an MCP server. Three concrete use cases (UI-based search / reading, MCP-powered retrospective, find-mistakes-in-your-sessions). Prose architecture diagram: capture → fetch → browse/export (UI) + MCP (programmatic). The lost-account / data-portability beat is a supported edge case mentioned briefly, not the hook.

### Part 2 — *Using the Web App*
Install & first run. Screenshot walkthrough: conversation list, full-text search, keyboard nav, message tree, dark mode, mobile. Export to Markdown & PDF. **Three sources** in the sidebar's source filter — Claude AI (Desktop web app via the unofficial API), Claude Code (local `~/.claude/projects/**/*.jsonl`), Claude Cowork (Desktop's local-agent-mode sessions under `~/Library/Application Support/Claude/local-agent-mode-sessions/`). All three are indexed by the same FTS5 search and exported by the same Markdown/PDF pipeline. Userdoc-twin sync plan (run once the long-form is frozen): `PLANS/articles/part2-userdoc-twin-reconciliation.md`.

### Part 3 — *The MCP Server in Claude Code and Claude Desktop*
What MCP is. The 5 tools. Claude Code config (CLI + JSON). Claude Desktop config paths for macOS/Windows/Linux. **Headline demo:** *"I used this MCP server to mine this project's own build history to write this series."*

### Part 4 — *Building It with Claude Code, Part 1 — Reverse-Engineering the Claude API*
Where Claude Desktop stores data. mitmproxy + cert pinning. Unofficial API mapping. Pivot from proxy to Playwright.

### Part 5 — *Building It with Claude Code, Part 2 — Backend, Frontend, and MCP*
FastAPI backend. React/Tailwind/shadcn frontend. MCP server. Retrospective on Claude Code pair-programming.

### Part 6 — *The LLM Council: Adversarial Code Review with Heterogeneous Models*
The methodology that produced the codebase the previous five parts describe. Three personas — Senior Principal Engineer (GPT-5.2), Software Architect (Gemini-3-Pro), CTO (Opus 4.7) — running blind Round-1, cross-critique Round-2, CTO-synthesis Round-3, with explicit Decision Records and WWCMM (What Would Change My Mind) on every position. Concrete receipts from two waves: (a) the Phase 19 keyboard focus-model reframe + the Phase 20 MCP server design from the original build, and (b) a 2026-05-21 multi-day cleanup pass that caught a shipping crash (`claude-explorer fetch` was crashing on every invocation with `TypeError: org_id` — no test covered it), four security findings (CWE-200 exception leak, launchd plist XML injection, two session-key prefix leaks in console banners), an unbounded retry loop, a half-wired error classifier, and a §5.12 testing-discipline rule that came out of the process. Negative example included (intended-council-degraded-to-solo for a perf pass). When NOT to use the council (routine fixes, small refactors, anywhere single-model is good enough). Now also covers the strict code-quality review (receipt 4.11), a single-model maintainability rubric distinct from the heterogeneous council; its same-day second pass caught the regressions the first round of decomposition fixes shipped and hardened the skill itself (a `tsconfig.app.json`-aware Phase 0 pre-flight). Detailed plan: `PLANS/articles/part6_llm_council_plan.md`.

### Part 7 — *Hunting Latency: A Performance Postmortem in Practice*
The story of taking the V1 build from "shippable but laggy" to "blazing fast" via a sequence of empirical wins on the real corpus. Three acts: (1) **Search-typing storm** — React.memo bypass via useContext, per-key TanStack `select`, memoized Provider value, MessageBubble subscription removal; 88s → 11s cumulative Long Task. (2) **Backend** — sync handler swap to `async def` + `asyncio.to_thread`, cooperative cancel via `is_disconnected()` / HTTP 499, FTS5 + conversations projection table (250K → 344 row scan; 13.6× cold), W1–W4 lifespan warmup, GZipMiddleware-blocks-event-loop trap and per-route bypass via SelectiveGZipMiddleware (~700ms saved per conv fetch). (3) **Render** — empirical baseline (Playwright MCP + PerformanceObserver longtask + MessageChannel macrotask sampler), cached-per-id ref-setter pattern (Rule P11.A11.1), `@tanstack/react-virtual` integration with scroll-coordinate gotchas; 95% warm-switch reduction (10.3s → 514ms). **Late addition (F53, 2026-05-28):** the virtualizer-scroll-recovery quirk in `ConversationPage.tsx:680-704` — the auto-scroll effect silently no-op'd whenever the selected message was outside the virtualizer's overscan window (the row wasn't mounted, so `messageRefs.current.get(uuid)` returned `undefined` and `scrollIntoView` was called on nothing). Fix: fall back to `virtualizer.scrollToIndex(visIdx, { align: 'center' })`. New TDD pin at `frontend/e2e/keyboard-nav-alt-jump-virtualizer-recovery.spec.ts` uses a 600-message fixture (mirroring `conversation-pane-virtualization.spec.ts` shape) to force real mount/unmount; the existing short-fixture keyboard-nav specs never exercised this path. **The F53 beat is the methodological capstone for Act 3:** automated tests can pass while UX is broken when the fixture isn't shaped like the production case — when adding perf-driven indirection (virtualization, lazy mounting, suspense), every UX flow that touches the indirection needs at least one test against a fixture large enough to exercise it. Close with the lesson that became `CLAUDE-TESTING.md §5.14` and `llm-council-coding.md` Rule P11: **profile, don't guess** — and the five-belief falsification chain that earned the lesson.

**Sidebar (added 2026-05-24): The misleading-green e2e — and the harness that catches it.** Same article, smaller bite. The "Settings page flashes on and disappears" regression on 2026-05-24 made it past my Playwright e2e because I asserted only on DOM state (URL stays at `/settings`, expected elements present) — not on the browser console. The user found the bug on first manual test by opening dev tools. The fix was a project-wide auto-fixture in `frontend/e2e/fixtures.ts` that hooks `page.on('pageerror')` and `page.on('console')` for every spec, then asserts empty at teardown modulo an explicit allowlist (Vite HMR handshake, React DevTools install hint). Codified as `CLAUDE-TESTING.md §5.15`. The lesson generalizes §5.13/§5.14: a test that pins "the right element exists" is half-blind; tests must also pin "no unexpected errors fire during the user's flow." Concrete sidebar arc: (a) the bug; (b) why my e2e missed it; (c) the auto-fixture (~20 lines, opt-out via per-test allowlist extension); (d) what it caught in the back-fill pass across the full e2e suite.

**Sub-beat (2026-06-01): the suite that reported green while running nothing.** A later session stacked two misleading-greens. (1) A backgrounded `playwright test … | tail` reported `exit 0` that was actually `tail`'s exit code, not the runner's (a shell pipeline's status is its last stage's). (2) Underneath that, 13 specs carried a duplicate `withNetRetry` import and threw `SyntaxError` at parse time, so Playwright skipped them and still "completed." The assistant told the user the suite passed; it was not even running 13 files. Where §5.15 is "tests ran but asserted too little," this is "tests reported green without running at all," and the fix is a verification discipline, not a fixture: never trust a piped exit code, and treat `0 failed` as meaningless until the pass COUNT is checked against a known baseline and the output is grepped for `SyntaxError` / `no tests ran`. The kicker, and the reason it belongs in this article: fixing the parse errors un-hid 26 further pre-existing failures the broken suite had masked for days. A dead test suite does not just fail to catch new bugs; it conceals the ones already there. Codified as `CLAUDE-TESTING.md §5.16`, the project `CLAUDE.md` test-execution-integrity invariant, and the `llm-council-coding.md` Test Suite Integrity section; recovery tracked in `PLANS/2026.06.01-e2e-suite-recovery.md`.

Receipts: `PLANS/POSTMORTEM-search-typing-lag-2026-05-22.md` (the 5-belief falsification chain), `PLANS/PERFORMANCE_BASELINE_2026-05-23.md` (numbers + methodology + fixtures), `frontend/e2e/fixtures.ts` (the auto-fixture), `CLAUDE-TESTING.md §5.14` (perf-budget rule), `§5.15` (the misleading-green e2e rule), and `§5.16` (the prove-the-suite-actually-ran rule), `~/.claude/agents/llm-council-coding.md` Rules P0–P11 (the council-driven perf workflow) plus its Test Suite Integrity section, `PLANS/2026.06.01-e2e-suite-recovery.md` (the 26-failure recovery the dead suite had been hiding), `PLANS/articles/2026.05.27-react-doctor-notes.md F53` (virtualizer-scroll-recovery 2026-05-28 addition), `frontend/e2e/keyboard-nav-alt-jump-virtualizer-recovery.spec.ts` (the 600-message fixture pinning F53), `frontend/e2e/conversation-pane-virtualization.spec.ts` (the sibling 600-message DOM-budget pin for the original render-side virtualization work). **Commit enumeration for the planning session:** `git log --grep='perf\|profile\|virtualiz\|FTS5\|warmup\|cold-' --before=2026-05-27 main -- backend/ frontend/ CLAUDE.md CLAUDE-TESTING.md` yields the ~31 pre-session perf-related commits; the consolidated packaging is `51f9def perf(search,api,ui): typing-lag hunt + virtualization + lifespan warmups + v11→v12 Cowork-search recovery`. The schema self-healing companion is `7832b86 feat(search): Show Compactions end-to-end — body + title filter + multi-source compaction detection (schema v14)`. Detailed plan TODO at `PLANS/articles/part7_perf_postmortem_plan.md`.

## Session Inventory

See `PROCESS/00_session_inventory.md` for the full table. In scope: `a70251a5-…` (5207 msgs, main build) and `76fe578b-…` (41 msgs, this planning session). Skipped: 7 Gmail-scanner sessions from 2026-04-08.

## Progress Tracker

| # | Phase | Status | Artifact |
|---|---|---|---|
| A | Scaffold `PROCESS/` + copy plan | ✅ | `PROCESS/README.md`, `00_session_inventory.md`, `99_styleguide.md`, `PLANS/articles/medium-articles.md` |
| B | Main-session outline | ✅ | `PROCESS/a70251a5/outline.jsonl` (5006 rows) + `outline_digest.md` + `_build_outline.py` |
| C | Phase boundary detection | ✅ | `PROCESS/a70251a5/phases.md` — 25 → **21 phases** after user-directed fold (2026-04-19); Phase 02 SKIPPED (off-topic); Phases 19–23 merged into `keyboard_and_search_navigation` |
| D | Per-phase extractions | ✅ | **20 of 20 done.** Phase 02 skipped (off-topic) per user directive. |
| E | Current + skipped | ✅ | `PROCESS/76fe578b/summary.md`, `PROCESS/skipped/gmail_sessions.md` |
| F | Synthesis | ✅ | `PROCESS/{90_themes,91_memorable_quotes,92_timeline,93_use_cases}.md` |
| G | User review gate | ⬜ | pause |
| H | Article drafts | 🟡 | **3 of 7 published.** Parts 1–3 are live on Medium. Part 3 (*The MCP Server*) shipped **2026-06-10**: all three twins (quickstart, user guide, long-form) published + verified against the repo, the LinkedIn announcement deck rebuilt (whole-slide-PNG builder `scripts/build_part3_deck.py`), and the `v1.0.7` release landed on PyPI (wheel) + the GitHub Release (`.mcpb`). **Next up: Part 4** (*Reverse-Engineering the Claude API*) — source already mined in `PROCESS/a70251a5/phase_04_fetcher_and_mitmproxy_capture.md` (+ phases 09/13). Part 6 (LLM Council) plan: `PLANS/articles/part6_llm_council_plan.md`; Part 7 (Perf Postmortem) plan TODO: `PLANS/articles/part7_perf_postmortem_plan.md`. Open decision: Part 6/7 ordering. |
| I | Series intro | ⬜ | `PLANS/articles/00_series_intro.md` |
| J | PII sweep | ⬜ | final versions |

Legend: ⬜ not started • 🟡 in progress • ✅ done • ⏸️ paused • ⏭️ skipped

## Phase D Sub-Tracker (per-phase extractions)

Each subagent reads `PROCESS/a70251a5/outline.jsonl` + `phases.md`, pulls specific messages via `mcp__claude-sessions__get_messages(session_id="a70251a5-b932-4b61-aba1-16a70410b98e", positions=[…])`, and writes one `phase_NN_<slug>.md` in the Phase 01 template. Prompt template at end of this file.

| Phase | Slug | Positions | Status | File |
|-------|------|-----------|--------|------|
| 01 | intent_and_planning | `[0..57]` | ✅ | `phase_01_intent_and_planning.md` |
| 02 | figma_mcp_detour | `[58..112]` | ⏭️ | SKIPPED (off-topic per user) |
| 03 | initial_scaffold_backend_ui | `[113..335]` | ✅ | `phase_03_initial_scaffold_backend_ui.md` |
| 04 | fetcher_and_mitmproxy_capture | `[336..575]` | ✅ | `phase_04_fetcher_and_mitmproxy_capture.md` |
| 05 | file_and_pdf_attachments | `[576..703]` | ✅ | `phase_05_file_and_pdf_attachments.md` |
| 06 | playwright_e2e_harness | `[704..987]` | ✅ | `phase_06_playwright_e2e_harness.md` |
| 07 | viewer_tool_calls_and_branches | `[988..1250]` | ✅ | `phase_07_viewer_tool_calls_and_branches.md` |
| 08 | open_in_desktop_and_edit_retry | `[1251..1435]` | ✅ | `phase_08_open_in_desktop_and_edit_retry.md` |
| 09 | fetcher_403_and_rebrand | `[1436..1492]` | ✅ | `phase_09_fetcher_403_and_rebrand.md` |
| 10 | claude_code_local_files_unification | `[1493..1818]` | ✅ | `phase_10_claude_code_local_files_unification.md` |
| 11 | perf_caching_tool_results | `[1819..2109]` | ✅ | `phase_11_perf_caching_tool_results.md` |
| 12 | caveat_filter_and_rename_explorer | `[2110..2389]` | ✅ | `phase_12_caveat_filter_and_rename_explorer.md` |
| 13 | playwright_login_alt_credential | `[2390..2649]` | ✅ | `phase_13_playwright_login_alt_credential.md` |
| 14 | project_grouping_and_sidebar | `[2650..2787]` | ✅ | `phase_14_project_grouping_and_sidebar.md` |
| 15 | caveat_titles_and_exporter_cleanup | `[2788..2861]` | ✅ | `phase_15_caveat_titles_and_exporter_cleanup.md` |
| 16 | connection_status_popup | `[2862..3112]` | ✅ | `phase_16_connection_status_popup.md` |
| 17 | dev_env_noise_and_pkill_permission | `[3113..3280]` | ✅ | `phase_17_dev_env_noise_and_pkill_permission.md` |
| 18 | settings_page_kbd_dark_mode_plan | `[3281..3810]` | ✅ | `phase_18_settings_page_kbd_dark_mode_plan.md` |
| 19 | keyboard_and_search_navigation | `[3811..4842]` | ✅ | `phase_19_keyboard_and_search_navigation.md` (merged from old 19–23) |
| 20 | mcp_server_design_and_build | `[4843..4993]` | ✅ | `phase_20_mcp_server_design_and_build.md` |
| 21 | mcp_server_selftest | `[4994..5005]` | ✅ | `phase_21_mcp_server_selftest.md` |

**Pause cadence:** pause for user review every 5 extractions. Next pause: after Phase 06 finishes (5th extraction since 01 started; skipped 02 doesn't count against the 5). Then pauses after 11, 16, 21.

## Phase D subagent prompt template (reuse verbatim per phase)

```
**You are NOT in plan mode.** Ignore any plan-mode system reminders — stale. Execute autonomously.

---

You are a Phase D extractor for the Medium-article pipeline. Plan: /Users/rpeck/Source/claude-desktop-message-exporter/PLANS/articles/medium-articles.md. Template: PROCESS/a70251a5/phase_01_intent_and_planning.md.

## Job

Write one file: /Users/rpeck/Source/claude-desktop-message-exporter/PROCESS/a70251a5/phase_<NN>_<slug>.md.

Source:
- Session: a70251a5-b932-4b61-aba1-16a70410b98e
- Positions: [<start>..<end>]
- Dates: <d1> → <d2>
- Theme (from phases.md): <theme>
- Real prompts: <N>

## Output format

Match phase_01_intent_and_planning.md: H1 + session/positions/dates header, then sections Goal / Opening prompt / Key decisions / Code outcome / Missteps / Memorable moments / Tone / Cross-refs. Every bullet and quote cited [pos=N msg=UUID8…].

## How

1. `uv run python -c "..."` to filter PROCESS/a70251a5/outline.jsonl for <start> <= pos <= <end>, sender=="human", non-empty summary.
2. mcp__claude-sessions__get_messages(session_id="a70251a5-b932-4b61-aba1-16a70410b98e", positions=[real prompts + +1 follow-ups], include_tool_calls=false, include_tool_results=false) — ~15 positions per call, 2 calls max.
3. Do NOT pull all <total> positions.

## Deliverable back

≤300 words: one-line summary, top 3 highlights (≤15 words each), "Interesting for Medium" flag, "done, file written" signoff. Don't paste contents.
```

## Execution Phases

### A — Scaffold ✅
- `PROCESS/` directory created at repo root.
- `PROCESS/README.md` — citation format and directory map.
- `PROCESS/00_session_inventory.md` — session table + skip-note.
- `PROCESS/99_styleguide.md` — voice notes for drafting subagents.
- `PLANS/articles/medium-articles.md` — this file.

### B — Main-session outline (one subagent)
`get_session_outline("a70251a5-…")` → `PROCESS/a70251a5/outline.jsonl` (one row per message) + `outline_digest.md` (every 100th human message rendered). Returns counts + date markers.

### C — Phase boundary detection (one subagent)
Reads outline, produces `PROCESS/a70251a5/phases.md`: ~20–40 phase boundaries with slug, position range, theme, seed `msg_uuid`s.

### D — Per-phase extractions (one subagent per phase, sequential)
Each → `PROCESS/a70251a5/phase_NN_<slug>.md` with: **Goal**, **Opening prompt** (quoted, `msg_uuid`), **Key decisions** (bullets, cited), **Code outcome**, **Missteps / reverts**, **Memorable moments**. **Pause every 5 phases.**

### E — Current + skipped sessions
`PROCESS/76fe578b/summary.md` + `PROCESS/skipped/gmail_sessions.md`.

### F — Cross-cut synthesis (one subagent)
`PROCESS/90_themes.md`, `91_memorable_quotes.md`, `92_timeline.md`, `93_use_cases.md`.

### G — User review gate (**PAUSE**)
User reads the four synthesis files, picks emphases, corrects, flags PII.

### H — Part-by-part drafts (one subagent per part, sequential)
Writes `PLANS/articles/part_N_<slug>.md`. User reviews each before the next starts.

### I — Series intro
`PLANS/articles/00_series_intro.md` — column-intro style, analogous to the Python series intro.

### J — PII sweep
Grep all drafts for `sk-ant-`, `sessionKey`, anything resembling an org UUID or personal email. Replace with placeholders.

## Verification

- **After Phase D:** sample 3 random `phase_NN_*.md` files; pick one cited `msg_uuid` per file and verify it resolves via `get_messages`.
- **After Phase F:** user reviews `90_themes.md`; confirms narrative matches memory.
- **After each Phase H part:** user reads draft; iterate with tight tone-fit focus.
- **Phase J:** `grep -rE "sk-ant-|sessionKey|[0-9a-f]{8}-[0-9a-f]{4}" PLANS/articles/` — manual review of each hit.

## Open Questions (surface as they arise)

- **Creation-story part count** — decide at Phase G after synthesis (1, 2, or 3 parts).
- **Per-section emphasis** — ask at each Phase H part before drafting.
- **Specific Part-1 use cases** — confirm at Phase G from `93_use_cases.md`.
- **PII to scrub** — flag at Phase J.
- **Part-2 screenshot coverage** — confirm at Part-2 draft time.
- **Part 6 placement** — currently slotted as Part 6 (after the build-story arc). Could alternatively slot as Part 3.5 between MCP and build-story. Decide at Part-6 draft time; current placement reflects "build → reflect on how" narrative arc.
- **Part 6 vs LinkedIn callback** — the user's existing LinkedIn LLM-Council post needs to be pasted into the part-6 plan before drafting so the article can extend (not duplicate) it.
