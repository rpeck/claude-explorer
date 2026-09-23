# Postmortem — Search typing lag, 2026-05-22 → 2026-05-23

## Problem

On the user's 16K-message corpus (4014 rendered `MessageBubble` nodes),
typing into the SearchPanel input produced multi-second main-thread Long
Tasks that:

- Blocked the 200 ms debounce timer from firing on schedule.
- Stalled keystrokes ("type three letters, wait, see the rest appear").
- Held Cmd+F open for ~3 s before the input gained focus.
- Caused the user to report 10 s of "nothing happens" before the
  backend even saw the request.

The user was right. The backend was idle. The browser was at 100 % CPU.

Total elapsed: five commits over ~18 hours, in chronological order:

| When | Commit | One-liner |
|---|---|---|
| Fri 15:00 | `e0cc917` | Snippet/Full toggle Long Task — per-key select + memoized `SettingsContext` value |
| Fri 16:28 | `7623c12` | Drop `async` from `/api/search` handlers |
| Fri 17:06 | `4f4a03e` | Explicit `cancelQueries` on `debouncedQuery` change |
| Fri 18:44 | `6cb5192` | Backend disconnect-bail + memoize `SearchPanelContext` value |
| Sat 09:09 | `266b9c8` | Kill two render storms (`useContext` leak + per-bubble `searchQuery`) |

Of these, only `266b9c8` actually killed the dominant Long Task the
user was reporting. `e0cc917` killed a *different* dominant Long Task
(the Snippet/Full toggle) on Friday afternoon and is treated as
prior-art in this postmortem rather than a missed attempt. The
intermediate three (`7623c12`, `4f4a03e`, `6cb5192`-frontend-half) all
addressed real but secondary problems; the user kept reporting the
same typing-lag symptom because the dominant cost was never measured
directly until Saturday morning.

This document walks each commit, names the false belief that drove it,
states the evidence that should have falsified it earlier, and proposes
a concrete rule that would have collapsed the chain into one or two
commits.

---

## Timeline

### Commit `7623c12` — "drop async from /api/search handlers"

**Belief**: "Search feels synchronous because it IS synchronous on the
wire — the handler is `async def` but calls a sync function inline,
freezing the event loop."

**What was true**: Real. The GET and POST `/api/search` handlers were
`async def` wrapping a sync `search_conversations(...)` call. FastAPI
ran them on the asyncio loop, so concurrent searches from multiple tabs
serialized through one backend, and one slow search blocked every other
endpoint. Dropping `async def` so FastAPI auto-routes them to its anyio
threadpool was correct on its own merits.

**Why it didn't fix the user's pain**: Backend warm-path latency
was ~140 ms. The Long Tasks the user was reporting were measured later
at **88 s of cumulative main-thread time per "snapshot" typing pass** —
560× the backend time. The async-handler fix removed a real ceiling
on backend concurrency under multi-tab load, but the user is one user
with one tab. The bottleneck was never on the backend.

**Falsifiable belief that was wrong**: "The user's reported typing lag is
caused by event-loop blocking in the backend."

**Evidence that should have falsified it on day one**:
1. Add a single `performance.mark()` pair around the `fetch()` call in
   `useSearch.ts`. Watch the dev console. If the fetch starts within
   a few ms of debounce settle and returns within a few hundred ms but
   the input still lags, the bottleneck is the renderer, not the wire.
2. Open Chrome DevTools → Performance → record one keystroke. Sort by
   Total Time. If the top frame is `MessageBubble` rendering or any
   React reconciliation, the backend cannot be at fault.

**Rule that would have prevented the cycle**: **Measure the user's
top-line metric in the user's browser before changing any backend
code.** Total Long Task time, captured live in the actual app on the
actual corpus, is the only number that maps to the user's reported
feeling. If you fix something and that number doesn't move, you fixed
something else.

This is the failure mode `TESTING.md §5.13` warns about. The
test we wrote ("handler is sync def, runs on threadpool, three
concurrent searches finish in parallel") asserts an *implementation
rule*. It does not assert the *user-observable contract* ("typing the
word 'snapshot' emits less than N seconds of cumulative Long Task
time on the 16K corpus"). The rule was right; pinning it didn't help
the user.

---

### Commit `4f4a03e` — "explicit cancelQueries on debouncedQuery change"

**Belief**: "Now that the wire is unblocked, the remaining lag is
abandoned in-flight queries piling up because React Query v5's default
`gcTime: 5min` lets a fetch run to completion after its observer rebinds
to a new key."

**What was true**: Also real. The prior keystroke's fetch did keep
running so the cache could be primed. For a typing input where the prior
key (`q='aardvar'`) will essentially never be re-observed, that's
wasted backend CPU and a held threadpool slot.

**Why it didn't fix the user's pain**: Same reason as `7623c12`. The
backend was already responding well within the debounce window. Saving
backend CPU is good hygiene; it does not move the renderer's frame
budget.

**Falsifiable belief**: "Cancelling abandoned queries will reduce
end-to-end typing lag."

**Evidence that should have falsified it**: After this commit, the user
reported the same symptom in the same shape. That, on its own, should
have been treated as a falsification event, not a "must be another
backend thing" event.

**Rule**: **A user re-reporting the same symptom after a fix shipped
is a falsification event for the diagnosis, not the implementation.**
The next move is to re-instrument and re-measure, not to ship a second
fix in the same suspected layer.

---

### Commit `6cb5192` — "memoize SearchPanelContext value"

This commit shipped two independent fixes: a backend disconnect-bail
on `/api/search` (so abandoned client requests stop wasting threadpool
slots) and the frontend `SearchPanelContext` memoization. The backend
half is orthogonal to the typing-lag root cause and is not analyzed
here; only the frontend half is on the critical path.

**Belief, part 1**: "The Long Tasks I'm now seeing on the frontend come
from `SearchPanelProvider` returning an inline `value={{ ... }}` object
literal — every keystroke rebuilds the value identity, notifies every
`useSearchPanel()` consumer, and `ConversationPage` walks its
20K-MessageBubble `.map()` through the reconciler."

**What was true**: The inline-literal context value WAS a bug. Wrapping
it in `useMemo` was a direct port of the pattern already known good for
`SettingsContext` (`e0cc917`, two days earlier). After this commit,
typing did get measurably faster. The user noticed.

**Why it didn't fix the user's pain**: Because there was a second, much
larger render storm hiding behind it — one that this commit didn't
touch and didn't measure. The memoized context value stops the
PROVIDER from re-rendering when its value identity stays stable, but it
does not stop the CONSUMERS from re-rendering when a different prop or
context they depend on flips. Two such cascades survived:

1. `MessageBubble` was a `useContext(SettingsContext)` consumer for
   `showToolCalls` and `expandAllTools`. Pressing Cmd+F dispatched
   `setRightPaneTab('search')`, which mutated SettingsContext's value
   identity. All 4014 bubbles re-rendered synchronously.
2. `ConversationPage` was threading the `searchQuery` deferred value as
   a prop to EVERY `MessageBubble`, with the memo comparator including
   `searchQuery`. Every debounce settle invalidated every bubble's
   memo, every `MarkdownRenderer` re-walked its AST and re-wrapped
   tokens in `<mark>`.

**Falsifiable belief**: "The dominant Long Task source is
SearchPanelProvider's value-identity churn."

**Evidence that should have falsified it earlier**: A counter incremented
inside `MessageBubble`'s render function would have shown 4014 renders
per keystroke. That's a four-line patch. Adding it to MessageBubble was
exactly what `e0cc917`'s diagnosis section says was done for the
Snippet/Full toggle — and that diagnosis happened *the day before*. The
same instrument, applied to typing, would have found the same render
storm.

**Rule**: **When you find one render storm via a render-counter, leave
the counter in for the next session.** The same scaffolding will find
the next storm immediately. Removing the counter is throwing away the
diagnostic that just paid for itself.

---

### Commit `266b9c8` — "kill two render storms"

This is the commit that actually fixed the user's reported symptom.

Two root causes found, both via the same technique: a `PerformanceObserver`
for `longtask` entries combined with a render-counter on `MessageBubble`,
run live against the real corpus.

1. **`useContext(SettingsContext)` in MessageBubble bypassed `React.memo`**.
   React invalidates every context consumer on provider value-identity
   change, ignoring the memo comparator entirely. Pressing Cmd+F mutated
   SettingsContext's value (it lives in the same provider as
   `setRightPaneTab`), so all 4014 bubbles re-rendered. Fix: remove the
   context subscription, accept `showToolCalls` and `expandAllTools` as
   props, thread them from `ConversationPage` (which already calls
   `useSettings()` at the top), include them in the memo comparator.

2. **`searchQuery` threaded as a prop to every bubble**. The memo
   comparator failed for all 4014 bubbles on each debounce settle. The
   fix: gate `searchQuery` on `message.uuid === highlightMessageId`.
   Only the actively-navigated bubble (URL `?highlight=<uuid>`) re-
   renders when the query changes. The SearchPanel sidebar still lists
   every match with its own highlights — the in-bubble `<mark>` only
   needs to track the one bubble you scrolled to.

Empirical result: `typing 'snapshot' total Long Task time 88,517 ms →
11,004 ms` (−87.6%). `Cmd+F focus latency 851 ms → 4-8 ms` (−99%).
`prepend "that " max single Long Task 14,120 ms → 379 ms` (−97.3%).

The fix is real and the numbers are large. But the diagnosis took four
commits longer than it should have.

---

### A note on `useDeferredValue`

Earlier in the chain, `useDeferredValue` was introduced to push the
`<mark>`-rewrap work off the typing critical path. Empirically it
deferred the work to a later tick but kept it on the main thread, so
the same Long Tasks fired one frame later. The user still felt the
lag, just shifted in time.

**Belief**: "`useDeferredValue` will prevent the bubble walk from
blocking input."

**Reality**: It only de-prioritizes the work relative to a higher-
priority update. The work still has to happen, still on the main
thread, still synchronously. The next high-priority update (the next
keystroke) gets the same Long Task to walk through before it can
commit.

**Rule**: **Measure Total Long Task time before and after, on the same
corpus, in the same browser.** "It feels smoother" is not a signal
when the next keystroke is going to land on top of 8 seconds of queued
reconciliation. `useDeferredValue` defers; it does not eliminate.

---

## Cross-cutting lessons

Three patterns repeat across all five commits.

### Lesson 1: Don't fix until you've reproduced and measured

Each of the first four commits shipped a real, defensible fix for a
real, observable problem. None of them targeted the dominant cost.
The dominant cost was visible from the start in any 30-second
DevTools Performance recording on the real corpus, but the recording
was never taken until commit five.

**Concrete rule**: For any user report shaped like "X feels slow",
the first commit on the branch must be a *measurement commit* — even
if it lands no code. The output is a one-line number ("typing
'snapshot' = 88 s of Long Task on the real corpus") that the next
commits have to move. If a fix doesn't move that number, it isn't
the fix.

### Lesson 2: `useContext` bypasses `React.memo`. Always.

This caught us on `e0cc917` (the Snippet/Full toggle, two days before
the typing-lag chain even started) and again on `266b9c8`. Both
incidents had the same shape: a "settings" provider's value-identity
changed for an unrelated reason, every leaf component that subscribed
to that provider re-rendered, even the ones whose `React.memo`
comparator was airtight.

**Concrete rule (`TESTING.md §5.14` proposal below)**: any
component that is rendered N times in a list (N ≥ 100) must NOT call
`useContext` on a *churning* provider — that is, a provider whose
value identity changes in response to user interaction (toggles,
typing, navigation). The list-owning parent calls `useContext` once
and threads the relevant fields as props. Two narrow carve-outs are
allowed:

1. **Dispatch-only context**: a context whose value is a stable
   `dispatch` / setter function created once, identity never changes.
   This category cannot trigger the bypass-memo cost because the
   value reference is referentially equal across all renders.
2. **Provably stable provider**: a context whose provider value is
   wrapped in `useMemo([])` over a constant input (e.g., a theme
   that only changes on a full app remount). Re-rendering 4000 rows
   on a rare theme flip is fine; re-rendering them on every keystroke
   is not.

A static grep test pins the common-case violation: no
`useSettings()` / `useSearchPanel()` / `useBookmarks()` import inside
`MessageBubble.tsx` or any file imported by a virtualized list row,
because those providers' values are known to churn on user input.

### Lesson 3: "Implementation rule" tests protected the bug

`TESTING.md §5.13` codifies this: a test that pins HOW the
system works ("handler is sync def", "context value is memoized")
protects the rule. A test that pins WHAT the user observes ("typing
the test query on the test corpus emits ≤ N ms of Long Task time")
protects the user. The two are not equivalent.

The perf-chain commits added structural tests (`handler is sync def`,
`MessageBubble does not import useSettings`). Those tests are valuable
as regression guards going forward, but the *user-observable*
counterpart — a Playwright test that drives typing on a 4K-bubble
fixture and asserts on `PerformanceObserver` Long Task totals — was
never written. If it had been written first, RED, it would have
exposed all four false starts in minutes.

---

## Proposed `TESTING.md §5.14` patch

A patch for `TESTING.md`, slotted between §5.13 and §6.

```markdown
### 5.14 · Performance regressions need a user-observable budget test

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
re-reported the same symptom three more times.

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
```

---

## What I would do differently

If the user reports a perf symptom tomorrow, the first commit on the
branch is a measurement commit. The deliverable is one number
captured live in the actual app on the actual corpus. Every
subsequent commit has to move that number or it gets reverted. No
more "this looks like it must help" patches.

The render-counter and `PerformanceObserver` snippets above are
cheap enough that there is no excuse for not running them on every
perf incident. They would have collapsed this chain from five
commits to one — and saved the user from re-reporting the same
symptom three times to be told "this should be better now."
