# EGM Stage 9: Implementation Readiness Test

- **Target:** `PLANS/2026.11.18-codex-source.md`, DetailedRange lines 262-378
- **Reviewers:** egm-implementation-readiness and egm-codebase-details (both with `implementation-readiness-standard`), egm-style

## egm-style

- **Status:** completed. 0 Blocker, 11 Major, 10 Nit (findings 12-21). No marketing language or filler.

| ID | Severity | Lines | Finding | Fix |
|---|---|---|---|---|
| IS-1 | 🟡 Major | 307, 312-313 | N1 and N2 both own `sources.generated.json` in wave 2. (Same as CO-20.) | Exclude it from N2, or say N2 only reads it. |
| IS-2 | 🟡 Major | 312, 317, 319, 320 | Owns entries mix full and bare paths; N7 "`main.py` (lifespan)" vs N6 `cli/main.py`. (Same as CO-23.) | Full repo-root paths everywhere. |
| IS-3 | 🟡 Major | 337-338, 344 | "Failing tests stop the work" contradicts step 1 (tests that fail on purpose). "P0" is not defined. "Fix it first" can need files outside the node. | "A test that fails on `main` before the node's change is a P0 bug." Say which rule wins. |
| IS-4 | 🟡 Major | 307, 340, 348 | "Fix sibling bugs in the same PR" conflicts with file ownership. "Sibling bug" is not defined. | Out-of-scope sibling: stop and report to the orchestrator. |
| IS-5 | 🟡 Major | 339 | "Combine the two worktrees" names no git operation. Does the coder get test files or only output? (Same as CO-21.) | Name the operation and what the coder receives. |
| IS-6 | 🟡 Major | 319 | "Through both layers" (4.3 has four). (Same as CO-22.) | Name layers 1 and 2. |
| IS-7 | 🟡 Major | 319 | "The corrupt-config gate stops every path" can read as stopping the watcher, which CLAUDE.md forbids (image loss). | "The gate skips `archive_sync`. Image capture and the drift pass still run." |
| IS-8 | 🟡 Major | 121, 312, 358 | One acceptance rule stated three ways (unchanged / bug-fix diff allowed / byte-for-byte). | One rule, repeated in the N1 row. |
| IS-9 | 🟡 Major | 314, 317 | "Works" cannot be measured; `state_db` with an unknown schema has no stated result. | Use "has tests" and state each expected result. |
| IS-10 | 🟡 Major | 318, 357, 371 | N6's test leaves out titles (§8 forbids them). The decoded-size measure is not in N6 or §8, and "10 times larger" has no baseline. (Same as CO-24.) | Add "no title"; add the size measure and its baseline. |
| IS-11 | 🟡 Major | 355 | "Readers must … close each file fast" cannot be checked, and no node owns it. The N3 test runs copiers only, so it cannot prove reader behavior. | "A reader keeps no handle open after `load` returns." Add a reader-vs-copier test, or narrow the claim. |
| IS-12 | 🟢 Nit | 264 | "Unless the table says otherwise" is vague. | Name the N2 exception. |
| IS-13 | 🟢 Nit | 294 | Is a wave a barrier? | The DAG controls start times; waves are derived. |
| IS-14 | 🟢 Nit | 268-320 | Node names differ between the diagram and the table. The NF `test/` prefix hides a runtime dependency. | One name per node. |
| IS-15 | 🟢 Nit | 312-323 | Partial ownership ("(roots only)") is not defined. | Define a scope in parentheses. |
| IS-16 | 🟢 Nit | 121, 311 | "Detail" vs "viewer"; no MCP golden directory. | One term; name the directory. |
| IS-17 | 🟢 Nit | 321 | "Unexpected" console errors. | Cite the existing console-gate allowlist. |
| IS-18 | 🟢 Nit | 328-330 | The gate items are hard to check: an unnamed Windows subset; "issues closed" passes with no reports or won't-fix; "list bugs" has no place. | Write each item as a checkable condition; require one passing report. |
| IS-19 | 🟢 Nit | 341 | `git commit --only` does not add new files. | "Stage new files with `git add <path>`." |
| IS-20 | 🟢 Nit | 342 | "Report a failure plainly" has no channel. | Name it, or drop the sentence. |
| IS-21 | 🟢 Nit | 369 | The salt rule is not stated. (PT-18 says to print counts only.) | Choose, or drop the hash. |

## egm-implementation-readiness

- **Status:** completed. **Verdict: NOT READY** (fails the Zero-Decision Rule). 6 Blocker, 10 Major, 8 Nit. The DAG shape is good: dependencies and waves agree, and the wave 3 and wave 5 file sets do not overlap.

| ID | Severity | Lines | Finding | Guidance |
|---|---|---|---|---|
| IR-B1 | 🔴 Blocker | 69, 74-81, 320 | The Codex id string, label, and flag values are not defined, nor the mapping of `archived_sessions/` and `state_5.archived` to `is_archived` (the store hides archived by default). (Same as SR-6, DE-M14, PT-8.) | Add the full Codex descriptor and the `is_archived` rule. |
| IR-B2 | 🔴 Blocker | 89-90, 192-216, 316 | The projection output is not defined (keys, `sender` values, `tool_use`/`tool_result` shapes, the encrypted-reasoning block type, the warnings field, the message uuid rule), and N4 runs in parallel with N1, which defines the protocol. The uuid rule matters most: the outline cache, search jump, and bookmarks key on it, and legacy records have no item ids. (Related to DE-M9.) | A schema table for `load` and `read_summary`, with the uuid rule per history mode. |
| IR-B3 | 🔴 Blocker | 93, 127-157, 314, 320 | `threads/<id>.json`, `<stem>.meta.json`, and `home.json` have no field list and no format version, and they are never deleted, so an invented schema becomes a permanent migration. `SyncContext`, `SyncReport`, `WatchRoot` have no fields. Step 7 is Codex-specific, so the N3/N8 split of steps 1-8 is undefined. | JSON schemas with `version`; dataclass signatures; map steps 1-8 to N3 and N8. |
| IR-B4 | 🔴 Blocker | 35, 317-320, 350 | The Codex home configuration is not decided, and no node owns `backend/config.py`. **No node owns test isolation:** once N8 registers the adapter, existing Refresh, MCP, and watcher tests run `archive_sync` against the developer's real `~/.codex`, which breaks D10. (Related to PT-3, DE-M5.) | Decide the config surface. Assign `config.py`. Add an autouse fixture in both conftests that points `CODEX_HOME` at an empty tmp dir (like `_no_remote_cowork_network`). |
| IR-B5 | 🔴 Blocker | 321 | In e2e, `data_dir` is `backend/tests/fixtures/desktop`, so the archive goes there, not to `fixtures/codex/**`. `home_key` differs per checkout. `home.json` holds an absolute path (pre-push check #2). A sync in e2e writes into the fixture tree and fails `git diff --exit-code backend/tests/fixtures`. No env flag disables it. | Commit a pre-synced archive with a fixed `home_key` and placeholder path, or add a `CLAUDE_EXPLORER_DISABLE_ARCHIVE_SYNC` flag. Fix N9's Owns. |
| IR-B6 | 🔴 Blocker | 323 | N11 needs new fields in `backend/models.py` and `frontend/src/lib/types.ts`, owns neither, and the fields are not defined. (Same as CO-20, ZO-7.) | Define the fields; move them to N8 or N10 (Z8-8 proposes one `notices` field in N8). |
| IR-M1 | 🟡 Major | 311-323 | Owns lists omit test files (N1, N7, N8, N11; others say "their tests") and use bare paths. (Same as IS-2.) | Full repo-relative paths, test files included. |
| IR-M2 | 🟡 Major | 107-108, 299, 312-313 | N1/N2 overlap on `sources.generated.json`. If the UI fetches `/api/sources`, 77 Playwright specs that intercept `**/api/**` get an unmocked call and can trip the console gate, and N2 does not own `frontend/e2e/**`. (Strengthens ZO-1.) | Exclude the JSON from N2; state each source's role; or drop the route. |
| IR-M3 | 🟡 Major | 4.5, 312, 314, 320 | Eager vs lazy imports on the MCP path are not decided (`_assert_deps` needs an exact match). N1 puts `backend/sources/**` on the MCP path but does not own `build-mcpb.py`. **`backend.cc_watcher` is forbidden in the MCP closure**, so `SingleFlight` cannot live there, and no node switches `cc_watcher` to it. The bundle dependency strings are not given. | Decide the import strategy. Name the `SingleFlight` module. List the `DYNAMIC_IMPORT_MODULES` additions and bundle dependency lines. |
| IR-M4 | 🟡 Major | 171-179, 319 | The MCP trigger, the watcher order, the 60 s timeout result, and the SSE payloads are not given. N7 changes visible Refresh behavior but does not own `UX.md`. D9 reverses a CLAUDE.md statement, and no node owns `CLAUDE.md`. | Specify each; add `UX.md` and `CLAUDE.md` to N7. |
| IR-M5 | 🟡 Major | 318, 357, 365-371 | N6 needs N3 and N8 for "archive results". Does `codex-report` sync (a writer) or only read? The salt is undefined. The decoded-size measure is missing. N6's test file is not owned. (Same as CO-19, IS-10.) | Fix the dependencies; decide; add the size line. |
| IR-M6 | 🟡 Major | 315-322 | Only NF owns `codex_fake.py`, but N4, N5, and N10 need more record kinds (damaged lines, encrypted reasoning, images, `turn_context`, parallel `call_id`, a locked `state_5`, the imports file, subagent JSON, `compacted`, rollback, fork, `history_base`). N10 lacks the record shapes. | List every kind in NF, or let each consumer own the builder. Give the slice 2 shapes. |
| IR-M7 | 🟡 Major | 121, 311 | N0 goldens: no normalization of tmp paths and mtimes, no format, no update command (that needs a conftest option, which no node owns), FTS vs linear search not chosen, and no remote Cowork fixture. | Specify each. |
| IR-M8 | 🟡 Major | 186, 314, 328 | `run_vm_tests.py` does not exist in the repository (only `scripts/vm-setup/*.ps1`, `*.sh`). The Windows subset is undefined. | Name the real command, or add the runner to N3. List the test ids. |
| IR-M9 | 🟡 Major | 314-323 | Ticket tests are missing: the Codex home byte-unchanged after a sync (#37 AC6); "newest" for D8 and its test (AC5); test cases in N8; the slice 1 warnings notice. (Same as DE-M1.) | Assign each, with assertions. |
| IR-M10 | 🟡 Major | 116-119, 313 | Bookmark migration: `claude_code` covers both CC and Cowork; when the file is rewritten; downgrade breaks (old versions reject new values). (Same as ZO-3, CO-10.) | Specify, or drop the change (ZO-3). |
| IR-n1 | 🟢 Nit | 320 | The exact export header lines and whether PDF and bundle exports get them. | Give them. |
| IR-n2 | 🟢 Nit | 316, 322 | The N10 hook signature. (Z8-5 removes the hook.) | Define or remove. |
| IR-n3 | 🟢 Nit | §6 | Contradictory rules. (Same as IS-3, IS-4, IS-5.) | Resolve. |
| IR-n4 | 🟢 Nit | — | No `SourceFilter` type. (Same as DE-N3.) | Define it. |
| IR-n5 | 🟢 Nit | 108, 312 | No generator command for `sources.generated.json`; the fixture check runs only in the e2e section of `check.py`. | Name both. |
| IR-n6 | 🟢 Nit | — | Where `atomic_write_bytes` lives. (Same as ZO-9.) | `fetcher/atomic.py`. |
| IR-n7 | 🟢 Nit | — | No N5 module parses `external_agent_session_imports.json`. | Name it. |
| IR-n8 | 🟢 Nit | 320 | The Codex icon and color are not given. | Give them. |

**Confirmed claims:** SPEC.md omits Cowork at the cited lines; `_run_drift_single_flight` is at `cc_watcher.py:481`; `cc_image_cache.py:146-150` has no temp file; `_atomic_write_text` has no fsync; `portalocker` is a dependency; `.github/ISSUE_TEMPLATE/` does not exist yet.

## egm-codebase-details

- **Status:** completed. **Verdict: FAIL** (Zero-Decision Rule and file-boundary rule). 5 Blocker, 17 Major, 6 Nit. Most citations check out (the list matches IR's confirmed list).

| ID | Severity | Lines | Finding |
|---|---|---|---|
| CD-B1 | 🔴 Blocker | 69, 74-81, 320 | N8 must add Codex to `SourceId` in `backend/sources/ids.py` but does not own it. The Codex id, descriptor values, and `is_archived` mapping are undefined. (Same as IR-B1.) |
| CD-B2 | 🔴 Blocker | 312 | N1 cannot register `/api/sources`: routers mount in `backend/main.py:1015-1027`, which N1 does not own. (Moot if the route is dropped, Z8-5.) |
| CD-B3 | 🔴 Blocker | 65, 110-115, 313, 320 | Hard-coded rules missing from §4.1: `Sidebar.tsx:299-311` (filter items), `Sidebar.tsx:424`, `ConversationList.tsx:364` and `:898`, `conversationListView.ts:196`, `:260`, `SourceBadge.tsx:52-68`, `types.ts:376`. N8 owns none of the files it needs. The rules are in **11** frontend files, not 7. (Same as ZO-2.) |
| CD-B4 | 🔴 Blocker | 157, 222-224, 322-323 | N10 and N11 need undefined fields in `models.py` and `types.ts`, which they do not own. `SubagentSummary` has no child-to-parent field. (Same as IR-B6.) |
| CD-B5 | 🔴 Blocker | 136-138, 321 | The e2e `data_dir` puts the archive under `fixtures/desktop/codex/`; `home_key` differs per checkout; a sync breaks the fixture diff check. (Same as IR-B5.) |
| CD-M1 | 🟡 Major | 230-232, 320 | `mcp_server/tests/test_build_mcpb.py:138` hard-codes the 4 allowed deps (N8 does not own it). **`compression` is not in `sys.stdlib_module_names` on 3.11-3.13**, so an eager `import compression.zstd` reads as an unknown package and fails the build; a lazy import hides `zstandard` and fails the other direction. Fix the import form, `DYNAMIC_IMPORT_MODULES`, and the marker. `CLAUDE.md:91-92` and `assets/mcpb-README.md:3` go stale. |
| CD-M2 | 🟡 Major | 230, 319-320 | MCP as writer: SPEC lines 7 and 443 become false. The debounce code is in `backend.cc_watcher`, and the gate template is in `backend.deps`; **both are forbidden in the MCP closure**. |
| CD-M3 | 🟡 Major | 178, 319 | **The CLI hook point contradicts "local first, no credentials":** `ClaudeFetcher.run` runs only after `cli/main.py:149-162` loads credentials, so a user without credentials gets an error before any local sync. The fix belongs in `cli/main.py` (N6 owns it in the same wave). `--output-dir` and `rehydrate` behavior are unspecified. |
| CD-M4 | 🟡 Major | 107-108, 312-313 | The N1/N2 overlap. Importing JSON needs `resolveJsonModule`, which `tsconfig.app.json` does not set. No generator path. The fixture check runs only in the e2e section of `check.py`. |
| CD-M5 | 🟡 Major | 312-323 | Test files missing from Owns; no node owns the conftests; no autouse fixture for an empty Codex home; how a fake adapter registers "only in tests" and how the registry resets (TESTING §5.1). (Same as IR-B4, IR-M1.) |
| CD-M6 | 🟡 Major | many | Paths are not exact, and new vs changed is not marked. |
| CD-M7 | 🟡 Major | 315 | `codex_fake.py` has no API and no record schemas; NF omits what N5 and N10 need. (Same as IR-M6.) |
| CD-M8 | 🟡 Major | 128-157 | Archive formats undefined; `rev-<n>` numbering; `thread_id` is not validated before it becomes a file name (TESTING §5.9). (Same as IR-B3, DE-N5.) |
| CD-M9 | 🟡 Major | 91, 94, 312 | A multi-file fingerprint does not fit `update_drifted_files` or `_note_unloadable`. `summary_cache.LOGIC_VERSION` does not exist. (Same as DE-M6, DE-N4.) |
| CD-M10 | 🟡 Major | 171, 314, 319 | No node owns the `SingleFlight` migration; the test reset hooks (`cc_watcher.py:800-814`) use module globals. |
| CD-M11 | 🟡 Major | 180, 314, 348 | Sibling bugs outside N3: `preferences.py:67-78` (fixed `.tmp`, no fsync), `bookmarks.py:137-142` (no retry, no fsync); hand-written patterns in `bulk_fetch.py:879`, `credentials.py:521-537`, `migrate_to_v2.py:307`. (Same as ZO-9.) |
| CD-M12 | 🟡 Major | 318, 357, 370 | N6 dependencies, decoded size, salt, gate. (Same as IR-M5.) |
| CD-M13 | 🟡 Major | 174, 319, 351 | `FetchProgress.type` (`models.py:514-523`) and `FetchToast.tsx:20-21` must change; payloads and toast text unspecified; `UX.md` and `CLAUDE.md` (SSE list, gate lines 567 and 576) not owned; image copy must stay ungated; the watcher's `get_settings()` cache is never cleared. (Same as PT-4, IS-7.) |
| CD-M14 | 🟡 Major | — | **Today the observers create their root folder** (`cc_watcher.py:655-656`, `:750-751`). A generic `watch_roots()` port would create `~/.codex` on a machine without Codex, which breaks the #37 read-only rule. No behavior for a root that appears later. No Codex setting in `backend/config.py`. |
| CD-M15 | 🟡 Major | 257 | **Existing bug:** `export_session` rebuilds `ConversationDetail` field by field (`mcp_server/server.py:739-755`), so a sliced export drops `sandbox_path`, `organization_*`, and `compact_markers` today, and would drop the 4.6 fields. `model_copy(update={"messages": ...})` fixes all of them. |
| CD-M16 | 🟡 Major | 311 | Golden normalization and update procedure. (Same as IR-M7.) |
| CD-M17 | 🟡 Major | 186, 314, 328 | `run_vm_tests.py` is not in the repo. (Same as IR-M8.) |
| CD-n1 | 🟢 Nit | 312 | SPEC.md also omits Cowork in §5 (lines 88-94) and §8 (line 447). |
| CD-n2 | 🟢 Nit | 115 | The source-union copies: `useConversations.ts:115`, `api.ts:140`, `MessageBubble.tsx:22`, `types.ts:364`, `types.ts:376`. |
| CD-n3 | 🟢 Nit | — | Reuse `displayPath(path, home_dir)` (`ListFilterButton.tsx:53`) for `~` display and `CopyValueButton.tsx` for copy buttons. |
| CD-n4 | 🟢 Nit | — | Small open values: the `state_db` timeout, the stem regex, the `atomic_write_bytes` module, "both layers", "newest", the N10 hook, the Codex icon. |
| CD-n5 | 🟢 Nit | §6 | "Combine the worktrees" is unspecified; TESTING.md baseline counts drift after N9. |
| CD-n6 | 🟢 Nit | — | `codex-report` reads `~/.codex` directly, which contradicts D7's wording. |
