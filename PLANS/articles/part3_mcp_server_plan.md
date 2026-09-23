# Plan: Part 3 — *The MCP Server* (story-led restructure + userdoc twin + a real CLAUDE.md tuning-loop run)

> Ratified plan (approved 2026-06-02). Mirrors the harness plan file `~/.claude/plans/bright-jingling-comet.md`. Built and pressure-tested across two LLM-council rounds (gpt-5.4 + gemini-3.5-flash, both ~9/10, convergent).

## Context

Part 3 of the *Unlocking Your Claude History* Medium series covers the `claude-sessions` MCP server (read-only, stdio, FastMCP; five tools: `list_sessions`, `list_projects`, `get_session_outline`, `get_messages`, `export_session`). A 34 KB draft already exists at `articles/part_3_mcp_server.md`, but it is implementation-forward (per-tool JSON request/response envelopes, full SQL DDL for the cache, per-tool token arithmetic) and its one "real workflow" section uses **hypothetical placeholders** (`8f2c3c1e-…`, "positions 47–55").

The author's directive: **light on implementation, heavy on real, receipt-backed use cases**, with a hard rule against softening *or* overstating claims. This plan restructures the long-form into a story-led piece, adds a **new userdoc twin** (for a non-expert beta user on macOS, Windows, or Linux), and elevates two real use cases now grounded in the project's own history:

1. **"We used the MCP server to mine the build history that seeded this whole series."**
2. **A real CLAUDE.md tuning-loop run** — we actually ran it bottom-up over all project sessions.

Both were mined this session via the MCP server (dogfooding) plus three research agents; every claim is citation-backed by `msg_uuid`.

### Grounded findings driving the rewrite (all real, cited by `msg_uuid`)

- **Origin/self-test:** the server's first-ever query, *"Find all the sessions for project claude-desktop-message-exporter"* `[a70251a5 msg=e3690a05]`, returned 9 sessions led by the very session that built it, **plus 7 unrelated "Scan Gmail" sessions** that had run from the same working directory (not a mis-grouping: a project here is defined by its directory, so anything run from that folder groups in). *"The MCP server is working."* `[msg=f8dd72c3]`.
- **Extraction pipeline (headline):** build session = **5,207 total / 5,006 active-branch / 312 real user prompts** — an **April snapshot**; that session is still live and now ~21k+ messages. Flow: `get_session_outline` → 5,006-row outline → 21 phases → 20 bounded `get_messages` pulls → synthesis files → drafts. Origin prompt `[msg=ff2ee72e]`; token-paranoia decision `[msg=2b09a3a9]`; append-only-cache insight `[msg=9bd17125]`; measured fixed cost 4,681 chars / ~1,200–1,600 tokens `[msg=41b1fe2b]`.
- **Dogfooding extent (corrected 2026-06-05):** the bulk mining was **front-loaded** — the server mined the build session into citable `PROCESS/` artifacts up front, and much of the drafting then ran against those artifacts. But it was **not** a one-shot. The MCP server was re-queried repeatedly during Part 2 to update the writing as the UI/UX improved. *(An earlier version of this caveat wrongly claimed the store was used "once," "not re-queried," and that only two sessions touched the series; the author corrected it. Those were unverified assertions about how he actually worked.)*
- **Tuning loop (we ran it):** ~7 of ~10 recurring mistakes were **already codified** (validation that the rules system works), plus a few genuinely new ones. The two best to showcase: **(R1)** *read the actual data shape on disk before coding against it* (the `files_v2` PDF nested-asset silent no-op; a JSONL chunk-merge bug); **(R2)** *never hard-code/stub a user-visible value to hit a perf budget* (`message_count=0` fast path; a 30-line read that broke full-text search). Several findings generalize to the cross-project `llm-council-coding.md`.
- **Caps & counts:** the "5,000-match cap" the Part 2 teaser mentions is a **capability never actually hit** (and is a search-match cap, distinct from `list_sessions`' own max-100). Cite by `msg_uuid` (positions drift).
- **Platform:** **all three (macOS, Windows, Linux).** The author briefly chose macOS-only earlier in the session, then reversed it (2026-06-03) because Windows and Linux beta users are waiting. Install is **no-clone, PyPI-primary** (decided 2026-06-04): both twins use `uvx claude-explorer mcp`, which pulls the pre-built wheel — no Node, instant. A `git+https` GitHub source build was considered and rejected: it triggers the `hatch_build.py` hook (`npm ci && npm run build`), so it needs Node and fails loudly without it; staleness is handled in the release pipeline instead (auto-publish to PyPI on a passing push). Claude Code leads with the CLI helper (`claude mcp add --scope user|project`), then hand-edited JSON for both user scope (`~/.claude.json`) and project scope (`.mcp.json` at the repo root — MCP servers never live in `.claude/`). Claude Desktop is reached via **Settings → Developer → Edit Config** (the Extensions browser is bundle-only, so a raw stdio server is a paste, not one-click). Only gotcha is `uvx`-on-`PATH` for GUI apps.

## Deliverables

1. **Restructured long-form** — edit `articles/part_3_mcp_server.md` into the 7-section story-led shape below.
2. **New userdoc twin** — `articles/part_3_mcp_server_userdoc.md` ("how a normal human connects the server and mines their own sessions," cross-platform, no internals), cross-linked to the long-form like the Part 2 twins.
3. **Tuning-loop run artifact** — `PLANS/articles/part3-tuning-loop-run.md`: the run method, the honest "mostly re-validated" result, and the **proposed diffs** (with citations) for `CLAUDE.md` / `TESTING.md` / memory / `~/.claude/agents/llm-council-coding.md`. **Proposals only; nothing applied without the author's per-diff approval.**

## Long-form outline (7 sections + short intro/wrap)

Reuse strong existing prose where it fits; this is a restructure, not a from-scratch rewrite. Drop the per-tool JSON envelopes, the SQL DDL block, and the per-tool char-count table.

- **Intro (short):** same archive, different reader — bridge from Part 2 in one paragraph.
1. **The First Useful Query** — open with the origin/self-test as a *real* round-trip and the 7-unrelated-Gmail twist. Fold "what MCP is / FastMCP / read-only-stdio / five tools" into 2–3 sentences here. *Lead with proof, not architecture.*
2. **Connecting It** — no-clone, **PyPI-primary** install (`uvx claude-explorer mcp`; the pre-built wheel avoids a Node-requiring source build). Claude Code: CLI helper first (`claude mcp add --scope user|project`), then JSON for both user scope (`~/.claude.json`) and project scope (`.mcp.json` at repo root; not `.claude/`). Claude Desktop: Settings → Developer → Edit Config (Extensions browser is bundle-only) plus per-OS config paths; verify; full-restart note; explicit-only note; `uvx`-on-`PATH` gotcha for GUI apps.
3. **The Five Tools, by Example** — workflow-ordered (find → outline → pull → export) with real natural-language round-trips and small snippets; **no JSON envelopes, no SQL.** Weave in the explicit-only token-paranoia design and the single measured fixed cost. `list_projects` as a mini-step.
4. **The Workflow That Mined This Series** — the headline. Real April-snapshot numbers; outline → 21 phases → 20 bounded pulls → synthesis → drafts. **Outline-first is the load-bearing pattern.** **Dogfooding pattern:** the server does the heavy excavation up front to stabilize the build session into `PROCESS/` artifacts, then gets re-queried in a targeted way as the product changes (e.g., the Part 2 UI/UX work). Not a one-shot.
5. **Running the CLAUDE.md Tuning Loop for Real** — frame validation as success (expected → actual → why-that's-good). Showcase **R1** and **R2** with concrete mini-stories; one paragraph "a few others"; one sentence on cross-project generalization to `llm-council-coding.md`.
6. **What I've Actually Used It For (and What I Haven't)** — honesty capstone: proven vs merely-enabled; the 5,000-match cap as a never-hit capability; the April-snapshot/now-21k caveat; one sentence on the append-only cache as *why outlines stay fast* (no SQL).
7. **Security, Scope & Wrapping Up** — read-only / local / stdio / explicit-only; absorb "what this is not for"; brief forward-point to Part 4.

Target ~6,000–8,000 words (range 5,000–10,000).

## Userdoc twin outline (`part_3_mcp_server_userdoc.md`)

Task-first, jargon-purged, cross-platform. A. What this lets you do; B. Connect it (macOS / Windows / Linux); C. Your first query; D. The outline-first habit; E. Three things to ask it to do (summarize a sprawling session; the personal tuning loop; export a slice); F. A few important limits; G. Wrapping up. Target ~1,500–2,500 words. Cross-link to the long-form like the Part 2 twins.

## Tuning-loop run artifact (`PLANS/articles/part3-tuning-loop-run.md`)

Method (bottom-up over all `claude-desktop-message-exporter` sessions via the MCP server; off-limits excluded: Synology-NAS, `~/Bin`, empornium/erscripts); validation (the ~7 already-codified classes, named); proposed diffs (each with ≥2 `msg_uuid` citations, classified *project* vs *global*):

- NET-NEW → `llm-council-coding.md` (GLOBAL): **R1** read-the-data-shape Step-1 precondition.
- STRENGTHEN → `llm-council-coding.md` (GLOBAL): **R2** new rule P12 (no correctness sacrifice for a perf number).
- STRENGTHEN → `TESTING.md`: broaden "re-report = falsification / reproduce on real corpus" beyond perf; un-stale the "no SQLite" note in §5.8.
- NET-NEW → `CLAUDE.md`: independent-count cross-check; pre-push step 13 / `check-article-formats.py` should fail on dead TOC `#anchor` links.
- NET-NEW → memory: `feedback_reread_user_edited_files.md`; `feedback_no_fabricated_user_facts.md`; (minor) ambiguous-referent restatement.

**Applying these diffs is a separate, per-diff approval gate after the discussion** — the article only reports the run.

## Critical files

- **Edit:** `articles/part_3_mcp_server.md` (restructure).
- **New:** `articles/part_3_mcp_server_userdoc.md` (twin); `PLANS/articles/part3-tuning-loop-run.md`.
- **Read-only ground truth:** `PROCESS/a70251a5/phase_20_*.md`, `phase_21_*.md`, `outline_digest.md`, `90_themes.md`, `93_use_cases.md`; `mcp_server/server.py`; `PLANS/articles/medium-articles.md`; `PROCESS/99_styleguide.md` (voice).
- **Proposed edits, gated:** `CLAUDE.md`, `TESTING.md`, `~/.claude/agents/llm-council-coding.md`, new memory files.

## Voice & honesty rules

Active voice (top priority), no em-dashes, no "X, not Y", no martial metaphors, ⌘ glyph, "back end/front end" nouns, executable commands in fenced blocks, motivate-from-user-perspective first. Cite mined quotes naturally in the article (no `msg_uuid` tags in published prose; uuids live in this plan + the tuning-loop artifact for verification). Clean/avoid profanity. Userdoc twin keeps zero internals. Favor blockquotes/code blocks over screenshots; the 5 existing `![[...]]` placeholders are removed or deferred.

## Verification (post-write)

- Voice read-through (active voice, no em-dash, no "X not Y", undefined jargon, no martial metaphors).
- `python3 scripts/check-article-formats.py` clean on both twins.
- **Bidirectional citation check:** every mined quote/number resolves via `mcp__claude-sessions__get_messages` by its `msg_uuid`.
- Userdoc-twin "no internals" grep ≈ 0.
- Word counts within range.
- Tuning-loop proposed diffs reviewed by the author before any `CLAUDE.md`/`llm-council-coding.md` change lands.
