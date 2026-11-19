# EGM Stage 8: Intermediate Zoom Out Review

- **Target:** `PLANS/2026.11.18-codex-source.md`, lines 1-378 (not edited since stage 5)
- **Reviewer:** egm-zoom-out. It read the stage 5, 6, and 7 reports, but not `ponytail_report.md`.
- **Verdict:** no new Blockers. About half of the fixes that stages 6 and 7 propose add parts that the need does not justify, and five of them conflict. Apply the simplifications first: they make about 25 later findings moot and keep the plan from doubling. The PR split is mostly right; it needs four changes and maybe a slice 1 release gate (a user decision).

## A. Conflicts between proposed fixes

| ID | Severity | Conflict | Resolution |
|---|---|---|---|
| Z8-1 | 🟡 Major | DE-M3 (append in place) vs ST-6, CO-17 (whole-file replace) | **Both, for different files.** Append new bytes to `rollouts/<stem>.jsonl` under the lock. Replace the small JSON files (`home.json`, `.meta.json`, `threads/<id>.json`) whole. A diverged copy stays rename plus full write. Readers already ignore a last line with no newline, and a torn append is still a prefix of the source. Record only the copied length (no tail hash). Layer 4 becomes: "Metadata files are replaced atomically. A rollout copy only grows. Readers ignore a last line without a newline." This is also the real reason for decoded JSONL (IC-7). |
| Z8-2 | 🟡 Major | MCP trigger: IC-2 (background thread) vs DE-M7 (startup try-lock) vs ZO-5 (one try-lock pass) | **ZO-5.** At the start of a tool call, if this process's last pass is older than about the debounce, run one pass inline with a non-blocking try-lock. If the lock is busy, read the archive as it is. No thread, no timer, no retry. Accept: the first call after install pays for the first copy. Keep the D9 gate check, the SPEC §1 and §8 rewrite (N7), the `DYNAMIC_IMPORT_MODULES` entries, and one "why MCP copies" sentence. |
| Z8-3 | 🟡 Major | Duplicate homes: DE-M5 ("also in <home>") vs IC-6 (collapse by stem) | The copier syncs one configured home (a `Settings` field; normalize the path before the hash). Readers read every archived `<home_key>` tree (D1). `discover()` dedupes once, by D8, with one defined "newest" key, so list, search, and MCP see one path per thread id. **Reject** "also in <home>", collapse by stem, earlier home paths, and the host name in `home_key`. Write: "A `data_dir` that two machines share is not supported." |
| Z8-4 | 🟡 Major | Cap: IC-4 (record and copy raw) vs DE-M4 (no cap) | **DE-M4.** Delete the 512 MB cap. The decode streams, appends need bounded memory, and the largest real file is 2.2 MB compressed. A memory guard, if any, goes in the projection, which already has `incomplete`. |
| Z8-5 | 🟢 Nit | Stage 5 removals | Drop `/api/sources` (moots ST-8, SR-n9, DE-N8, CO-12; N1 loses `routers/sources.py`; N2 only reads the generated file). Drop the bookmark change (moots CO-10, ST-n12; N2 becomes frontend-only). Extend `SOURCE_PRESETS` instead of a second map; N2 makes the filter dropdown read the registry, so N8 needs no `Sidebar.tsx`. Remove the N10 hook and `logic_version()` (moots DE-N4). |

## B. Additions that over-build

| ID | Severity | Topic | Resolution |
|---|---|---|---|
| Z8-6 | 🟡 Major | `fingerprint()`, DE-M6's new column | **Drop `fingerprint()` and the Cowork sidecar fix from N1.** State instead: "The copier rewrites `threads/<id>.json` when a rollout that it lists changes, or when its metadata changes. The file records each rollout's copied length." Then every stat-keyed cache (drift, `summary_cache`, MCP outline, `FileCache`) works unchanged, and no column is needed. Fix the Cowork sidecar gap in its own small leaf PR. |
| Z8-7 | 🟡 Major | DE-M2 split pass | Accept the gap; reject the split pass. Step 1's early stop skips only steps 2-6. Step 7 runs every pass and writes the thread file only when it differs. The watch filter matches `.jsonl.zst`, `session_index.jsonl`, `state_5.sqlite*`. A pass that wrote anything schedules the existing drift single-flight. No changed-path lists in `SyncReport`. |
| Z8-8 | 🟡 Major | Status fields | One field: `notices: list[str]` on `ConversationDetail`, defined in N8 (warnings count, incomplete + reason, ambiguous current rollout, "Imported from Claude Code"). Links use `SubagentSummary`. Do not stretch `error`. N11 shows "N older rollouts are kept in the archive" instead of a revision viewer, and owns no `models.py` or `types.ts`. MCP outline returns `notices`. |
| Z8-9 | 🟡 Major | Projection gaps | DE-M9: accept a thread-namespaced `message_uuid` and one descriptor flag ("messages can change in place") that forces a full outline rebuild; state the rule, not the formula. DE-M10: `state_5.history_mode` if present, else paginated if any `item_completed` exists. DE-M11: one generic fallback block per unmapped type, counted in warnings; reject listing every upstream type and turn interruption in slice 1; the N6 shape report counts decide later mappings. DE-M12: group by `cwd`; delete "use the Git root". |
| Z8-10 | 🟡 Major | Refresh and lock details | Accept: on a 60 s timeout, report it, skip the local sync, continue with claude.ai; wait off the event loop; the CLI uses the Refresh policy; "backstop" = `scan_once`. Reject: four new SSE types (use `progress` with a Codex phase); lock-holder metadata; stale-lock stealing (the OS releases a dead holder's lock). N7 then owns no frontend files. |
| Z8-11 | 🟡 Major | Alternatives and Non-Goals | Justified, but capped. Alternatives: a ~6-row table for the derived and design choices only (D7, decoded vs original bytes, append vs replace, lock vs single writer, adapter shape), merged with the Rationale column. Do not re-argue the user's decisions. Non-Goals: ~8 bullets (the #37 non-goals; "No Explorer process writes, renames, locks, or opens for writing any file under a Codex home"; the local CC and Cowork gap is out of scope and N3's next user; D5 replaces "no always-on watcher"). Add a 6-line map from #37 acceptance criteria to nodes. AC6 owners: N8 runs the before-and-after hash test of the fake home; N5 shows that opening `state_db` creates no `-wal` or `-shm`. |
| Z8-12 | 🟢 Nit | Corrupt-config visibility | One WARNING log line per skipped pass. No new banner text. |
| Z8-13 | 🟢 Nit | `source_home` display | Show it with `~` in the UI, the export, and MCP. `home.json` keeps the absolute path. |
| Z8-14 | 🟢 Nit | Sibling writers | Keep N3 (critical path) to `atomic_write_bytes` plus the two named writers. Move the bookmarks and preferences writers to a separate leaf PR. |

## C. Slicing

| ID | Severity | Resolution |
|---|---|---|
| Z8-15 | 🟡 Major | The shape is right; add no nodes for these fixes. The DAG has **13** nodes (N0-N11 + NF); the #37 update says 12, so correct one. Changes: (1) N6 depends on N7 and moves to wave 4. (2) N2 owns all of `frontend/src/**` and no backend file; N7 owns no frontend file. (3) N1 shrinks: no `/api/sources`, `fingerprint`, `logic_version`, or Cowork fix. (4) N10 owns the slice 2 extensions of `codex_fake.py`; N11 owns no `models.py`. **User decision:** give slice 1 its own release gate (N9 + N6). N4 detects slice 2 records and adds an "incomplete" notice, so slice 1 never shows a falsely complete transcript. #37 stays open until slice 2. |

## D. Altitude

- **Z8-16 (🟢 Nit):** apply the deletions before the clarifications.
  - **Moot or answered by Z8-1 to Z8-10:** ST-4, ST-6, ST-7, ST-8, ST-10, ST-n12, ST-n16, CO-5, CO-8, CO-10, CO-12, CO-16, CO-17, CO-22, DE-M4, DE-M6, DE-N2, DE-N4, DE-N6, DE-N8, SR-8, SR-n9, IC-4, IC-6.
  - **Accept as written:** DE-M13, DE-M14, DE-M15, SR-6, CO-4, CO-6, CO-9, ST-1/SR-3.
  - **Keep code-level detail out of the plan:** the formulas, "the next free integer", hash formats. State the rule, and let each node's tests pin the detail.
