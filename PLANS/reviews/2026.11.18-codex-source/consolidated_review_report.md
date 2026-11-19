# Codex Source Plan: Consolidated Review (EGM + Ponytail)

- **Target:** `PLANS/2026.11.18-codex-source.md` (378 lines), not edited during the review
- **Date:** 2026-11-18
- **Reviews run:**
  - EGM 0.1.1, "consolidated report" preset, stages 5-9, 10 Goldfish reviewers, all Claude (Opus 5.5).
  - Ponytail 5.3.3 `ponytail-review` at level `ultra`.
- **LLM Council: not run.** The Gemini seat has no quota (429 on `gemini-3.8-flash`; `gemini-3.7-flash` routes to the same quota). The user is choosing between a paid key (estimated under $1), a wait of about 12 hours, or a run with two models. All findings below therefore come from **one model family**. Agreement between reviewers is not cross-provider agreement.
- **Stage reports:**
  - `zoom_out_report.md` (stage 5)
  - `comprehension_report.md` (stage 6)
  - `critic_report.md` (stage 7)
  - `intermediate_zoom_out_report.md` (stage 8)
  - `implementation_readiness_report.md` (stage 9)
  - `ponytail_report.md`

## Verdict

**The plan is not ready to implement.** Its design shape holds, and every reviewer accepts these parts:

- the adapter comes first
- the archive is append-only
- one writer at a time for each Codex home
- goldens guard the refactor
- a generic copier

But stage 9 found that an orchestrator would have to make design decisions that the plan does not make, and several nodes cannot finish inside their own file lists. Stage 8 shows that about half of the fixes that the reviewers propose would over-build. **Apply the deletions first**, then the missing definitions.

## Facts that I checked myself

| Claim | Result | Source |
|---|---|---|
| `session_meta` Git metadata has a root path (plan line 216) | **False.** `GitInfo` has only `commit_hash`, `branch`, `repository_url`. | `openai/codex` `protocol.rs` at `f2d3086` |
| The plan maps all completed-item types | **False.** `ThreadItemPayload` has 19 types; the plan maps 4. Compaction arrives as `ContextCompaction`. | `app-server-protocol/src/protocol/v2/item.rs` at `f2d3086` |
| §3 corpus counts | **Out of date.** Now `sessions/` 2 plain + 35 `.zst`, `archived_sessions/` 33 plain, `state_5` 66 threads. About 30 threads were archived since 2026-11-17. | Local `~/.codex`, metadata only |
| `has_user_event` | **0 for all 66 threads.** A test on it would hide every session (strong support for D2). | `state_5.sqlite` |
| MCP sliced export keeps all fields | **False (existing bug).** `export_session` rebuilds `ConversationDetail` field by field and drops `compact_markers`, `sandbox_path`, `organization_*`, `git_branch`, and others. | `mcp_server/server.py` `export_session` |
| A generic watch-root port is harmless | **False.** Both watchers call `root.mkdir(parents=True)`; a copy would create `~/.codex`. | `backend/cc_watcher.py` |
| `compression` is a stdlib name on the dev Python | **False on 3.13** (true on 3.14), so the MCPB closure analyzer would treat it as an unknown package. | `sys.stdlib_module_names` |
| `state_5.sqlite` always opens read-only | **A short failure was seen once** ("unable to open database file") while Codex closed the database; a retry worked. | Local test |

## 1. Delete first (stage 8 and Ponytail agree; these make about 25 findings moot)

| # | Delete | Why | IDs |
|---|---|---|---|
| D-1 | `GET /api/sources` and `routers/sources.py` | The frontend and backend ship in one wheel. A run-time fetch adds a loading state, a `main.py` change, and unmocked calls in 77 Playwright specs. Keep only `sources.generated.json`. | ZO-1, IC-3, PT-10, CD-B2, IR-M2, Z8-5 |
| D-2 | The bookmark source change | Nothing reads the field. It risks user data on downgrade. | ZO-3, PT-12, IR-M10, CO-10, Z8-5 |
| D-3 | `fingerprint()` and the Cowork sidecar fix in N1 | The thread file is the one change signal, so every stat-keyed cache works unchanged. A new meaning for `indexed_files.mtime` makes old and new processes reindex each other forever. Fix the Cowork gap in its own leaf PR. | Z8-6, DE-M6, PT-2, CD-M9, ST-7 |
| D-4 | `logic_version()` and the empty N10 hook | No reader; code for later. | ZO-10, ZO-11, PT-15, PT-17, DE-N4 |
| D-5 | Four new SSE event types | Use `progress` frames with a Codex phase, as remote Cowork does. N7 then needs no frontend files. | ZO-4, PT-11, Z8-10 |
| D-6 | The `SingleFlight` class | Call the copy from the existing drift single-flight. The portalocker lock also blocks a second thread. | PT-9 (Z8-1 and IR-M3: the class cannot live in `cc_watcher`, which the MCP closure forbids) |
| D-7 | Lock-holder metadata | No reader; a second-handle write fails on Windows. | ZO-8, PT-16, DE-N6, Z8-10 |
| D-8 | The 512 MB cap | Breaks D1 and D2; the decode streams. | DE-M4, PT-19, IC-4, Z8-4 |
| D-9 | A second presentation map in `lib/sources.ts` | Extend `SOURCE_PRESETS` in `SourceBadge.tsx`. | ZO-2, Z8-5 |
| D-10 | "Use the Git root" | The field does not exist (checked). Group by `cwd`. | DE-M12, Z8-9 |

## 2. 🔴 Blockers: definitions the plan must add

| # | Add | IDs |
|---|---|---|
| B-1 | **Alternatives Considered** (a table of about 6 rows for the derived and design choices only: archive-only reads, decoded bytes, append vs replace, lock vs single writer, adapter shape). **Non-Goals** (about 8 bullets: the #37 non-goals; "No Explorer process writes, renames, locks, or opens for writing any file under a Codex home"; the local CC and Cowork archive gap is out of scope and is N3's next user; D5 replaces "no always-on watcher"). A map from the #37 acceptance criteria to nodes. | SR-1, DE-B1, SR-4, DE-M1, ZO-6, Z8-11 |
| B-2 | **The Codex descriptor:** the id string, label, all flag values, and `is_archived` = in `archived_sessions/` OR `state_5.archived` (hidden by default, as Cowork). N8 owns `backend/sources/ids.py`. | IR-B1, CD-B1, SR-6, DE-M14, CO-11, PT-8 |
| B-3 | **The projection output schema:** the keys, the block shapes, the warnings field, and a thread-namespaced message uuid rule for each history mode. A descriptor flag ("messages can change in place") forces a full MCP outline rebuild. | IR-B2, DE-M9, Z8-9 |
| B-4 | **The archive file formats** (`threads/<id>.json`, `<stem>.meta.json`, `home.json`, each with `version`), the `SyncContext`/`SyncReport`/`WatchRoot` fields, and the split of copy steps 1-8 between N3 (generic) and N8 (Codex). Validate `thread_id` before it becomes a file name. | IR-B3, CD-M8, DE-N5 |
| B-5 | **Metadata changes reach the archive:** step 1's early stop skips only the data copy; step 7 runs on every pass and writes the thread file only when it differs. Watch `.jsonl.zst`, `session_index.jsonl`, and `state_5.sqlite*`. A pass that wrote anything schedules the drift pass. | PT-1, DE-M2, IC-1, Z8-7 |
| B-6 | **Test isolation and the Codex home setting:** a `codex_home` field in `Settings` (env var, then `config.json`, then `~/.codex`), read the same way by every process. An autouse fixture in both conftests points it at an empty temp folder. **Without this, existing tests copy the developer's real transcripts.** | IR-B4, CD-M5, CD-M14, PT-3, DE-M5 |
| B-7 | **E2E fixtures:** a committed pre-synced archive with a fixed `home_key` and a placeholder path, or a `CLAUDE_EXPLORER_DISABLE_ARCHIVE_SYNC` flag. Fix N9's Owns to match. | IR-B5, CD-B5 |
| B-8 | **File ownership that lets each node finish:** full repo-relative paths, test files included; N8 owns the frontend files it needs, or N2 makes the filter dropdown and all 11 files read the registry; status fields go to N8 as one `notices: list[str]` field, so N11 owns no model file; NF lists every record kind, or each consumer owns `codex_fake.py`. | CD-B3, CD-B4, IR-B6, IR-M1, IR-M6, CO-20, IS-1, IS-2, Z8-8, Z8-15 |

## 3. 🟡 Major: rules to state

| # | Rule | IDs |
|---|---|---|
| M-1 | **Writes:** append new bytes to `rollouts/<stem>.jsonl` under the lock; replace the small JSON files whole; readers ignore a last line with no newline. If the source is a prefix of the archive (a short read), do nothing. If both `.jsonl` and `.jsonl.zst` exist, read the plain file. | Z8-1, DE-M3, PT-6, PT-20, ST-6 |
| M-2 | **A lock policy for each of the five callers:** watcher and backstop (`scan_once`) try without a wait and reschedule; MCP runs one inline try-lock pass per call when its last pass is older than about the debounce, with no thread or timer; Refresh, `/fetch/start`, and the CLI wait up to 60 s off the event loop, and on timeout report it, skip the local sync, and continue with claude.ai. | CO-1, CO-2, ST-9, DE-M7, DE-M8, IC-2, ZO-5, PT-13, Z8-2, Z8-10 |
| M-3 | **The CLI hook goes in `cli/main.py`, before credentials load,** so a Codex-only user can sync. Not in `fetcher/bulk_fetch.py` (import cycle). | PT-14, CD-M3 |
| M-4 | **MCP as a writer:** state why (an MCPB-only user has no `serve` and no watcher); rewrite SPEC §1 and §8 in N7; fix the import form for `compression.zstd`; add the lazy modules to `DYNAMIC_IMPORT_MODULES`; update `test_build_mcpb.py:138`, `CLAUDE.md`, and `assets/mcpb-README.md`; keep the gate code out of `backend.deps` and `backend.cc_watcher` (both forbidden in the closure). Smoke-test the built bundle. | ZO-5, DE-M15, PT-7, IR-M3, CD-M1, CD-M2 |
| M-5 | **One name chain:** `source_name` = `session_index` name, else `state_5.name`. Title = `source_name`, else `state_5.title`, else the first human message, else "Codex session <date>". | CO-5, ST-10, SR-8, DE-M13, PT-22 |
| M-6 | **Projection rules:** paginated if `state_5.history_mode` says so, else if any `item_completed` exists; any of the 15 unmapped item types becomes a labeled fallback block and counts as a warning; N6's counts decide later mappings. | DE-M10, DE-M11, CO-7, CO-14, Z8-9 |
| M-7 | **Duplicate thread ids:** the copier syncs one configured home (normalize the path before the hash); readers read every archived home tree; `discover()` dedupes once by a defined "newest" key. A shared `data_dir` across machines is not supported. | Z8-3, DE-M5, CO-8, ST-2 |
| M-8 | **Corrupt-config gate:** call `get_settings.cache_clear()` once per pass; the gate skips `archive_sync` only, so image capture and the drift pass still run; one WARNING line per skipped pass. | PT-4, IS-7, CD-M13, IC-5, Z8-12 |
| M-9 | **A summary cache for Codex list rows,** keyed by the thread file's `(mtime, size)` (reuse the `_cowork_summaries` pattern). | PT-5 |
| M-10 | **`source_home` shows with `~`** in the UI, the export, and MCP (reuse `displayPath`). `home.json` keeps the absolute path. | SR-5, DE-N7, Z8-13, CD-n3 |
| M-11 | **N6:** depend on N7 (and N8 for Codex results); print counts by kind with no hash; add the decoded size and its baseline; say "no title" in its test; read-only (no sync). | CO-19, IR-M5, CD-M12, PT-18, IS-10, Z8-15 |
| M-12 | **N0 goldens:** normalize temp paths and times; name the format and the update command; pin the search path; add a remote Cowork fixture. | IR-M7, CD-M16 |
| M-13 | **Orchestrator rules:** "a test that fails on `main` before the node's change is P0"; an out-of-scope sibling bug goes to the orchestrator; name the git operation that combines the tester and coder worktrees; `git add <path>` for new files. | IS-3, IS-4, IS-5, IS-19, CO-21 |
| M-14 | **Windows runner:** `run_vm_tests.py` is not in the repo. Name the real command and the test ids. | IR-M8, CD-M17 |
| M-15 | **Ticket tests that have no owner:** the Codex home is byte-unchanged after a sync (#37 AC6); N5 shows that a `state_db` open creates no `-wal` or `-shm`; "newest" across homes (AC5); a transient `state_5` open failure. | DE-M1, IR-M9, Z8-11 |
| M-16 | **Fix the existing `export_session` slicing bug** with `model_copy(update={"messages": ...})` (in N1 or its own PR). | CD-M15 |
| M-17 | **Sibling writers:** keep N3 to `atomic_write_bytes` (in `fetcher/atomic.py`) plus the two named writers; move the bookmarks and preferences writers to a separate leaf PR. | ZO-9, CD-M11, Z8-14 |
| M-18 | **Docs ownership:** `UX.md` and `CLAUDE.md` to N7; `TESTING.md` counts after N9. | IR-M4, CD-M13, CD-n5 |
| M-19 | **Correct the facts:** §3 counts; 11 frontend files, not 7; 3 inline source-union copies plus the `types.ts` filter lists; `LOGIC_VERSION` is in `backend/cc_jsonl_io.py`; the DAG has 13 nodes, not 12 (fix issue #37 too). | PT-21, CD-B3, CD-n2, DE-N4, Z8-15 |

## 4. 🟢 Nits

About 60 Nits, in the stage reports. Most are terms to define or link (the "code map", drift pass, MCPB, leaf walk), one style for cross-references and code references (symbols, not line numbers), and labels to make the same everywhere. Apply them in the same edit pass, after sections 1-3.

## 5. Decisions for the user

1. **The council:** paid Gemini key (estimated under $1), wait about 12 hours, or two models now.
2. **A slice 1 release gate (Z8-15):** release Codex visibility after N9 and N6, before slice 2. N4 then marks slice 2 records "incomplete", so slice 1 never shows a falsely complete transcript. #37 stays open until slice 2 ships.
3. **Apply the fixes:** edit the plan now, with sections 1-3 in that order.

## Traceability

Each row above lists its source finding IDs. Prefixes:

| Prefix | Source |
|---|---|
| IC | egm-inventive-catalyst |
| ZO | egm-zoom-out (stage 5) |
| CO | egm-comprehension |
| ST | egm-style, stage 7 |
| SR | egm-structural |
| DE | egm-design |
| Z8 | egm-zoom-out (stage 8) |
| IS | egm-style, stage 9 |
| IR | egm-implementation-readiness |
| CD | egm-codebase-details |
| PT | Ponytail |
