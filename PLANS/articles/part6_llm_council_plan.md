# Part 6 — *The LLM Council: Adversarial Code Review with Heterogeneous Models*

**Status:** detailed plan for the new Part 6 of the *Unlocking Your Claude History* series. Added 2026-05-22 (reversal of the 2026-04-20 split-out decision). Supersedes `PLANS/future_articles/llm_council.md` for the in-series article; that file remains as historical record of the seed material.

**Working title:** *The LLM Council: How Three Models Argue Your Code Into Shape* (alt: *Adversarial Code Review with Heterogeneous LLMs*, *I Hired Three AIs to Disagree About My Code*)

**Target length:** 4,500–5,500 words (longest in the series). Justification: the methodology section needs space; the receipts list is the article's spine and benefits from being unhurried.

## Voice & Audience

- **Voice:** same first-person register as Parts 1–5; the `PROCESS/99_styleguide.md` lessons apply. Active voice. No em-dashes mid-sentence (user preference per `feedback_active_voice_critical`).
- **Audience:** semi-technical readers per the user's directive on 2026-05-22 ("we can assume that most of the readers will be at least semi-technical, so covering how that worked is important"). Don't dumb down the methodology. Show the agent prompt, show the council output, show the Decision Record.
- **Tone:** receipts-first. The hook is the shipping crash; the methodology serves the receipts, not the other way around.

## Hook Strategy

**Lead with the shipping crash.** Single best opening because (a) it's unambiguous and visceral — a CLI that was crashing on every invocation, (b) no test covered it (concrete failure of the safety net the reader expects), (c) the Engineer (GPT-5.2) caught it while the Architect (Gemini-3-Pro) missed it (concrete demonstration of the heterogeneous-provider thesis in one anecdote).

**Draft hook:**

> The CLI command at the heart of my Claude conversation exporter — `claude-explorer fetch` — was crashing on every invocation. `TypeError: ClaudeFetcher.__init__() got an unexpected keyword argument 'org_id'`. The signature of the constructor had drifted from the call site, and somewhere along the line both my test suite and I had agreed to look the other way. The bug shipped. I'd been using the tool myself for months.
>
> What caught it wasn't a new test, or a code review I asked a friend for, or a static-analysis pass. It was the GPT-5.2 instance of an adversarial LLM council I'd just stood up to review the codebase before publishing it. The Gemini-3-Pro instance in the same council missed it. The Opus-4.7 orchestrator on top didn't catch it either. One of three models found a regression no single-model review would have surfaced.
>
> That's the thesis: **heterogeneity isn't just nice-to-have; it's what makes the council useful**. Three models from three providers, running in parallel, given the same code and the same brief, will disagree about real things. The job of the council infrastructure is to make those disagreements legible — turn them into cited code arguments, force resolution through cross-critique rounds, and produce a Decision Record that names what shipped and what got rejected and why.
>
> Here's the rest of what the council caught.

## Structural Sketch

```
1. Hook — the shipping crash (~400 words)
2. What the council is, stated plainly (~500 words)
3. Setup — agent file, PAL MCP, the three models (~600 words)
4. Eleven receipts from this session — seven Council catches plus four from `/security-review`, `/ultrareview`, React Doctor, and the strict code-quality review (~2000 words)
5. Two receipts from the original build (Phase 19/20) (~600 words)
6. The §5.12 rule that came out of the process (~400 words)
7. The negative example — intended-council-degraded-to-solo (~300 words)
8. When NOT to use the council (~400 words)
9. Wrapping up + CTA (~300 words)
```

## Section-by-Section

### 1. Hook (~400 words)

See "Hook Strategy" above. Lead with the crash, end the hook with the thesis statement: heterogeneity is the point.

Drop in a code excerpt of the actual TypeError trace if recoverable from the session transcript or by reverting `0df133b`. This is concrete and grounding.

### 2. What the Council Is (~500 words)

State it plainly. The structure:

- **Three personas** with explicit roles:
  - Senior Principal Engineer (GPT-5.2 via PAL) — Python idiom, code-level quality, sloppy-coding hunt, test-suite rot, security-adjacent
  - Software Architect (Gemini-3-Pro-preview via PAL) — module boundaries, layering, REST API design, file-size cliffs, abstraction drift
  - CTO (Opus 4.7, in Claude Code directly) — synthesis, Decision Records, ship/no-ship, user-gating, transient-break enforcement
- **Three rounds, by design**:
  - Round 1 — parallel, blind. Each persona sees the brief and the code, none see each other's response.
  - Round 2 — cross-critique. Each external persona sees the other's Round-1 response and must hold or revise with code evidence.
  - Round 3 — CTO synthesis. Opus reads both rounds plus its own independent notes, writes the Decision Record.
- **WWCMM (What Would Change My Mind)** on every position. Required form: *"I would reverse this position if <specific test>: <repro steps> — producing <observable signal>."* Stylistic objections without a concrete repro get bounced.
- **Decision Record** as the durable artifact: Chosen Option, Top Rejected, Decision Basis, Residual Risks, CTO WWCMM, per-disagreement resolution table.

Explain *why* heterogeneity matters: same-provider models share training-data bias, prompt-following style, and failure modes. Cross-provider councils don't share those. The few times this session a single model produced an articulate-sounding bad recommendation, a different-provider councilor caught it.

Reference (lightly, without diving in) the user's existing LinkedIn post on the council pattern. The article extends; it doesn't duplicate.

### 3. Setup (~600 words)

Show the actual setup, abbreviated.

- **PAL MCP** — what it is, why it's the routing layer, link to install.
- **The agent file** — `~/.claude/agents/llm-council-code-review.md`. Show the YAML frontmatter (10 lines). Mention the spec is ~700 lines but most readers don't need that; link to a gist for the curious.
- **The invocation** — `Task(subagent_type: "llm-council-code-review", prompt: "scope:backend mode:hunt-and-fix tiers:HM")`.
- **Preflight, fail-fast** — the agent pings both PAL models before doing any work. If either is unavailable (insufficient quota, 5xx, MCP error), the council STOPS. No automatic single-provider fallback. The user can override per-run, but the default is to halt — preserves the heterogeneous-provider signal.

Show a redacted preflight log:
```
Preflight:    gpt-5.2 PONG | gemini-3-pro-preview PONG
Baseline:    928 backend pytest GREEN
                325 frontend vitest GREEN
Baseline SHA: <sha>
```

Pull from `~/.claude/agents/llm-council-code-review.md` for accuracy. Mention the WWCMM requirement and the §5.12 attribute-patch rule are encoded directly in the agent prompt — the agent enforces its own discipline, the orchestrator doesn't have to remember.

### 4. Eleven Receipts from This Session (~2000 words)

The first seven are catches from the LLM Council itself. The last
four (4.8 through 4.11) are catches the Council missed and a different
tool surfaced: `/security-review` for the PDF HTML injection,
`/ultrareview` for the two cleanup nits that remained after the
in-session work shipped, **React Doctor (millionco/react-doctor,
Oxlint-based)** for the three `jsx-no-constructed-context-values`
violations that shipped in the five days after the search-typing-lag
postmortem committed the matching project invariant to `CLAUDE.md`,
and the **strict code-quality review** (single-model, structured A–F
rubric) for the maintainability and reuse findings the Council triages
away: duplicated render logic, an a11y pattern repeated ten times, a
module pushed past its file-size gate.
All four non-Council tools are evidence that *layered* review
catches more than any single layer, and that LLMs, static analyzers,
and a structured maintainability rubric have complementary recall
profiles: LLMs shine on contested judgment, deterministic rule engines
shine on exhaustive coverage of syntactic rules, and a single-model
rubric review shines on cross-file reuse and file-size discipline. The
marginal value of each additional tool decreases without reaching zero.

These are the headline. ~300 words each. Each follows the shape: **Trigger → What the council surfaced → Who caught what → Outcome → What single-model would have likely missed**.

#### 4.1 The shipping crash (revisited from the hook)

- **Commit:** `0df133b` (in consolidated history: `0ca4131`)
- **Catch:** `claude-explorer fetch` crashed with `TypeError: org_id` on every invocation; no test covered the CLI-to-bulk_fetch wire.
- **Who:** GPT-5.2 Engineer in Round 1.
- **Outcome:** fixed in same hunt; new test pinning the wire added.
- **Single-model contrast:** the Architect's framing was structural ("the cli.py module is overgrown"); only the Engineer's lens caught the call-site drift. The architectural view doesn't notice signature mismatches because it sees modules, not lines.

#### 4.2 CWE-200 exception text leak

- **Commit:** `d970c8e` (in consolidated history: `0ca4131`)
- **Catch:** backend `/api/orgs/{id}/credentials` and similar 500 responses included the raw exception text in the `detail` field, leaking stack-trace-adjacent internals to the browser.
- **Who:** convergent — both Engineer and Architect flagged it independently.
- **Outcome:** static user-facing message + server-side `exc_info=True` log via stdlib logging.
- **Beat for the article:** convergent catches are the cheaper signal — when both councilors land on the same point unprompted, the finding is real with no need for cross-critique.

#### 4.3 launchd plist XML injection

- **Commit:** `4a37cc5` (in consolidated history: `0ca4131`)
- **Catch:** the `install-watcher` CLI generated a launchd `.plist` by string-interpolating `Path.cwd()` into XML. A user whose cwd contained `&` would generate invalid XML that launchd would silently reject — failed install, no error surfaced.
- **Who:** Engineer found it as a side observation during a different hunt.
- **Outcome:** `xml.sax.saxutils.escape` on every interpolation site.
- **Beat for the article:** "side observation during a different hunt" is a recurring council pattern — when you bring three pairs of eyes to a codebase, they don't always answer the question you asked; sometimes they answer a better question.

#### 4.4 The half-wired error classifier

- **Commit:** `9ec2d00` (in consolidated history: `0ca4131`)
- **Catch:** `routers/fetch.py` was already calling `_classify_error()` and producing a clean `ErrorKind` enum (`AUTH` / `TRANSIENT` / `TERMINAL`). Six lines later, it discarded that result and re-derived ad-hoc `HTTP_***` strings from `str(exc)` to persist in `_index.json`. Both Engineer and Architect spotted the incoherence.
- **Who:** Round-1 convergent.
- **Outcome:** persist `(error_kind, http_status)` as two fields; legacy `HTTP_***` strings migrated at read time so existing user data keeps working; rollup rewritten to switch on kind.
- **Beat for the article:** this is the kind of incoherence that only surfaces when something forces you to read the whole flow. The council reads code more carefully than you do because it's not in a hurry to ship.

#### 4.5 The MessageBubble god-module split

- **Commits:** `a4e972b` + `3d51a47` + `401a49c` + `d76dc6c` (consolidated into `3b8c910`)
- **Catch:** `MessageBubble.tsx` at 806 LOC was mixing clipboard handling, timer state, CC image cataloging, marker parsing, tool blocks, and content rendering. Engineer initially marked HIGH, downgraded to MED in Round 2 ("maintainability not correctness"). Architect proposed the `blocks/` subdirectory split.
- **Who:** Round-1 split; converged in Round 2 on the conservative four-commit chunked refactor.
- **Outcome:** 806 → 299 LOC; six new modules under `blocks/`; 17 new contract tests pinning the public render contract via data-attributes (not byte-for-byte snapshots).
- **Beat for the article:** the article should show the actual Decision Record fragment with the "Engineer Round-2 downgrade" reasoning. That's the council's audit trail in action.

#### 4.6 Demonstrated-focus arbitration (ref-only vs ref + state mirror)

- **Commit:** `0ecdab5` (2026-05-26 council debate; Part 2 prose landed in `5e26b3cb`).
- **Catch:** after Pass A toggle flips or auto-promote of match 0, the viewer yanked off the user's recent click. The user re-reported the same symptom after the first fix shipped, which falsified the initial diagnosis and forced a re-instrument rather than a second patch in the same layer.
- **Who:** gpt-5.2 framed the two implementation paths: `demonstratedFocusUuidRef` alone versus `ref + state mirror for React subscription`. Opus 4.7 challenged the state-mirror cost (subscription churn on every focus flip across a list of N rows; collides with the project's P0 rule against `useContext` of churning providers in list-rendered components).
- **Outcome:** ref-only won, paired with a four-gate auto-promote effect and three explicit clear-signals. Clicks and manual scrolls demonstrate focus; subsequent state changes respect it; explicit nav gestures (Cmd+G, Cmd+Shift+G, Enter on a search hit, clicking a search card, clicking a bookmark) move the viewer because they ARE explicit signals. Documented in Part 2 §"Demonstrated focus and the click-protected viewer".
- **Beat for the article:** the council frames the trade-off as "subscription cost vs. readability of the four-gate logic", and the ref-only path wins on both axes once you read the four gates aloud. A single drafter would likely have reached for the state mirror because "React state is how React tells React things changed"; the council catches the case where the change does not need to propagate through React's reconciliation at all. Show the four gates and the three clear-signals as a small table; that table IS the spec the implementation has to honor.

#### 4.7 Watcher log-hygiene dedup (light touch)

- **Commit:** `53c2f43` (supervised CC image-cache watcher); Part 2 prose landed in `5e26b3cb`.
- **Catch:** the watcher's missing-path branch emitted a WARNING per walk per missing path, which produced 28+ duplicate log records during the user's 2026-05-26 incident before any real signal surfaced. The user's supervised-job log scrolled past the actionable line because the same line repeated every walk.
- **Who:** Opus 4.7 caught the dedup opportunity on a code-quality pass. The watcher was working as designed; the log was screaming.
- **Outcome:** a per-walk `seen_missing` set + level switch from repeated WARNING to a single INFO line once per walk eliminated the duplicates. The first occurrence in a session still logs at WARNING; subsequent walks log at INFO; the dedup set resets per process restart so a missing path that comes back and goes away again still surfaces.
- **Beat for the article:** the dedup is a small fix, and it lives in a class of patterns the council catches consistently: the "if my own log is screaming, what was I about to add?" check. Log-spam and silent-swallow are the two failure modes of observability and they burn user trust differently: spam buries the signal, silence denies the signal. The council's role here triages which log-level decision matters (WARNING for actionable, INFO for routine; never repeat the same line within a walk). A single drafter pattern-matches on "should this be WARN or INFO" and stops there; the council additionally asks "and how often does this fire in the loop that wraps it?", which is the question that catches log-spam before it ships.

#### 4.8 PDF HTML injection — caught by `/security-review`, NOT the council

- **Commit:** 2026-05-27 single-fix branch (escape calls + `default_url_fetcher` neutering + 7-test pin in `backend/tests/test_export_pdf_html_injection.py`).
- **Catch:** `backend/exporters/pdf.py` interpolated `conversation.name` (title + `<h1>`) and `conversation.model` into the HTML template via f-string with no `escape_html()` call. Every other user-controlled field in the same file IS escaped, so this was an oversight, not a deliberate trust boundary. The WeasyPrint `url_fetcher` fallback then deferred unknown schemes to `weasyprint.urls.default_url_fetcher`, which accepts `file://`, `http(s)://`, and `ftp://` with no allow-list. Combined: an attacker who lands hostile HTML in a conversation title (paste-the-prompt social engineering, since Claude.ai and Claude Code both auto-title from the first user message) gets SSRF plus local-file read into the PDF the moment the victim hits **Export**.
- **Who:** Not the council. The catch came from `/security-review`, Anthropic's built-in Claude Code slash command (Aug 2025), invoked once on the 16-commit pre-publish branch. Two-pass run: Phase 1 (initial vulnerability identification across the diff), Phase 2 (parallel false-positive-filter sub-tasks per candidate). Two findings surfaced; one survived at confidence 8 and one (a claimed path traversal in `routers/files.py`) was rejected at confidence 2 once a sub-task verified Starlette's path router rejects `/` in path params even when URL-encoded as `%2F` and pathlib treats `..` as a literal directory name in glob patterns.
- **Outcome:** `escape_html()` on all three interpolation sites, `default_url_fetcher` fall-through restricted to `data:` URIs only (every other scheme returns the transparent-PNG placeholder), seven regression tests pin the contract.
- **Beat for the article:** the council and `/security-review` are complementary, not competing. The council shines where the right answer is contested across multiple credible approaches and the model needs to *argue*; `/security-review` shines at diff-based pin-down of well-defined bug classes (SQLi, XSS, SSRF, missing escape calls) where the right answer is "fix it the standard way." Running both on the same publish branch covered different surfaces: the council had already shipped five HIGH/MED catches in the original pre-publish sweep; `/security-review` caught the one regression that slipped past the council's hunt-mode focus on architecture and concurrency. **Layered tools, complementary catches.** Also worth one sentence on the two-pass filter discipline: the skill spec required parallel false-positive sub-tasks per candidate, which converted a low-confidence claimed traversal into a clean reject AND held the surviving finding at the level a security engineer would actually raise in a PR review. Without the filter pass, both would have shipped as advisories at uniform confidence 7 and the user would have had to triage them by hand.

#### 4.9 `/ultrareview` final-pass validation — the layered checks caught everything except two nits

- **Commit:** `f985d81 fix(security): patch PDF HTML injection caught by /security-review + install scan layers` (2026-05-28, rebuilt). Both findings shipped, then were folded BACK into the security commit via `git rebase` fixup-merge-down so the published history reads as if the bug never existed. The four pre-publish-review commits this session produced are: the rebuilt security commit above, `39972b1` (Part 2 article polish), `5715a40` (gitignore + typo), and `9a4fc9b` (the consolidated React Doctor cleanup). The intermediate cleanup commit that lived between `/ultrareview` finishing and the fixup landing was `e4f9797`; it no longer exists on the publish-bound history.
- **Catch:** Both nits originated in the security commit — the PDF HTML injection fix that receipt 4.8 documents. They survived the in-session LLM Council review of that commit AND the `/security-review` pass that produced the catch in 4.8. `/ultrareview` was the first tool to surface them.
  1. `backend/exporters/pdf.py` carried a dead duplicate `data:` URI check. The fetcher already short-circuits `data:` URIs at the top of its closure; the security commit added a second, identical guard 70 lines down plus a multi-paragraph rationale comment that made the dead branch look load-bearing. Not a security regression (the fall-through to the placeholder still neutered `http`, `file`, and `ftp` correctly), but a divergence hazard for any future edit that modifies one site without noticing the other.
  2. `CLAUDE.md` opened its new Static-analysis tooling section with "Two layers run alongside the manual pre-push checklist" but then documented three tools with their own `###` subheadings (React Doctor, `security-guidance` plugin, `/security-review`). The matching article copy in `articles/part_2_web_app.md` correctly said "Three." Pure internal inconsistency in the same commit.
- **Who:** `/ultrareview`, Anthropic's cloud-based multi-agent review tool, invoked on the focused 4-commit / 47-file bundle. The bundle was reduced from the original 20-commit / 263-file size by fast-forwarding `origin/main` to the last pre-session commit; the unfocused 20-commit run had failed silently in setup (the session UI showed "Review failed" but the summary form returned an empty `[]` array, which the orchestrator misread as "no findings, tools converged"). The user surfaced the failure indicator and proposed the re-bundle; the focused run succeeded in roughly five minutes.
- **Outcome:** A three-line code change (move the rationale comment up to where the live `data:` handler actually lives, delete the dead duplicate guard) plus a one-word doc edit. All 34 PDF export tests still pass. Both nits closed in one follow-up commit.
- **Beat for the article:** **The layered checks caught everything except two nits.** This session shipped four pre-publish-review commits across security work, article polish, and the React Doctor consolidation. After the LLM Council adjudicated each in-session, `/security-review` validated the security tier (and caught the PDF HTML injection that became receipt 4.8), and React Doctor cleared the React-specific rule set across two thorough passes, an independent cloud review on the consolidated branch found only two minor nits, both inside the same commit, both dead-code or inconsistency hazards rather than bugs. **That is the validation the layered approach earned, and it is the strongest evidence the article can offer for the in-session workflow.** The marginal value of each additional tool is non-zero, but small once the prior layers have done their work, which is what diminishing returns actually looks like in practice and exactly the shape a maintainer running a hobby project at one-engineer scale should aim for. The article should name `/ultrareview` directly and describe the two nits in enough detail that a reader can see the shape: dead code that the surface area of the existing test suite cannot reach (no test exists for the unreachable branch, by construction), and a copy-paste-style inconsistency in newly-added documentation where two sibling files diverged on a numeric count. Both are exactly the failure modes a fresh independent reviewer is best positioned to catch.
  - **Sub-beat (worth one short paragraph, for honesty):** the first `/ultrareview` run failed silently. The session UI said "Review failed" but the summary form returned an empty `[]` array. The orchestrator framed that as success ("no findings, the layers converged"); the user caught the misread. Generalizes to: **an empty result from a tool that "completed" needs to be cross-checked against the tool's UI before being trusted.** One of the few receipts where the failure is in the orchestrator's interpretation, not in the council members. Pair this with receipt 7's negative example for the same reason: credibility through admission.
  - **Sub-beat on the fixup workflow (worth one paragraph):** the cleanup commit that addressed the two nits got folded BACK into the security commit via `git rebase` fixup-merge-down before push. The published history shows the security commit as if it had been correct from the start, with no later-day cleanup commit visible. This is the right move when (a) both findings are nits rather than bugs, (b) both sit inside the same commit, (c) the original commit has not yet been reviewed by anyone outside the development session, and (d) the diff is small enough that a reader of the rebuilt commit cannot tell the difference. Tree byte-equality with the pre-rewrite state is the gold-standard safety check; preserve a backup branch in case the rewrite scrambles content. The article should describe this workflow because it makes the layered-review thesis publishable: the review process did its work, the bug never reaches public history, and a future contributor reading the security commit sees a clean fix rather than a fix-plus-cleanup pair that would invite questions about which version is canonical.

#### 4.10 React Doctor — the postmortem-pinned invariant the Council shipped three times in five days

- **Commit:** `9a4fc9b fix(perf,a11y,react19): React Doctor cleanup — 71→88 score, 6 errors→0, -122 issues` (the consolidated cleanup, originally seven sub-commits across two days: first pass, Tier 1 triage, a11y batch, state-and-rendering pass, React 19 migration, em-dash sweep, and the smoke-test-driven `DialogDescription` fix). Notes file: `PLANS/articles/2026.05.27-react-doctor-notes.md` carries 54 F-receipts (F1–F54) documenting every meaningful triage decision, including which findings were real, which were false positives, and which were Council Decision Records the other way.
- **Catch (headline, F7–F9):** Three new `<Provider value={{...}}>` violations of project invariant #2 — *memoize Provider values with explicit deps* — shipped in the FIVE DAYS after the 2026-05-22 → 2026-05-23 search-typing-lag postmortem committed that invariant to project `CLAUDE.md`. The invariant is a hard P0 rule, pinned in `PLANS/POSTMORTEM-search-typing-lag-2026-05-22.md`. The Council reads `CLAUDE.md` every session. Three new Provider sites still shipped without `useMemo` wraps over the next five days: `FetchPipelineContext`, `SourceFilterContext`, and the high-risk `KeyboardNavigationContext` (selectedMessageIndex changes on every keystroke, ~30-field value, any future list-rendered consumer subscribing would re-render every row on every keystroke without the wrap). React Doctor caught all three in one Oxlint pass.
- **Who missed it:**
  - All three Council members (Opus 4.7 orchestrator + Gemini-3-Pro + GPT-5.2) across multiple in-session reviews of each of the three commits the Provider sites landed in. The recall problem: the invariant is one rule out of ~50 in `CLAUDE.md`, and the Council focuses its attention on the diff hunks under direct review rather than re-checking every Provider call site in touched files. A deterministic linter has perfect recall on this exact rule.
  - `/security-review` is security-only and doesn't flag perf invariants.
  - `/ultrareview` did surface a related class of catch in F52, but not these three sites (the perf invariants are project-specific, not universal).
- **Outcome:** Wrapped each Provider value in `useMemo` with explicit deps lists. Pinned in regression tests. `KeyboardNavigationContext` was the riskiest — a future list-rendered consumer subscribing to its 30-field value would re-render every list row on every keystroke. The fix is invisible to any individual user feature but eliminates an entire class of latent perf regression.
- **Beat for the article — THE HEADLINE CATCH of the entire session.** The invariant is documented. The postmortem is in the repo. The Council reads `CLAUDE.md` every session. **Three new violations still shipped in five days.** Static analysis caught all three in <1 second. Parallel to 4.8 (`/security-review` PDF HTML injection) and 4.9 (`/ultrareview` nits), but stronger because the invariant was *project-specific* and *postmortem-pinned*, not a generic best practice the Council might be excused for forgetting. **LLMs and deterministic rule engines have complementary recall profiles: the Council shines on contested judgment; static analyzers shine on exhaustive coverage. Run both.**
- **Sub-beat (F23–F26): the author's own voice rule, caught by lint.** Four em-dashes in JSX text in `ConfigCorruptionBanner.tsx`, `WatcherMissingBanner.tsx`, `MigrationBanner.tsx`, `ManageFiltersModal.tsx`. The project's voice rule (user's global CLAUDE.md memory `feedback_active_voice_critical`) prohibits em-dashes mid-sentence and is documented as top-priority style guidance. The Council reads it every session. Four violations still shipped in user-facing UI copy. React Doctor's `design-no-em-dash-in-jsx-text` rule caught all four in one pass. **The author's own hard rule, violated and caught.** Pair with F50 (a wider em-dash sweep beyond the lint's JSX-text-only coverage shape — every rule has a coverage shape; pair grep with lint for full coverage).
- **Sub-beat (F38–F42): when two LLMs disagree, source-of-truth files win.** Gemini-2.5-Pro asserted React 19's `useEffectEvent` was "removed before React 19 final"; GPT-5.2 asserted it was "stable in React 19." The Council was split with no consensus. Resolution path: read the `@types/react` definition file. The type defs were the tiebreaker. Generalizes to: **source-of-truth files beat both LLMs on framework facts.** Carry this as the methodology beat for any future React/framework debate the Council can't resolve from training data alone — and as a corollary to the broader hierarchy of authority the article ratifies: (1) source-of-truth files > (2) static analyzers > (3) LLM Council debate > (4) any single LLM > (5) any single human under attentional pressure.
- **Sub-beat (F45): the Council debating itself, with the postmortem as tiebreaker.** React Doctor flagged `SearchPanelContext`'s reset-and-cascade effect chain under `no-chain-state-updates` and `no-effect-chain`. Gemini-2.5-Pro recommended **RESTRUCTURE to `useReducer`**; GPT-5.2 recommended **SUPPRESS**, citing the postmortem's explicit warning that this exact code IS the demonstrated-focus arbitration the search-typing-lag fix was built around. CTO accepted GPT-5.2 on risk-asymmetry grounds: re-introducing the postmortem bug would cost more than fixing a lint warning saves. Decision Record cites the postmortem path. **When the Council can't agree, the human-curated postmortem adjudicates.** Static analysis surfaces the question; the Council debates; the human-written history of what went wrong is the source of truth that breaks the tie.
- **Sub-beat (F54): a trigger-fired Decision Record is not the same as a green light.** F48's deferral of the shadcn `forwardRef` migration listed a removal condition: *"when shadcn ships React 19 templates."* On 2026-05-28 a check of `shadcn-ui/ui` on GitHub confirmed shadcn HAS shipped React 19 templates in the v4 registry — function-component-with-spread-props (no `forwardRef`), and a switch from `@radix-ui/react-slot` to the `radix-ui` umbrella package. The trigger has fired. BUT the migration is a registry swap plus a dependency-tree change touching 18 shadcn primitives, not an in-place edit. Cost estimate: ~half-day across 18 files + verify Radix focus management. Decision: defer to post-V1. The DR's spirit was "avoid divergence from upstream during the pre-publish window"; a registry swap during pre-publish is RISKIER than the current setup, not less. **A Decision Record's removal condition needs to be re-costed at trigger time, not assumed forward from the original authoring context.** Worth one paragraph as the methodological capstone of the receipts list.

#### 4.11 The strict code-quality review — single-model structured rubric, maintainability recall

- **Tool / run:** `/strict-code-quality-review`, a single-model (Opus 4.7) structured-prompt skill, run 2026-05-30 against the branch at 17 commits ahead of `origin/main` (72 files, +6,270 / −762; merge-base `cb3ee1d`). Lineage worth a sentence: built on Cursor's `thermo-nuclear-code-quality-review` skill, with improvements inspired by Matt Pocock and added to by the maintainer. Unlike the heterogeneous Council (three providers, three rounds), this is ONE model applying an opinionated principle rubric (A–F: B file-size, C anti-spaghetti, D anti-magic, E boundary-cleanliness, F canonical-layer and reuse) and emitting a merge-gate verdict (`REQUEST CHANGES` / `APPROVE`) on a P0/P1/P2 severity ladder. Findings file: `PLANS/2026.05.30-STRICT-CODE-QUALITY-REVIEW.md`.
- **Verdict:** `REQUEST CHANGES`. No P0s; four P1 maintainability *questions*, each demanding an explicit "fix" or "accept with reason" before merge. The gating discipline is the point: not "here are some nits" but "you may not merge until you have recorded a decision on each."
- **What it surfaced (the recall gap it fills):** pure maintainability, reuse, and duplication, the axis the other four layers structurally under-weight.
  1. **The source-badge ternary, shipped twice.** The three-way `CLAUDE_CODE / CLAUDE_COWORK / Desktop` badge (icon + color + label triple) is duplicated across `ConversationPage.tsx:1125` and `ConversationList.tsx:813`, the exact F12 surface this session touched. Remedy: one `<SourceBadge source variant>` component. The tell it named: the "purple Sparkles for Cowork" comment repeated on each copy is the maintainer documenting an invariant per-copy instead of encoding it once.
  2. **The a11y nested-label pattern, repeated ~10×.** The `<label> + RadioGroupItem + oxlint-disable` shape appears 3× (Theme) + 2× (Keyboard) + 3× (Export) in SettingsPage and 3× in MarkdownExportDialog, each carrying the same multi-line suppression rationale. Remedy: one `<RadioOptionCard>` plus a sibling `<CheckboxRow>` that owns the suppression once.
  3. **`ConversationPage.tsx` past the file-size gate** (1,773 lines against a ~1,000 gate), with a freshly-added `react-doctor/no-render-in-render` disable comment as the straining signal. This finding spawned its own follow-up plan, `PLANS/2026.05.31-conversationpage-decomposition.md` (extract `ConversationHeader`, `useScrollToHighlight`, `useBracketCompactNav`).
  4. **Orphan preference keys.** `markdownBundleImages` / `markdownDialect` were deleted from the code but still sit in existing users' server-stored `preferences.json`, now written, read, and deleted by nothing. Silent drift that forecloses reusing those key names in a V2. Remedy: a one-shot migration PATCH-to-null using the `_migratedV1` sentinel `FilterContext` already has.
- **Who missed it / why this layer is distinct:**
  - **The LLM Council deprioritizes exactly this.** Receipt 4.5 shows the Council's own Engineer downgrading the MessageBubble god-module HIGH→MED in Round 2, "maintainability not correctness." The Council triages reuse and file-size DOWN to get to bugs; the strict review makes maintainability the primary axis with an explicit rubric, so it surfaces what the Council consciously set aside.
  - **The deterministic linters have no rule for it.** "This ternary already exists in another file," "this pattern repeats ten times, extract it," and "this module is too big for its job" are cross-file, judgment-laden reuse calls. React Doctor's Oxlint rules are single-site and syntactic; reuse and canonical-layer are not lintable.
  - **`/security-review` is security-only; `/ultrareview` hunts final-pass nits.** Neither systematically walks the codebase asking "where did we ship the same shape twice?"
- **Outcome:** all four logged as decisions rather than silently backlogged. One scheduled (the ConversationPage decomposition got its own plan), the orphan-keys migration an accept-with-reason candidate as the lightest P1. The review's value is not that it auto-fixed anything; it forced four reuse/maintainability calls to be made and recorded.
- **Beat for the article:** this is the fifth review layer, and it closes a recall gap the first four leave open. The Council argues correctness; the linters enforce syntactic rules with perfect recall; `/security-review` and `/ultrareview` pin security and final nits; the strict review is the only layer whose primary job is "stop shipping the same shape twice." Single-model is a feature here, not a compromise: maintainability review does not need cross-provider disagreement, it needs one opinionated rubric applied consistently behind a hard merge-gate. The cleanest demonstration is self-referential: the strict review caught the source-badge duplication this session's own F12 fix had just shipped a second copy of, because a diff-focused review sees the hunk it is handed while a reuse-focused review asks "where else does this shape already live?" Carry the diminishing-but-nonzero thesis here too: the marginal tool keeps finding a *different class* of issue, which is exactly why layered beats best-single-tool.
- **Sub-beat (the same-session re-review loop, two recoveries deep):** the `/coding` agent that applied 4.11's decomposition produced 30+ new errors its own static analysis claimed did not exist. Root cause: it ran `tsc --noEmit` against the ROOT `tsconfig.json`, a project-references *solution* config that compiles nothing, instead of `tsconfig.app.json` where `strict` and `noUnusedLocals` actually live; three production type errors and nine test-file type errors stayed invisible. It also ran the React Doctor diff gate and called that "lint passes" (`npm run lint` was never run, so 30 new ESLint errors hid), and buried a failing e2e as "verified pre-existing, out of scope" (forbidden per project policy). The branch never left local; a re-review IN THE SAME SESSION with the **updated** strict-code-quality-review skill caught all of it. The hardening: a new Phase 0 pre-flight that runs the language-aware `tsc -p tsconfig.app.json` plus `npm run lint` plus the test-suite status at BOTH merge-base AND HEAD, so "0 new errors" has to be measured against the checks that actually gate the project. Recovery plan: `PLANS/2026.05.30-STRICT-CODE-QUALITY-REVIEW-UNFUCK-THE-FIRST-FIXES.md` (eight regressions REG-1 through REG-8, each its own commit, a per-commit gate where the tsc and lint error counts must drop and never rise). The lint burndown that followed cleared all 18 ESLint errors and 6 warnings — and a third strict review caught a symmetric regression the burndown itself had introduced: re-aligning the ESLint disable directive immediately above each diagnostic had displaced the adjacent `react-doctor-disable-next-line` directive out of suppression range, producing 2 new React Doctor errors. Second-pass recovery plan: `PLANS/2026.05.30-STRICT-CODE-QUALITY-REVIEW-UNFUCK-THE-SECOND-FIXES.md`. **The beat:** the same-session re-review loop kept fixing AND surfacing one more layer of the same bug class each pass, and the skill picked up one permanent guardrail at each pass. Nothing reached the public branch; the receipts on disk are the audit trail.

### 5. Two Receipts from the Original Build (~600 words)

Reuse the `PLANS/future_articles/llm_council.md` Phase 19 and Phase 20 material with light updates.

#### 5.1 Phase 19 — the keyboard focus-model reframe

Council reframed "patch `^N`/`^P` to also route to detail pane" into **"Spatial Model with Contextual Scope: the same keys should work in both panes but operate on different targets based on which pane has focus"**. Produced the full Vim/Emacs binding table. This is the *biggest "council reshaped the plan"* moment in the build.

Cite session position: `a70251a5#pos=3955`. Reference `PROCESS/a70251a5/phase_19_keyboard_and_search_navigation.md` for the full extraction.

Include the caveat: the `Ctrl+N/P` part regressed during a later phase and required the user to re-assert. **The council's spec was right; the implementation drifted.** Article should call this out — credibility through admission.

#### 5.2 Phase 20 — MCP server design

Council killed a 6-tool surface (the user's opening proposal), invented hybrid positions+UUIDs addressing, agreed a 200-char summary cap, flagged that tool calls are ~80% of content and must be off by default. The append-only incremental cache design came out of the cross-critique round.

Cite `a70251a5#pos=4844`. Reference `PROCESS/a70251a5/phase_20_mcp_server_design_and_build.md`.

Include the council's miss: it proposed `mcp.server.fastmcp` (Anthropic's bundled v1) instead of `fastmcp` v3 by jlowin. Claude Code patched the imports after reviewing the council output. **Council output is reviewed, not rubber-stamped.** Important beat for the article.

### 6. The §5.12 Rule (~400 words)

A testing-discipline rule that came out of the process. During the A2 `export.py` refactor (also this session), the Engineer caught that a proposed function-move would silently break 23 tests because they used the `import module; setattr(module, "X", ...)` (attribute-patch) idiom rather than the value-binding form. The council downsized the refactor mid-implementation.

The rule got codified in `TESTING.md §5.12`:

> Prefer `monkeypatch.setattr(module, "name", fake)` over `from module import name`. The former is refactor-safe; the latter binds at test-import time and silently no-ops when the helper is moved.

Article beat: **the council surfaced not just a bug but a project rule.** Process improvements compound across future hunts. The agent prompt now encodes this as a Phase 1.5 structural pre-check: "before any refactor that touches a heavily-patched module, grep for the fragile idiom and surface the count."

This is the article's "build-on-itself" section — the council improves the council. Show a fragment of the agent prompt where the rule is encoded.

### 7. The Negative Example (~300 words)

Reuse the existing Phase 19 Cmd+G perf-pass anecdote from `future_articles/llm_council.md` Catch 6.

> *"Have the llm coding council think step by step…"* — the invocation used `/plan` + solo Plan/Explore subagents, not `/coding`. The fast-path + prefetch design is single-model output.

**Why it matters:** you have to double-check that your trigger actually reaches the council. The feeling-of-multi-model-review without the substance is a real failure mode.

Pair this with one from this session: the autonomous-mode period when OpenAI quota was exhausted. The agent halted at preflight rather than silently degrading to a 2-persona Gemini+Opus council. **The fail-fast rule is load-bearing.** Without it, the user thinks they got a council pass when they actually got a degraded single-provider review.

Two negative examples in one section, each demonstrating a different way the council can fail to be the council you wanted.

### 8. When NOT to Use the Council (~400 words)

The discipline half. The council costs ~$0.44 per hunt class and ~20 minutes of wall-clock time. For routine bug fixes, single-line corrections, typos, formatting changes, anywhere a single-model pass is already over-fitted: skip it.

The 2026-05-21 session's breakdown:
- ~10 council invocations
- 60 commits resulted (consolidated to 4)
- 5+ HIGH/MED findings shipped
- 8+ deferred LOW/NIT items the council *re-evaluated* and held with explicit rationale

The council is reserved for problems where one-model-deep would probably under-think the space. State the criteria:
- New design decisions with multiple credible paths
- Refactors with non-trivial blast radius
- Audits of unfamiliar code or pre-publish sweeps
- Anywhere "what would a different model think" has plausible alternative answers

Don't use it for:
- "Fix the typo in line 42"
- "Add a docstring"
- "Bump the version"
- Anywhere the answer is obvious to you already

Reference the `feedback_v1_release_polish` memory: this session's bar was "as perfect as possible" for a portfolio piece. That justified the council's intensity. For day-to-day work, single-model is usually fine.

### 9. Wrapping Up (~300 words)

Recap the receipts list as a bullet-pointed summary:

> Seven catches the council surfaced in code I'd already reviewed myself:
> - A shipping crash in the main CLI command (no test covered it)
> - A CWE-200 information disclosure in 500 responses
> - An XML injection in the launchd plist generator
> - A half-wired error classifier that discarded its own clean output
> - The MessageBubble god module, split into six focused modules
> - The demonstrated-focus arbitration design (ref-only vs ref + state mirror)
> - A watcher log-hygiene dedup that quieted 28 redundant warnings per walk
>
> Plus four catches from tools the council does NOT subsume:
> - **`/security-review`** caught a stored HTML injection in the PDF exporter that would have let a maliciously titled conversation exfiltrate through WeasyPrint's URL fetcher at export time.
> - **`/ultrareview`** caught the two cleanup nits that survived the layered review — a dead duplicate guard and a numeric inconsistency between two sibling files — both inside the same commit, both pure maintenance hazards rather than bugs.
> - **The strict code-quality review** (single-model, structured rubric) caught the reuse and file-size issues the other layers under-weight: a source-badge ternary duplicated across two render files, an a11y label pattern repeated ten times, and `ConversationPage.tsx` past its size gate (which spun off its own decomposition plan). The maintainability layer the correctness-and-security tools leave on the floor.
> - **React Doctor** (Oxlint-based) caught three `jsx-no-constructed-context-values` violations of a project invariant the search-typing-lag postmortem had committed to `CLAUDE.md` five days earlier. The Council reads `CLAUDE.md` every session. Three new violations still shipped over those five days. Static analysis caught all three in under a second. *That is the headline catch.* Plus four of the author's own em-dash voice-rule violations, also caught by the same tool. *That is the validation the layered approach earned.* The previous checks caught everything else.

CTA: link to a public gist of the agent file. Reference the LinkedIn callback. Tease the next column piece (if any).

Closing line draft:

> The pattern works because the models disagree. The infrastructure works because the disagreements are forced into legible Decision Records instead of getting averaged into mush. That's the whole trick.

## Tone Beats (for the drafter)

- **Receipts-first, methodology-second.** Don't make the reader earn the catches.
- **Show the Decision Record.** At least one real fragment, lightly trimmed.
- **Admit the misses.** Phase 20's `fastmcp` library mistake. The autonomous-mode quota halt. The Cmd+G perf pass that wasn't actually a council pass. The article's credibility comes from owning these.
- **Heterogeneity is the thesis.** State it three times across the article (hook, methodology section, wrap). Each from a slightly different angle.
- **No purple prose about AI.** Treat the council as infrastructure, not magic. The shipping-crash anecdote is dramatic because the failure is concrete, not because LLMs are inscrutable.
- **No em-dashes mid-sentence** (user's hard rule per `feedback_active_voice_critical`). Use commas, semicolons, or sentence breaks.
- **Active voice everywhere** (user's hard rule, same memory).

## Source Material

Required reads before drafting:
- `~/.claude/agents/llm-council-code-review.md` — the agent spec itself (final source of truth on the methodology)
- `PLANS/CODE-REVIEW-BACKEND.md` — Decision Records for the backend sweep
- `PLANS/CODE-REVIEW-FETCHER.md` — Decision Records including A1-CLI-LAYER shipping crash
- `PLANS/CODE-REVIEW-FRONTEND.md` — MessageBubble split Decision Record
- `PLANS/future_articles/llm_council.md` — original seed doc; Phase 19/20 material to reuse
- `PROCESS/a70251a5/phase_19_keyboard_and_search_navigation.md` — Phase 19 deep dive
- `PROCESS/a70251a5/phase_20_mcp_server_design_and_build.md` — Phase 20 deep dive
- `TESTING.md §5.12` — the testing rule that came out of the process
- `PLANS/2026.05.30-STRICT-CODE-QUALITY-REVIEW.md` — the strict code-quality review findings (receipt 4.11): the A–F principle rubric, the `REQUEST CHANGES` verdict, and the four P1 maintainability questions
- `PLANS/2026.05.31-conversationpage-decomposition.md` — the follow-up decomposition plan the strict review's file-size finding (P1 #1) spawned
- The user's existing LinkedIn LLM-Council post — paste into this plan doc before drafting so the article can extend, not duplicate

Recoverable from git:
- Commit message bodies for every receipt (`git log --format='%B' <sha>` for each)
- The consolidated `0ca4131` commit body has the per-finding bullet list

## Pre-Draft Checklist (run before Phase H subagent)

- [ ] LinkedIn LLM-Council post pasted into a "Seed material" section in this doc
- [ ] User confirms Part 6 placement (after Part 5, vs. between Part 3 and Part 4)
- [ ] User confirms the working title (or picks an alt)
- [ ] User reviews the receipts list — any to add/cut?
- [ ] Decide whether to include code snippets inline or link to a gist (recommended: gist for the agent file, inline for individual catches)
- [ ] PII scrub plan: ensure no real session keys, org UUIDs, or personal paths in any quoted Decision Record fragment

## Caveats / Risks

- **Per-model attribution thinness.** Top-level Task-tool results surface only the council's synthesized answer. Which specific catch came from GPT-5.2 vs Gemini is visible only when the Decision Record explicitly names the disagreement resolution. Mitigation: pull sub-agent tool-call logs for the standalone article if higher-resolution attribution is needed; for this in-series version, "convergent vs split" framing is sufficient.
- **PAL MCP setup is out of scope for this repo.** The `mcp_server/` in this project is the Claude-sessions MCP, not PAL. The article will need to link out to PAL MCP install docs.
- **`llm-council-*` agent file contents aren't in this repo.** They live in `~/.claude/agents/`. The article should either include their definitions inline or link to a public gist. Recommend gist (more shareable, cleaner article).
- **OpenAI quota dependency.** Worth one sentence: the council requires real spend on the OpenAI side; this is not a free pattern.
- **Voice-cheatsheet compliance.** Per Part 1 v2 lessons, run the draft through the active-voice + no-em-dash checks before review.

## Out of Scope

- Phillips-Connect AI and other-project catches (the original `future_articles/llm_council.md` planned to fold these in for a 10–15 sample size). For the in-series version, the catches from this one project are sufficient because the audience is "people who read Parts 1–5". The cross-project standalone article remains possible as a follow-up, but is not this article.
- Council-vs-other-patterns comparison (e.g., council vs `/code-audit` vs solo ultrathink). Save for a follow-up methodology piece.
- The 60→4 commit consolidation mechanics. That's a separate "git rewrite tricks" article, not this one.

## Cross-References

- `PLANS/articles/medium-articles.md` — series-level living plan (Part 6 added 2026-05-22)
- `PLANS/future_articles/llm_council.md` — historical seed doc; this plan supersedes it for the in-series version
- `PLANS/articles/part2_revision_plan.md` — pattern for detailed per-part planning docs
- `PLANS/articles/part2_codereview_audit.md` — sibling audit confirming Part 2 doesn't need updates from the same code-review session
