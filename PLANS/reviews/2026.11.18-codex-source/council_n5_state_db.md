# Council: N5 reads of `state_5.sqlite`

- **Date:** 2026-11-19
- **Plan:** [2026.11.18-codex-source.md](../../2026.11.18-codex-source.md), §1.2, D6, §3 "`state_5.sqlite.threads`", §4.2, §4.4 "N5 (metadata)", §5, §6.1 row N5
- **Ticket:** [context/issue-37.md](context/issue-37.md), "Safety and refresh requirements"
- **Question:** how does N5 (`backend/codex/state_db.py`) read Codex's WAL-mode `state_5.sqlite` without breaking §1.2 or #37?
- **Result: unanimous.** All three seats agree on the final rules (E3), a checked private copy. Each seat voted AGREE on all 13 rules, on tests T1 to T17, and on the text changes. The Gemini seat cast its vote in round 3, after its round 2 calls failed (see [[#Council members]]).
- **Status:** a council recommendation. The user has not seen it yet.

## Council members

| Seat | Model that answered | Rounds answered |
|---|---|---|
| Lead | Claude Opus 5.5 (direct) | 1, 2, 3 |
| Gemini seat | `gemini-3.8-flash` (direct Google key) in round 1; `google/gemini-3.8-flash` (OpenRouter) in round 3 | 1, 3 |
| GPT seat | `chatgpt/gpt-5.6-terra` (ChatGPT plan) | 1, 2, 3 |

- **PAL ping:** `gemini-3.8-flash`, direct route, answered "PAL OK".
- **Gemini calls: 6.**
  - Call 1: the ping.
  - Call 2: round 1. It answered.
  - Call 3: round 2. It failed with **503 UNAVAILABLE** ("high demand") after PAL's 4 internal attempts.
  - Call 4: round 2 again. It sent nothing for 1800 s, and the MCP client stopped it.
  - Call 5: the round 3 vote. The orchestrator authorized it after the first report, and said the cap of 3 was its own budget, not a user rule. `gemini-3.7-flash` (direct) failed with **429 RESOURCE_EXHAUSTED** (the free tier's daily limit of 20 requests).
  - Call 6: the same vote through OpenRouter, `google/gemini-3.8-flash`, as the orchestrator directed for any failure (the user authorized this route for this run). It answered.
- **The OpenRouter route was not used in round 2.** At that time it was authorized only for a 429, and neither round 2 failure was a 429.

## The decision (E3)

**N5 never opens the live `state_5` with SQLite.**

- It reads `state_5.sqlite` and its `-wal` as plain bytes, read-only, into a private temp folder.
- It accepts the copy only after checks that prove the copy is a committed state.
- It then opens the copy with SQLite.

Plan §1.2 stays as it is. The #37 safety bullet and four places in the plan change (see [[#Text changes]]).

### Rules N5 must implement

1. **Files.**
   - In the Codex home, open only `state_5.sqlite` and, when present, `state_5.sqlite-wal`.
   - Open each read-only, only for one read, and close it at once.
   - Never open `state_5.sqlite-shm`.
   - Never pass a Codex-home path to `sqlite3`.
   - Never use `immutable=1` or `readonly_shm=1`, and never use `mode=ro` on the live file.
2. **Windows open.**
   - Open with ctypes `CreateFileW`, `GENERIC_READ`, and `FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`, so that Codex can still delete or truncate its `-wal` during the copy.
   - If that open fails, fall back (rule 11).
   - Do not use the private CPython module `_winapi`.
3. **Rollback journal.** If `state_5.sqlite-journal` exists at the start or at the end, fall back.
4. **Size cap.** If the main file plus the `-wal` are larger than 64 MiB (a constant), fall back with a warning.
5. **Deadline.**
   - The whole read gets 250 ms: every attempt, the copy, the hashes, `quick_check`, and the query.
   - Copy in 1 MiB chunks, and check the deadline between chunks.
6. **One attempt,** in this order:
   1. t0: stat the main file and the `-wal`. Read the `-wal` header salts (bytes 16 to 23) as S0. S0 is `None` if the `-wal` is absent or shorter than 32 bytes.
   2. Read the main file into the temp copy, and hash it on the way (H1).
   3. If the `-wal` existed at t0, copy it.
   4. Read the salts from the copied `-wal` header (Sc).
   5. Read the main file again and hash it (H2).
   6. t3: stat the main file and the `-wal`, read the source salts again (S3), and check for `-journal`.
7. **Accept the copy only if every check passes:**
   - H1 equals H2.
   - The main file has the same inode, size, and `mtime_ns` at t0 and t3.
   - The `-wal` exists at both t0 and t3 or at neither. If it exists, it has the same inode.
   - S0, Sc, and S3 are equal. `None` counts as a value.
   - No `-journal` exists.
8. **Retries:** up to 3 attempts, with a backoff of 10 ms and then 40 ms, inside the deadline. Then fall back.
9. **Open the copy:**
   - Open it in the temp folder, normally.
   - Run `PRAGMA query_only=1`.
   - `PRAGMA quick_check` must return `ok`.
   - The §3 columns must exist.
   - Read all rows in one transaction, then close.
   - Run no other PRAGMA and no SQL that writes.
10. **The temp folder:**
    - `tempfile.mkdtemp(prefix="claude-explorer-state5-")` in the system temp folder, mode 0700. Never use `data_dir`: it can be cloud-synced (D13).
    - Delete it in `finally`.
    - At startup, delete leftover folders, but only folders that meet all of these:
      - the name has that prefix;
      - the current user owns it;
      - its mode is 0700;
      - it is not a symlink or a reparse point.
    - When a cleanup fails, log a warning without titles, paths, or cwd values.
11. **Fallback.**
    - Log one warning per pass, with no titles, paths, or cwd values.
    - A thread that has archived `state_5` values (the D6 snapshot) keeps them. Never overwrite good values with fallback values, or titles will flap between passes.
    - A thread with no archived values uses `session_meta` and the title chain (§4.4).
    - If the rollout set of a thread changed since its archived values, set `current_ambiguous: true`.
    - Rollouts never wait on metadata.
12. **Cache.**
    - The key is (main stat, `-wal` stat, S0).
    - Reuse the last accepted result only when the key is the same and the result is less than 60 s old.
13. **Discovery** never waits on this read (unchanged from §4.4).

### Why an accepted copy is a committed state

1. **Main changes only in a checkpoint.** In WAL mode, SQLite writes the main file only during a checkpoint. A checkpoint writes only pages that are committed frames of the current WAL generation.
   - A change to rollback-journal mode creates `-journal` and rewrites header bytes 18 and 19. Rule 3 and the double read catch it.
2. **A generation never reuses salts.** Within one generation, SQLite never rewrites a committed frame. A WAL restart writes new salts (salt-1 plus 1, a new random salt-2). `TRUNCATE` empties the file, so the header is gone.
3. **H1 = H2 bounds when main changed.** A page that changed after read 1 had read it shows up in read 2, so H2 differs.
   - GPT's correction: this alone does not prove that no page changed. An identical-bytes rewrite (ABA) can hide a change. Point 4 covers that case.
4. **One generation spans the window.** S0 = Sc = S3, with the same inode, means one WAL generation G lasted the whole window.
   - So every page that a checkpoint wrote into main by then is a committed frame of G. That frame already existed when the WAL copy started.
   - The copy holds the frame, and SQLite reads that page from the copied WAL, not from the copied main file.
   - A torn last frame fails its checksum, and SQLite drops it. The result is an older commit.
5. **So the copy is a valid snapshot.** It is the main file at one instant plus a prefix of G's committed frames. That is the state that SQLite shows a normal reader that starts at the last commit in the copied prefix.

The WAL frame and salt layout is the documented, stable SQLite file format ("WAL file format" in fileformat2). It is not a private detail of one SQLite build.

**Windows note.** SQLite's Windows locks are mandatory byte-range locks.

- On the database file, they sit on the lock-byte page at offset 1 GiB, so a byte read of a smaller file never touches them.
- The WAL-index locks are on `-shm`, which N5 never opens.
- T14 below checks this on a real Windows machine.

### Tests N5 must have

All tests use a synthetic database in the fake home (D10). A "home hash" means the name, size, `mtime_ns`, and SHA-256 of every file in the fake home.

| # | Test | Pass condition |
|---|---|---|
| T1 | Quiescent home, only `state_5.sqlite` | Rows correct. Home hash unchanged. No `-wal` or `-shm`. |
| T2 | A writer process commits rows only into the `-wal` and then stays idle with the connection open | N5 sees the WAL rows. Home hash unchanged across the N5 call. |
| T3 | A writer process exits with `os._exit` and leaves `-wal` and `-shm` | N5 sees the WAL rows. Home hash unchanged. |
| T4 | Replay of the GPT round 2 sequence with hooks and 1 s stat rounding ([[#Appendix: the replay of the GPT sequence]]) | N5 never returns a state that was not committed. It retries, then falls back. |
| T5 | A hook changes the main file during the copy | Rejected, retried. Changing on every attempt gives a fallback with a warning. |
| T6 | A hook runs `wal_checkpoint(TRUNCATE)` and a new commit during the `-wal` copy | Rejected. |
| T7 | `-journal` exists | Fallback. |
| T8 | `state_5` missing, unreadable (POSIX `chmod 000`), garbage bytes, unknown schema | Fallback with a warning. Rollouts are still archived. |
| T9 | The home folder is read-only (`chmod 0555`) | N5 reads the rows and creates nothing. |
| T10 | Temp folder | Mode 0700. Gone after success and after an exception. The startup sweep removes a leftover and skips a symlink and a folder of another name. |
| T11 | Cache | Same key and less than 60 s: no copy. A changed key or an older result: copy. |
| T12 | Guard | A monkeypatched `sqlite3.connect` never receives a path under the Codex home. The module source contains neither `immutable` nor `readonly_shm`. |
| T13 | Deadline and size | A slow copy (hook) past 250 ms falls back. A file over the cap is not copied. |
| T14 | Windows only | The writer can delete and truncate `-wal` while N5 holds it open. A failed share-delete open falls back. |
| T15 | Fallback pass | Good archived titles stay the same. A thread with a changed rollout set gets `current_ambiguous: true`. |
| T16 | Codex holds an open write transaction (the AC5 "locked" case) | N5 reads the last committed state, and the writer is not blocked. |
| T17 | Opt-in slow stress (marker, not in the default run): a churning writer process | 0 accepted copies that break the invariant. |

## Experiment results

**Setup.**

- Python 3.13.3 and SQLite 3.47.1 on macOS (APFS).
- A synthetic WAL database in the session scratchpad; no test touched `~/.codex` or `~/.claude`.
- The writer was a separate process.
- An invariant held in every committed state: `meta.n = count(*)` of `threads`, written in the same transaction. The stress runs added `max(id) = count(*)` and `sum(length(pad)) = 3000 * count(*)`.
- The scripts are in the session scratchpad (`exp/run.py`, `writer.py`, `stress.py`, `writer2.py`, `gpt_sequence.py`). That folder is temporary. The replay is copied in the appendix.

**Strategies tested.**

- **A:** `mode=ro`.
- **B:** `mode=ro&immutable=1`.
- **C:** `mode=ro&readonly_shm=1`, an undocumented unix-VFS flag.
- **D:** a hybrid.
  - When `-wal` and `-shm` both exist, read as A.
  - When neither exists, read as B, then check that the main file did not change and that no `-wal` appeared.
  - Otherwise, fall back.
- **E:** a copy of the main file and the `-wal`, accepted when the main file's stat did not change.
- **E2:** E plus a check of the copied salts and the `-wal` inode.
- **E3:** the decision.

### Which files each read creates or changes

In the fd columns, `r` is a read-only handle and `u` is a read/write handle (from `lsof`).

| Scenario | A `mode=ro` | B `immutable=1` | C `readonly_shm=1` | D hybrid | E copy |
|---|---|---|---|---|---|
| S1 clean home, rows (want 50) | 50 | 50 | error "unable to open database file" | 50 (B path) | 50 |
| S1 files created | **`-wal` 0 B, `-shm` 32768 B** | none | **`-wal` 0 B** | none | none |
| S1 handles | db r, `-wal` u, `-shm` u | db r | | db r | none in the home |
| S2 live writer, 5 commits only in `-wal` (want 55) | 55 | **50 (stale)** | 55 | 55 (A path) | 55 |
| S2 files changed | `-shm` content (read marks) | none | none | `-shm` content | none |
| S2 handles | db r, `-wal` u, `-shm` u | db r | db r, `-wal` u, `-shm` r | db r, `-wal` u, `-shm` u | none in the home |
| S3 crashed writer left `-wal`/`-shm` (want 55) | 55 | **50 (stale)** | 55 | 55 | 55 |
| S3 files changed | `-shm` mtime and content (WAL-index recovery) | none | none | `-shm` mtime and content | none |

**S6, a reader open while the writer exits** (A and C):

- The writer cannot delete `-wal` and `-shm`, because it is not the last connection.
- They stay after the reader closes too, because a read-only connection cannot checkpoint or delete them.

**Conclusions from these runs:**

- The tester's finding is true: a plain `mode=ro` open creates `-wal` and `-shm` in a clean home.
- No SQLite URI flag avoids that and still reads the WAL.

### Churn: a writer commits and checkpoints while the reader loops

- "Bad" means a read that returned without an error but broke the invariant.
- "Writer txns" is the writer's throughput while that reader ran.

**Run 1** (6 s per cell, `writer.py`): one-row commits, `TRUNCATE` every 20 commits. In S5 the writer also closed and reopened every 7 commits.

| Cell | reads | ok | bad | errors or fallbacks |
|---|---|---|---|---|
| S4 A | 1515 | 1515 | 0 | 0 |
| S4 B | 759 | 393 | **57** | **309 "database disk image is malformed"** |
| S4 C | 1183 | 1109 | 0 | 74 "unable to open" |
| S4 D | 1168 | 1025 | 0 | 143 fallbacks |
| S4 E | 120 | 63 | 0 | 57 fallbacks |
| S5 A | 1653 | 1653 | 0 | 0 |
| S5 B | 1281 | 938 | **81** | **262 malformed** |
| S5 C | 2267 | 1742 | 0 | 525 |
| S5 D | 1792 | 1786 | 0 | 6 fallbacks |
| S5 E | 376 | 313 | 0 | 63 fallbacks |

**Run 2** (15 s per cell, `writer2.py`):

- 8-row transactions; every 5th transaction also rewrites 1/13 of the old rows.
- `PASSIVE` checkpoint every 10 transactions, `TRUNCATE` every 25.
- With oc=1, the writer also closes and reopens every 6 transactions.
- E3 cells ran for 20 s.

| Cell | reads | ok | bad | fallbacks | writer txns |
|---|---|---|---|---|---|
| oc=0 A | 1744 | 1744 | 0 | 0 | **731** |
| oc=0 B | 668 | 365 | **57** | 246 errors | 2431 |
| oc=0 D | 1366 | 1366 | 0 | 0 | **831** |
| oc=0 E | 203 | 161 | 0 | 42 | 2371 |
| oc=0 E2 | 222 | 90 | 0 | 132 | 2531 |
| oc=0 E3 | 124 | 39 | 0 | 85 | 3071 |
| oc=0 E3, 1 s timestamps | 120 | 19 | 0 | 101 | 3506 |
| oc=1 A | 1461 | 1461 | 0 | 0 | **796** |
| oc=1 B | 926 | 367 | **78** | 481 errors | 2331 |
| oc=1 D | 1412 | 1409 | 0 | 3 | **981** |
| oc=1 E | 218 | 144 | 0 | 74 | 2006 |
| oc=1 E2 | 248 | 76 | 0 | 172 | 2456 |
| oc=1 E3 | 134 | 31 | 0 | 103 | 2846 |
| oc=1 E3, 1 s timestamps | 137 | 34 | 0 | 103 | 3326 |

**Readings:**

- **B fails on an active database.** It returns stale rows, torn rows, and "malformed" errors.
- **A and D never returned a bad row.** But a tight loop of reads cut the writer's throughput to about a third. The likely cause: checkpoints wait for the readers' marks.
  - N5 reads far less often, so the real effect is small.
  - The copies (E, E2, E3) do not slow the writer.
- **The copies returned 0 bad rows in 970 accepted copies (376 in run 1, 594 in run 2).**
- **Under this extreme churn, E3 falls back on 70 to 85% of reads.** Codex checkpoints far less often (the default autocheckpoint is 1000 pages), but its real write rate is not measured. Rule 11 keeps the archived titles during a fallback.

### The GPT seat's attack on E2, replayed

The GPT seat (round 2) gave an event sequence that E2 accepts:

1. A checkpoint tears the main copy within one coarse timestamp tick.
2. E2 copies the `-wal` header of generation G.
3. The writer runs `TRUNCATE` and commits into a new generation G' at the same inode.

E2 compared only the copied salts, so it accepted. SQLite then ignores the G' frames and reads the torn main file.

The Lead replayed the sequence deterministically. A 1 s stat rounding emulates coarse timestamps: HFS+ (1 s), FAT and exFAT (2 s), some SMB mounts, and the Linux coarse clock (about 1 to 4 ms).

| Variant | Fine timestamps | 1 s timestamps |
|---|---|---|
| E2 | rejected (main mtime changed) | **accepted; `quick_check` "ok"; rows 2 and 19 = ("old", "new"), a state that never committed** |
| E3 | rejected: "source WAL salt changed" and "second read of main differs" | rejected, for the same two reasons |

E3 exists because of this attack.

## Text changes

### Plan §1.2

No change. E3 meets it as written.

### New plan decision row (§2)

| # | Decision | Source | Reason |
|---|---|---|---|
| D17 | N5 reads `state_5` from a checked private copy. No SQLite connection ever opens a file in the Codex home. | Derived (council N5, 2026-11-19) | A SQLite reader creates `-wal` and `-shm` in a clean Codex home (§1.2). `immutable=1` returns stale and torn rows while Codex writes. |

### Plan §3, a new bullet under "`state_5.sqlite.threads`"

> - **A SQLite reader leaves files.** In a home with no `-wal` and no `-shm`, `sqlite3.connect("file:...?mode=ro", uri=True)` creates `state_5.sqlite-wal` (0 B) and `state_5.sqlite-shm` (32 KiB) and leaves them after it closes. `immutable=1` creates nothing, but it misses WAL commits and returns torn rows or "malformed" while Codex writes. (Council N5, 2026-11-19, SQLite 3.47.1.)

### Plan §4.4, replace the "`state_5` access" bullet and the paragraph after it

> - **`state_5` access (D17):**
>   - Open only `state_5.sqlite` and, when present, `state_5.sqlite-wal`, read-only. Never open `-shm`, and never give SQLite a path in the Codex home.
>   - Copy them into a private temp folder (mode 0700, in the system temp folder, never `data_dir`), and open the copy.
>   - Accept the copy only if every check passes: two reads of the main file are equal; the main file and the WAL keep their identity; the WAL salts at the start, in the copy, and at the end are equal; and no `-journal` exists. Then `quick_check` and the column check must pass.
>   - Make up to 3 attempts in 250 ms in total. Do not copy more than 64 MiB.
>   - Never wait on it in discovery.
>
>   If it is missing, too large, changing on every attempt, or has an unknown schema, add a warning, and keep each thread's last archived `state_5` values. Use `session_meta` and the title chain only for a thread with no archived values. If the rollout set of a thread changed since those values, set `current_ambiguous: true`.

### Plan §5, replace the N5 bullet

> - **N5:** reading `state_5` changes no file in the fake home. A hash of every file (name, size, mtime, content) is the same before and after the read: with no writer, with an idle live writer, and after a crashed writer. No `-wal` or `-shm` file appears. A replay of a checkpoint and a WAL restart during the copy never gives a state that Codex did not commit.

### Plan §6.1, row N5, replace the last three sentences

Old:

> A missing, locked, or unknown `state_5` falls back with a warning. A failed open is retried once. No `-wal` or `-shm` file appears.

New:

> A missing, oversized, changing, or unknown `state_5` falls back with a warning and keeps the archived values. A rejected copy is retried, up to 3 attempts. No file in the fake home changes.

### Plan §8, a new risk bullet

> - **`state_5` fallbacks:** under heavy Codex writes, every copy attempt can fail. Titles then keep their archived values. If work-machine reports show frequent fallbacks, add a count of them (a number only) to the N6 report.

### Issue #37, replace the second safety bullet

Old:

> Any SQLite metadata reads use `mode=ro`, short bounded reads, and normal WAL-aware SQLite access—not `immutable=1` or raw copying of an active database. No migrations, journal-mode changes, checkpoints, or repair. An unavailable metadata database must not prevent reading independently usable rollouts; ambiguity about the current revision must be surfaced.

New:

> Any SQLite metadata read never creates, changes, or locks a file in the Codex home. Explorer opens `state_5.sqlite` and, when present, `state_5.sqlite-wal` read-only, never opens `state_5.sqlite-shm`, and never passes a Codex-home path to SQLite. It copies those files into a private temp folder and opens the copy. It accepts the copy only when every check passes: two reads of the main file are equal; the main file and the WAL keep their identity; the WAL salts at the start, in the copy, and at the end are equal; and no `-journal` file exists. Else it retries, and then falls back. Do not use `immutable=1`, `mode=ro` on the live file, or an unchecked copy. No migrations, journal-mode changes, checkpoints, or repair. An unavailable metadata database must not prevent reading independently usable rollouts; ambiguity about the current revision must be surfaced.

Also add a D17 line to the #37 "Update 2026-11-18: decisions and corrections" section. That keeps its rule "where a decision differs from the text below, the decision wins".

## Positions by round

| Round | Lead (Opus 5.5) | Gemini (`gemini-3.8-flash`) | GPT (`gpt-5.6-terra`) |
|---|---|---|---|
| 1 | E, hardened. Keep §1.2, change #37. | **E.** Keep §1.2, change #37. Use a stat-key cache and a Windows share-delete open. Flagged a WAL that appears during the main copy (the Lead rejected this: the snapshot is then only a few ms old and still consistent). | **A.** Relax §1.2 with a sidecar allowlist. Its reason: E's stat check is a heuristic, and a raw copy is not SQLite's supported path. Found a real bug: E never compared the `-wal` after the copy. |
| 2 | E2 (WAL salt and inode check, `quick_check`, startup cleanup). Answered the GPT flaw list: 2 accepted, 2 rejected. | No answer (503, then the timeout). | **Still A.** Gave the E2 attack sequence, which the Lead then replayed and confirmed. |
| 3 | E3 (end-salt check, double read of main, a fallback rule that keeps archived values). | **AGREE on all 13 rules, on T1 to T17 ("no additional tests required"), on the text changes, and on E3 overall.** It could not build a sequence that makes E3 accept a never-committed state. Its reasons: H1 = H2 catches a checkpoint during the window; a salt change catches an emptied or restarted WAL; with one generation, the copied WAL frames take priority over the main pages. | **Accepts E3.** Asked for 10 changes, all folded into the rules and text above: the proof correction (step 3), an inclusive deadline and a size cap, short-lived handles and the share mode, a cache TTL, hardened cleanup, the kept-values fallback, an idle writer in T2, more tests, the #37 wording, and §1.2 unchanged. |

**Why A lost.**

- In a clean home, A creates `-wal` and `-shm` and leaves them behind.
- It writes read marks into Codex's `-shm` and takes locks.
- In a tight loop, it cut the writer's throughput to about a third.
- It fails when the home folder is read-only and `-shm` is absent.
- Each of these breaks §1.2, a user-facing promise, and the AC6 test of the home hash.
- GPT accepted E3 after E3 closed its concrete attack and met §1.2 as written.

**Why D lost.** It still runs A whenever Codex is running, so it keeps A's side effects. Its existence check also has a race: if Codex closes between the check and the open, the open creates both files.

## Appendix: the replay of the GPT sequence

T4 can reuse this. It uses only a synthetic database in a temp folder.

```python
def stat_key(p, coarse):
    st = os.stat(p)
    r = (lambda t: t // 1_000_000_000) if coarse else (lambda t: t)
    return (st.st_ino, st.st_size, r(st.st_mtime_ns), r(st.st_ctime_ns))

# Setup: 20 rows of 3000 bytes ('old'), clean close, then a writer connection w with
# wal_autocheckpoint=0 commits T: UPDATE threads SET title='new' WHERE id IN (2, 19).
b_main, b_wal, b_salt = stat_key(db, coarse), stat_key(wal, coarse), salt(wal)
# Copy the first half of main (row 2, old). Hook: w runs wal_checkpoint(PASSIVE).
# Copy the rest (row 19, new).
# Copy the first 32 bytes of -wal (header of G). Hook: w runs wal_checkpoint(TRUNCATE),
# then commits UPDATE ... WHERE id = 10 (new generation G', same inode). Copy the rest.
# E2 accepts when: main stat equal, -wal inode equal, copied salt == b_salt.
# E3 also requires: source salt at the end == b_salt, and main read twice is equal.
```

The E2 result with `coarse=True`: accepted, `quick_check` "ok", rows `[(2, 'old'), (19, 'new')]`.
