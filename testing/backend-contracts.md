# Testing: Contracts, performance, and proof of execution

Part of [TESTING.md](../../TESTING.md). Read this file when: you pin a user-visible contract, add a performance budget, or report a run.

## 5.13 · User-observable contracts over implementation-pinned rules

A test that pins the **resolution rule** of an internal mechanism
("prefers server over localStorage", "prefers Pass A over Pass B",
"falls back to X then Y") protects the rule. A test that pins the
**user-observable contract** ("set value, simulate restart, see same
value") protects the user. The two are NOT equivalent. When the rule
is wrong for users, an implementation-pinned test silently ratifies
the bug.

**Incident**: the 2026-05-22 prefs bug. The `usePreferences` hook
resolved `value = serverValue ?? localValue ?? fallback` — server
wins. A test `'dual-read: prefers the server value over localStorage'`
asserted exactly this rule. When the server cached a stale value (a
stray tab, a Playwright run, a 500'd PATCH), every reload silently
overrode the user's most recent local choice. The user reported
"frontend keeps restarting in dark mode" — they kept changing it
back, and every reload kept reverting. The test that should have
caught this was instead protecting the bug: as long as the
resolution kept preferring server, the test stayed green, even
though the user-observable behavior was broken.

**Anti-pattern (what I wrote)**:

```typescript
it('prefers the server value over localStorage', async () => {
  installPrefsHandlers({ theme: 'dark' });
  window.localStorage.setItem('theme', JSON.stringify('sepia'));
  await waitFor(() => expect(result.current[0]).toBe('dark'));
});
```

This asserts the *resolution rule*. The rule was wrong. The test
protected the wrong rule.

**Pattern (what I should have written)**:

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

This asserts the *user-observable contract*. If the resolution rule
ever flips back to server-first, this test goes red — protecting the
user, not the implementation.

**Rule**: for any dual-write / dual-read / multi-source system, the
test suite MUST include user-observable persistence tests:

1. **Set → restart → read** (single source of truth). After
   `setValue(X)`, simulate a restart (unmount, fresh QueryClient,
   etc.), and assert `value === X`.
2. **Stores-disagree matrix** (multi-source). For two stores A and B,
   test all 4 cells:
   - Both empty → fallback wins
   - Only A set → A wins
   - Only B set → B wins
   - Both set, **different values** → the chosen authority wins, AND
     the test name says WHICH authority and WHY (e.g. "local-first:
     latest signal from this browser wins over stale server").
3. **Stale-store stress** (the failure mode I missed). Explicitly
   construct the state "stale store has X, user-recent store has Y"
   and assert Y wins. This is the regression test for the bug class.

**Smell to grep for in your own work**: test names like
`'prefers X over Y'`, `'falls back to Z'`, `'resolves to W'` are
implementation-pinned. If you wrote one, also write the user-
observable counterpart that says WHY the user wants that resolution.
If you can't articulate the user-observable WHY, the rule is probably
wrong.

**Codebase grep before shipping** any new resolution / fallback /
precedence code:

```bash
# Surface every test name that pins an implementation rule.
# Each match needs a paired user-observable test, or the rule is
# protected by a test that ratifies it instead of challenging it.
grep -rnE "it\(['\"](prefers|falls back|resolves to|wins over)" frontend/src
```

## 5.14 · Performance regressions need a user-observable budget test

`§5.13` argues that resolution-rule tests can ratify a bug. The same
trap exists for performance. A test that asserts "handler is sync def"
or "context value is memoized" protects an *implementation rule that
we believe correlates with performance*. It does not measure
performance.

**Incident**: the 2026-05-22 search-typing lag took five commits to
diagnose. Each of the first four addressed a real but secondary
problem. The dominant cost — 88 seconds of cumulative `longtask`
time per "snapshot" typing pass, blocking every keystroke debounce —
was visible in a 30-second DevTools recording on the real corpus,
but no recording was taken until commit five. Each intermediate
commit shipped a "rule" test (`test_handler_is_sync_def`,
`test_context_value_is_memoized`) that passed green while the user
re-reported the same symptom three more times. Full walk:
`PLANS/POSTMORTEM-search-typing-lag-2026-05-22.md`.

**Rule**: any commit whose message contains `perf(`, `fix(perf)`, or
addresses a user-reported "slow" or "laggy" symptom MUST be preceded
on the same branch by a *measurement commit* whose deliverable is:

1. A reproducer script or Playwright test that exercises the user-
   reported flow on a fixture sized to match the user's reality (not
   a 3-row synthetic).
2. A numeric measurement of the user's top-line metric. For
   browser-side perf this is `PerformanceObserver` Long Task total
   time, or `performance.mark()` deltas around the input event. For
   backend perf this is end-to-end wall time including serialization
   and transfer, on a realistic payload.
3. The number written into the commit message of the fix, with
   before/after.

If the perf fix lands without that number moving, the diagnosis is
wrong. Revert. Do not stack another fix on top.

**Concrete instrumentation snippet** (drop into any React app for
the duration of a perf hunt):

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

This costs four lines and answers every "is my fix actually working"
question for free.

**Rule, second clause**: any list-rendered component instantiated N
times (N ≥ 100) must NOT subscribe to a *churning* context — one
whose provider value-identity changes in response to user input.
`useContext` bypasses `React.memo`: in Fiber, context dependencies
are resolved during `beginWork` before the memo bailout check, so a
provider value-identity change forces every consumer to re-render
regardless of comparator. The list-owning parent must call
`useContext` once and thread the relevant fields as props.

Two carve-outs are allowed without changing the rule:

1. **Dispatch-only contexts** whose value is a stable function /
   setter (identity never changes across renders) cannot trigger
   the cascade and are safe.
2. **Provably stable contexts** wrapped in `useMemo([])` over a
   constant input (theme that only changes on full app remount) are
   safe in practice; rare full re-renders of 4000 rows on a deliberate
   theme flip are acceptable.

A static grep test pins the common-case violation, naming the
specific known-churning providers in this codebase:

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

The grep list is intentionally explicit rather than blanket-banning
`useContext`: stable dispatch-only contexts (e.g., the lightbox
opener) and never-changing config contexts remain legal.

**Smell to grep for in your own work**: a test named
`test_<thing>_is_<implementation_detail>` (`is_sync_def`,
`is_memoized`, `uses_threadpool`). For every such test, also write
the user-observable counterpart that says WHY the user wants that
detail. If you can't state the user-observable budget in numbers,
the rule is probably defending the bug.

**Why two clauses in one section**: the postmortem's five-commit
chain had ONE diagnostic technique (a 30-second `PerformanceObserver`
recording on the real corpus) that would have collapsed it to one
commit, and ONE specific antipattern (`useContext` in a list row)
that was the dominant cost. Either rule alone leaves a gap; together
they pin the failure mode end-to-end.

## 5.16 · A "passing" run must PROVE it executed (no piped exit codes, no silent non-execution)

§5.13–§5.15 catch tests that run but assert the wrong thing. This one
catches the layer below: a suite that reports success while running
**nothing**. Two misleading-greens stacked on 2026-06-01 and a
confident "the test suite passes" went to the user when it did not:

1. **A pipe swallowed the exit code.** `npx playwright test
   --reporter=line | tail -60` ran as a background job that reported
   `exit 0`. That `0` was *`tail`'s* status, not Playwright's — a
   shell pipeline's exit status is the LAST stage's. Playwright had
   actually failed. **Never pipe a test / type-check / lint command
   through `tail` / `head` / `grep` / `sed` when pass/fail matters.**
   Run it bare and read the output, redirect full output to a file and
   read the file, or force the runner's status to survive:
   `set -o pipefail` and/or `${PIPESTATUS[0]}` (bash),
   `$pipestatus[1]` (zsh). A background task's "exit 0" is meaningless
   when the command it ran was a pipeline.
2. **Files silently never ran.** 13 Playwright specs carried a
   duplicate `import { … withNetRetry … }` and threw
   `SyntaxError: Identifier 'withNetRetry' has already been declared`
   at parse time. Playwright skipped the unparseable files and the run
   still "completed." A parse error, an import/collection error, or a
   filter that matches nothing all produce **zero failures while
   exercising zero behavior**. `0 failed` is NOT `green`.

**The rule** — before telling anyone (including yourself) a suite is
green, prove it RAN:

- **Read the runner's own summary AND the real exit code.** pytest →
  `N passed`; vitest → `Test Files N passed`; Playwright → `N passed`
  with `0 failed` and zero `Error:` / `SyntaxError` lines anywhere in
  the output.
- **Check the COUNT against the known baseline** (this repo,
  2026-06-01: backend pytest **1139 passed / 1 skipped**; vitest
  **538 passed / 67 files**; Playwright **~441 tests**). A count that
  *drops* means something stopped being collected — investigate before
  declaring green; never assume "fewer tests = they were deleted."
  Keep these numbers current as the suites grow, so "the count
  dropped" stays a usable signal.
- **Count the test FILES on disk and confirm the runner collected
  exactly that many.** This is the baseline-free version of the check
  above — it self-references the current tree, so it catches silent
  non-execution even when you do not know the historical numbers. A
  parse / import / collection error drops a *whole file*, so
  `disk-file-count > collected` is the direct signal. Per runner
  (numbers current 2026-06-01):
  - **vitest** — `find frontend/src \( -name '*.test.ts' -o -name
    '*.test.tsx' \) | wc -l` (= **67**) must equal the `Test Files N
    passed (N)` number the reporter prints.
  - **Playwright** — `find frontend/e2e -name '*.spec.ts' | wc -l`
    (= **113**) must equal the file count Playwright reports in the
    `Total: N tests in M files` footer of `npx playwright test --list`
    (and `--list` *errors outright* on a parse-broken file, so a clean
    list whose `M` matches `find` proves every spec is collectable).
    Grab `M` with:
    `npx playwright test --list 2>&1 | sed -nE 's/^Total: [0-9]+ tests in ([0-9]+) files$/\1/p'`.
  - **pytest** — `find backend fetcher -name 'test_*.py' | wc -l`
    (= **142**) vs the unique-file count pytest collects:
    `uv run pytest --collect-only -q 2>&1 | grep -oE '^[^:]+\.py' | sort -u | wc -l`
    (= **141**). The expected delta is the **deliberately-deselected**
    files: the default `addopts = -m 'not serial'` drops the serial
    benchmark `backend/tests/test_search_index_benchmark.py`, so
    142 vs 141 is correct, not a gap.
  **Rule:** investigate *every* mismatch. A file legitimately excluded
  by a marker filter (`-m 'not serial'`), an `--ignore`, or a
  manual/benchmark gate is fine **once you have confirmed each one is
  deliberate** — keep the known-excluded list current so the expected
  delta is known. An *unexplained* drop means files never executed ⇒
  not green, regardless of what the pass line says.
- **Grep the raw output for non-execution tells:** `SyntaxError`,
  `Error:`, `Cannot find module`, `failed to load`, `collected 0`,
  `no tests ran`, `0 passed`, `did not run`. Any hit ⇒ not green.
- **Report only what you verified.** A confident "the suite passes"
  from an unread or piped result is a falsification event the moment
  it is wrong (per `feedback_never_accept_failing_tests`): correct it
  loudly and immediately; never let the false claim stand.

The corollary that earned this section: a dead suite does not merely
fail to catch *new* bugs — it **conceals** the ones already there.
Fixing the 13 parse errors un-hid 26 further pre-existing failures the
broken suite had masked for days (recovery plan:
`PLANS/2026.06.01-e2e-suite-recovery.md`).
