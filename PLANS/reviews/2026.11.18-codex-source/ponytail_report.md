# Ponytail Review (ultra)

- **Target:** `PLANS/2026.11.18-codex-source.md`
- **Method:** Ponytail 5.3.3 `ponytail-review`, level `ultra`, run by a general-purpose agent that read the two skill files. The agent read the code that the plan touches. It respected the user decisions D1-D5 and the identity fields.
- **Load assumed:** one user on one machine, up to four processes at once.
- **Verdict:** fix 1 to 8 first, as plan edits before N1, N3, and N5 start.
- **Lean:** about -300 lines possible (findings 9-14 and 16-18, with their tests).

## Must fix

| # | Finding | Fix |
|---|---|---|
| 1 | **A Codex rename never reaches the archive.** Step 1 stops when the rollout is unchanged, so `threads/<id>.json` is rebuilt only when the rollout changes. A rename goes to `session_index.jsonl` and `state_5`, not to the rollout. `state_5.archived` and `title` changes are lost the same way. No watch root covers `session_index.jsonl`. | Copy a rollout only when its fingerprint changes. Rebuild every thread file on every pass, and write it only when the bytes differ (else every pass re-indexes all threads). Watch `session_index.jsonl`. |

## Should fix

| # | Finding | Fix |
|---|---|---|
| 2 | The plan swaps the fingerprint in for mtime at only one of three places: `_drift_first_scan`, the check-read-check in `update_drifted_files`, and `_note_unloadable`. A Cowork session with a newer sidecar then re-indexes on every pass, under the write lock. | In N1, name all three places, and make each one call `adapter.fingerprint(path)`. |
| 3 | `CODEX_HOME` from the shell does not reach the supervised watcher or the MCPB server. `cli/watcher.py:95-101` passes only the backstop interval. | Use the `Settings` pattern of `claude_dir` (`backend/config.py`): env var, then a `codex_home` key in `config.json`, then `~/.codex`. |
| 4 | The watcher never clears the corrupt-config gate: `get_settings` is `lru_cache`d, and only `/api/config` clears it. A watcher that starts during a half-saved `config.json` refuses every copy until it restarts. | Call `get_settings.cache_clear()` once per pass before the gate check. |
| 5 | The Codex list re-parses every rollout on every list request. No summary cache is named. | Reuse `ConversationStore._cowork_summaries`: a dict keyed by the thread file's `(mtime, size)`. |
| 6 | A short read of a source creates a fake revision. If the decoded source is shorter than the archive (for example during a decompress on resume), step 5 moves the full copy to `.rev-1`. The plan does not say which file to read when `stem.jsonl` and `stem.jsonl.zst` both exist. | If the source is a prefix of the archive, do nothing this pass. If both forms exist, read the plain file. Test both cases in N3. |
| 7 | The MCPB bundle can leave out the new modules. A lazily imported Codex adapter, `archive_sync`, or `backend/codex/*` needs `DYNAMIC_IMPORT_MODULES` entries, or the extension raises `ModuleNotFoundError` on the first Codex call. | In N8, add each lazy module to `DYNAMIC_IMPORT_MODULES`, and test an import from the built bundle folder. |
| 8 | Archived Codex sessions are undecided. `list_conversations` hides `is_archived` rows unless `show_archived` is on, and the toggle shows only when `has_archive_state` is set. | In N8: `has_archive_state = true`; `is_archived` from `state_5.archived` (fallback: the file was in `archived_sessions/`); hidden by default, as Cowork. |
| 9 | Do not build a `SingleFlight` class. The portalocker lock also blocks a second thread in the same process, so the class adds no safety, and it reworks code from the 2026-11-11 fix. | Call `archive_sync_all()` at the top of `_event_drift_pass` and `_backstop_drift_pass`. If the lock is busy, call `_schedule_drift(...)`. Refresh takes the same lock with a timeout. |
| 10 | The same flags reach the frontend twice (`/api/sources` and `sources.generated.json`). | Keep only `sources.generated.json`. Drop the route. |
| 11 | Four new SSE event types, where `progress` frames with a `phase` already work for remote Cowork. | Send `progress` frames with `phase: "codex"`. A failure is a `progress` message, never a stream `error`. |
| 12 | The bookmark source change is not needed: nothing reads the field, and Codex bookmarks work as `claude_code`. | Delete the bullet. Remove `bookmarks.py` from N2. |
| 13 | MCP has no timer loop for retries, and `SPEC.md:7` ("ships no write paths") becomes false. | Try the lock once with no wait, at most once per N seconds per process, before a tool reads. If busy, just read. Update `SPEC.md` and the MCPB section of CLAUDE.md in N7. |
| 14 | The CLI hook in `fetcher/bulk_fetch.py` adds the first `fetcher` → `backend` import (cycle risk). | Call the sync from the `fetch` command in `cli/main.py`. |
| 15 | `logic_version()` has no reader. Example: after N10 hides rolled-back turns, old search rows keep the N4 text. | Drop the method and bump `search_index.SCHEMA_VERSION` in N10. Or make the drift pass compare a stored version. Choose in N1. |

## Nice to have

| # | Finding | Fix |
|---|---|---|
| 16 | The lock-holder metadata has no reader, and a second-handle write can fail on Windows while the lock is held. | Use the short shape of `fetcher/credentials.py:560-582` with `timeout=0` (watcher, MCP) or `60` (Refresh). No class. |
| 17 | The empty N10 hook in N4 is code "for later". | Remove it. Give N10 ownership of `projection.py`. |
| 18 | The salted thread-id hash in the shape report needs a stored salt and still links reports. | Group warnings by kind and print counts. |
| 19 | The 512 MB cap works against D1, and the decode streams anyway. | Remove the cap, or log loudly and copy anyway. |
| 20 | "Append-only" contradicts "replace the whole file". A coder can append in place, and a reader then sees a half line. | Say once: the content only grows, and each write is a whole-file atomic replace. |
| 21 | Facts that are off: there are three inline copies of the source union, not four. The §3 counts are out of date (see the count check in the consolidated report). `has_user_event = 0` for all threads, so a test on it hides every session (strong support for D2). `thread_source` also has `guardian_review` and NULL. | Correct §3 and §4.1. Add a `guardian_review` thread to `codex_fake.py`. |
| 22 | The title chain and the `source_name` chain disagree. | `name = source_name or first_human_message or "Codex session <date>"`. |

## Not checked

- Codex record shapes against the `openai/codex` source.
- Whether Codex writes `.zst` and decompressed files through a temp file and a rename (finding 6 matters only if it does not).
- The real decoded size of the corpus (sets the weight of finding 5).
- A Python 3.14+ build without `_zstd`: the marker skips `zstandard` there, so the shim has no fallback.
