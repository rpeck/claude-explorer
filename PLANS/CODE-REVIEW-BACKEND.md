# Code Review — backend (2026-05-21)

Initial pilot run: `mode:hunt` (no fixes implemented).
Follow-up run (2026-05-21): `mode:hunt-and-fix tiers:H` — HIGH cycle fix shipped.
Second follow-up (2026-05-21): `mode:hunt-and-fix tiers:HM` — both MED splits shipped.
Hunt class: A2 — God modules / oversized files.
Scope: `backend/` (excluding `backend/tests/`).

## Council

- Architect: gemini-3-pro-preview (thinking_mode=high)
- Engineer: gpt-5.2-pro (thinking_mode=high)
- CTO: opus-4.7
- Preflight: PASS, both PONG'd

## Commit range

- Baseline SHA (pilot hunt): 82f9d8ff0ebc821948bd62c261760c6539cba731
- Baseline SHA (HIGH fix run): b567150d140642d8a51f8d03aba1a594b0fd2c97
- Baseline SHA (MED fix run): e812d5d51fb5ab8cbf1be0effd707197531de6f5
- Final SHA: 9c252e0 (HEAD as of 2026-05-21 MED-fix run)
- Commits added in HIGH fix run:
  - `425377a refactor(search): break search.py <-> search_index.py cycle (council A2)`
- Commits added in MED fix run:
  - `1a00f84 refactor(fetch): extract patch-safe SSE helpers + drop sys.path hack (council A2)`
  - `81be897 refactor(export): extract shared helpers to backend/exporters/_shared (council A2)`
  - `606842b refactor(export): move markdown surface to backend/exporters/markdown (council A2)`
  - `d7bc1bd refactor(export): move PDF/HTML surface to backend/exporters/pdf (council A2)`
  - `9c252e0 refactor(export): move bundle surface to exporters/bundle, leave export.py as pure facade (council A2)`

## Recon

```
1644  backend/search_index.py
1457  backend/search.py
1310  backend/export.py
1179  backend/routers/fetch.py     (NOT in user's preliminary list)
 824  backend/store.py
 817  backend/cc_message_transforms.py
 803  backend/cc_watcher.py
 663  backend/main.py
 502  backend/summary_cache.py
```

`backend/routers/fetch.py` at 1179 LOC surfaced during recon; it was not in
the user's preliminary `wc -l backend/*.py` because that command did not
recurse into `backend/routers/`. Worth noting for future invocations:
the recon command in the agent spec must search subdirs.

Baseline test suite: 857 passed (backend/tests + fetcher/tests), 38 warnings, 50s.

## Decision Records

### Decision Record — A2 god-modules

| Field | Content |
|-------|---------|
| Chosen Approach | One HIGH (search ↔ search_index cycle break), two MED splits (routers/fetch.py, export.py); five LOW-or-better (store.py, main.py, cc_watcher.py, cc_message_transforms.py, summary_cache.py). |
| Top Rejected | Architect's Round-1 "export.py:HIGH" (Engineer convinced the council it's MED maintenance drag, not compound architectural failure). Engineer's Round-1 "main.py:MED" and "cc_watcher.py:MED" (Architect convinced the council operational cohesion holds). |
| Decision Basis | Round-2 produced REAL convergence on 5 of 6 disagreements with code-cited reversals, not rubber-stamping. The HIGH finding is the only one with a concrete grep-verifiable structural defect (line 66 + line 495 cycle), so it stands alone in the highest tier. |
| Residual Risks | (a) The cycle fix moves `_extract_searchable_text` which is the search-tool-awareness invariant pinned by `test_search_extract_no_double_index.py` — any move that changes its body breaks the contract. (b) The router split risks SSE event-frame ordering changes the frontend depends on. (c) The export split risks "one truth, three surfaces" policy drift across markdown/PDF/bundle. |
| CTO WWCMM | I would reverse the HIGH on the search cycle (downgrade to MED) if `python -X importtime -c "import backend.search_index"` reveals the lazy import at search.py:495 is genuinely needed because of an unrelated side-effect ordering constraint — repro: trace import timeline before and after a proposed top-level promotion. Observable signal: a test in `test_lifespan_cold_start.py` regresses after promoting the import. |

### Per-disagreement resolution

| Disagreement | Architect R1 | Engineer R1 | Resolution | Evidence cited |
|---|---|---|---|---|
| search_index.py + search.py cycle | LOW + MED (split projection only) | HIGH + HIGH (cycle is structural hazard) | **HIGH** — Architect REVISED after Engineer cited `search_index.py:66` and `search.py:495` | search_index.py:66 `from .search import _extract_searchable_text`; search.py:495 lazy `from .search_index import get_search_index` — both verified by grep |
| export.py | HIGH ("3 orthogonal output domains; must split before V1") | MED ("predictable maintenance drag, not compound failure") | **MED** — Architect REVISED. Engineer's argument: `create_markdown_bundle` (line 1066) never calls `create_pdf` (line 813); WeasyPrint lazy-imported inside `create_pdf` (line 824) | Verified: line 824 `from weasyprint import HTML` is inside `create_pdf` body, scoped lazy import |
| routers/fetch.py | MED | MED | **MED** (convergence — no resolution needed) | routers/fetch.py:15-16 `sys.path.insert` hack inside router; lines 601, 974, 1015 are three stream generators that belong in `backend/fetch_pipeline.py` |
| main.py | LOW ("linear boot sequence; splitting saves vertical space without decoupling") | MED ("orchestration drift; extract to bootstrap.py") | **LOW** — Engineer REVISED. Architect's argument: lifespan orchestrates work already extracted to subsystem modules; bootstrap.py would be a file-hop with no coupling reduction | main.py:124 `lifespan` is procedural-linear; subsystems (`migrate_to_v2`, `run_watcher`, `build_full_index`, `summary_cache`) are already in their own modules |
| cc_watcher.py | LOW ("cohesive, single-responsibility — supervises both observers in one task") | MED ("docstring admits the second observer is easy to miss") | **LOW** — Engineer REVISED. Architect's argument: textual cohesion ≠ operational cohesion; one shared backstop poll + one supervisor would have to be duplicated on split | cc_watcher.py:652 `async def run_watcher(stop_event)` is single supervisor; module docstring explicitly justifies single-file design |
| store.py | MED (extract graph traversal to `conversation_tree.py`) | LOW (helpers are store-specific) | **LOW** — Architect REVISED. Engineer's argument: until a second consumer requires `build_message_tree`, extraction adds indirection without value | store.py:195 `build_message_tree` is consumed only by `ConversationStore.get_conversation_tree`; no external caller |

## Findings table

| Class | File | LOC | Severity | Status | Proposed action |
|---|---|---|---|---|---|
| A2 | backend/search_index.py | 1644 | HIGH (cycle) | DONE (425377a) | Moved `_TOOL_PLACEHOLDER_RE`, `_extract_searchable_text`, `_stringify_tool_input` to new `backend/search_text.py` (stdlib-only leaf). Updated import at search_index.py:66 from `.search` to `.search_text`. |
| A2 | backend/search.py | 1457 | HIGH (cycle) | DONE (425377a) | Re-exports the three symbols from `search_text.py` for backwards compat with existing test imports. The lazy import at search.py:495 was KEPT (council dissent on initial proposal — load-bearing for `mock.patch` semantics in 10+ tests AND for the `try/except ImportError` "search never goes down" fallback at search.py:533). Cycle is broken structurally: search_index no longer imports from search. |
| A2 | backend/routers/fetch.py | 1179 | MED | DONE (1a00f84) | CONSERVATIVE split per fix-run abbreviated council (Engineer dissented on full split; landmine: 60+ tests patch `backend.routers.fetch.<name>` and moving consumers breaks `monkeypatch.setattr` resolution). Moved 3 patch-safe SSE helpers (`_send_event`, `_is_session_expired_error`, `_drain_retry_events`) to new `backend/fetch_pipeline.py`. Heavier pipeline (`_fetch_phase_stream`, `_capture_phase_stream`, `refresh_pipeline_stream`, `_run_capture_with_keepalive`) intentionally KEPT in routers/fetch.py because their bodies reference test-patched names. Also removed the `sys.path.insert` hack at lines 15-16 (verified `fetcher` package imports cleanly via pyproject.toml's wheel `packages` entry). routers/fetch.py: 1179 → 1147 LOC. |
| A2 | backend/export.py | 1310 | MED | DONE (81be897 + 606842b + d7bc1bd + 9c252e0) | FULL package split: created `backend/exporters/{_shared.py, markdown.py, pdf.py, bundle.py}`. `backend/export.py` is now a pure backwards-compat facade (~126 LOC) re-exporting every name through an explicit `__all__`. Council A2 placement corrections honored: `CC_IMAGE_MARKER_RE` and `_resolve_attachment_path` live in `_shared.py` (consumed by both pdf and bundle), `render_content_block` lives in `markdown.py` (only consumed by markdown surface). WeasyPrint stays lazily imported inside `create_pdf`. Dependency graph is acyclic: markdown/pdf/bundle each depend only on `_shared`. Tests use `from backend.export import X` (value-binding) — no patches break. export.py: 1310 → 126 LOC. |
| A2 | backend/store.py | 824 | LOW | KEEP | Council REJECTED extraction. One domain (read/parse conversations); helpers are store-specific. |
| A2 | backend/cc_message_transforms.py | 817 | LOW | KEEP | Pure-leaf transform pipeline; cohesive. |
| A2 | backend/cc_watcher.py | 803 | LOW | KEEP | Operational cohesion (shared backstop poll + supervisor) wins over textual cohesion. |
| A2 | backend/main.py | 663 | LOW | KEEP | Lifespan orchestrates already-extracted subsystems; splitting adds file-hop without decoupling. |
| A2 | backend/summary_cache.py | 502 | NIT | KEEP | One class, one concern, barely over threshold. |

## Tests pinning the deferred refactors

For when the user implements these, the existing suite provides the contract.

### HIGH — search cycle fix (search_text.py extraction)

Pinned by:
- `backend/tests/test_search_extract_no_double_index.py` — must continue to pass byte-for-byte (this test specifically asserts `_stringify_tool_input` doesn't double-index)
- `backend/tests/test_search_snippet_fragments.py` — frag rendering invariants
- `backend/tests/test_search_index.py::test_upsert_rollback_on_executemany_failure` — the schema-rebuild rollback path (the index still imports from the moved location)
- `backend/tests/test_search_equivalence.py` — FTS5 vs linear-scan parity
- `backend/tests/test_search_tool_awareness.py` — `include_tool_calls` semantics depend on the projection
- `backend/tests/test_search_index_thinking_purge.py` — index-time text projection

Refactor methodology: pure-move + import-rewire. No body changes. If any of these tests need non-mechanical edits, the move is not pure → STOP and rethink.

### MED — routers/fetch.py split (fetch_pipeline.py extraction)

Pinned by:
- `backend/tests/test_fetch_concurrency.py` — the `_refresh_in_progress` lock + 409 contract
- `backend/tests/test_fetch_errors.py` — error classification (verify `_classify_error` import path stays clean)
- `backend/tests/test_fetch_refresh_sse.py` — SSE event-frame ordering and shape
- `backend/tests/test_fetch_start_sse.py` — legacy stream contract
- `backend/tests/test_refresh_pipeline.py` — combined-pipeline integration
- `backend/tests/test_force_refetch.py` — per-conv force-refetch path
- `backend/tests/test_error_classification.py` — explicit unit cover for the helper

Refactor methodology: pure-move + reroute via `from ..fetch_pipeline import …` in routers/fetch.py. The router file should drop to ~250 LOC.

### MED — export.py split (exporters/ package)

Pinned by:
- `backend/tests/test_export.py` — top-level
- `backend/tests/test_export_bundle.py` — bundle layout
- `backend/tests/test_export_bundle_attachments.py` — non-image attachment plan
- `backend/tests/test_export_all_markdown.py` — empty-corpus README contract
- `backend/tests/test_export_pdf_images.py` — PDF image bytes
- `backend/tests/test_export_pdf_concurrency.py` — concurrent PDF render safety
- `backend/tests/test_export_images.py` — image marker rewrite
- `backend/tests/test_export_excludes_markers.py` — argless-marker exclusion (one truth, three surfaces)
- `backend/tests/test_export_no_tool_placeholder.py` — tool-placeholder strip parity

Refactor methodology: same — pure-move + re-export from `backend/export.py` for backwards compatibility. The "one truth, three surfaces" invariant (visible-message rules consistent across MD/PDF/bundle) means `message_has_visible_content` MUST stay shared — put it in `_shared.py`.

## Open items (user-deferred — pilot was hunt-only)

When you re-invoke for fix mode:

- **HIGH search cycle** is the cleanest first-fix candidate. Pure refactor, well-pinned by tests, breaks a real structural fragility. Estimated: 1 commit, <50 LOC of diff.
- **MED routers/fetch.py split** is the second cleanest. ~8 functions move to a new file. Estimated: 1–2 commits.
- **MED export.py split** is largest. Multi-file move with package introduction. Estimated: 3–4 commits (markdown, pdf, bundle, shared).

## Pilot-run methodology notes

These notes are for tuning the agent prompt, not part of the Decision Record proper:

- **Both PAL preflight pings PONG'd** end-to-end. No fallback triggered. Heterogeneous-provider signal preserved.
- **Round 2 had real disagreement** on 6 of 9 findings. 5 of those 6 produced code-cited reversals; 1 was already convergent (routers/fetch.py). Not rubber-stamp convergence.
- **Engineer hit gpt-5.2-pro's 76,800-token file budget** when sent all 10 files in Round 1. Retry with 5 files (top-4 + CLAUDE.md) succeeded. **Action item for agent spec**: file budget heuristic — pre-estimate token count and cap initial file send to 6 files max, inline summaries for the rest.
- **Engineer was rigorous about LINE-marker citations** in Round 2 — initially refused to proceed without them. Got unstuck when prompted with explicit ack of how PAL's file embedding works. **Action item**: agent should explain the LINE-marker convention up front, or the personas may stall.
- **Both personas grounded citations correctly** — line 66 and line 495 cycle finding was grep-verified during synthesis. No hallucination detected on the 7 line citations I cross-checked.
- **The Decision Record DID have decision-relevant content** — 5 real reversals with code evidence is a substantive output, not boilerplate.

## Follow-up actions for the user

1. ~~Decide whether to greenlight the HIGH search-cycle fix as the first invocation of `mode:hunt-and-fix`.~~ **DONE — commit 425377a, 2026-05-21.**
2. Decide whether the agent prompt should default `class:A2-god-modules` recon to include subdirectories (`backend/routers/`, etc.). Pilot showed the wc-l recon misses these by default. (Patched in agent spec 2026-05-21.)
3. Decide if you want a fresh hunt for Category A1 (module-boundary violations) — the routers/fetch.py finding partially overlaps with A1.
4. ~~The two MED splits (`routers/fetch.py`, `export.py`) remain DEFERRED. Re-invoke with `tiers:HM` when ready to ship them.~~ **DONE — commits 1a00f84 + 81be897/606842b/d7bc1bd/9c252e0, 2026-05-21.**
5. The remaining backend god-module candidates (`backend/store.py` 824 LOC, `backend/cc_message_transforms.py` 817 LOC, `backend/cc_watcher.py` 803 LOC, `backend/main.py` 663 LOC, `backend/summary_cache.py` 502 LOC) all classified as LOW or KEEP by the original council and are NOT scheduled for the V1 polish window. Re-invoke for class:A2 only if usage patterns shift (e.g., a second consumer of `build_message_tree` appears in store.py).

## Fix-run addendum (2026-05-21, commit 425377a)

### Confirmation-round council outcome

The agent ran an abbreviated council (single round, no Round 2 cross-critique) since the pilot Decision Record was already convergent. Both personas were asked to confirm OR dissent on the proposed fix shape.

**Convergent CONFIRMs** on (a) fix shape, (b) leaf-safety check, (d) backwards-compat re-export approach.

**Convergent DISSENT** on the proposal to promote the lazy import at `search.py:495` to a top-level import after breaking the cycle. Both personas independently cited:

1. The comment block at `backend/search.py:482-484` explicitly documents that the lazy import exists for **test patchability**, not just cycle avoidance. Tests patch `backend.search_index._search_index` after `backend.search` is loaded; a top-level `from .search_index import get_search_index` would bind the unpatched reference at module load time.
2. The surrounding `try/except ImportError` at `backend/search.py:533` codifies the module docstring promise "search never goes down" — a top-level import would prevent `backend.search` from loading at all if `search_index` ever fails to import.

CTO accepted the dissent. The lazy import was kept exactly where it was. Verified by grep against `backend/tests/`: 10+ tests use `monkeypatch.setattr(si, "_search_index", idx)` — the pattern that requires late-binding.

### Result

- Structural cycle broken (`search_index.py` no longer imports from `search.py`).
- Lazy import at `search.py:495` preserved for the orthogonal reasons it actually existed for.
- New leaf `backend/search_text.py` (256 LOC) — stdlib-only.
- `backend/search.py` shrank from 1457 to ~1220 LOC.
- `search.py` re-exports the three symbols (`__all__` explicit) so existing test imports work byte-for-byte.
- Test suite: 857 passed before and after (zero regressions). 123 pinning tests run as a focused subset before the full-suite pass.

### Agent-prompt patches verified in this run

1. **Recon recursion**: confirmed working — the pilot had already surfaced `backend/routers/fetch.py` (1179 LOC), but this run didn't need the recursion since it was a targeted fix.
2. **File-budget pre-check**: not exercised heavily this run (only 3 files attached to each PAL call: `search.py`, `search_index.py`, the plan file). Both calls returned cleanly, no token-budget retry needed.
3. **CITATION CONVENTION pre-explanation**: included in both Architect and Engineer prompts. The Engineer cited line numbers with the expected precision (`search.py:482-484`, `search.py:495`, `search.py:533-535`, `search_index.py:66`, `search_index.py:543-545`) without asking the agent to clarify line-marker semantics — the pre-explanation worked.

### Surface for agent spec tuning

- The **lazy-import-with-test-patch pattern** is a recurring landmine class. The agent should add a hunt-class check: when a refactor proposes promoting a lazy import to top-level, grep `tests/` for `monkeypatch.setattr|patch.*<module>\.` against the affected module first, and surface the count to the council. (This run caught it via the council's own scrutiny — good — but the check could be cheaper.)
- The **abbreviated-council short-circuit** worked well for a convergent pre-documented finding. Worth codifying as a documented pattern in the agent spec: when entering fix mode on a finding already documented in a Decision Record, prefer "confirm OR dissent" prompts to a fresh 3-round council. Saved ~$0.40 and ~6 minutes of wall-clock.

## Fix-run addendum (2026-05-21, tiers:HM — second deferred-MED run)

### Abbreviated-council outcomes

The agent ran the patched-spec abbreviated-council pattern for both deferred MED findings, in sequence (no parallel — both touch backend/ internals and the export refactor's package introduction would have raced any concurrent backend work).

#### routers/fetch.py — Engineer DISSENT on full split, Architect P2

The structural pre-check (a new addition per the agent spec patch) found 60+ tests patching `backend.routers.fetch.<name>` — including `ClaudeFetcher`, `capture_credentials`, `load_credentials`, `DEFAULT_CREDENTIALS_PATH/OUTPUT_DIR/FILES_DIR`. The Engineer (gpt-5.2-pro) cited the test-patch landmine and refused the full split: moving `_fetch_phase_stream`, `_capture_phase_stream`, `refresh_pipeline_stream` would resolve `ClaudeFetcher` in the new module's namespace, breaking `monkeypatch.setattr(fetch_router, "ClaudeFetcher", ...)`. The Architect (gemini-3-pro-preview) was P2 ("accept mechanical test churn and ship the full split").

**CTO decision: Engineer wins.** A "mechanical" sed pass wouldn't work because 23 tests use `from backend.routers import fetch as fetch_router; monkeypatch.setattr(fetch_router, "X", ...)` — these are attribute patches that require X to remain a top-level name on the router module. The Architect's "0/10 on conclusion" missed this layer. Conservative split shipped:

- Moved 3 helpers: `_send_event`, `_is_session_expired_error`, `_drain_retry_events` to new `backend/fetch_pipeline.py` (86 LOC).
- Heavier pipeline functions intentionally kept in routers/fetch.py.
- `sys.path.insert` hack at lines 15-16 REMOVED (verified `fetcher` is importable via pyproject.toml's wheel `packages` entry).
- routers/fetch.py: 1179 → 1147 LOC.
- Test suite: 857 passed before, 857 passed after.

#### export.py — CONVERGENT CONFIRM on full package split

The structural pre-check found that ALL test imports use `from backend.export import X` (value-binding) and NONE do `monkeypatch.setattr(backend.export, "X", ...)`. The test patches on the export route are at `backend.routers.export.X` (the router's local binding). This is materially different from fetch.py: a facade pattern is genuinely safe.

Both personas confirmed Option A (full package split with backwards-compat facade). Council refinements applied:

- `CC_IMAGE_MARKER_RE` placed in `_shared.py` (used by both pdf.py and bundle.py — keeping it in bundle.py would have forced pdf→bundle import).
- `_resolve_attachment_path` placed in `_shared.py` (same — bundle.py uses it via `_resolve_bundle_attachment_path`).
- `render_content_block` (markdown variant) placed in `markdown.py`, NOT `_shared.py` — surface-specific.
- Explicit `__all__` list in the facade (no star imports).
- WeasyPrint stays lazily imported inside `create_pdf`.

Shipped as 4 chunks for reviewability:
- `81be897`: _shared.py (334 LOC)
- `606842b`: markdown.py (190 LOC)
- `d7bc1bd`: pdf.py (504 LOC)
- `9c252e0`: bundle.py (453 LOC) + facade rewrite

`backend/export.py`: 1310 → 126 LOC (pure facade with explicit `__all__`).
Test suite: 857 passed before, 857 passed after each chunk.

### Agent-spec patches verified in this run

1. **Structural pre-check** (lazy-import-with-test-patch grep): caught the routers/fetch.py landmine BEFORE the council convened, surfaced 60+ patch sites to both personas. This is exactly the pattern the agent spec called out — worked as designed.
2. **Phase 1.5 abbreviated-council short-circuit**: used for both findings since both had pre-documented Decision Records. For routers/fetch.py, the council SPLIT (not converged) and the agent escalated properly — Engineer's evidence won. For export.py, the council CONVERGED with refinements. The pattern's escalation rule (split → CTO synthesis with code evidence) held up well.
3. **No worktree concurrency**: ran sequentially as planned. routers/fetch.py first (smaller, less risk), then export.py (larger).
4. **>3 files per commit guardrail**: respected — each export.py chunk modified exactly 2 files. The facade rewrite (chunk 4) modified `export.py` + created `bundle.py` (2 files) and the diff was conceptually self-contained.

### Surface for further agent-spec tuning

- The Architect's reflex to "P2: accept mechanical test churn" on routers/fetch.py would have produced a broken commit if shipped without the structural pre-check. The agent spec's heuristic "if Architect proposes mass test churn, the Engineer must explicitly cite the patch failure mode" is **load-bearing**, not optional. Worth promoting from "watch for it" to a hard prompt-level instruction in the Architect persona.
- The convergence-CONFIRM vs convergence-DISSENT distinction is more useful than I'd weighted it. When the council CONVERGES on a CONFIRM (export.py), shipping is low-risk. When they CONVERGE on a DISSENT (the search.py lazy-import preservation in the HIGH fix), shipping is also low-risk. When they SPLIT (routers/fetch.py), the abbreviated council should have an explicit "show me code evidence" round — which is what happened here organically.
- The facade pattern (`backend/export.py` re-exports everything) was a clean win precisely because the tests already used the value-binding import idiom. Worth documenting in TESTING.md as a convention: prefer `from module import X` (value-binding) over `import module; module.X` (attribute lookup) for tests, because it makes refactoring the underlying module non-breaking.

## A1 hunt (2026-05-21, tiers:HM — module-boundary violations)

### Council
- Architect: gemini-3-pro-preview (thinking_mode=high)
- Engineer: gpt-5.2-pro (thinking_mode=high)
- CTO: opus-4.7
- Preflight: PASS, both PONG'd

### Commit range
- Baseline SHA: 643f4d2bd0e670f3a036d832aaf01ba10d68128c
- Final SHA: cc7278397b55b678b6369a9a165d9a9c421d4ec5
- Commits added:
  - `67f41bc refactor(search): replace SearchIndex private reach-through with public title_match_uuids (council A1)`
  - `9a35e51 chore(fetch): remove unused _refresh_lock (council A1)`
  - `cc72783 chore(fetch): replace asyncio.get_event_loop() with get_running_loop() (council A1)`

### Recon

The standard A1 grep panel returned **ZERO violations**:

```
1. `from fastapi` outside routers/main/deps                                       0 hits
2. `HTTPException` outside routers/main/deps                                      0 hits (sole main.py SPA 404 = bootstrap-layer)
3. `Depends` outside routers/deps                                                 0 hits
4. `Request/Response/BackgroundTasks/UploadFile/APIRouter` outside routers        0 hits
5. Non-router functions returning FastAPI Response types                          0 hits
6. Starlette imports                                                              0 hits
7. Raw Request access (.headers/.cookies/.query_params) in non-routers            0 hits
8. fetcher/ sibling module FastAPI coupling                                       0 hits
```

The codebase is **textbook clean at the import level**. The recent A2 refactors (search↔search_index cycle break, routers/fetch.py extract, export.py facade) actually *strengthened* the boundary — `backend/fetch_pipeline.py` deliberately uses `TYPE_CHECKING` for `ClaudeFetcher` and has zero FastAPI imports.

Per Phase 1 triage gate, recon-zero would normally skip the council. But the user requested a full review for portfolio-grade, so the council engaged on the meta-question: "Is the rubric correct, or are we missing subtler boundary smells (transport-shaped coupling, persisted-schema HTTP semantics, private-method reach-through)?"

### Decision Record — A1 module-boundary violations

| Field | Content |
|-------|---------|
| Chosen Approach | Ship the import-level boundary AS-IS (textbook clean). Fix three contained, behavior-preserving items now under tiers:HM: (1) extract a public SearchIndex.title_match_uuids() method to replace the search.py private reach-through (MED), (2) delete the unused _refresh_lock (LOW, free alongside A1 hygiene), (3) modernize get_event_loop() → get_running_loop() at the two router sites (LOW). STOP-and-report the HTTP_*** magic-string finding — it crosses into persisted-schema/API-contract territory and exceeds the "behavior-preserving" guard rail of tiers:HM. |
| Top Rejected | Move/rename `fetch_pipeline.py` under a transport-named directory. Engineer initially proposed it; Architect rejected with "the docstring already names the coupling honestly — moving sweeps it under a different rug"; Engineer conceded in Round 2 ("any further boundary cleanup here is a 'pay down test coupling' project, not V1 work"). |
| Decision Basis | Council Round 1 converged on three "behavioral boundary" items beyond what grep can catch. Round 2 collapsed the fetch_pipeline.py file-move proposal (Engineer conceded with cite to docstring at lines 6-12), and split the remaining two: Architect rated search.py reach-through HIGH ("Repository pattern break"), Engineer rated MED ("contained legacy path, public alternative trivial"). Engineer's blast-radius argument won — it's one block in the context_size=full path, the fix is ~30 LOC. The HTTP_*** finding split between "rename magic strings" (Architect) and "thread the clean `kind` through" (Engineer). Deeper recon found a SECOND generator site at fetcher/bulk_fetch.py:964 + persistence via _index.json + SSE "reason" field — making any rename an API/schema commitment that exceeds tiers:HM's "behavior-preserving" rule. |
| Residual Risks | (a) HTTP_*** vocabulary persists in user-disk schema and SSE `"reason"` field — surfaced to user for sign-off, NOT silently ignored. (b) `fetch_pipeline.py` naming remains "pipeline" rather than "transport"; the docstring is honest about the coupling and the move would churn 50+ test patch sites without reducing coupling. (c) `store.py:281-282` `or get_settings()` fallback was reviewed and accepted — it's an infrastructure-adapter default pattern, not a boundary leak (Engineer correctly distinguished domain entities from infra adapters). |
| CTO WWCMM | I would reverse the "ship import boundary as-is" position if a non-router/non-main/non-deps module gains a `from fastapi import` line, OR if any business-logic module starts emitting `data: ...\n\n` SSE frames (a second instance of fetch_pipeline.py-style transport coupling). Specific test: `grep -rnE 'from fastapi\|"data: \|text/event-stream\|Cache-Control: no-cache' backend/ \| grep -v routers/ \| grep -v main.py \| grep -v deps.py \| grep -v fetch_pipeline.py` — non-empty result flips the verdict to "boundary leak is spreading, needs structural intervention". |

### Per-disagreement resolution

| Disagreement | Architect | Engineer | Resolution | Evidence cited |
|---|---|---|---|---|
| Move fetch_pipeline.py? | NO (no churn; sweeps under different rug) | YES → CONCEDED NO | NO — Engineer conceded with cite to docstring | `backend/fetch_pipeline.py:6-12` makes the coupling explicit; 50+ patch sites would churn |
| search.py `_private` reach-through severity | HIGH (Repository break) | MED (contained, legacy path) | **MED** — Engineer's blast-radius argument: it's one block in context_size=full, fix is ~30 LOC | `search.py:967-974` is the only reach-through; `SearchIndex` has 23 internal `_get_read_conn()` callers — truly internal infra |
| `store.py:281-282` `or get_settings()` fallback | MED (DI smell) | NOT a smell (infra adapter) | **NOT a smell** — Engineer correct: `ConversationStore` is infra (not domain); production path via `deps.py:get_store()` IS clean DI; the fallback exists for CLI + test ergonomics with explicit override via constructor args | `deps.py:get_store()` exists; `store.py:274` accepts `Path \| None`; the comment at 275-280 explicitly documents the precedence order |
| HTTP_*** "rename strings" vs "coherence bug" | Rename (MED) | Coherence bug — thread `kind` through (MED) | **STOP-AND-REPORT** — exceeds tiers:HM. Engineer's framing wins (it IS a half-finished refactor: `_classify_error()` is already called at line 674 producing clean ErrorKind, then immediately discarded). But the change touches persisted on-disk schema (`_index.json` `error_code`) AND SSE `"reason"` field — API/schema commitment, not behavior-preserving. Generated in TWO places (router + fetcher/bulk_fetch.py:964). | `fetcher/bulk_fetch.py:964` (second generator); `fetcher/bulk_fetch.py:996-998` (SSE `"reason": error_code`); `_index.json` writes via `save_index(error_code=...)` |
| `_refresh_lock` unused | LOW | LOW (test docstring at test_fetch_concurrency.py:5 confirms it's known-dead) | **LOW — DELETE** | Defined at `routers/fetch.py:53`, never acquired; test docstring explicitly says "declared-but-unused" |
| `asyncio.get_event_loop()` deprecated | LOW | LOW | **LOW — REPLACE** | Both call sites inside `async def` generators; `get_running_loop()` is the modern idiom (Python 3.10+) |

### Findings table

| Site | Severity | Type | Status | Commit |
|---|---|---|---|---|
| **Import-level A1 (entire backend/)** | — | none | TEXTBOOK CLEAN, ship as-is | — |
| `routers/fetch.py:53` `_refresh_lock` | LOW | dead-code | DONE | 9a35e51 |
| `routers/fetch.py:299, 634` `asyncio.get_event_loop()` | LOW | deprecation | DONE | cc72783 |
| `search.py:967-974` private reach-through | MED | encapsulation | DONE | 67f41bc |
| `routers/fetch.py:680-682, 918-922` + `fetcher/bulk_fetch.py:964, 996` HTTP_*** persistence | MED | persisted-schema / API contract | DONE — council A1 follow-up | `9ec2d00` |
| `fetch_pipeline.py` SSE coupling | LOW | naming | DEFER — docstring is honest; moving creates churn | — |
| `routers/bookmarks.py:106-146` inline persistence | LOW | layering | DEFER — not strictly A1 | — |
| `deps.py:41-45` hardcoded refusal-template path | NIT | hardcoded path | DEFER (NIT) | — |
| Router-inlined Pydantic DTOs (FetchStatus, Bookmark*, etc.) | NIT | none | SKIP — correct modern FastAPI co-location | — |
| `store.py:281-282` `or get_settings()` | NIT | none | KEEP — infra adapter pattern, explicitly overrideable | — |

### Tests added

| Test file | Function | Class | Bug repro |
|---|---|---|---|
| backend/tests/test_search_index.py | test_title_match_uuids_returns_substring_hits | A1 | Title sweep stops catching substring hits FTS5 can't reach |
| backend/tests/test_search_index.py | test_title_match_uuids_empty_needle_returns_empty | A1 | Empty/whitespace needle returning every row via `LIKE '%%'` |
| backend/tests/test_search_index.py | test_title_match_uuids_no_hits_returns_empty | A1 | SQL error swallowed without returning a valid empty set |
| backend/tests/test_search_index.py | test_title_match_uuids_source_filter | A1 | Source filter ignored on title sweep (cross-source bleed) |
| backend/tests/test_search_index.py | test_title_match_uuids_conversation_uuids_scope | A1 | Sidebar-scope filter fails on title-only hits |
| backend/tests/test_search_index.py | test_title_match_uuids_empty_conversation_uuids_returns_empty | A1 | Empty set treated as None — returns all instead of nothing |
| backend/tests/test_search_index.py | test_title_match_uuids_byte_for_byte_matches_legacy_reach_through | A1 | Public-method extraction subtly changes SQL semantics |

Total: 7 new tests. Test suite: 857 → 864 passed. Zero regressions. Transient-break verified for the search_index refactor by `git stash` of the new method file and confirming RED for expected `AttributeError`, then `git stash pop`.

### DONE — HTTP_*** persisted error_code vocabulary (council A1 follow-up, 2026-05-21)

Shipped under user-authorized `mode:hunt-and-fix class:A1-error-vocabulary tiers:H` invocation. Commit `9ec2d00`.

**The finding (original)**: `routers/fetch.py:680-682` derived `error_code = "HTTP_401" if "401" in str(exc) else ("HTTP_403" if "403" in str(exc) else "HTTP_404")` and persisted this value via `fetcher.save_index(error_code=error_code, ...)` into the user's per-org `_index.json` file. Same vocabulary re-generated independently at `fetcher/bulk_fetch.py:964` and emitted via SSE as the `"reason"` field at `fetcher/bulk_fetch.py:996-998`. Half-finished refactor: `_classify_error()` was ALREADY called at `routers/fetch.py:674` producing a clean `ErrorKind = Literal["AUTH", "TRANSIENT", "TERMINAL"]`, which was then immediately discarded.

**Council confirmation round (Phase 1.5 abbreviated)**:
- Convergence: both personas confirmed `Literal` (b) over `Enum`, both confirmed steps 3+4 (rollup + lockstep bulk_fetch), both dissented step 6 (`_classify_error` has no dead branches post-fix).
- Split dissent — CTO synthesis: Architect won step 2 (REPLACE `error_code`, don't keep legacy mirror). Evidence: `grep -rn '"error_code"\|error_code' backend/ fetcher/ --include='*.py' | grep -v tests/` shows only writers, zero readers post-write. The frontend keys off SSE `kind`, never on-disk `error_code`. Back-compat field carries no signal.
- Step 5 resolution: rollup operates on in-memory `org_results` (not disk reads), so "read-time migration" is defensive tolerance for in-flight legacy records via `migrate_legacy_error_code()`. Existing on-disk legacy records sit dormant until the org's next failure overwrites them (the merge in `save_index` rebuilds the failing org's entry wholesale per call).

**What shipped**:
1. New `PersistedErrorKind = Literal["AUTH_EXPIRED","ORG_FORBIDDEN","ORG_NOT_FOUND","TRANSIENT","TERMINAL"]` typed alias in `fetcher/bulk_fetch.py` — matches existing precedent (`ErrorKind = Literal[...]` at `routers/fetch.py:92`), avoids JSON-serialization churn.
2. Helpers in `fetcher/bulk_fetch.py`: `kind_from_http_status(int)`, `extract_http_status_from_message(str)`, `migrate_legacy_error_code(str)`.
3. `save_index()` signature: `error_code` → `error_kind: PersistedErrorKind | None, http_status: int | None`. Persisted record carries both fields.
4. Router (`backend/routers/fetch.py`) and bulk-fetch CLI (`fetcher/bulk_fetch.py`) updated in lockstep — both call sites for AUTH/TRANSIENT/TERMINAL now persist `(error_kind, http_status)`.
5. New `_rollup_bucket_for(record)` helper in `routers/fetch.py` switches on `error_kind` (new path) with `migrate_legacy_error_code()` fallback for any in-flight legacy records.
6. Frontend SSE contract unchanged — `FetchToast.tsx` keys off `kind` from error events, never read on-disk `error_code`.

**Tests**: 19 new tests across `backend/tests/test_error_vocab_persistence.py` (12) + `fetcher/tests/test_error_vocab_persistence.py` (5) + updates to 2 pre-existing shape-only tests. Transient-break verified by `git stash` of implementation files (kept tests) → 17 RED for expected reasons (missing `error_kind` field, missing imports) → `git stash pop` → all GREEN.

**Test suite**: 864 → 883 passed (backend + fetcher). 325 frontend tests GREEN (FetchToast included). Zero regressions.

**Residual risk**: orgs that succeed post-upgrade keep dormant legacy `error_code` in their `_index.json` entry until their next failure event. Acceptable — diagnostic-only, unread by any production code path.

### Methodological notes (for agent-spec tuning)

- **The "recon found zero" outcome is a legitimate council outcome.** A1 finding zero at the import level IS the right answer for this codebase, and the council correctly extended the inquiry into "behavioral boundary smells grep can't catch" rather than rubber-stamping. The Phase 1 triage gate ("0 hits → skip council") should have an explicit override: if `class:` is in the invocation, run the council on the meta-question. Worth promoting to the agent spec.
- **The Engineer's Round-2 "files_required_to_continue" demand** for line-numbered evidence was the correct discipline call — they refused to do a code-citing critique without a citable evidence pack. Providing the line-numbered evidence in a single follow-up message worked well; both personas then produced sharp, code-cited critiques. The agent should pre-emptively include a line-numbered evidence pack in Round 2 if the Round 1 prompt didn't already.
- **The HTTP_*** finding surfaced a SECOND generator site at fetcher/bulk_fetch.py:964 only on deeper recon.** The council's original framing was "router-contained" — incomplete. Worth adding a recon step: when a magic-string finding lands, grep the entire repo (not just the file in question) for additional generator sites before scoping the fix.
- **STOP-and-report worked correctly**: the agent escalated the HTTP_*** finding to user-sign-off territory rather than silently broadening tiers:HM. The escalation criteria (API/schema commit, persisted-state change, multi-module touch) are good agent-spec material.

### Follow-up actions for the user

1. **HTTP_*** rename**: DONE in commit `9ec2d00` under follow-up `mode:hunt-and-fix class:A1-error-vocabulary tiers:H` invocation. See the "DONE — HTTP_*** persisted error_code vocabulary" section above.
2. **fetch_pipeline.py naming**: deferred indefinitely — moving creates churn without reducing coupling. The docstring is honest about the test-patch-pinned architecture. Revisit only if the test idiom is migrated away from attribute patching.
3. **routers/bookmarks.py inline persistence**: LOW. Not blocking V1. Worth a future hunt class (a "B-class" — REST API quality / data-layer placement) but not A1.
4. **deps.py hardcoded refusal-template path**: NIT. The user can decide whether to thread the live `Settings.config_path` through the template, or accept the small inaccuracy if the user overrode the config dir.


## 2026-05-21 — Categories B (REST API quality) + D (Pydantic / data modeling) batch

Invocation: `scope:backend mode:hunt-and-fix tiers:HM`. Both categories in one
council pass; B-class hunts before D-class since B findings can motivate
D-class model changes.

### Council
- Architect: gemini-3-pro-preview (thinking_mode=high)
- Engineer: gpt-5.2-pro (thinking_mode=high)
- CTO: opus-4.7
- Preflight: PASS, both PONG'd

### Commit range
- Baseline SHA: `ab09b7b4e9545f9e36fd8afb15035f5d263b0c66`
- Final SHA: `c232532`
- Commits added (4):
  - `c8e1a39 fix(orgs): unify credentials-corrupt response with rest of backend (council B1+B3)`
  - `d970c8e fix(routers): redact raw exception text from 500 detail responses (council B1)`
  - `e60917f refactor(models): centralize fetch wire contracts + ForceRefetchResponse (council D1+D3+B6)`
  - `c232532 docs(api): explicit OpenAPI summary on every route + media-type docs (council B6)`

Baseline test suite: 883 passed → 897 passed (+14 new tests; zero regressions).

### Recon results

- **B1** (HTTP status code consistency): 30+ HTTPException sites grepped.
  Three outliers found: orgs.py:62 used 500 for credentials_corrupt (vs.
  files.py:71 uses 503); export.py:139 and fetch.py:513 return
  `detail=str(e)` / `f"Fetch failed: {e}"` (raw-exception leak).
- **B2** (response envelopes / pagination): 1 inconsistency — `/api/conversations`
  returns bare `list`. All other multi-item routes use envelopes. **DEFERRED
  to LOW report** per council convergence — endpoint is performance-sensitive
  (ORJSONResponse + skinny projection), FE breakage cost vs. portfolio
  benefit is unfavorable for V1.
- **B3** (error response shape): 1 outlier (orgs.py — dict detail). 40 other
  sites use string detail. Headline finding.
- **B4** (HTTP verb misuse): no findings. Skipped.
- **B5** (routes doing too much): no findings — handlers thin; SSE generators
  are stream-coordination work, not "route doing too much." Skipped.
- **B6** (OpenAPI metadata gaps): every route relied on auto-derived summary;
  force_refetch returned bare dict; export/SSE routes had no documented
  media-type. 30 routes affected.
- **D1** (duplicate models): FetchStatus / FetchProgress lived in
  routers/fetch.py (FE-mirrored shapes — belonged in models.py per Task B
  convention). Bookmarks/Preferences/Search router-local models stay
  (they're stable + not in scope for the convention).
- **D2** (extra=ignore): no work — input models already at `extra='forbid'`
  from prior Hunt #6 round.
- **D3** (validators with side effects): 2 sites — `model_post_init(self,
  __context: Any)` in `ConversationListItem` + `ConversationSummary`.
  Double-underscore triggers Python name-mangling; anti-idiomatic.
- **D4** (discriminated unions): ContentBlock.type is a candidate but
  forward-compat trumps strict typing for an ingestor. **DEFERRED with
  endorsement of current state.**

### Decision Records

#### B3 — error response shape (the headline)

| Field | Content |
|-------|---------|
| Chosen Approach | (i) Keep `{"detail": "<string>"}`. Fix orgs.py only. |
| Top Rejected | (ii) Introduce `ApiErrorDetail(code, message, ...)` and nest under `detail`. |
| Decision Basis | (ii) requires touching all 41 HTTPException sites + frontend ApiError parser + adding a global exception handler. (i) is one-file. The FE `api.ts:263` explicit `typeof parsed?.detail === 'string'` check would have to be rewritten in (ii). Both Architect and Engineer converged on (i) after Round-2 cross-critique. |
| Residual Risks | Future need for machine-readable error codes — mitigated by optional `X-Error-Code` header pattern available later as additive change. |
| CTO WWCMM | Reverse to (ii) if >5 distinct user-visible error types ever need FE branching that brittle string-match can't carry. Repro: list every FE error-handling branch; observable signal: regex-based detail parsing. |

#### B1 — status code consistency + exception-leak hardening

| Field | Content |
|-------|---------|
| Chosen Approach | orgs.py credentials_corrupt: 500 → 503. export.py + fetch.py: redact raw exception text from `detail`; log `exc_info=True` server-side. |
| Top Rejected | Leave 500. (Mismatch with files.py + CWE-200 leak of `/Users/<name>/...` paths.) |
| Decision Basis | Files.py is the precedent for "credentials_corrupt" condition. 503 is semantically correct (Service Unavailable due to required dependency invalid). Exception messages from WeasyPrint + bulk_fetch routinely embed local paths and session keys — CWE-200 in a portfolio-piece app. |
| Residual Risks | None for the redact — generic message + server log is the documented pattern. 503 → 500 is a public contract change but no FE caller inspected the status. |
| CTO WWCMM | Reverse 503 if a proxy layer retries 503s aggressively and breaks UX. Reverse redact if support is materially harmed by missing exception text (no current support flow depends on it). |

#### B6 — OpenAPI completeness

| Field | Content |
|-------|---------|
| Chosen Approach | Add explicit `summary=` on every route. Add `responses={}` documenting media_type for SSE + export + file-proxy routes. Add `ForceRefetchResponse` model + `response_model=` on force_refetch. Build-fail test asserting both contracts. |
| Top Rejected | Rely on docstring first-line for summary (current state). |
| Decision Basis | Auto-derived "Export Pdf", "Search Post" is portfolio-piece embarrassing. /docs is the first thing a reviewer sees. Build-fail test prevents regression. |
| Residual Risks | New routes lacking summary= will fail CI — intentional. The auto-derive heuristic is keyword-overlap-based; a sentence-cased summary that happens to share words with the operation_id could false-positive (none in current codebase). |
| CTO WWCMM | Reverse if `/docs` is genuinely not part of V1 user journey — but it is (public V1, portfolio piece). |

#### D1 — model placement convention

| Field | Content |
|-------|---------|
| Chosen Approach | Move FetchStatus + FetchProgress + new ForceRefetchResponse to `backend/models.py`. Re-export from `backend/routers/fetch.py` for backward-compat test imports. Leave Bookmarks/Preferences/Search router-local. |
| Top Rejected | Move ALL router-local models to models.py. |
| Decision Basis | Convention is "models.py for wire shapes mirrored by frontend/src/lib/types.ts." FetchStatus + FetchProgress are mirrored (lib/api.ts re-declares FetchStatus inline). Bookmarks/Preferences are FE-mirrored but stable + router-local hasn't caused drift; minimal-diff applies. |
| Residual Risks | If models.py becomes a dumping ground, future split to `backend/models/` package. Not yet. |
| CTO WWCMM | Reverse if PR churn on models.py causes merge conflicts >1/sprint. Observable signal: rebase pain. |

#### D3 — `__context` rename

| Field | Content |
|-------|---------|
| Chosen Approach | Rename `model_post_init(self, __context: Any)` to `model_post_init(self, context: Any, /)`. Positional-only marker preserves intent. |
| Top Rejected | Switch to `@computed_field`. |
| Decision Basis | `@computed_field` is read-only / lazy; eager assignment to `self.project_name` preserves the existing wire-format invariant (field always present on serialized dict). Rename is the smaller-blast-radius fix. WWCMM test verifies Pydantic doesn't pass context as a kwarg literally named `__context`. |
| Residual Risks | None — Pydantic passes context positionally. Test pins this. |
| CTO WWCMM | Reverse if Pydantic v3 changes to pass context by kwarg name and breaks the rename. Repro: `model_validate(.., context={...})` raises TypeError. |

### Findings table

| Class | File | Severity | Status | Commit |
|---|---|---|---|---|
| B1+B3 | backend/routers/orgs.py:62 | HIGH | DONE | c8e1a39 |
| B1 | backend/routers/export.py:139 | HIGH | DONE | d970c8e |
| B1 | backend/routers/fetch.py:513 | HIGH | DONE | d970c8e |
| B6 | backend/routers/fetch.py force_refetch (no response_model) | MED | DONE | e60917f |
| B6 | 30 routes missing explicit summary= | MED | DONE | c232532 |
| B6 | Export/SSE/file-proxy routes missing responses= media-type docs | MED | DONE | c232532 |
| D1 | FetchStatus/FetchProgress in routers/fetch.py | MED | DONE | e60917f |
| D3 | model_post_init(__context) name-mangle | LOW (bundled) | DONE | e60917f |
| B2 | /api/conversations bare list (envelope inconsistency) | LOW | DEFERRED | — |
| D4 | ContentBlock.type discriminated union opportunity | NIT (endorse current) | DEFERRED | — |
| B1 | files.py:91 curl_cffi ImportError as 500 | NIT | DEFERRED | — |

### Tests added (14 new)

| Test file | Function | Class |
|---|---|---|
| backend/tests/test_orgs.py | test_endpoint_three_state_corrupt (updated) | B3 |
| backend/tests/test_orgs.py | test__get_orgs__credentials_v2_invalid__returns_503_corrupt (renamed) | B3 |
| backend/tests/test_orgs.py | test__get_orgs__credentials_truncated_json__returns_503_corrupt (renamed) | B3 |
| backend/tests/test_orgs.py | test__get_orgs__credentials_corrupt_detail_must_not_be_dict | B3 (bidirectional negative) |
| backend/tests/test_export_pdf_concurrency.py | test_pdf_export_runtime_error_does_not_leak_exception_text | B1 (CWE-200 canary) |
| backend/tests/test_force_refetch_messaging.py | test_force_refetch_internal_error_does_not_leak_exception_text | B1 (CWE-200 canary) |
| backend/tests/test_models_post_init_context.py | 7 tests covering D3 rename WWCMM | D3 |
| backend/tests/test_openapi_polish.py | test_every_route_has_an_explicit_summary | B6 |
| backend/tests/test_openapi_polish.py | test_no_route_has_an_empty_summary | B6 (bidirectional) |
| backend/tests/test_openapi_polish.py | test_every_json_route_documents_a_response_schema | B6 |
| backend/tests/test_openapi_polish.py | test_force_refetch_response_schema_documented | B6+D1 regression |

Transient-break verified for each HIGH fix (orgs.py, export.py, fetch.py
exception leak) by `git stash` of the implementation file → re-run → confirm
RED for same reason → `git stash pop`. D3 was refactor (5B); no
transient-break required.

### Open items (deferred to LOW report — user discretion for V1)

- **B2 — `/api/conversations` envelope inconsistency**. Endpoint returns
  bare list while SearchResponse / OrgsResponse / BookmarkList use envelopes.
  Council deferred for V1 due to: (a) performance optimization on the
  endpoint (ORJSONResponse + skinny projection), (b) FE breakage cost vs.
  portfolio benefit. Revisit if external script callers ever materialize
  and need consistency.
- **D4 — ContentBlock.type discriminated union**. Council endorses CURRENT
  state ("type: str + all-optional fields + extra='ignore'"). A discriminated
  union would 422 on novel upstream block types (CC adds new variants
  silently), which is exactly wrong for an ingestor. Optional documentation:
  add a `Literal` typed alias to make the known set readable, but don't
  enforce.
- **B1 (NIT) — files.py:91 `curl_cffi` ImportError returns 500**. Council
  flagged as P2 but it's a programmer error not a runtime user error. The
  programmer-vs-user distinction is more honest than blanket-503ing
  "missing dep."

### Follow-ups requiring user action

- Frontend: optional cleanup of `if (typeof parsed?.detail === 'string')`
  defensive parse in `lib/api.ts:263`. Now that backend never returns dict
  detail, the check is dead defensive code. Low-priority.
- Frontend: optional removal of inline `getFetchStatus` re-declaration in
  `lib/api.ts` lines 270+ — replace with import of the new backend-shipped
  `FetchStatus` model contract. Requires generating TS from OpenAPI or
  hand-mirroring (current approach). LOW.
- Run `scope:frontend` invocation for Category E hunts (TS assertion lies,
  TanStack Query hygiene, context-storm subscriptions, file-size cliffs,
  effect dependency lies, test rubber-stamps).

---

## Sweep 4 — Categories C (correctness) + F (hygiene), 2026-05-21

### Council
- Architect: gemini-3-pro-preview
- Engineer: gpt-5.2-pro
- CTO: opus-4.7
- Preflight: PASS, both PONG'd
- Baseline SHA: `6b01dc506cf89647a93a548fa2ac492710548ae8`
- Baseline tests: 897 pytest GREEN

### Recon summary (10 classes batched)

| Class | Description | Recon result |
|---|---|---|
| C1 | async/sync violations in production async paths | **0 findings**. All blocking I/O properly off async handlers. Skipped (no council). |
| C2 | Threading correctness | Both councillors initially flagged candidate races (Architect: `_drift_timer = None`; Engineer: `_seen` set). BOTH WITHDREW at Round 2 after CTO code-walk: (a) `_drift_lock` held across the assignment so no race; (b) `copy_marker_image_to_cache` writes are content-addressed (sha8 in filename) so racing writes produce identical bytes — wasted CPU only. |
| C3 | Silent exception swallowing | **1 HIGH**: `backend/store.py:651-654` — fallback summary read swallowed all exceptions with no log, breaking the function's own contract ("never silently returns 404 when the data IS on disk"). 18 sites in `routers/fetch.py` are intentional SSE error mapping; lifespan + bookmarks/preferences atomic-tmp-file patterns intentional. |
| C4 | Resource lifecycle | 4 sqlite3.connect() not in `with` — all long-lived cache-singleton write connections (canonical pattern). Skipped. |
| C5 | Type-hint return annotations | 6 private helpers. Both councillors agreed at Round 2: defer — "release-grade vs minor". Mostly cc_watcher.py factory helpers (4) and search.py nested closures (2). |
| C6 | Function length ≥80 lines | 35 functions. Both councillors converged: KEEP all — sequentially cohesive state machines (lifespan, SSE generators, watcher) or extraction would risk byte-for-byte search drift (search.py paths). No extractions for V1. |
| F1 | Dead code | **0** real findings (2 false positives — FastAPI route handlers referenced via `@router` decorator). |
| F2 | Magic numbers | Not material. Existing named constants (`CHUNK=500`, `CAPTURE_TIMEOUT_SECONDS=300`, `_FALLBACK_SNIPPET_LEN=300`) are appropriate. |
| F3 | Module docstrings | **0 missing**. |
| F4 | Public API surface (`__all__`) | 5 modules without `__all__` (models.py, search_index.py, cc_watcher.py, cc_message_transforms.py, routers/bookmarks.py). Both councillors agreed: NIT — performative without `from X import *` usage. |
| F5 | Logging hygiene / PII | **0 PII leaks**. 12 path-logging sites all diagnostic-appropriate; no session keys, tokens, or org IDs in log messages. |
| F6 | Caching invalidation contracts | **Already documented** in module docstrings (cache.py, summary_cache.py, search_index.py all carry the canonical "Cache landscape" docstring with each cache's invalidation rule). |

### Decision Record — C3 silent swallow (the only HIGH)

| Field | Content |
|---|---|
| Chosen Approach | `logger.warning("Failed to read Claude Code summary for %s while resolving uuid=%s", jsonl_path, uuid, exc_info=True); continue`. Behavior-preserving minimal diff. |
| Top Rejected | Engineer's "warn-once-per-call dedupe" hardening — adds state for an operationally-rare path; revisit only if logspam ever materializes in practice. |
| Decision Basis | The fallback loop's leading comment explicitly promises "never silently returns 404 when the data IS on disk" — the exception swallow was contradicting the function's own documented contract. Both councillors converged HIGH; bidirectional test design rules out regressions in both directions (logs on failure, silent on success). |
| Residual Risks | If `read_conversation_summary_fast` is called frequently against a persistently-bad JSONL, log volume could grow. Operationally rare (summary_cache fast path covers 99%+); deferring dedupe per minimal-diff principle. |
| CTO WWCMM | Reverse "no other fixes" if a stress test of `handle_one_path()` from N=10 threads × 100 calls produces any non-identical destination bytes OR partial files — would re-elevate the C2 `_seen` race to MED. |

### Per-disagreement resolutions

| Disagreement | Architect | Engineer | Resolution | Evidence |
|---|---|---|---|---|
| C2 `_drift_timer = None` race | claimed MED | no claim | Architect withdrew Round 2 | `cc_watcher.py:471-475` + `:498-508` hold `_drift_lock` continuously across the assignment |
| C2 `_seen` race | no claim | MED→LOW conditional | Engineer settled LOW | `cc_image_cache.py:118-119` content-addressed sha8 filenames + write_bytes(identical_bytes) |
| C5 4 cc_watcher.py annotations | LOW/NIT all 6 | initially MED→withdrew | Both deferred Round 2 | "don't churn" + no CI enforcement of strict annotations |
| C6 conditional search.py extractions | NIT all | "if adjacent work this PR" | Both SKIP this run | drift risk > value for V1 |
| F4 `__all__` | NIT | NIT | Both NIT | no `from X import *` usage anywhere |

### Implemented fixes

| Commit | Site | Severity | Type |
|---|---|---|---|
| `72d16bd` | backend/store.py:651-654 | HIGH | 5A bug fix |

### Tests added

| Test file | Function | Class |
|---|---|---|
| backend/tests/test_store_find_diagnostics.py | test_find_logs_warning_when_summary_read_raises | C3 (must-MATCH) |
| backend/tests/test_store_find_diagnostics.py | test_find_does_not_log_when_summary_reads_succeed | C3 (must-NOT-MATCH boundary) |

Transient-break verified: `git stash` of `backend/store.py` → re-run → RED for same reason ("Got no WARNINGs — silent-swallow bug has regressed") → `git stash pop`.

### Open items (deferred to LOW/NIT — user discretion)

- **C2 `_seen` race in cc_watcher.py** (LOW). Wasted CPU under concurrent watchdog event + scan_once for the same image path. Content-addressed writes (sha8) mean no corruption. If high-event-rate scenarios ever cause measurable log/CPU pressure, add a `_seen_lock = threading.Lock()` around the check-then-act block.
- **C5 cc_watcher.py 4 factory-helper return annotations** (LOW). Could add `-> Observer | None`, `-> FileSystemEventHandler`, etc. for reader clarity. Defer unless a CI strict-typing rule is added.
- **C5 search.py 2 nested closure annotations** (NIT). Skip — types are visually inferable and adding them would force `datetime` import for trivial value.
- **C6 35 functions ≥80 LOC**. All judged cohesive. The conditional candidates (search.py snippet helper / title pseudo-message / rows-to-results assembly) should be revisited only when adjacent search semantics are changing in the same PR — drift risk on user-visible search results exceeds extraction value for a portfolio-piece V1.
- **F4 `__all__` for 5 modules** (NIT). Performative without `from X import *` usage. Revisit only if `backend.models` becomes a published SDK surface.
- **18 broad except sites in routers/fetch.py** (LOW). All intentional SSE error mapping with inline comments. No silent swallows; user receives the error on the wire. Leave.
- **C4 4 sqlite3.connect() without `with`** (NOT a finding). All are long-lived cache-singleton write connections — the canonical pattern for SQLite write-pool architectures. The C4 grep over-reports for this pattern.

### Follow-ups requiring user action

- (Optional) Run a stress test of `handle_one_path()` if you want to formally close the `_seen` LOW finding's WWCMM.
- The CODE-REVIEW-BACKEND.md plan is now complete across sweeps 1-4 covering Categories A (architecture), B (REST API), C (correctness), D (Pydantic), F (hygiene). Category E (frontend) is the remaining scope for the matching `scope:frontend` invocation.

## Cross-reference — Council fetcher Category A (2026-05-21)

A council sweep of `fetcher/` Category A explicitly considered cross-boundary refactoring opportunities between `fetcher/` and `backend/`. Full Decision Records and findings live in `PLANS/CODE-REVIEW-FETCHER.md`. Summary of cross-boundary results that touch backend:

- **`backend/tests/conftest.py`** updated — the multi-site monkeypatch fixture for `DEFAULT_CREDENTIALS_PATH` was extended to include the new canonical site `fetcher.paths.DEFAULT_CREDENTIALS_PATH` (commit `a7b4cff`). The four pre-existing patch targets (fetcher.credentials, fetcher.bulk_fetch, backend.routers.fetch, backend.routers.orgs) all remain.
- **Backend's import of `PersistedErrorKind` and friends from `fetcher.bulk_fetch`** continues to work via re-exports added in commit `53753ea` (Council A2-SPLIT). The canonical definitions moved to `fetcher.http_retry`; backend imports were intentionally NOT updated in the same commit to keep the cross-boundary blast radius small.
- **No new cross-boundary findings** beyond the above — `PersistedErrorKind`, `OrgRef`, `CredentialsV2`, `load_credentials` all live canonically in `fetcher/` and backend imports them through the correct (downward) layering direction.
- **Cross-boundary follow-up for backend** (low priority): backend imports of `PersistedErrorKind` and `extract_http_status_from_message` from `fetcher.bulk_fetch` could be migrated to `fetcher.http_retry` (the new canonical home). Cleanup, no behavior change. Defer.
