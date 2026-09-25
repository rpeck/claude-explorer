# Testing: Contracts, performance, and proof of execution

Part of [TESTING.md](../TESTING.md). Read this file when you do one of these tasks:

- Pin a user-visible contract.
- Add a performance budget.
- Report a test run.

## 5.13 · User-observable contracts over implementation-pinned rules

Two types of test give two different types of protection:

- A test that pins the **resolution rule** of an internal mechanism protects the rule.
  Examples: "prefers server over localStorage", "prefers Pass A over Pass B", "falls back to X then Y".
- A test that pins the **user-observable contract** protects the user.
  Example: "set value, simulate restart, see same value".

These two types are NOT equivalent.
If the rule is wrong for users, an implementation-pinned test silently approves the bug.

**Incident**: the 2026-05-22 prefs bug.

- The `usePreferences` hook resolved `value = serverValue ?? localValue ?? fallback`.
  Thus the server value won.
- A test `'dual-read: prefers the server value over localStorage'` asserted exactly this rule.
- Sometimes the server cached a stale value.
  Causes included a stray tab, a Playwright run, and a PATCH that failed with a 500 error.
- In that case, every reload silently overrode the most recent local choice of the user.
- The user reported "frontend keeps restarting in dark mode".
  The user changed the theme back again and again, and every reload reverted it.
- The test that should have caught this bug protected the bug instead.
  While the resolution preferred the server, the test stayed green.
  During all that time, the user-observable behavior was broken.

**Anti-pattern (the test that I wrote)**:

```typescript
it('prefers the server value over localStorage', async () => {
  installPrefsHandlers({ theme: 'dark' });
  window.localStorage.setItem('theme', JSON.stringify('sepia'));
  await waitFor(() => expect(result.current[0]).toBe('dark'));
});
```

This test asserts the *resolution rule*.
The rule was wrong, so the test protected a wrong rule.

**Pattern (the test that I should have written)**:

```typescript
it('regression: stale server does NOT clobber a user-recent local choice on reload', async () => {
  // User explicitly set 'light' in this browser (localStorage).
  // Server has a stale 'dark' from a previous session.
  // After GET resolves: the user's local choice MUST stick.
  installPrefsHandlers({ theme: 'dark' });
  window.localStorage.setItem('theme', JSON.stringify('light'));
  const { qc } = makeWrapper();
  // Gate on the GET completing — otherwise we'd be asserting the
  // initial render where serverValue is undefined regardless of rule.
  await waitFor(() => {
    expect(qc.getQueryData(['preferences'])).toBeDefined();
  });
  expect(result.current[0]).toBe('light');
});
```

This test asserts the *user-observable contract*.
If the resolution rule changes back to server-first, this test fails.
The test protects the user, not the implementation.

**Rule**: for every dual-write, dual-read, or multi-source system, the test suite MUST include these user-observable persistence tests:

1. **Set → restart → read** (single source of truth).
   - Call `setValue(X)`.
   - Simulate a restart (unmount, fresh QueryClient, etc.).
   - Assert `value === X`.
2. **Stores-disagree matrix** (multi-source).
   For two stores A and B, test all 4 cells:
   - Both empty → fallback wins.
   - Only A set → A wins.
   - Only B set → B wins.
   - Both set, **different values** → the chosen authority wins.
     The test name MUST also say WHICH authority wins and WHY.
     Example: "local-first: latest signal from this browser wins over stale server".
3. **Stale-store stress** (the failure mode that I missed).
   - Explicitly construct the state "stale store has X, user-recent store has Y".
   - Assert that Y wins.
   - This test is the regression test for the bug class.

**Smell to grep for in your own work**:

- Test names such as `'prefers X over Y'`, `'falls back to Z'`, and `'resolves to W'` are implementation-pinned.
- If you write one of these tests, also write the user-observable counterpart.
  The counterpart says WHY the user wants that resolution.
- If you cannot state the user-observable WHY, the rule is probably wrong.

**Codebase grep before shipping**: run this grep before you ship any new resolution, fallback, or precedence code.

```bash
# Surface every test name that pins an implementation rule.
# Each match needs a paired user-observable test, or the rule is
# protected by a test that ratifies it instead of challenging it.
grep -rnE "it\(['\"](prefers|falls back|resolves to|wins over)" frontend/src
```

## 5.14 · Performance regressions need a user-observable budget test

`§5.13` states that resolution-rule tests can approve a bug.
Performance tests have the same trap:

- Some tests assert an implementation rule, for example "handler is sync def" or "context value is memoized".
- Such a test protects an *implementation rule*.
  We believe that this rule correlates with performance.
- Such a test does not measure performance.

**Incident**: the 2026-05-22 search-typing lag.
The diagnosis took five commits.

- Each of the first four commits addressed a real but secondary problem.
- The dominant cost was 88 seconds of cumulative `longtask` time per "snapshot" typing pass.
  This cost blocked every keystroke debounce.
- A 30-second DevTools recording on the real corpus showed this cost.
  But nobody made a recording until commit five.
- Each intermediate commit shipped a "rule" test (`test_handler_is_sync_def`, `test_context_value_is_memoized`).
  These tests passed green.
  During that time, the user reported the same symptom three more times.
- Full analysis: `PLANS/POSTMORTEM-search-typing-lag-2026-05-22.md`.

**Rule**: some commits MUST have a *measurement commit* before them on the same branch.
The rule applies to any commit that does one of these things:

- Its message contains `perf(` or `fix(perf)`.
- It addresses a user-reported "slow" or "laggy" symptom.

The measurement commit must deliver these items:

1. A reproducer script or Playwright test.
   It exercises the user-reported flow on a fixture sized to match the reality of the user (not a 3-row synthetic).
2. A numeric measurement of the top-line metric of the user.
   - For browser-side perf, use `PerformanceObserver` Long Task total time, or `performance.mark()` deltas around the input event.
   - For backend perf, use end-to-end wall time on a realistic payload.
     Include serialization and transfer.
3. The number in the commit message of the fix, with the before and after values.

If the perf fix lands and that number does not move, the diagnosis is wrong.
Revert the fix.
Do not stack another fix on top.

**Concrete instrumentation snippet**: add this snippet to any React app for the duration of a perf hunt.

```typescript
useEffect(() => {
  const obs = new PerformanceObserver(list => {
    let total = 0
    for (const e of list.getEntries()) total += e.duration
    if (total > 50) console.log(`[longtask] +${total.toFixed(0)}ms`)
  })
  obs.observe({ entryTypes: ['longtask'] })
  return () => obs.disconnect()
}, [])
```

This snippet costs four lines.
It answers every "is my fix actually working" question at no cost.

**Rule, second clause**: a list-rendered component instantiated N times (N ≥ 100) must NOT subscribe to a *churning* context.

- A churning context is a context whose provider value-identity changes in response to user input.
- `useContext` bypasses `React.memo`.
  In Fiber, React resolves context dependencies during `beginWork`, before the memo bailout check.
- Thus a change of provider value-identity forces every consumer to re-render, regardless of the comparator.
- The list-owning parent must call `useContext` once and pass the relevant fields as props.

The rule allows two carve-outs.
The carve-outs do not change the rule:

1. **Dispatch-only contexts** are safe.
   The value of such a context is a stable function or setter, and its identity never changes across renders.
   Thus the context cannot trigger the cascade.
2. **Provably stable contexts** are safe in practice.
   The code wraps such a context in `useMemo([])` over a constant input (for example, a theme that only changes on a full app remount).
   A rare full re-render of 4000 rows on a deliberate theme flip is acceptable.

A static grep test pins the common-case violation.
The test names the specific known-churning providers in this codebase:

```typescript
it('MessageBubble does not subscribe to a churning context', () => {
  const src = readFileSync(
    'frontend/src/components/message/MessageBubble.tsx',
    'utf8',
  )
  // These three providers' values change on every keystroke / toggle.
  // Subscribing here would re-render all 4K bubbles per input event.
  expect(src).not.toMatch(/use(Settings|SearchPanel|Bookmarks)\b/)
})
```

The grep list is intentionally explicit.
It does not ban all uses of `useContext`.
Stable dispatch-only contexts (e.g., the lightbox opener) and never-changing config contexts remain legal.

**Smell to grep for in your own work**: a test named `test_<thing>_is_<implementation_detail>` (`is_sync_def`, `is_memoized`, `uses_threadpool`).

- For every such test, also write the user-observable counterpart.
  The counterpart says WHY the user wants that detail.
- If you cannot state the user-observable budget in numbers, the rule probably defends the bug.

**Why two clauses in one section**: two facts from the five-commit chain in the postmortem explain this.

- ONE diagnostic technique could reduce the chain to one commit: a 30-second `PerformanceObserver` recording on the real corpus.
- ONE specific antipattern was the dominant cost: `useContext` in a list row.

Each rule alone leaves a gap.
Together, the two rules pin the failure mode end-to-end.

## 5.16 · A "passing" run must PROVE it executed (no piped exit codes, no silent non-execution)

§5.13, §5.14, and [§5.15](playwright.md) catch tests that run but assert the wrong thing.
This section catches the layer below: a suite that reports success but runs **nothing**.
On 2026-06-01, two misleading green results stacked.
As a result, the user got a confident "the test suite passes", but the suite did not pass:

1. **A pipe hid the exit code.**
   - `npx playwright test --reporter=line | tail -60` ran as a background job.
     The job reported `exit 0`.
   - That `0` was the status of *`tail`*, not the status of Playwright.
     The exit status of a shell pipeline is the status of the LAST stage.
   - Playwright actually failed.
   - **Never pipe a test, type-check, or lint command through `tail` / `head` / `grep` / `sed` when pass/fail matters.**
     Use one of these methods instead:
     - Run the command bare and read the output.
     - Redirect the full output to a file and read the file.
     - Force the status of the runner to survive: `set -o pipefail` and/or `${PIPESTATUS[0]}` (bash), `$pipestatus[1]` (zsh).
   - If a background task ran a pipeline, its "exit 0" means nothing.
2. **Files silently never ran.**
   - 13 Playwright specs had a duplicate `import { … withNetRetry … }`.
   - At parse time, these specs threw `SyntaxError: Identifier 'withNetRetry' has already been declared`.
   - Playwright skipped the unparseable files, and the run still "completed."
   - Each of these conditions produces **zero failures while it exercises zero behavior**:
     - a parse error
     - an import or collection error
     - a filter that matches nothing
   - `0 failed` is NOT `green`.

**The rule**: before you tell anyone (including yourself) that a suite is green, prove that it RAN.

- **Read the summary of the runner AND the real exit code.**
  - pytest → `N passed`.
  - vitest → `Test Files N passed`.
  - Playwright → `N passed` with `0 failed`, and zero `Error:` / `SyntaxError` lines anywhere in the output.
- **Compare the COUNT with the known baseline.**
  - The baseline numbers are in [§0](../TESTING.md). They are kept in that one place only.
  - If the count *drops*, the runner stopped collecting something.
    Investigate before you declare green.
  - Never assume "fewer tests = they were deleted."
  - Update the numbers in §0 as the suites grow.
    Then "the count dropped" stays a usable signal.
- **Count the test FILES on disk. Confirm that the runner collected exactly that many.**
  - This check is the baseline-free version of the previous check.
    It uses the current tree as its own reference.
    Thus it catches silent non-execution even when you do not know the historical numbers.
  - A parse, import, or collection error drops a *whole file*.
    Thus `disk-file-count > collected` is the direct signal.
  - The check for each runner follows. [§0](../TESTING.md) has the current numbers.
    - **vitest**: `find frontend/src \( -name '*.test.ts' -o -name '*.test.tsx' \) | wc -l` must equal the total in parentheses in `Test Files N passed (N)`. If a file fails, the reporter prints `M failed | N passed (T)`, and `T` is still the total.
    - **Playwright**: `find frontend/e2e -name '*.spec.ts' | wc -l` must equal the file count in the `Total: N tests in M files` footer of `npx playwright test --list`.
      - On a parse-broken file, `--list` *fails outright*.
        Thus a clean list whose `M` matches `find` proves that every spec is collectable.
      - Get `M` with this command:
        `npx playwright test --list 2>&1 | sed -nE 's/^Total: [0-9]+ tests in ([0-9]+) files$/\1/p'`.
    - **pytest**: compare `find backend fetcher mcp_server -name 'test_*.py' | wc -l` with the unique-file count that pytest collects.
      - Get the collected count with this command:
        `uv run pytest --collect-only -q 2>&1 | grep -oE '^[^:]+\.py' | sort -u | wc -l`.
      - The expected difference is the **deliberately-deselected** files.
      - The default `addopts = -m 'not serial'` drops the serial benchmark `backend/tests/test_search_index_benchmark.py`.
        Thus a difference of one file is correct and is not a gap.
  - **Rule:** investigate *every* mismatch.
    - A file can be legitimately excluded by one of these:
      - a marker filter (`-m 'not serial'`)
      - an `--ignore`
      - a manual or benchmark gate
    - Such an exclusion is fine **after you confirm that each one is deliberate**.
    - Keep the known-excluded list current, so that you know the expected difference.
    - An *unexplained* drop means that some files never executed.
      Then the run is not green, regardless of what the pass line says.
- **Grep the raw output for signs of non-execution.**
  Any hit on one of these strings means that the run is not green:
  - `SyntaxError`
  - `Error:`
  - `Cannot find module`
  - `failed to load`
  - `collected 0`
  - `no tests ran`
  - `0 passed`
  - `did not run`
- **Report only what you verified.**
  - A confident "the suite passes" from an unread or piped result is a falsification event at the moment it is wrong.
  - If this happens, correct the claim loudly and immediately.
  - Never let the false claim stand.

The corollary behind this section: a dead suite does more than fail to catch *new* bugs.
It **conceals** the bugs that are already there.

- The fix for the 13 parse errors exposed 26 more pre-existing failures.
- The broken suite hid those failures for days.
- Recovery plan: `PLANS/2026.06.01-e2e-suite-recovery.md`.
