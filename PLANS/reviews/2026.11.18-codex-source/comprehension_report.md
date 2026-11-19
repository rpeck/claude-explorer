# EGM Stage 6: Comprehension Test

- **Target:** `PLANS/2026.11.18-codex-source.md`. FeatureRange 1-261, DetailedRange 262-378.
- **Reviewer:** egm-comprehension (registered 0.1.0 adapter, 0.1.1 persona; the reviewer reports that the two are identical). It read the document alone.
- **Result:** no Blockers ("I could paraphrase every section"), 13 Major, 14 Nit.

## What this proposes

The plan adds OpenAI Codex as a fourth conversation source in Claude Explorer, both in the web UI and in the MCP server. First it adds a source-adapter layer (`backend/sources/`) and moves the three current sources (claude.ai, Claude Code, Cowork) onto it. Golden-output tests make sure that move changes nothing, and capability flags served from `GET /api/sources` replace the hard-coded rules in the frontend. A copier copies every Codex rollout file from the Codex home into an append-only, never-delete archive under `data_dir`. Five possible callers trigger the copy, and they are kept apart by an in-process single-flight guard plus a cross-process file lock. All Explorer readers read only that archive. A Codex projection turns the archived rollouts into Explorer messages. Slice 1 covers ordinary messages, reasoning and commands. Slice 2 covers compaction, revert, forks, subagents and prefix references. Each Codex session shows its thread ID, its Codex name and its Codex home. The work is split into 13 PR nodes run as a DAG by an orchestrator, with separate tester and coder sessions. Before release, people on work machines check it using a report that holds no private data.

## How the existing system works, per this document

There are three sources today: `CLAUDE_AI`, `CLAUDE_CODE` and `CLAUDE_COWORK`. Each is wired by hand: a `Literal` type repeated in about 25 signatures, `if source ==` branches in `store.py`, `search_index.py` and `cc_watcher.py`, and rules in 7 frontend files. A file watcher (one in-process inside `serve`, one supervised process) and a manual Refresh keep data current. A search-index "drift pass" re-indexes files whose mtime changed, but it misses changes to Cowork sidecar files. Nothing coordinates file copies across processes. Some writers are not atomic (`remote_sessions._atomic_write_text` does not fsync, and `cc_image_cache` writes without a temp file). Bookmarks store a separate two-value source (`claude_code` / `claude_desktop`), with Cowork mapped to `claude_code`. On the Codex side, sessions are rollout JSONL files that Codex compresses after 7 days, decompresses on resume, and moves when archived. `state_5.sqlite` holds thread metadata, and `thread_history_1.sqlite` is a cache built from the rollouts.

The weakest section is the slice 2 projection (4.4, lines 218-224).

## Findings

### FeatureRange (lines 1-261)

| ID | Severity | Lines | Finding |
|---|---|---|---|
| CO-1 | 🟡 Major | 161-174 | The lock rules do not match the caller list. "Backstop" is not one of the five callers and is not defined. The `claude-explorer fetch` CLI gets no lock rule. "Retry after the debounce time" has no meaning for the MCP process. |
| CO-2 | 🟡 Major | 166, 230, 319 | The plan never says what triggers the MCP sync (startup, each call, a timer). |
| CO-3 | 🟡 Major | 18, 45, 223 | `history_base`, rollback, revert, and fork are not described: which record carries them, and what following a link produces. Slice 2 cannot be paraphrased past "follow up to 32 links". |
| CO-4 | 🟡 Major | 149, 212, 244 | `session_index.jsonl` is used as the first name source, but §3 never introduces it (location, shape, why the latest name wins). D6 leaves it out. |
| CO-5 | 🟡 Major | 211-215, 244-245 | The title chain and the `source_name` chain disagree (`state_5.name` is in one only). So the claim "`source_name` tells a reader if the title came from Codex" fails when `state_5.title` is used. The difference between the `title` and `name` columns is not explained. |
| CO-6 | 🟡 Major | 51, 149, 154-157 | The plan never says how the copier learns a rollout's thread id: the stem, `session_meta`, or `state_5.rollout_path`. This matters most when `state_5` is missing. |
| CO-7 | 🟡 Major | 44, 192-204 | The plan never says how the reader decides if a rollout is paginated or legacy: `state_5.history_mode` (which can be missing) or the records themselves. |
| CO-8 | 🟡 Major | 33, 128, 136, 240 | The plan never says how Explorer learns which Codex homes exist. "Keep the newest copy" does not say what "newest" is measured by, or whether "keep" means "show". |
| CO-9 | 🟡 Major | 34, 319 | "Corrupt-config gate" is a project term with no definition or link. |
| CO-10 | 🟡 Major | 116-119 | The mapping of old bookmark values is missing: an old `claude_code` value can be Claude Code or Cowork. |
| CO-11 | 🟡 Major | 69, 74-81, 320 | The Codex source id string and its capability flag values are never given (for example `has_archive_state`). |
| CO-12 | 🟢 Nit | 107-109 | The purpose of `sources.generated.json` next to `/api/sources` is unclear. |
| CO-13 | 🟢 Nit | 22, 33, 35 | The intro calls D6-D10 derived, but D8 and D10 are marked "User". |
| CO-14 | 🟢 Nit | 194-199, 356 | Unlisted paginated item types: dropped or sent to `warnings`? |
| CO-15 | 🟢 Nit | many | Terms used without definition or link: Pi, Hermes, "the code map of 2026-11-18", drift pass, leaf walk, MCPB, `CompactMarker`, `SubagentSummary`, `data_dir`, `cowork-remote/`, `serve`, supervised watcher, `ctx`, `SyncReport`, install matrix, the inline source-union copies, CLAUDE.md principle 1, UX.md §16. |
| CO-16 | 🟢 Nit | 91, 131, 143 | "Fingerprint" has two meanings (adapter float mtime vs copier size, mtime_ns, path). |
| CO-17 | 🟢 Nit | 130, 146, 176 | "Append-only" next to "replaces the whole file" reads as a contradiction. |
| CO-18 | 🟢 Nit | 92, 121, 312 | "No change in behavior" in N1 has unstated exceptions (the Cowork drift fix, the SPEC change). |

### DetailedRange (lines 262-378)

| ID | Severity | Lines | Finding |
|---|---|---|---|
| CO-19 | 🟡 Major | 276-277, 318, 357, 370 | N6 reports archive results and measures the decoded size, so it needs N3 (and probably N7), but it depends only on N4 and N5. |
| CO-20 | 🟡 Major | 307, 311-323 | File ownership does not match the work. N1 and N2 both own `sources.generated.json` in wave 2. N8 must change `SourceId` but does not own `ids.py`. N11 must add fields but owns neither `models.py` nor `types.ts`. N10 needs new fake data but does not own `codex_fake.py`. NF leaves out `external_agent_session_imports.json`. N7 changes the fetch UI but does not own `UX.md`. |
| CO-21 | 🟡 Major | 335-339 | The tester and coder workflow is incomplete: who runs the tester's tests against the coder's code, in which worktree, and the tester worktree path. |
| CO-22 | 🟢 Nit | 319 | "Through both layers" and "both consumers" are not named. |
| CO-23 | 🟢 Nit | 312, 319, 320 | Owned paths without a directory (`main.py` in N7 vs `cli/main.py` in N6). |
| CO-24 | 🟢 Nit | 318, 371 | N6's privacy test leaves out titles, which §8 forbids. |
| CO-25 | 🟢 Nit | 6, 35, 336, 364 | "Tester" means both an AI session and a person. How people get the build is not stated. |
| CO-26 | 🟢 Nit | 321, 328, 346, 349 | Undefined: "the relevant Windows subset", STE, `pr-assets` branch, "unexpected" console errors. |
| CO-27 | 🟢 Nit | 255-257, 311 | Only 3 of the 5 MCP tools are named. |
