# Testing — Claude Explorer

This guide is the single home for testing in this project. Every agent and
every contributor starts here, whatever tool they use.

## How to use this guide

The guide is split into short topic files, so that you read only what your
task needs.

1. Read §0 in this file. It applies to every test run.
2. Find your task in the table below.
3. Open the file in that row.

Each section keeps its number. Test docstrings and comments across the
codebase cite sections as `TESTING.md §5.14`. Use the table to find the file
for any cited number.

## Where each section lives

| § | Topic | File | Read it when |
|---|---|---|---|
| §0 | Running the tests, and trusting the result | this file, below | Always, before you report a test result. |
| §1 | Black-box, spec-driven discipline | [principles.md](testing/principles.md) | You write or review any test. |
| §2 | Bidirectional verification | [principles.md](testing/principles.md) | You write or review any test. |
| §3 | Playwright-specific gotchas | [playwright.md](testing/playwright.md) | You write an end-to-end spec. |
| §4 | Test fixture design | [principles.md](testing/principles.md) | You write a fixture. |
| §5 | Backend test discipline (introduction) | [backend-isolation.md](testing/backend-isolation.md) | You write a backend test. |
| §5.1 | Test isolation: lru_cache, env vars, module singletons, time | [backend-isolation.md](testing/backend-isolation.md) | A test touches settings, caches, singletons, or time. |
| §5.2 | Mock at the boundary, not the nesting | [backend-isolation.md](testing/backend-isolation.md) | You mock anything. |
| §5.3 | Strong assertions, not "field exists" | [backend-isolation.md](testing/backend-isolation.md) | You write an assertion. |
| §5.4 | Negative-space assertions | [backend-isolation.md](testing/backend-isolation.md) | You test what must not happen. |
| §5.5 | Migration tests MUST seed the legacy shape | [backend-isolation.md](testing/backend-isolation.md) | You test a data migration. |
| §5.6 | SSE streaming tests | [backend-behaviors.md](testing/backend-behaviors.md) | You test an SSE endpoint. |
| §5.7 | Realistic data sizes | [backend-behaviors.md](testing/backend-behaviors.md) | You choose fixture sizes. |
| §5.8 | Concurrency and atomic-op tests | [backend-behaviors.md](testing/backend-behaviors.md) | You test locks, races, or atomic writes. |
| §5.9 | Security-adjacent inputs | [backend-behaviors.md](testing/backend-behaviors.md) | You test paths, IDs, or untrusted input. |
| §5.10 | Async / await pitfalls | [backend-behaviors.md](testing/backend-behaviors.md) | You test async code. |
| §5.11 | Pydantic / FastAPI specifics | [backend-behaviors.md](testing/backend-behaviors.md) | You test models or routes. |
| §5.12 | Monkeypatching: prefer attribute-patch over value-binding | [backend-isolation.md](testing/backend-isolation.md) | You monkeypatch. |
| §5.13 | User-observable contracts over implementation-pinned rules | [backend-contracts.md](testing/backend-contracts.md) | You decide what a test should pin. |
| §5.14 | Performance regressions need a user-observable budget test | [backend-contracts.md](testing/backend-contracts.md) | You fix or guard a performance problem. |
| §5.15 | E2E tests MUST assert zero unexpected console errors / warnings | [playwright.md](testing/playwright.md) | You write an end-to-end spec. |
| §5.16 | A "passing" run must PROVE it executed | [backend-contracts.md](testing/backend-contracts.md) | You report that a suite passed. |
| §5.17 | Specs with a local `mockBackend(page)` MUST mock `/api/preferences` | [playwright.md](testing/playwright.md) | A spec mocks the backend. |
| §6 | Test review checklist | [principles.md](testing/principles.md) | You review a test change. |
| §7 | What CI proves | [ci.md](testing/ci.md) | You change CI, or rely on it. |
| §8 | Verifying each platform by hand | [manual-checks.md](testing/manual-checks.md) | You verify a release by hand. |
| §9 | Protecting credentials on every platform | [ci.md](testing/ci.md) | You touch credential storage. |
| §10 | What cannot be automated, and why | [ci.md](testing/ci.md) | You plan a new check. |
| — | Reference incidents | [principles.md](testing/principles.md) | You add a new rule to this guide. |

---

## 0 · Running the tests, and trusting the result

Run each suite from the repository root:

```bash
uv run pytest                        # backend, fetcher, MCP server; parallel
uv run pytest -n 0                   # the same suite, serially
cd frontend && npx vitest run        # frontend unit tests
cd frontend && npx playwright test   # end-to-end tests
```

**Run the Python suite serially from time to time.** Parallel execution hides
order-dependent failures.

- Before the fix, two such bugs passed under pytest-xdist. They failed only
  when the suite ran serially.
- On 2026-09-23, the suite passed in three orders:
  - parallel
  - serial
  - reversed

### Test-execution integrity (hard invariant)

**Test-execution integrity is a HARD invariant.** The project added this rule
on 2026-06-01. On 2026-06-01, a confident report said "the suite passes", but 13
spec files did not run at all.

A run is not "green" until you verify that it actually executed. Use the *real*
exit code of the runner to verify it.

1. **Never pipe a test, type-check, or lint command through `tail`, `head`, or
   `grep` when pass/fail matters.**
   - The exit status of a shell pipeline is the exit status of the LAST stage.
   - Thus `pytest … | tail` returns the `0` of `tail`, even when the runner
     failed.
   - A backgrounded `playwright test … | tail` does the same.
   - Use one of these methods instead:
     - Run the command bare, and read the output.
     - Redirect the full output to a file, and read the file.
     - Keep the runner status through the pipeline: `set -o pipefail`,
       `${PIPESTATUS[0]}` in bash, or `$pipestatus[1]` in zsh.
2. **"0 failed" is not "green". Verify the COUNT.**
   - A parse, import, or collection error can make a suite "succeed" while it
     tests nothing. An empty filter can do the same.
   - Example: on 2026-06-01, 13 Playwright specs threw `SyntaxError` at parse
     time, and the run still "completed".
   - Before you report green, confirm that the pass count of the runner is at
     or above the known baseline:
     - Python pytest: **1299 passed / 2 skipped** (measured again on
       2026-09-23).
     - vitest: **538 passed / 67 files**.
     - Playwright: **~441 tests**.
   - Before you report green, also grep the output for each of these strings:
     - `SyntaxError`
     - `Error:`
     - `no tests ran`
     - `collected 0`
   - A count that *dropped* is a failure signal. It does not mean "tests were
     removed."
   - **Then do the baseline-free file-count check. Count the test files on disk
     independently, and confirm that the runner collected exactly that many.**
     - A parse or collection error drops a whole file silently.
     - Thus, a disk file count larger than the collected count is the direct
       sign of this problem.
   - Examples (numbers current on 2026-06-01):
     - vitest: `find frontend/src \( -name '*.test.ts' -o -name '*.test.tsx' \) | wc -l`
       (= 67) must equal the `Test Files N passed (N)` count.
     - Playwright: `find frontend/e2e -name '*.spec.ts' | wc -l` (= 113) must
       equal the `M` in the `Total: N tests in M files` footer of
       `npx playwright test --list`. The `--list` option fails with an error
       on a file that does not parse.
     - pytest: compare `find backend fetcher mcp_server -name 'test_*.py' | wc -l`
       (= 173, 2026-09-23) with the unique files from
       `uv run pytest --collect-only -q -n 0` (= 172).
       - The **expected** difference is the serial benchmark, deselected on
         purpose: `-m 'not serial'` drops
         `backend/tests/test_search_index_benchmark.py`.
   - Investigate every mismatch. Confirm that each excluded file is
     intentional.
   - An unexplained drop means that some files never ran. The run is not
     green.
3. **Report only verified results.**
   - Never tell the user that the suite passes from a background "exit 0" or
     from a truncated tail.
   - First, read the summary line and the real exit code.
   - A green claim that later proves false is a falsification event. Correct it
     immediately, and state the correction clearly and prominently.
   - Full detail: [§5.16](testing/backend-contracts.md).

---
