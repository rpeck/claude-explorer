# CLAUDE.md Tuning-Loop Run — 2026-06-02

> A real bottom-up run of the "mine your own sessions for recurring mistakes → sharper rules" workflow, driven through the `claude-sessions` MCP server. This is both a genuine improvement pass for the project AND the receipt the Part 3 article reports. **Every proposed diff below is a PROPOSAL — nothing here has been applied. Each needs the author's per-diff approval before it lands.**

## Method

- **Corpus:** all `claude-desktop-message-exporter` sessions (the pwd project; old name of `claude-explorer`), via `mcp__claude-sessions__list_sessions(project=...)`. Two substantive sessions carry essentially all the engineering history: `a70251a5` (the build session, now ~21–25k messages live; 5,006 on the active branch at the April extraction) and `76fe578b` (the Medium-drafting session, now ~7k). Seven short "Scan Gmail for meeting invites" sessions were skipped as off-topic noise.
- **Retrieval:** outline-first via `get_session_outline`, then bounded `get_messages` pulls around moments where the user corrected the assistant. The already-distilled `PROCESS/a70251a5/phase_*.md` "Missteps / reverts" sections were the efficient ground truth for the build session.
- **Off-limits (excluded per policy):** Synology-NAS sessions, `~/Bin` sessions, and the previously-flagged sensitive content. None appeared inside `project=claude-desktop-message-exporter`; a cross-project keyword search surfaced a Synology and a Bin session only incidentally, and both were excluded.
- **Citations:** by `msg_uuid` (8-char prefix). Live positions have drifted as both sessions kept growing, so positions are indicative only; the UUIDs are stable.

## Headline result (the honest one)

The loop **validated a proactive habit more than it discovered new failures.** Roughly **7 of ~10** recurring mistake-classes were already codified in `CLAUDE.md` / `TESTING.md` / the memory files, often verbatim, because rules here get written **in the moment a mistake bites**. The git log shows them landing in the same commits as the work (`953fbdd8 codify test-execution-integrity checks after a false-green report`, `3528d221 e2e console-error assertions + testing rules`, etc.), and the ~24 memory files carry inline `originSessionId` provenance, not retrospective-mining provenance. So the loop did not meet these failures for the first time; it audited whether the in-the-moment codification had kept pace (it mostly had) and surfaced the genuine gaps.

**Reconciliation (which proposed diffs are already substantially handled):** several "strengthen" items below were already caught proactively, which is the point, not a knock on the loop:
- **R-reproduce** (re-report = falsification): already in `CLAUDE.md:25` (perf) + `CLAUDE.md:15` (test-green) and the search-correctness reference incident `TESTING.md:1080`/`§5.13`. Open part = the *universal* phrasing only.
- **R-sqlite** (concurrent-writer): the fix already shipped (task #247) and the rule already exists at `TESTING.md:789`, just phrased conditionally ("if we ever use SQLite"). Open part = un-stale the conditional.
- **R4** (cross-check counts): the test-collection version already exists at `CLAUDE.md:14`. Open part = the general statement.
- A test-side cousin of **R2** already exists (`TESTING.md:516`, "could be hardcoded to 0").

**Genuinely net-new** (confirmed absent in the targets): **R1** (read-the-data-shape, global), the **coding side of R2** (don't-stub-a-value-for-speed, global), **R-reread** and **R-bio** (two un-written article-drafting memory rules), and **R-toc** (a tooling gap).

The two strongest, which the Part 3 article showcases:
- **R1 — Read the actual data shape on disk before coding against it.** (net-new, global)
- **R2 — Never hard-code or stub a user-visible value to hit a perf budget.** (strengthen, global)

## Validation — recurring mistakes already codified (no change needed)

Listed for honesty and as the article's "the system works" evidence. Recurrence confirms each rule is load-bearing.

| Already-codified rule | Where | Recurrence evidence |
|---|---|---|
| Scope every process-kill to a port; never broad `pkill` | `feedback_pkill_uvicorn.md` | `[a70251a5 msg=36b396b2]` then re-asserted weeks later `[msg=1854813a]` "I'm working on multiple projects that use Uvicorn" |
| Active voice is the top voice rule | `feedback_active_voice_critical.md`, `99_styleguide.md` | repeated voice corrections across `76fe578b` |
| No em-dashes / no "X, not Y" | `99_styleguide.md` | repeated |
| e2e must assert zero unexpected console errors | `feedback_e2e_console_assertions.md`, `CLAUDE-TESTING §5.15` | settings-flash incident |
| "Tests pass" proves nothing until the run is verified | `CLAUDE.md` test-integrity invariant, `§5.16` | the 13-spec parse-error incident |
| Dual-session git races → `git commit --only` | `feedback_parallel_agent_git_races.md`, `feedback_dual_session_history_rewrite.md` | recurring |
| Council confabulates file contents | `feedback_council_not_for_file_audits.md`, `feedback_verify_council_citations.md` | recurring |
| No CLI for normal ops | `feedback_no_cli_for_normal_ops.md` | recurring |

## Net-new proposed rules

### R1 — Read the actual data shape on disk before coding against it  *(GLOBAL → `llm-council-coding.md`)*
**Rule:** Before writing parse/render code against any external payload (JSON, JSONL, API response), open a real on-disk example and confirm the exact (possibly nested) field path; never code against a remembered or assumed schema.
**Evidence (recurs):** `files_v2` PDFs silently no-opped because the code assumed flat `thumbnail_url` instead of nested `document_asset.url` `[a70251a5 msg=82391f1f / msg=895d7bb9; live compaction-summary corroboration msg=ac522fb1]`. JSONL streaming chunks parsed as separate messages → blank messages, requiring a rewrite not a patch `[msg=5b6972ce / msg=db93d67b]`. Fetcher URL missing `render_all_tools=true` → placeholder-only tool blocks `[msg=a977f490]`. Cross-cut in `93_use_cases.md:201`.
**Status:** NET-NEW. `llm-council-coding.md` references "real corpus" only for *perf* baselines; there is no correctness rule to inspect data shape first.
**Proposed diff (Step-1 proposal precondition):**
> *Data-shape precondition: if the change parses or renders an external payload, the proposal MUST cite a real on-disk example (path + the exact nested field path used). "Assumed shape" or schema-from-memory is a BLOCK — the `files_v2` nested-asset bug (silent no-op) and the JSONL chunk-merge bug both shipped from coding against an imagined shape.*

### R4 — Cross-check any decision-driving count against the raw source  *(project → `CLAUDE.md`)*
**Rule:** When a count or total drives a scoping or correctness decision, verify it against the raw source independently (glob + `wc`), not against the number the app reports.
**Evidence:** an independent JSONL count surfaced 223 hidden agent sub-conversations (258, not 35) `[a70251a5 msg=bd51590b]`; "Did you find any branched conversations?" → 0/68 `[msg=a74f3efa]`.
**Status:** NET-NEW. The disk-file-count test rule in `CLAUDE.md` is the same instinct but scoped only to test collection.
**Proposed diff (new short invariant):**
> *Any count that drives a scoping or correctness decision gets an independent cross-check against the raw source (glob + `wc`, not the app's reported number). An independent JSONL count is what revealed 223 hidden agent sub-conversations the listing had filtered out.*

### R-reread — Re-read a file before editing it if the user may have touched it out-of-band  *(memory → `feedback_reread_user_edited_files.md`)*
**Rule:** Before editing any file the user co-edits in an external editor (articles in Obsidian, configs, screenshots-in-markdown), Read it again immediately prior to the edit; never apply a diff computed against a stale read.
**Evidence:** `[76fe578b msg=8a2c93cc]` "you…reverted some of my updated screenshots… Check all the screenshots."; `[msg=e6169340]` "I typed ⌘-S in Obsidian." Related git-clobber class `[a70251a5 msg=b74f7dd9]`.
**Status:** NET-NEW. `feedback_dual_session_history_rewrite.md` covers *git* races between two CC sessions; neither it nor the ExitPlanMode rule covers the *user* editing a shared file in an external editor between your Read and your Edit.
**Proposed diff (new memory file body):**
> *When the user co-edits a file out-of-band (Obsidian, manual saves), Read it again right before each Edit. A diff computed against an earlier Read silently reverts their intervening changes — this reverted screenshot embeds in the Part 2 article on 2026-05-31. Links: [[feedback_dual_session_history_rewrite]].*

### R-bio — Never fabricate a biographical/historical fact about the author  *(memory → `feedback_no_fabricated_user_facts.md`)*
**Rule:** Do not invent résumé, employer, dates, or anecdote details about the author; if a draft needs one, ask or leave a `[TODO: confirm]` placeholder.
**Evidence:** `[76fe578b msg=d582dcc8]` "Uh, I didn't work at SGI. Where did you get that?"; `[msg=f2421731]` corrects fabricated tool-history details.
**Status:** NET-NEW. `feedback_no_silent_article_softening.md` and `feedback_no_article_pre_authoring.md` cover claim-softening and invented *timelines*, not invented *personal facts*.
**Proposed diff (new memory file body):**
> *Never fabricate facts about the author's life or career (employer, years, tooling history). On 2026-05-31 a draft claimed the author worked at SGI; he never did. Ask, or insert `[TODO: confirm]` — do not guess. Links: [[feedback_no_silent_article_softening]], [[feedback_no_article_pre_authoring]].*

### R-toc — `check-article-formats.py` must validate intra-doc TOC anchor links  *(tooling → `CLAUDE.md` pre-push step 13 + the script)*
**Rule:** Extend the pre-push article check to fail when a `## Contents` `#anchor` link has no matching heading slug.
**Evidence:** `[76fe578b msg=8a90adbf / msg=3ef8f760]` "Shouldn't I expect the TOC links to work in Obsidian? When I click on them they don't work." The script currently treats `#`-prefixed targets as always valid and only checks image embeds.
**Status:** NET-NEW (tooling).
**Proposed diff:** add to step 13 / `scripts/check-article-formats.py`: *fail on TOC `#anchor` links with no matching heading slug (broke in Part 2 on 2026-06-01).*

### R-referent — Restate the antecedent of an ambiguous terse directive  *(memory, minor)*
**Rule:** When a terse directive ("hold off", "skip that") has two plausible antecedents, name the one you're acting on in a half-sentence before proceeding.
**Evidence:** "hold off" on SQLite was read as "skip the message-count fix"; required a correction turn `[a70251a5 msg=2ae07954 / msg=4077056d]` "I didn't mean to skip the message count; I meant to skip caching in sqlite!"
**Status:** NET-NEW, minor (single incident). Adjacent to `feedback_least_surprise.md`.

## Strengthen-existing proposed rules

### R2 — No correctness sacrifice for a perf number  *(GLOBAL → `llm-council-coding.md`, new rule P12)*
**Rule:** An optimization that hard-codes, stubs, or short-circuits a user-visible value (count, status, search corpus) to hit a budget is rejected even if the number improves. A fast path that reads less data MUST still compute displayed values from the full data, or be gated so the slow-but-correct caller diverges (e.g. a `full_content` flag).
**Evidence:** a fast reader hard-coded `message_count=0` "for speed" → every session showed "0 msgs" `[a70251a5 msg=b741f295 / msg=254d0019]`; user caught it `[msg=0e03b4a8]` "If you're reading only 30 lines will you have the full count?" Same pass: a 30-line fast reader broke ⌘-K full-text search `[msg=875551ea]`. Canonical "over-eager optimization" in `93_use_cases.md:204`.
**Status:** STRENGTHEN. The P-rules enforce measurement and falsification but don't forbid trading correctness for the benchmark.
**Proposed diff (new P12):**
> *P12 — No correctness sacrifice for a perf number. An optimization that hard-codes, stubs, or short-circuits a user-visible value to hit a budget is rejected even if the number improves. The `message_count=0` fast path and the 30-line-search-corpus regression both shipped this way.*

### R3 — Verify an external integration before building UI on top of it  *(memory → `feedback_verify_external_integration.md`)*
**Rule:** Before building UI on top of an external integration (custom URL scheme, OS handler, third-party API behavior), verify the integration does what you assume with one real test.
**Evidence:** "Open in Claude Desktop" `claude://` deep links wired into two surfaces before testing — the scheme only launches the app `[a70251a5 msg=02ad1e52 / msg=416a55ac]`. Project grouping declared done but invisible `[msg=3b8c22cb]` "Look for yourself." Branch-switcher UI built against mock data; the corpus had 0/68 branches `[msg=55d11b76]`.
**Status:** PARTIAL. `feedback_e2e_console_assertions.md` and the Test Evidence Ladder cover *tests*; no rule covers verifying integration-with-external-systems empirically before building on top.

### R-reproduce — "Re-report = falsification" applies to ANY functional bug, not just perf  *(→ `TESTING.md` §2 / new §5.18)*
**Rule:** For any user-reported functional bug, reproduce on the user's real corpus before AND after the fix; a green synthetic test plus a still-broken real corpus means the test pins the wrong contract. A second re-report is a falsification event — re-instrument, do not stack a second fix.
**Evidence:** multi-word/FTS search re-reported 3× `[a70251a5 msg=a4f67aaf]`, `[msg=674b6382]` "still…broken! Partial matches…shown as hits!", `[msg=ad740c11]` "the test suite should have found this extremely basic bug"; same shape later `[msg=11e7d59d]`.
**Status:** STRENGTHEN (generalize). `CLAUDE.md` "Performance Work #3" and `CLAUDE-TESTING §5.14` make this a *perf-only* rule; the class is broader (search correctness, snippet rendering).

### R-sqlite — Un-stale the SQLite concurrency note; add a concurrent-writer test  *(→ `TESTING.md` §5.8)*
**Rule:** `summary_cache.py` and `search_index.py` DO use SQLite now; each writer needs a test firing concurrent writers and asserting no `database is locked` reaches the client (verify WAL + `busy_timeout`).
**Evidence:** `[a70251a5 msg=aa7d3255]` "We need to be able to handle concurrent clients! summary_cache: upsert_many failed…database is locked"; `[msg=faddce5d]` "search_index: drift-upsert failed."
**Status:** STRENGTHEN. `§5.8` still says "Currently no SQLite — but…flag if so," which is now stale.

### R-commit — Commit before a phase pivot  *(→ `CLAUDE.md` git, minor)*
**Rule:** Land a clean commit at each phase boundary before exploratory or refactor work (the author's recurring "0. commit" convention).
**Evidence:** "Commit and then move on to the fetcher" `[a70251a5 msg=982a2bf2]`; "0. commit / 1. … / 2. …" `[msg=06d561c9]`; "First, commit what we have. Then, let's ultrathink" `[msg=5ab2e8ad]`.
**Status:** PARTIAL, low-to-medium value.

## Approval checklist (for the author)

Apply none of these without checking the box. Recommended split: land the two global rules (R1, R2) into `llm-council-coding.md`, the two `CLAUDE-TESTING` strengtheners, and the three memory files; treat the tooling change (R-toc) as its own small PR; the minor ones (R-referent, R-commit) are optional.

**LANDED 2026-06-02** (per user direction: "R1+R2 into `llm-council-coding.md`, the rest as memory files"):
- [x] R1 read-the-data-shape → `~/.claude/agents/llm-council-coding.md` (Step-1 proposal precondition)
- [x] R2 P12 no-correctness-for-a-perf-number → `~/.claude/agents/llm-council-coding.md` (Performance Playbook, Rule P12)
- [x] R-reread out-of-band file → memory `feedback_reread_user_edited_files.md`
- [x] R-bio no-fabricated-user-facts → memory `feedback_no_fabricated_user_facts.md`
- [x] R3 verify-external-integration → memory `feedback_verify_external_integration.md`
- [x] R4 independent-count → memory `feedback_independent_count_check.md` (as a memory, not `CLAUDE.md`)
- [x] R-toc TOC-anchor gap → memory `project_check_article_formats_toc_gap.md` (captured as a known-gap note, not a script change)

**NOT landed** (already substantially handled proactively — see Reconciliation above):
- [ ] R-reproduce — already at `CLAUDE.md:25` + `TESTING.md:1080`/`§5.13`; only the universal phrasing is open
- [ ] R-sqlite — fix already shipped (task #247); rule exists conditionally at `TESTING.md:789`
- [ ] R-referent / R-commit — minor, not landed
