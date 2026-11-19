# EGM Stage 7: Critic Review

- **Target:** `PLANS/2026.11.18-codex-source.md`, FeatureRange lines 1-261
- **Reviewers:** egm-design, egm-structural (both with `feature-spec-standard`), egm-style
- **Mode:** consolidated report; one pass, with no edits to the plan

## egm-style

- **Status:** completed. 11 Major, 24 Nit, no Blockers. The reviewer found no marketing language and did not flag the STE style.

| ID | Severity | Lines | Finding | Fix |
|---|---|---|---|---|
| ST-1 | 🟡 Major | 22, 33, 35 | The intro says D6-D10 are derived, but D8 and D10 are marked "User". The change rule thus points at the wrong set. | "D1-D5, D8, D10 come from the user; D6, D7, D9 are derived." State the change rule for both kinds. |
| ST-2 | 🟡 Major | 33 | "Keep the newest copy" conflicts with D1, and "newest" has no key. | "Show the newest copy, by <key>; keep both archive copies." |
| ST-3 | 🟡 Major | 34 | D9 says "a skipped copy loses no data", but §3 rests on one code search and keeps D1 "because Codex can change". | "Loses no data while Codex keeps its rollouts (no pruning code at `f2d3086`)." |
| ST-4 | 🟡 Major | 91, 143 | "Fingerprint" has two meanings. | Rename one (for example `source_stat`). Define each once. |
| ST-5 | 🟡 Major | 132, 138, 147, 221 | "Revision" means both an older physical rollout (another stem) and a `.rev-<n>` diverged copy (same stem). N11 and the N3 test depend on the difference. | Use two terms: "older rollout" and "diverged copy". |
| ST-6 | 🟡 Major | 130, 146, 176 | "Append-only" and "write the new bytes" against "replaces the whole file". A literal step 4 breaks layer 4. | Content only grows; every write is a whole-file atomic replace. |
| ST-7 | 🟡 Major | 92, 121 | The Cowork drift fix is a behavior change inside the "no change in behavior" node. | Name the exception, or move the fix. |
| ST-8 | 🟡 Major | 106-109 | Which source of flags the UI reads is unclear. | State the role of each in one sentence. |
| ST-9 | 🟡 Major | 161-174 | The lock policy leaves out the CLI. "Backstop" is not a listed caller. The result of the 60 s timeout is missing (read literally, it runs without the lock). | Assign the CLI to a policy. Map "backstop" to a caller. State the timeout result. |
| ST-10 | 🟡 Major | 211-215, 244-245 | The two name chains differ, so the "Thus" sentence at 245 does not hold. | Align the chains or state why they differ. |
| ST-11 | 🟡 Major | 83, 92, 169 | "The code map of 2026-11-18" is evidence that a reader cannot find. | Give a path, or cite code locations. |
| ST-n1 | 🟢 Nit | 5, 226 | N labels and "NF" appear before §5 defines them. | Add "Nx labels are the PRs in §5." |
| ST-n2 | 🟢 Nit | 12 | Moving and compressing do not lose content; only file loss justifies the copy. | Reword. |
| ST-n3 | 🟢 Nit | 16, 18, 174, 224 | "Safe", "show correctly", "cheap", "safely" cannot be measured. | Point each to its definition. |
| ST-n4 | 🟢 Nit | 33-34 | The Source column holds rationale. "(As Cowork does)" has no citation. | Move the rationale. Cite `newest_per_uuid`. |
| ST-n5 | 🟢 Nit | 35 | "Testers run the tests" can mean the synthetic tests or the real-data report. | Clarify. |
| ST-n6 | 🟢 Nit | 45 | "One thread can have more than one rollout" is inferred from counts only. | Cite the Codex revert behavior. |
| ST-n7 | 🟢 Nit | 46, 144 | Does "(compressed)" cover every size? Is the 512 MB cap decoded or compressed? | Clarify. |
| ST-n8 | 🟢 Nit | 48-52 | "No pruning" right after "deletes the plain file". | Say that compression replaces a file and keeps its content. |
| ST-n9 | 🟢 Nit | 65 | "About 25 signatures" is imprecise. "7 frontend files" does not match 110-115. | Give exact counts. |
| ST-n10 | 🟢 Nit | 69, 72, 116, 121 | The three current sources are never named once with their ids. | Name each once. |
| ST-n11 | 🟢 Nit | 77, 92 | "Leaf walk" and "cause a drift" are not defined. | Define. |
| ST-n12 | 🟢 Nit | 118-119 | Two bookmark sentences say the same thing. | Merge. |
| ST-n13 | 🟢 Nit | 121 | "Byte-for-byte" for exports probably includes PDF, which embeds a timestamp. | Name the formats. |
| ST-n14 | 🟢 Nit | 145, 147, 150 | "Drop a last line" reads as an edit of the source. `<n>` is not defined. Step 8 belongs first. | "Do not copy a last line…"; "the next free integer"; move step 8. |
| ST-n15 | 🟢 Nit | 173 | "Do not wait" repeats the sentence before it. | Delete. |
| ST-n16 | 🟢 Nit | 176 | Step 5 renames the old copy before it writes the new one, so `<stem>.jsonl` is briefly absent. | Narrow the claim. |
| ST-n17 | 🟢 Nit | 181-186 | "Run the test on Windows too" is not a result. | Move it out of the list. |
| ST-n18 | 🟢 Nit | 203, 206, 208 | Awkward "decode"; "decode" should be "decrypt"; "read a path" should be "open a file at a path that a record names". | Reword. |
| ST-n19 | 🟢 Nit | 209 | "It never looks complete when it is not" repeats the sentence before it. | Delete. |
| ST-n20 | 🟢 Nit | 239, 245, 250, 251 | One concept has four labels ("Session name", "Codex name", `source_name`, "name"). | Pick one UI label and map it to the field. |
| ST-n21 | 🟢 Nit | 240 | `~/.codex` is not a resolved path. | Give an absolute example. |
| ST-n22 | 🟢 Nit | 260 | "The UI API" is unclear. | "The web API (`/api/...`)". |
| ST-n23 | 🟢 Nit | 128 | "Resolved CODEX_HOME, first seen" is unclear. | Name the fields. |
| ST-n24 | 🟢 Nit | 157 | `current_ambiguous` does not cover a missing thread row or a `rollout_path` outside the home. | Say whether those count as ambiguous. |

## egm-design

- **Status:** completed. **Verdict: does not pass** (1 Blocker), 15 Major (the reviewer's summary says 14; its list has M1-M15), 10 Nit. Most code citations check out. Two are wrong: `summary_cache.LOGIC_VERSION` and the missing `DYNAMIC_IMPORT_MODULES`.

| ID | Severity | Lines | Finding | Recommendation |
|---|---|---|---|---|
| DE-B1 | 🔴 Blocker | whole file | No alternatives section. Unargued choices: archive-only reads vs live read with a backup copy (D7); decoded storage vs original bytes; whole-file rewrite vs append; a five-caller lock vs a single writer that others signal; label imported duplicates vs dedupe by `content_sha256`; the adapter protocol shape. (Same as SR-1.) | Add "Alternatives Considered" with a concrete trade-off for each. |
| DE-M1 | 🟡 Major | 8-18, 32 | No Non-Goals, and no "never write to `~/.codex`" invariant. No node owns the #37 AC6 test ("no Codex file changed"). (Same as SR-4.) | Add Non-Goals and "No Explorer process writes, renames, or locks any file under a Codex home". Give the before-and-after hash test of the fake home to N5 or N7. |
| DE-M2 | 🟡 Major | 31, 143, 149, 244 | A metadata-only change (rename, `state_5` title, archived, or current-rollout change) never reaches the archive. A `.jsonl` suffix filter misses `.zst`, `session_index.jsonl`, and the `state_5` WAL. How a copy pass triggers the drift pass is not stated. (Same as PT-1, IC-1.) | Split the pass into a per-rollout step and a per-thread metadata step. Fingerprint `session_index.jsonl` and `state_5` separately. Specify the watch roots and filters. The copy pass calls the drift pass. |
| DE-M3 | 🟡 Major | 130, 144-147, 153, 176 | A whole-file rewrite on each event is O(n) per event and O(n²) over the life of a session, and on Windows each replace races the readers. | Append the delta in place under the lock, and record the size and a tail hash in `.meta.json`. Or justify the replace with a measured worst case. State the debounce. |
| DE-M4 | 🟡 Major | 144 | The 512 MB cap breaks D1 and D2: with D7, a large session is never visible. (Same as PT-19, IC-4.) | Stream with no cap. If a cap stays, copy the raw bytes and show an "incomplete" notice. |
| DE-M5 | 🟡 Major | 33, 136, 246 | Each process resolves `CODEX_HOME` from its own environment, so callers can resolve different homes (issue #36 warns about this). Hashing a path without case folding gives two lock files for one home on macOS and Windows. "Newest" has no key. Search keeps the copy indexed last, while the list shows the newest, which breaks N9 parity. (Overlaps PT-3, IC-6.) | Make the Codex home a `Settings` field. Normalize the path before hashing. Define "newest". Index only the copy that the list shows. Show "also in <home>". |
| DE-M6 | 🟡 Major | 91-92 | If the fingerprint goes into `indexed_files.mtime` with no schema change, an old watcher process and a new `serve` reindex Cowork sessions forever, each against the other (`_drift_first_scan`, `needs_update`). (Related to PT-2.) | Put the fingerprint in a new additive column (the `message_rowids` precedent). Update the check-read-check too. |
| DE-M7 | 🟡 Major | 166, 230 | MCP as a writer breaks SPEC.md §1 and §8. The trigger, the wait, and the staleness are not stated. Claude Code starts one MCP process per client session, so many writers can run. (Same as IC-2, ZO-5, PT-13.) | A background try-lock at startup that never blocks a tool call. State the staleness. Revise SPEC §1 and §8 in N7 or N8. |
| DE-M8 | 🟡 Major | 174 | The result of the 60 s Refresh timeout is not stated, nor that the wait runs off the event loop. A hung holder keeps its lock. (Same as ST-9.) | On timeout, report it, skip the local sync, and continue with the claude.ai fetch. Wait in a worker thread. Test it in N7. |
| DE-M9 | 🟡 Major | 194, 221-223 | The MCP outline cache rebuilds only positions at or after the cached count (SPEC §6.3 case 5), but Codex replaces items in place, so summaries go stale. Codex `message_uuid`s are not defined, and `message_summaries.message_uuid` is a global key: a fork that reuses item ids steals rows. | `message_uuid = uuid5(thread_id, turn_id, item_id)`, with a line-number fallback for legacy. Force a full outline rebuild for Codex on change. Test in N4 and N10. |
| DE-M10 | 🟡 Major | 204 | No rule tells paginated from legacy when `state_5` is missing. A content heuristic misclassifies a paginated session before its first `item_completed`. (Same as CO-7.) | Use a `session_meta` or `turn_context` field if Codex has one. Name the fallback, with a warning. Test both modes with `state_5` missing. |
| DE-M11 | 🟡 Major | 195-199, 209 | Only four item types are mapped. Other kinds (file changes, MCP tool calls, web search) may vanish, which breaks the plan's own rule and #37 AC6. Turn interruption and commentary/final phases are missing from slice 1. (Related to CO-14.) | Map unknown types to a placeholder block and count them in `warnings`. List the item types from `thread_history_projection.rs` at the pinned commit. Add turn interruption to slice 1. |
| DE-M12 | 🟡 Major | 216 | "Use the Git root" assumes that `session_meta` holds a root path. The reviewer believes it holds the commit, branch, and repository URL only. **Not verified.** | Check `protocol.rs` at `f2d3086`. If there is no root, group by `cwd` or by `repository_url`, and say which. |
| DE-M13 | 🟡 Major | 211-215, 244-245 | The two name chains disagree. (Same as ST-10, SR-8, CO-5, PT-22.) | One chain. `source_name` = `session_index` name, then `state_5.name` only. `state_5.title` is a title fallback only. |
| DE-M14 | 🟡 Major | 74-81 | The Codex flag values and the archived mapping are not given. (Same as CO-11, SR-6, PT-8.) | Give the values. Archived = in `archived_sessions/` OR `state_5.archived`, with a rule for a conflict. |
| DE-M15 | 🟡 Major | 230-232 | `DYNAMIC_IMPORT_MODULES` is not updated, so a lazy module is missing from the bundle, and the canary cannot see it. The stripped `pyproject.toml` must keep the `python_version` marker. (Same as PT-7, ZO-5.) | Eager imports from the registry, or add each module. Smoke-test the bundled tree against the fake home. |
| DE-N1 | 🟢 Nit | 34, 52 | D9 contradicts §3. While the gate is active, new sessions are invisible (D7). | One stance; say so. |
| DE-N2 | 🟢 Nit | 92, 121 | N1's Cowork fix is a behavior change. (Same as ST-7.) | Name the exception, with its own test. |
| DE-N3 | 🟢 Nit | 69 | Most signatures use `Literal["all", …]`, not `SourceId`. | Define `SourceFilter` next to `SourceId`. |
| DE-N4 | 🟢 Nit | 94 | `summary_cache.LOGIC_VERSION` does not exist; the constant is in `backend/cc_jsonl_io.py`. The cache has one global key, so a per-adapter bump would clear every source. | Fix the citation. Key per source, or a combined hash. |
| DE-N5 | 🟢 Nit | 133, 138, 149 | `thread_id` goes into a path with no validation. | Validate it like `<stem>`. |
| DE-N6 | 🟢 Nit | 172 | The `migrate_to_v2` pattern writes holder metadata through a second handle, which Windows rejects while the lock is held. | Write through the lock's own handle (or drop it, PT-16). |
| DE-N7 | 🟢 Nit | 246, 257 | `source_home` can leak the user name in exports and MCP output. (Same as SR-5.) | Show it with `~`. |
| DE-N8 | 🟢 Nit | 107-108 | Two places hold the flags. (Same as IC-3, ZO-1, PT-10.) | State the purpose of the generated file. |
| DE-N9 | 🟢 Nit | many | "Archive" means both the Explorer copy and Codex's archived state. | Use distinct terms ("Explorer copy", "Codex-archived"). |
| DE-N10 | 🟢 Nit | 136 | A `data_dir` that syncs between machines gives two machines the same `home_key`, and the lock does not work across machines. | Add the host name to `home_key`, or say the case is not supported. |

## egm-structural

- **Status:** completed. **Verdict: FAIL** (1 Blocker), 9 Major, 11 Nit. Headings, code blocks, and the table are well formed.

| ID | Severity | Lines | Finding | Fix |
|---|---|---|---|---|
| SR-1 | 🔴 Blocker | whole range | No "Alternatives Considered" section, which the feature-spec standard requires. The main choices name no rejected option: archive copy vs live read (D1/D7), adapter first vs Codex-only (D3), portalocker vs other coordination, the thread file vs the rollout as the conversation file, newest-wins vs namespaced ids (D8), decoded storage vs archive size. | Add the section with each rejected option and its reason. |
| SR-2 | 🟡 Major | 22-35 | "User" decisions have no link. D8 overrides a #37 requirement on that basis alone. | Link the "Update 2026-11-18" section of #37. |
| SR-3 | 🟡 Major | 22, 33, 35 | The intro and the Source column disagree about D8 and D10. (Same as ST-1.) | Correct one of them. |
| SR-4 | 🟡 Major | 8-18, 32 | No Non-Goals section. The #37 non-goals (no Codex runtime or daemon, no cloud fetch, no write-back, no capture, no cloud-only conversations, no `history.jsonl`) and the read-only rules (never launch or resume Codex, never migrate its database, never rewrite rollouts) are missing. D5 reverses the #37 non-goal "no always-on watcher", and the plan does not say so. The goals do not map to #37 acceptance criteria 1-6. | Add Non-Goals and the read-only rules. Map the goals to the criteria. |
| SR-5 | 🟡 Major | 1-261 vs 353 | Risks sit outside the feature range, and risk text is scattered. **`source_home` sends an absolute path (it usually holds the user name) through MCP output, which an MCP client can pass to a model.** N6 forbids paths, but 4.6 adds one to MCP. | Move risks into the range. Decide how `source_home` appears in MCP (for example `~`-relative). |
| SR-6 | 🟡 Major | 69 | The Codex `SourceId` value and the node that adds it are not defined. | Define `"CODEX"` (or another id) and its node. |
| SR-7 | 🟡 Major | 63, 123, 234 | The node tags in headings are incomplete: 4.2 also needs N5 and N11, 4.6 needs N9, 4.1 needs N0. | Complete the tags. |
| SR-8 | 🟡 Major | 149, 211-215, 239-245 | The two name chains disagree, and the snapshot at 149 records only the `session_index` name. (Same as ST-10, CO-5.) | Use one chain. |
| SR-9 | 🟡 Major | 161-174 | The caller list and the lock rules do not match one to one. (Same as ST-9, CO-1.) | Match them. |
| SR-10 | 🟡 Major | 83, 92, 169 | The "code map" is not linked. (Same as ST-11.) | Link or cite code. |
| SR-n1 | 🟢 Nit | 5 on | Node ids are used before §5 defines them. | Add a pointer. |
| SR-n2 | 🟢 Nit | many | Mixed cross-reference styles ("§3" vs "4.6"). | Use one style. |
| SR-n3 | 🟢 Nit | many | Mixed code-reference forms; `migrate_to_v2.py` has no directory. | Repo-relative paths and function names. |
| SR-n4 | 🟢 Nit | 39, 48 | Upstream references have no pinned links. | Link `openai/codex` at `f2d3086`. |
| SR-n5 | 🟢 Nit | 33-34 | Rationale in the Source column. | Add a Rationale column. |
| SR-n6 | 🟢 Nit | 143-150 | Step 8 belongs first; `<n>` is undefined. (Same as ST-n14.) | Reorder; define. |
| SR-n7 | 🟢 Nit | 33, 239-257 | UI labels drift. "`None` when Codex has no name" should say "no name and no title". | Fix the strings once. |
| SR-n8 | 🟢 Nit | 248-257 | `export_session` gets new header fields, but the Web UI subsection does not mention the same change to the UI export and copy. | Add it. |
| SR-n9 | 🟢 Nit | 107-108 | Which flag source the frontend reads. (Same as ST-8.) | State it. |
| SR-n10 | 🟢 Nit | 115, 174 | Only one SSE event is defined in the range. The inline source-union copies have no locations. | Define or locate. |
| SR-n11 | 🟢 Nit | 132, 181-186 | A misaligned layout comment. "Run on Windows" is not a result. | Fix. |
