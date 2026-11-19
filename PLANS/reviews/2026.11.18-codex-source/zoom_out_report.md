# EGM Stage 5: Initial Zoom Out Review

- **Target:** `PLANS/2026.11.18-codex-source.md` (lines 1-378)
- **Preset:** consolidated report (uninterrupted), EGM 0.1.1
- **Reviewers:** egm-zoom-out, egm-inventive-catalyst

## egm-inventive-catalyst

- **Status:** completed. It ran as a general-purpose agent that read the 0.1.1 persona, because this session registered only the 0.1.0 adapters, and 0.1.0 has no inventive-catalyst adapter.
- **Result:** 3 Major, 4 Nit, no Blockers (REVIEW mode allows only Major and Nit).

| ID | Severity | Lines | Finding | Proposed resolution |
|---|---|---|---|---|
| IC-1 | 🟡 Major | 86-87, 138, 171, 178 | The watcher has no defined order for "copy, then index". A watchdog event on `~/.codex` can start a drift pass over an archive that the copier has not updated yet. Only Refresh states an order. | `archive_sync` returns the thread paths that changed, and the watcher sends them to `_schedule_drift` (event chaining). Or the drift observer watches `<data_dir>/codex/` too. State the order for all five callers. N7 test: a watcher event makes the thread searchable with no backstop scan. |
| IC-2 | 🟡 Major | 166, 173, 230, 319 | MCP becomes a writer. `SPEC.md:7` ("read-only facade … ships no write paths") and `SPEC.md:443` ("No tool mutates the conversation store") are not revised. The plan does not say when MCP syncs. A first sync on a call could be slow. | Stale-while-revalidate: the call reads the archive; a non-blocking try-lock sync runs at most once per N seconds on a background thread. Rewrite the SPEC invariant in N7 or N8. |
| IC-3 | 🟡 Major | 96-109, 312-313 | The capability flags reach the frontend twice: at run time (`/api/sources`) and at build time (`sources.generated.json`). The frontend ships inside the backend package, so the two cannot disagree. The run-time path adds a loading state and makes "no UI change" harder to meet. | Use only the generated JSON in the frontend. Drop `/api/sources` unless a consumer is named. |
| IC-4 | 🟢 Nit | 144 | The 512 MB cap is a silent, permanent gap. A rollout over the cap never reaches the archive. | Record `skipped_oversize` in the meta file and the shape report. Mark the thread `incomplete`. Optionally copy the raw `.zst` bytes. |
| IC-5 | 🟢 Nit | 33-34, 52 | D9 relies on "Codex does not prune", and §3 hedges against pruning. The gated skip is invisible. | Log one warning per skipped pass. Show "Codex archive paused since T" in the existing corrupt-config banner. |
| IC-6 | 🟢 Nit | 33, 136 | "Keep the newest" can hide a session. The `home_key` is a hash of the path, so a moved Codex home starts a second archive tree. | Collapse copies only when they share the rollout stem; else show both, with `source_home` to tell them apart. Record earlier paths in `home.json`, so a moved home reuses its tree. |
| IC-7 | 🟢 Nit | 139, 357 | The reason given for a decoded archive ("readers need no zstandard") saves nothing, because D4 installs zstd everywhere. The real reason is the prefix-and-append model. | State the real reason. In §7, name multi-frame zstd appends as the follow-up design. |

## egm-zoom-out

- **Status:** completed (the registered 0.1.0 adapter, given the 0.1.1 persona and standard; the reviewer reports that the two personas are identical).
- **Result:** 7 Major, 5 Nit, no Blockers. The reviewer checked the plan's line references against the code and found them accurate today.

| ID | Severity | Lines | Finding | Recommendation |
|---|---|---|---|---|
| ZO-1 | 🟡 Major | 96-108, 312 | The source data is in three places: the registry, `/api/sources`, and `sources.generated.json`. The frontend and backend ship in one wheel, so the list cannot change at run time. | Drop `GET /api/sources` and `routers/sources.py`. Import the generated JSON. (Same as IC-3.) |
| ZO-2 | 🟡 Major | 106-115, 313, 320 | `SourceBadge.tsx` already has `SOURCE_PRESETS` (icon, label, color), built so a new source "is a single-file change". The plan makes a second map in `lib/sources.ts`. The rule list misses: the filter dropdown items (`Sidebar.tsx:294-317`), the group-by-project toggle (`Sidebar.tsx:424`), the project-path collection (`conversationListView.ts:71, :108, :196, :260`), the group badge (`ConversationList.tsx:364`), the project label gate (`ConversationList.tsx:898`). N8 must add "Codex" to the filter but does not own `Sidebar.tsx`. | Extend or move `SOURCE_PRESETS`. N2 makes the dropdown and these gates read the registry. |
| ZO-3 | 🟡 Major | 116-119, 313 | Nothing reads the bookmark `source` field. A Codex bookmark stored as `claude_code` works. The change alters user data on disk in a "no behavior change" node, and older versions then skip the new entries (`bookmarks.py:117-129`), so bookmarks disappear after a downgrade. | Drop the bookmark change. |
| ZO-4 | 🟡 Major | 174, 319 | Four new SSE event types change the shared schema and both consumers. The remote Cowork sync already uses `progress` events with a `phase` field (`fetch.py:441-476`, `COWORK_PHASE`), and failures are `progress` messages, never a stream `error`. | Send `progress` events with a Codex phase. N7 then needs no frontend files. |
| ZO-5 | 🟡 Major | 161-167, 173, 230-232, 319 | MCP as a writer reverses `SPEC.md` §1 and §8 ("read-only"), and no node owns that change. The trigger is undefined. The stdio server has no timers or threads for "retry after debounce". MCP does not check the corrupt-config gate today. A lazy-imported copier needs `DYNAMIC_IMPORT_MODULES` entries too. This follows from D7, which is derived, not a user decision. | State why MCP copies (an MCPB-only user has no `serve` and no watcher). Use one try-lock pass, no retry timer. Give the SPEC change to N7. Or remove MCP from the copiers. (Same as IC-2.) |
| ZO-6 | 🟡 Major | 93, 121, D1 | `archive_sync` returns `None` "for a source that has no external files". That is false for Claude Code and local Cowork: CLAUDE.md records them as a known gap. | Say plainly that the CC and Cowork gap is out of scope, and name it as the next user of N3. Keep N3 free of Codex details. |
| ZO-7 | 🟡 Major | 157, 209, 224, 320, 323 | The projection makes `warnings`, `incomplete` + reason, `current_ambiguous`, revisions, and an imported flag, but no API field carries them, and no node owns them in `models.py` (N11 does not own it). The slice 1 warnings notice has no UI owner. `ConversationDetail.error` already drives an alert banner. | Define the status fields once and assign them to N8 and N11. Reuse the `error` banner for slice 1 warnings. |
| ZO-8 | 🟢 Nit | 172 | The portalocker lock pattern exists twice already (`credentials.py:_acquire_lock`, `migrate_to_v2.py:178-193`). The holder metadata has no reader in the plan. | Factor out one helper. Drop the metadata or name its reader. |
| ZO-9 | 🟢 Nit | 153, 180 | The plan does not say where `atomic_write_bytes` lives. Other hand-made writers remain: `bookmarks.py:_write_all` (fixed `.tmp` name, no Windows retry), `preferences.py:_write_atomic`, `bulk_fetch.py:879`, `migrate_to_v2.py:307`. | Put it in `fetcher/atomic.py`. Move at least the bookmarks writer in N3, or record why it is not a bug. |
| ZO-10 | 🟢 Nit | 316, 322 | "A hook for N10 that does nothing yet" is code for a future PR. | Let N10 add the call point. |
| ZO-11 | 🟢 Nit | 94 | `logic_version()` per adapter has no consumer. Only the CC reader has a `LOGIC_VERSION`, and `summary_cache` keeps one global key. | Say how the summary cache combines versions, or add the method later. |
| ZO-12 | 🟢 Nit | many | Wave 2 rewrites the files that the line references point to. `SPEC.md` names functions, not line numbers, so that references stay valid. | Use symbol names. |

## Agreement between the two reviewers

- **`/api/sources` is not needed** (IC-3, ZO-1).
- **MCP as a writer is not specified** (IC-2, ZO-5).

Both reviewers are Claude models, so this agreement is not cross-provider agreement.
