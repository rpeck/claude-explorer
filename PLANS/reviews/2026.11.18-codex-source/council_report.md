# Codex Source Plan: LLM Council Architecture Review

- **Target:** `PLANS/2026.11.18-codex-source.md` (378 lines). Plan review only. The council did not review code, and it did not edit the plan.
- **Date:** 2026-11-18
- **Council members that answered:**
  - **Systems Architect (lead):** Claude Opus 5.5, direct.
  - **Platform & Reliability Architect:** `google/gemini-3.8-flash`, through PAL on the OpenRouter route (provider `openrouter`). The direct Google quota returned 429. The orchestrator relayed the user's approval of the OpenRouter route for this run.
  - **Architecture Critic:** `chatgpt/gpt-5.6-terra`, through PAL on the ChatGPT-plan route (provider `chatgpt`). No escalation: the Critic gave HIGH confidence on every HIGH-severity item.
- **PAL ping:** both seats answered "PAL OK" before the analysis.
- **Calls:** each external seat had one ping, one blind round, and one cross-check round. The Gemini seat used 3 OpenRouter calls. No PAL call followed the user's answers.
- **Inputs:** the plan, `context/issue-37.md`, the project `CLAUDE.md` (a symlink to `AGENTS.md`), and `consolidated_review_report.md` (round 2 only).
- **Method:**
  - **Round 1 (blind):** each seat reviewed the plan and the ticket without the earlier report.
  - **Round 2:** each seat cross-checked the earlier report and attacked the lead's extra findings (L1 to L7).
  - **User decisions:** the user answered the six questions of the review (2026-11-18).
- **Scope note:** the caller scoped this run as a plan review. The 7-phase design process of the council agent did not run.

## Table of Contents

- [[#Verdict]]
- [[#User Decisions (2026-11-18)]]
- [[#Facts the Lead Checked in the Repo]]
- [[#1. Independent Review]]
- [[#2. Cross-Provider Check of the Earlier Report]]
- [[#3. Open Question Z8-15: a Slice 1 Release Gate]]
- [[#4. Ranked Changes Before Implementation]]
- [[#Disagreement Records]]
- [[#Unverified Claims]]
- [[#Questions for the User (Answered)]]

## Verdict

**The architecture is sound for one user on one machine.** All three seats accept these parts:

- the archive copy as the only input of the readers
- append-only rollout copies keyed by the file stem
- one lock for each Codex home
- goldens that guard the adapter refactor

**The plan is not ready to implement.** The two non-Claude seats confirm most of the earlier report's deletions and all 8 of its Blockers. They also found new defects:

- The DAG has ownership overlaps and hidden dependencies. A coder session would stop on them.
- The earlier report's deletion of `logic_version()` (D-4) leaves stale Codex search rows after slice 2 ships. All 3 seats agree.
- Four rules for the copier are missing:
  - what a decode error does
  - when a `.rev-` file is allowed
  - which lock guards threads in one process
  - which home wins when one thread id occurs in two homes

## User Decisions (2026-11-18)

The user answered the six questions of this review on 2026-11-18. The coordinator relayed the answers. The quotes below are the user's words.

| # | Question | Decision |
|---|---|---|
| U1 | A slice 1 release gate (Z8-15) | "Yes, with all conditions." Release slice 1 as a preview after N9 and N6. Before that release, the N6 shape report measures the share of "incomplete" sessions on a work corpus. |
| U2 | A `data_dir` in a cloud-synced folder | "I cannot have my sessions in a cloud-sync'd folder, but this might not be true of other users. Warn the user and make triple sure if they want to allow it." The rule is in [[#U2 Rule: Cloud-Sync Gate for the Codex Copy]]. |
| U3 | How the copier writes rollout bytes (M-1) | "Append under the lock." Replace only the small JSON files whole. |
| U4 | `source_home` in MCP output | "Show as ~/.codex." The UI also shows the `~` form. The UI copy button and `home.json` keep the absolute path. |
| U5 | The release gate | "Every issue closed." Every `codex-verification` issue is closed as fixed, and at least one passing report from a work machine exists. This holds for the slice 1 preview and for the final release. |
| U6 | MCP while the lock is busy | "Add a sync_pending flag." |

**No PAL call after the answers.** Each answer picks between options that the seats already judged in rounds 1 and 2, so no seat had to judge an item again.

### U2 Rule: Cloud-Sync Gate for the Codex Copy

**Why:** decoded Codex work transcripts must not reach a cloud service without the user's clear and repeated consent. The user cannot allow it. Another user can, but only on purpose.

**Detection** (in each process, at the start of each copy pass):

1. Resolve `data_dir` to a real path (follow symlinks). Normalize the case on macOS and Windows.
2. The folder is "cloud-synced" when it is inside one of these roots:
   - **iCloud Drive:** `~/Library/Mobile Documents/` (macOS), `%USERPROFILE%\iCloudDrive` (Windows).
   - **Dropbox:** each path in Dropbox's `info.json` (`~/.dropbox/info.json`; on Windows also `%APPDATA%\Dropbox\info.json` and `%LOCALAPPDATA%\Dropbox\info.json`), plus `~/Dropbox`.
   - **OneDrive:** the folders in the `OneDrive`, `OneDriveCommercial`, and `OneDriveConsumer` environment variables (Windows), and `~/OneDrive*`.
   - **Google Drive:** `~/Google Drive`, `~/My Drive`, and a mounted `My Drive` folder (Windows).
   - **macOS File Provider:** any folder under `~/Library/CloudStorage/`. Newer Dropbox, OneDrive, and Google Drive clients put their folders there.
3. Detection is best effort. It does not detect a sync client that is not on this list. UX.md and the README must say so.
4. If detection cannot decide (for example, `data_dir` cannot be resolved), treat the folder as cloud-synced.
5. The exact install paths of each client are UNVERIFIED. N3's tests pin the list.

**Gate:** no process copies anything into a cloud-synced `data_dir` until the user opts in. The copy needs **both** opt-ins:

1. **A config flag:** `allow_codex_archive_in_cloud_sync` in `config.json`. Its value is the resolved `data_dir` path, not `true`. If `data_dir` moves, the flag no longer matches, and the gate closes again.
2. **A confirmation in the UI, in three steps:**
   - a warning that names the detected provider and the folder, and says that copies of work transcripts will leave the machine
   - a check box: "I understand that Codex transcripts will be uploaded to <provider>"
   - a typed confirmation of the provider name (for example, the user types `Dropbox`)

   The UI writes the config flag only after the third step. The route follows the rules for a state-changing route (CLAUDE.md, "Host-header guard"): POST only, a custom header, and an Origin check. It obeys the corrupt-config gate.

**Every other caller only reports the gate.** The watcher, the backstop scan, MCP, and the CLI never ask for consent. They never copy while the gate is closed.

- **Watcher and backstop:** write one WARNING log line for each skipped pass.
- **CLI (`claude-explorer fetch`):** print a plain-English warning. It names the folder and says how to allow the copy (the UI dialog, or the config flag with the exact path).
- **MCP:** `list_sessions` returns a notice that the cloud-sync gate blocks the Codex copy. This notice is not `sync_pending`, because no copy is waiting.

**Other rules:**

- **Revoke:** delete the flag. The next pass copies nothing new. Copies that already exist stay (D1: never delete).
- **The same rule in every process:** each pass reads the flag through `Settings`, with `get_settings.cache_clear()` (as M-8 says).
- **Scope:** the gate covers only the Codex copy. The user did not decide whether the same gate also covers the other sources, which already keep data in `data_dir`. That point stays open for the plan author.
- **Tests** (synthetic folders only):
  - Each provider pattern is detected.
  - A symlink from `data_dir` into a cloud root is detected.
  - No file appears under `<data_dir>/codex/` before both opt-ins.
  - A flag for another path does not open the gate.
  - The watcher, MCP, and the CLI never copy and never prompt while the gate is closed.
  - The UI copies nothing until the typed confirmation matches.
- **Owners:**
  - **N3:** the detection function.
  - **N7:** the gate check in every caller, next to the corrupt-config check (D9).
  - **N8:** the UI dialog, the POST route, the MCP notice, and the UX.md text.

## Facts the Lead Checked in the Repo

| Claim | Result | Source |
|---|---|---|
| portalocker blocks a second thread in the same process | **True, with separate handles.** portalocker 3.2.0 uses `fcntl.flock` on POSIX (per open file description) and `msvcrt.locking` on Windows (per handle). A shared handle does not block. The Gemini seat's round-1 claim ("threads never block") was wrong. | `.venv/.../portalocker/portalocker.py` (default `LOCKER = fcntl.flock`, Windows `MsvcrtLocker`) |
| The MCPB bundle has portalocker | **False.** `EXPECTED_EXTERNAL_DEPS = {fastmcp, pydantic, orjson, platformdirs}`. N7 adds `archive_sync` to `mcp_server/server.py`, but N8 owns `scripts/build-mcpb.py`. | `scripts/build-mcpb.py:69-71` |
| `LOGIC_VERSION` refreshes search rows | **False.** A mismatch wipes only the `conversation_summaries` cache. FTS message rows change on mtime drift or on a `SCHEMA_VERSION` bump (a full rebuild; today 14). | `backend/search_index.py:281`, comments at `:296-306` |
| The Desktop reader could read `codex/**/threads/*.json` as Desktop data | **False.** It globs only `data_dir/*.json` (UUID names) and `data_dir/by-org/*/*.json`. | `backend/store.py:434-447` |
| A presentation map for each source exists in the frontend | **True.** `SOURCE_PRESETS: Record<ConversationSource, SourcePreset>`. | `frontend/src/components/conversation/SourceBadge.tsx:51` |
| portalocker is already a project dependency | **True** (`portalocker>=2.8`). | `pyproject.toml:59` |

## 1. Independent Review

### 1.1 Is the architecture sound at this scale?

**Yes, for all three seats.** The process count is higher than the plan says, but the design still holds:

- Each MCP client session can start its own MCP server process, so "5 callers" means 5 kinds of caller. The count of processes has no fixed limit.
- The per-home lock handles any count of processes.
- Each MCP process pays the try-lock cost on its first call.

### 1.2 Weakest points (merged, ranked)

1. **The DAG cannot run as written** (all 3 seats).
   - N1 and N2 both own `frontend/src/lib/sources.generated.json` in wave 2.
   - N4 must test the title chain of 4.4, which needs `session_index` and `state_5`. N5 owns both, and N4 does not depend on N5.
   - N7 puts `archive_sync` (and portalocker) on the MCP path, but N8 owns the MCPB dependency list. The fact is verified.
2. **No rule for the copier's error paths** (Gemini in round 1; accepted by GPT and the lead).
   - Codex can write a `.zst` file while the copier reads it, and the decode can then fail. The plan does not say what happens.
   - The "not a prefix" path (step 5) has no guard, so a repeated divergence makes one more `.rev-` file on every pass.
3. **Rollout writes replace the whole file** (Gemini; M-1 in the earlier report).
   - On Windows, `os.replace` fails while a reader has the file open.
   - Each pass rewrites the whole decoded rollout.
4. **Stale derived data after a projection change** (Gemini in round 1; L1).
   - Nothing refreshes the Codex search rows when the projection logic changes in a later release.
5. **Undefined identity rules** (GPT in round 1).
   - The plan does not define "newest" for one thread id in two homes.
   - `thread_id` becomes a file name without validation.

### 1.3 Missing failure modes (merged)

| Failure mode | Raised by | Rule to add |
|---|---|---|
| A `.zst` decode error while Codex writes the file | Gemini R1 | Skip the stem, keep the archive as it is, and retry on the next pass. A decode error never makes a `.rev-` file. |
| The `.rev-` files grow on every pass | Gemini R1 | Make a revision only for a confirmed, complete decode whose bytes differ from the newest revision. |
| The source is shorter than the archive | Earlier report M-1 | Do nothing. |
| Metadata changes with no rollout append (`session_index.jsonl`, `state_5.sqlite*`) | GPT R1 (independent of B-5) | Step 7 runs on every pass. Watch those files. A pass that wrote anything starts the drift pass. |
| `state_5` open fails (WAL, a missing `-shm`, a lock) | Gemini R1, earlier report fact | Discovery never waits on `state_5`. Fall back to `session_meta` for the cwd and the model, and to the title chain. |
| A shared lock handle across threads | Gemini R1, lead L6 | Put a `threading.Lock` in `ArchiveLock`, and open a fresh handle for each acquisition. Add a test with 2 threads. |
| A crash between the data write and the metadata write | GPT R1 | No new marker. B-5 (step 7 on every pass) repairs it on the next pass. |
| `data_dir` in a cloud-synced folder | Gemini R1, lead L7 | Decoded work transcripts would leave the machine (§8). The user decided: a gate with repeated opt-in (U2). |
| Short-lived MCP processes and lock contention | GPT R1 | Run one inline try-lock pass. If the lock is busy, read the archive as it is and return `sync_pending` (U6). |
| A projection change in a later release | Gemini R1, lead L1 | Add a Codex projection version (change 6). |

### 1.4 Over-building

The seats agree with most of the earlier report's deletions (section 2). Other proposals:

- **GPT:** move the two sibling-writer fixes (`remote_sessions`, `cc_image_cache`) out of N3.
  - **Lead: keep them in N3.** The user's rule is to fix sibling bugs in the same PR. N3 also has slack in the schedule: it is in wave 1, and its first dependent, N7, is in wave 3.
- **Gemini:** cut the Refresh lock wait from 60 s to 3-5 s.
  - **Lead: reject.** A first sync of a large home can take longer than 5 s, and the wait runs off the event loop.
- **GPT:** do not make "every verification issue closed" a release condition.
  - **Lead: this conflicts with the user's rule** "no release with a known bug". The user kept the rule (U5).
- **No seat proposed cutting a node.** The 13-node shape holds.

### 1.5 Are the DAG and its waves safe?

**No, not as written.** The fixes follow. None adds a node, and none lengthens the critical path (N0, N1, N7, N8).

| Hazard | Raised by | Fix |
|---|---|---|
| N1 and N2 both own `sources.generated.json` in wave 2 | Gemini, GPT, lead | Move N2 to wave 3, after N1 merges. After the D-5 deletion, N7 owns no frontend file, so N2 runs next to N7 and N8 does not wait. |
| N4 needs the N5 metadata for the title chain | Lead L2, both seats accept | N4 projects only the fields that the rollout gives. N5 owns the `source_name` and title chain, and N8 combines them. N4 and N5 stay parallel. |
| N7 adds portalocker to the MCP path, but N8 owns `build-mcpb.py` | GPT (verified) | N7 owns the portalocker entry in `build-mcpb.py`, plus a smoke test of the built bundle. N8 adds `zstandard` and the `compression.zstd` closure fix. |
| N6 and N7 both change `cli/main.py` in wave 3 (once M-3 applies) | Lead | N6 depends on N7 and moves to wave 4 (as Z8-15 says). |
| With a slice 1 gate, N10 can merge to `main` before the slice 1 release | Gemini | Tag the slice 1 preview after N9 and N6 merge, before N10 merges. N10 can still be built in parallel. |
| `models.py` and `mcp_server/server.py` have several owners (N1, N7, N8, N11) | Lead | Safe: the dependency edges order those nodes. |

## 2. Cross-Provider Check of the Earlier Report

**Key:** A = AGREE, M = MODIFY, D = DISAGREE. "Converged" means that both non-Claude seats reached the same result as the earlier report, or the same change to it.

### 2.1 Deletions (section 1)

| # | Gemini | GPT | Lead | Council result |
|---|---|---|---|---|
| D-1 `/api/sources` | M: keep the endpoint for operations; the frontend must not fetch it at boot | M, then conceded: drop the endpoint and keep the generated file | A: no consumer (MCP and the CLI read the registry in-process) | **AGREE.** Drop the endpoint. The frontend reads `sources.generated.json`. |
| D-2 bookmark source change | A | A | A | **AGREE** (converged). |
| D-3 `fingerprint()` and the Cowork fix in N1 | A | M: keep the Cowork fix as its own PR | A | **AGREE** (converged). The earlier report already moves the Cowork fix to a leaf PR. |
| D-4 `logic_version()` and the N10 hook | M | M | M | **MODIFY** (all 3). Delete the empty hook and the adapter method. Add a Codex projection version that refreshes the Codex FTS rows and summary rows (change 6). |
| D-5 four SSE event types | A | A | A | **AGREE** (converged). |
| D-6 `SingleFlight` class | D: a shared guard outside `cc_watcher` is needed | M: a file lock does not merge queued work | M: the lock claim is true for separate handles (verified), but no rule pins the handle | **MODIFY** (converged). No class in `cc_watcher`. `ArchiveLock` in `backend/archive_sync.py` is a `threading.Lock` plus portalocker on a fresh handle. The watcher keeps its "run again" through the existing drift single-flight. |
| D-7 lock-holder metadata | A | A | A | **AGREE** (converged). |
| D-8 512 MB cap | A | A (in round 1 GPT called the cap a Blocker on its own) | A | **AGREE** (converged). |
| D-9 second presentation map | A | M: unverified that the presets can hold Codex | A: `SOURCE_PRESETS` is a `Record` keyed by source (verified) | **AGREE.** |
| D-10 "use the Git root" | A | A | A | **AGREE** (converged). |

### 2.2 Blockers (section 2)

| # | Gemini | GPT | Lead | Council result |
|---|---|---|---|---|
| B-1 Alternatives, Non-Goals, AC map | A | M: keep it short | M | **MODIFY.** The Non-Goals and the AC-to-node map are Blockers, because the line "no Explorer process writes under a Codex home" stops the verified `mkdir` bug. The Alternatives table is useful, but it does not block. |
| B-2 Codex descriptor | A | M: `is_archived` is a conversation field, not a descriptor flag | A, with GPT's note | **AGREE** (converged). GPT found the archive-state gap in AC1 on its own in round 1. |
| B-3 projection output schema | A | A: keep "messages change in place" specific to Codex, not a generic flag | A | **AGREE** (converged). |
| B-4 archive file formats, `thread_id` validation | A | A (found the `thread_id` path risk on its own in round 1) | A | **AGREE** (converged). Also check that every archive path stays inside the archive. |
| B-5 metadata changes reach the archive | A | A (found it on its own in round 1) | A | **AGREE** (strong convergence). |
| B-6 test isolation and a `codex_home` setting | A | A | A | **AGREE** (converged). This is the top privacy Blocker. |
| B-7 E2E fixtures | A: pre-synced archive | A: pre-synced archive, no "disable sync" flag | A | **AGREE** (converged). Choose the committed pre-synced archive. |
| B-8 file ownership | A | A | A | **AGREE** (converged). Both seats found the N1/N2 overlap on their own. Add the fixes in 1.5. |

### 2.3 Selected Majors

| # | Gemini | GPT | Lead | Council result |
|---|---|---|---|---|
| M-1 append rollouts, replace JSON whole | A | M: write a temp file as a stream, then replace it atomically | A | **AGREE, 2 to 1.** See the disagreement record. The user confirmed append under the lock (U3). Add the decode-error and revision rules (change 4). |
| M-2 a lock policy for each caller | A (changed from round 1, where Gemini wanted MCP never to sync) | M: on contention, return the archive plus `sync_pending` | A | **AGREE.** The MCP inline try-lock stays, because an MCPB-only user has no server and no watcher. The user chose to add `sync_pending` (U6). |
| M-4 MCP as a writer | A | A | A | **AGREE** (converged). Add the owner: N7 changes `build-mcpb.py` for portalocker (verified gap). |
| M-7 duplicate thread ids | A | M: define the "newest" key (a Blocker in GPT's round 1) | M | **MODIFY** (converged). The plan must name the key. Proposal: the newest `updated_at` from `session_index` or `state_5`, else the newest `session_meta` timestamp, then `home_key` to break a tie. Every process must derive the same answer. |

### 2.4 What the earlier report got wrong or missed

1. **D-4 has no replacement rule** (all 3 seats). After slice 2, the Codex FTS rows stay stale. `LOGIC_VERSION` wipes only the summary cache (verified).
2. **M-6 is unsafe for item types that change the history** (GPT; the lead agrees). `ContextCompaction` (a verified type) must mark the session incomplete; a fallback block is not enough. Which other of the 15 unmapped types change the history is UNVERIFIED.
3. **M-4 lists the MCPB work, but gives no owner** (GPT; verified). N7 merges with a broken MCPB closure, or it breaks the file-ownership rule.
4. **The hidden N4 to N5 dependency** (lead L2; both seats accept).
5. **M-7 leaves "newest" undefined** (GPT, as a Blocker in round 1).
6. **D-6 rests on a handle rule that no line of the plan states** (all 3 seats want an explicit lock for threads in one process).
7. **Error paths of the copier:** the decode-error and `.rev-` growth rules (Gemini in round 1; L5).
8. **Merge timing under a slice 1 gate:** N10 must not merge before the slice 1 tag (Gemini).
9. **M-10 needs a split** (GPT). Show `~` in the UI. The copy button and `home.json` keep the absolute path. For MCP, the seats disagreed: GPT wanted the real path, and the earlier report wanted `~`. The user chose `~/.codex` (U4).
10. **A cloud-synced `data_dir`** (Gemini in round 1; L7). The earlier report does not mention it. The user decided on a gate (U2).

## 3. Open Question Z8-15: a Slice 1 Release Gate

**All three seats say yes**, each with conditions. **The user approved the gate with all five of the lead's conditions (U1).**

- **Gemini: yes.**
  - Mark the slice 2 features incomplete, with a visible notice.
  - Suppress the replacement context.
  - Isolate the caches, so that slice 2 re-indexes on its own.
  - Hold N10 and N11 until after the slice 1 milestone.
  - Gemini also marks subagent threads incomplete. **The lead disagrees:** a subagent transcript is complete without its link, which is only missing.
- **GPT: yes, as a scoped preview, not as the end of #37.**
  - Slice 1 detects every condition that slice 2 handles.
  - The incomplete state shows in the UI, the MCP outline, the exports, and the search results.
  - Rolled-back turns, replacement context, prefix bases, and forks never render as ordinary history.
  - The release notes say that the full history waits for slice 2.
  - #37 stays open.
- **Lead: yes.** It is safe because the archive stores the full decoded bytes. Slice 2 re-projects from the archive, with no data migration. Conditions:
  1. **N4 has a classifier.** These records set `incomplete` with a reason:
     - `compacted` and `ContextCompaction`
     - rollback and revert
     - `history_base`
     - an ambiguous current rollout
     - any unknown type that changes the history

     A fork or a subagent with no `history_base` is not incomplete. Only its link is missing.
  2. **The `notices` field (Z8-8) ships in N8,** so slice 1 can show the notice in the viewer, the MCP outline, and the export header. A search-result badge is optional.
  3. **A Codex projection version** (change 6), so slice 2 refreshes the Codex search rows and summary rows.
  4. **Tag the slice 1 release before N10 merges.**
  5. **Measure first:** run the N6 shape report on a real work corpus before the release. If most sessions would show "incomplete" (long Codex sessions compact often; UNVERIFIED), the slice 1 release has little value.

## 4. Ranked Changes Before Implementation

The convergence level is in brackets. "3/3" means that all three seats agree. "User" means a user decision of 2026-11-18 (see [[#User Decisions (2026-11-18)]]).

1. **Isolate the tests and add the Codex home setting** (B-6) [3/3].
   - Add a `codex_home` field to `Settings` (env var, then `config.json`, then `~/.codex`).
   - Add an autouse fixture in both conftests that points it at an empty temp folder.
2. **Add the cloud-sync gate for the Codex copy** (U2) [User].
   - Detect a `data_dir` inside a known cloud-sync root: iCloud Drive (`~/Library/Mobile Documents/`), Dropbox, OneDrive, Google Drive, and `~/Library/CloudStorage/`.
   - Copy nothing into such a folder until the user opts in twice: a three-step UI confirmation (a warning, a check box, the typed provider name), and a config flag that holds the resolved `data_dir` path.
   - The watcher, MCP, and the CLI only report the closed gate. They never prompt and never copy.
   - Owners: N3 (detection), N7 (the check in every caller), N8 (the dialog, the route, the MCP notice, UX.md).
   - The full rule is in [[#U2 Rule: Cloud-Sync Gate for the Codex Copy]].
3. **Repair the DAG** [3/3 on N1/N2 and N4/N5; GPT plus verification on N7; User on the tag].
   - Move N2 to wave 3.
   - N4 uses only the rollout fields, and N5 owns the name and title chain.
   - N7 owns the portalocker MCPB change and a smoke test of the built bundle.
   - N6 depends on N7 (wave 4).
   - Add full repo-relative ownership paths (B-8).
   - Tag the slice 1 preview release before N10 merges (U1).
4. **Write the copier rules into 4.2** [User (U3) on append; 3/3 on the rest].
   - Append to rollouts under the lock. Replace the JSON files whole.
   - A source that is shorter than the archive changes nothing.
   - A decode error skips the stem and retries. It never makes a revision.
   - Make a revision only for a complete decode whose bytes differ from the newest revision.
   - Delete the 512 MB cap.
   - `ArchiveLock` is a `threading.Lock` plus portalocker on a fresh handle, in `backend/archive_sync.py`.
   - Test with 2 threads and with 4 processes.
5. **Define the formats and the identity** (B-2, B-3, B-4, M-7, U4) [3/3; User on the display form].
   - Each archive JSON file gets a schema with a `version` field.
   - Validate `thread_id`, and check that each path stays inside the archive.
   - Name one key for "newest", the same in every process.
   - Write the Codex descriptor and its `is_archived` rule.
   - Write the projection output schema, with stable message ids.
   - `source_home`: the UI, the export header, and MCP show the `~` form (for example `~/.codex`). The UI copy button and `home.json` keep the absolute path (U4).
6. **Make slice 1 safe and the derived data refreshable** (D-4 MODIFY, M-6 MODIFY, Z8-15, U1) [3/3 on the version; GPT and the lead on M-6; User on the gate].
   - Add the N4 incomplete classifier (section 3, condition 1).
   - Ship the `notices` field in N8.
   - Add a Codex projection version that refreshes the Codex FTS rows and summary rows without a `SCHEMA_VERSION` bump.
   - Before the slice 1 preview, run N6 on a work corpus and record the share of "incomplete" sessions.
7. **Metadata changes reach the archive, and nothing writes under a Codex home** (B-5, B-1 Non-Goals) [3/3].
   - Step 7 runs on every pass.
   - Watch `.zst`, `session_index.jsonl`, and `state_5.sqlite*`.
   - Never create a watch root.
   - Add the AC-to-node map.
8. **Apply the lock policy for each caller** (M-2, M-3, M-4, M-8, U6) [3/3; User on `sync_pending`].
   - MCP runs one inline try-lock pass, with no thread and no timer.
   - If the lock is busy, MCP reads the archive as it is. `list_sessions` and `get_session_outline` return `sync_pending: true`. The flag is `false` after a pass of this process finishes. `mcp_server/SPEC.md` documents it (N7).
   - Refresh and the CLI wait up to 60 s off the event loop.
   - The CLI hook goes in `cli/main.py`, before the credentials load.
9. **Write the release gates into §5.2** (U1, U5) [User].
   - **Slice 1 preview:**
     - N9 and N6 are merged.
     - The N6 measurement on a work corpus is done.
     - Every `codex-verification` issue is closed as fixed.
     - At least one passing report from a work machine exists.
     - The release notes say that the full history waits for slice 2, and #37 stays open.
   - **Final release:** the same issue rules, after N10 and N11.
10. **Apply the agreed deletions** D-1, D-2, D-3, D-5, D-7, D-8, D-9, D-10, and the changed D-6 [converged].
11. **Correct the facts** (M-19, D-10) [verified earlier].
    - Update the §3 counts.
    - Remove the Git root.
    - Fix the node count (13).
    - Fix the file counts.

## Disagreement Records

<disagreement_record>

| Field | Content |
|---|---|
| Topic | How the copier writes new bytes to `rollouts/<stem>.jsonl` (M-1) |
| Position A | Gemini, lead, earlier report: append the new bytes under the lock. Replace only the small JSON files whole. Readers ignore a last line with no newline. |
| Position B | GPT: decode as a stream into a temp file in the same folder, `fsync`, compare, then replace atomically, so that a reader never sees a partial file. |
| Resolution | **Position A wins**, by evidence weight and by risk asymmetry. The user confirmed it (U3). A torn append holds bytes from the source, so it is a prefix of the source: an earlier valid state of a file that is behind the source anyway. Position B's replace fails on Windows while a reader has the file open (plan §7), and it rewrites the whole rollout on every pass. |
| Residual risk | For one pass, a reader can see a rollout that is newer than its `threads/<id>.json`. That is harmless, because the thread file lists rollouts but holds no byte counts that a reader trusts. A future reader that trusts a byte count in the thread file breaks this. |
| WWCTD | Evidence that Codex rewrites earlier bytes of a rollout in place, or a reader that must see the rollout and the thread file in one consistent state. |

</disagreement_record>

<disagreement_record>

| Field | Content |
|---|---|
| Topic | `GET /api/sources` (D-1) |
| Position A | Earlier report, lead, GPT (after it conceded): delete the endpoint and keep the committed `sources.generated.json`. |
| Position B | Gemini: keep both, for later operations and MCP use. The frontend must not fetch at boot. |
| Resolution | **Position A wins** by constraint fit. The endpoint has no consumer: MCP and the CLI read the registry in-process. |
| Residual risk | A later source that is configured at run time needs the endpoint back. |
| WWCTD | A source that a user can turn on without a new release (for example a plugin source). |

</disagreement_record>

<disagreement_record>

| Field | Content |
|---|---|
| Topic | Mark subagent threads incomplete in slice 1 |
| Position A | Gemini: yes. |
| Position B | Lead (GPT's conditions name only "fork/history_base"): no. A subagent transcript is complete without its parent link. Mark only the records that change the history. |
| Resolution | **Position B wins.** A notice on every subagent thread would warn without cause and train the user to ignore notices. The user approved the lead's conditions (U1), which use this rule. |
| Residual risk | A subagent thread that also has a `history_base` must still be marked. The classifier must check the record, not `thread_source`. |
| WWCTD | Evidence that a subagent rollout leaves out turns that only its parent holds. |

</disagreement_record>

## Unverified Claims

Each of these came from a seat or the lead. None is checked against the repo or `openai/codex`.

- Codex writes a `.zst` file without an atomic rename, so the copier can see a partial file (Gemini). The rule in change 4 is safe either way.
- SQLite `mode=ro` fails on a WAL database when the `-shm` file is missing (Gemini). It is consistent with the one transient failure that the earlier report saw.
- Codex never rewrites earlier bytes of a rollout in place. The append design depends on this, and the `.rev-` path catches the case.
- Which of the 15 unmapped `ThreadItemPayload` types change the history, besides `ContextCompaction`.
- How often real Codex sessions compact. This decides how many slice 1 sessions show "incomplete". The N6 measurement (U1) answers it.
- Cloud-sync clients lock files or make placeholder files in a synced `data_dir` (Gemini).
- The install paths of each cloud-sync client in the U2 detection list.
- The cost of 77 Playwright specs with an unmocked `/api/sources` call (the earlier report's reason for D-1). GPT marked it UNVERIFIED. D-1 holds without it.

## Questions for the User (Answered)

The user answered all six questions on 2026-11-18. The answers are in [[#User Decisions (2026-11-18)]].

One point stays open for the plan author: should the cloud-sync gate (U2) also cover the other sources, which already keep data in `data_dir`?
