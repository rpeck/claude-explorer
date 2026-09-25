# Testing: Principles, fixtures, and review

Part of [TESTING.md](../../TESTING.md). Read this file when: you write or review any test.

## 1 · Black-box, spec-driven discipline

When the same session writes both the feature and its tests, the tests
silently encode the implementation's quirks instead of verifying the
contract. Two real outcomes from this codebase:

- **CFR1 (filter v2 redesign).** Tests written alongside the impl
  asserted on `data-testid` everywhere. The shipped impl rendered
  Behavior + Mode + Match radios as `<button aria-pressed>` instead
  of `role="radio"`. The same agent's tests passed because they
  used the test-ids; an a11y-aware contract test would have failed.
- **2026-05-07 trash-icon regression.** The original canary asserted
  `toBeVisible()` + a row-anchored bounding-box check. Both passed
  even when the trash button was clipped by an `overflow: hidden`
  ancestor. The test was tuned to "the impl renders something" and
  not to "the user can see/click it".

### The contract

The UI contract lives in `UX.md`. The API contract lives in the
Pydantic models under `backend/models.py` and the FastAPI route
signatures. Backend test contracts also live in the OpenAPI shape
each route produces. Tests verify those — not the implementation.

### Selector priority (UI tests)

1. `getByRole('button', { name: /…/i })`
2. `getByLabel(/…/i)`
3. `getByPlaceholder(/…/i)`
4. `getByText(/…/i)` *(prefer the above; this catches non-interactive elements)*
5. `data-testid="…"` ONLY when the spec dictates a test-id

`getByRole` is load-bearing because it forces the implementation to be
accessible. If you find yourself reaching for `data-testid` because
`getByRole` "doesn't work", that is a finding to surface — the impl
likely shipped a div-with-onclick where the spec wanted a real button,
or a `<button aria-pressed>` where the spec said "radio". Don't
silently route around it; report.

### Spec-driven test files

For non-trivial features, write a small set of `spec-*.spec.ts` tests
derived from the spec **alone** — no implementation reads while
writing. These sit alongside the implementation-coupled tests and
catch contract drift those tests can't see.

Files in this codebase that use this pattern:

- `frontend/e2e/spec-filters-*.spec.ts` (54 tests; covers UX.md §615-738).

Add new `spec-*.spec.ts` files for new features. The "no app code
reads" rule is a discipline, not an enforcement — keep an explicit
allowlist of files you may consult while writing the spec test
(usually `UX.md`, the relevant plan doc, `frontend/e2e/fixtures.ts`,
and `frontend/src/lib/types.ts`). Read no others.

## 2 · Bidirectional verification

A new test must demonstrate BOTH:

1. It passes against the correct implementation, AND
2. It FAILS against a deliberately-broken implementation.

If you can't make it fail by reverting the fix, the test is asserting
something the bug doesn't violate.

The 2026-05-07 trash canary "passed on first run" — that should have
been a red flag that the assertions were too lax. Four separate
assertions (`toHaveCount` + `toBeVisible` + `toBeInViewport` +
row-anchored bounding-box) all passed even with the trash button
visually clipped. The fix: rewrote the canary with a real
clip-ancestor check, then verified bidirectionally — passed against
the fix, FAILED against the reverted-fix state.

### Workflow for bug-fix commits

1. **Reproduce the bug live first.** Take a screenshot. Note the
   actual broken state — not what you assume the bug is.
2. **Write the failing test FIRST.** Run it; verify it fails.
   Verify it fails *for the right reason* (read the failure message;
   if it's "selector not found" but the bug is "selector clipped",
   the test is targeting the wrong thing).
3. **Fix the code.** Run the test; verify it passes.
4. **Revert the fix temporarily** (`git stash` or `git revert
   --no-commit`); re-run the test; confirm it fails again with the
   informative message you'd want to see in the future. Re-apply the
   fix.

For non-bug-fix changes, write the test against the spec FIRST, fix
any spec drift the test surfaces, THEN ship. Same bidirectional rule.

### "Tests pass" proves nothing on its own

Always pair a green run with at least one falsification: run the test
in isolation against a known-broken state, OR have the test fail in CI
on a parallel branch that intentionally regressed the behavior. If the
test never fails, it never tested anything.

## 4 · Test fixture design

Use realistic edge-case data, not minimal happy-path data. Each
fixture should answer the question: "what's the most likely thing the
user has that breaks the layout / logic?"

### Long strings

For any UI that can show user-entered text (filter names,
conversation titles, project paths, attachment names), include at
least one fixture whose string is long enough to trigger
truncation, overflow, or wrap. A short name doesn't reproduce layout
failures.

The 2026-05-07 row-clip bug shipped because the canary used
`"Foo filter"` (12 chars) instead of something like
`"automated run of a scheduled task"` (33 chars). The Radix
`display: table; min-width: 100%` wrapper grew past 100% only when
content forced it — short names never triggered the wrapper to
overflow.

When in doubt, include a name ≥30 characters. If the impl uses
`truncate`, that's a hint that long strings exist in the wild;
include them in tests.

### Many items

For any list, scroll-area, or quantifier (group members,
conversations, search hits), include enough items to trigger
scroll, pagination, or virtualization paths. Two items don't test
overflow; ten or fifty often do.

### Empty state

Every list and every dependent input has an empty case. Test it.
The "Manage filters with zero filters" test (in
`spec-filters-active-picker.spec.ts`) was added in the spec-driven
sweep precisely because this case was easy to forget.

### Migration / legacy state

When shipping a schema migration, seed the fixture with the on-disk
shape USERS WILL HAVE, not the new shape. Otherwise the migration
code never runs in the test.

For the v1→v2 filter migration:

```ts
preferences: {
  // legacy v1 shape — has polarity, no behavior, no _migratedV2
  filters: {
    nodes: { 'a': { id: 'a', type: 'atom', name: 'X',
                    enabled: true, polarity: 'exclude',
                    patterns: ['*X*'], mode: 'glob', target: 'title' } },
    activeId: 'a',
    _migratedV1: true,
  },
}
```

Then assert the post-migration shape (with `behavior: 'hide'`,
`_migratedV2: true`) was PATCHed back to the server.

### Special characters

Test names / patterns containing spaces, `*`, regex metas, Unicode,
line breaks, leading/trailing whitespace. Pattern-matching code is
where these bite first; UI rendering is where they bite second.

For the `name` field specifically, include a fixture with `*` in
the name (which the auto-fill rule's metachar-strip would otherwise
remove — useful for testing that the strip behaves as documented).

### Fixture seeding rule

Build the smallest fixture that reproduces the failure mode you're
testing. Don't reuse another spec's fixture by import — that ties
two tests' definitions together and makes failures harder to read.
Build clean fixtures from the spec.

## 6 · Test review checklist

Before declaring a new test sufficient, confirm:

### Universal (UI + backend)

- [ ] Bidirectional verification: the test fails when the fix is
      reverted, with an informative error message. ("Test passes"
      proves nothing; can you make it fail?)
- [ ] Test name names the contract, not the impl. ("Manage Filters
      modal: every row exposes a visible, in-viewport, NOT-clipped
      delete affordance" — not "trash icon visible".)
- [ ] At least one fixture exercises an edge case (long string, many
      items, special chars), not just the happy path.
- [ ] Spec docs (`UX.md` for UI, the relevant model / route docstring
      for backend) updated to match any new contract the test
      asserts.
- [ ] Negative-space assertion when the contract has one: assert
      what should NOT change, not just what should.

### UI / Playwright

- [ ] Selector uses `getByRole`/`getByLabel` first; `data-testid` only
      where spec dictates.
- [ ] Visibility tests use `expectInsideClipAncestor` (or equivalent)
      when the assertion is "user can see this".
- [ ] An actionability check (`hover`/`click`) cross-tests
      reachability where it matters.
- [ ] Strict-mode locator: every `getBy*` query is unambiguous, OR
      explicitly scoped/`.first()`d.
- [ ] PATCH/route spies are registered AFTER `mockBackend` for LIFO
      precedence.

### Backend / pytest

- [ ] Test seeds the LEGACY shape (what users have on disk), not the
      new shape, when migration code is under test. Otherwise the
      migration code never runs.
- [ ] Real `tmp_path` for filesystem ops; no mocking the store /
      writer / serializer layer. Mock at the HTTP boundary or the
      filesystem boundary, not in between.
- [ ] Strong value assertion (not just "field exists"). If a field is
      hardcoded by design, the test asserts the meaningful expected
      value computed from a known fixture.
- [ ] Async test uses `async def` + `await` AND the pytest config
      surfaces "coroutine was never awaited" as a failure
      (`-W error::RuntimeWarning`).
- [ ] `lru_cache.cache_clear()` called after `monkeypatch.setenv` for
      any settings/config function that's cached.
- [ ] Module-level singletons (`_refresh_in_progress`, `_seen` sets,
      in-memory caches) reset per test via fixture.
- [ ] Migration test asserts: (a) post-migration on-disk shape; (b)
      tombstone keys explicitly nulled; (c) idempotency (running
      twice is a no-op); (d) sentinel flag set.
- [ ] SSE tests assert event ORDER + types + payload shape +
      termination; never just `status_code == 200`.
- [ ] Concurrency test where a lock or atomic op is part of the
      contract.
- [ ] Security-adjacent input test for every route taking a path /
      URL / pattern / external input (path traversal, symlinks,
      permission bits, regex DoS).
- [ ] For PDF / image / binary output: assert against a known fixture
      byte signature, not "≥1 stream present".
- [ ] Status code asserted EXACTLY (not "2xx") and at least one
      error path tested explicitly.

## Reference incidents

These are the bugs that produced this document. Read the linked
commits before adding a new section.

### UI / Playwright

| Date | Class | Root cause | Fix |
|---|---|---|---|
| 2026-05-07 | overflow-clipping false-pass | `toBeVisible` + row-anchored bbox blind to ancestor `overflow: hidden`; tame fixtures (short names) didn't reproduce | `8cb85fd` (impl), `0f29d6f` (canary upgrade with `expectInsideClipAncestor`) |
| 2026-05-07 | role-blind selectors hid a11y drift | tests used `data-testid` everywhere; CFR1 shipped Behavior/Mode/Match as `button aria-pressed` instead of `role=radio`; tests passed | `e2190cf` (impl: real ARIA roles); spec-driven sweep caught it |
| 2026-05-06 | filter Pin desync | seeding logic ran once on first mount, decoupled `pinned` from `activeFilterIds`; tests passed in fixture mode (empty initial state) | `2c94860` (composable graph + sidebar picker) |

### Backend / pytest

| Date | Class | Root cause | Fix |
|---|---|---|---|
| 2026-05-05 | weak-assertion false-pass on PDF images | "≥1 image stream present" passed even when WeasyPrint emitted broken-image-icon streams; fixture image bytes were never checked end-to-end | `37e45e0` (P5: WeasyPrint url_fetcher + byte-signature test against a fixture image) |
| 2026-05-05 | regex stripped TOOL_PLACEHOLDER inside fenced code blocks | "strip works outside fences" tested only the positive path; missing negative-space assertion (preserved-inside-fence) | `ff7db06` (impl: fenced-aware strip); council caught during review |
| 2026-05-05 | `/api/preferences` PATCH-deep-merge needs real on-disk round-trip | a mock-the-write test asserts the body that goes IN, not what lands on disk after the read-merge-write cycle | `a8cff17` (impl uses real tmp_path tests; per-key overwrite verified end-to-end) |
| 2026-05-07 | weak existence assertion on `conversation_count` | `assert "conversation_count" in data` passed for weeks while the field was hardcoded to `0`; only tested presence, not semantic value | `74de39d` (refactor: dropped misleading hardcoded field; tests now assert exact value via `/config/stats`) |
| 2026-05-07 | migration tombstone-keys must be explicit `null` in PATCH | omitting `savedFilters` and `activeFilterIds` from the PATCH leaves them on disk because backend uses per-key overwrite, not deep-delete | `2c94860` migration test asserts the PATCH body explicitly contains `savedFilters: null, activeFilterIds: null` |
| 2026-05-08 | `/api/attachments` path traversal — read-leak | `file_dir = _attachments_root() / conv_uuid / file_uuid` had no validation before `is_dir()`; the downstream `chosen.resolve().relative_to(file_dir.resolve())` only validates the FINAL chosen file. `conv_uuid="../../etc"` and absolute-path injection (`Path("a") / "/abs" == Path("/abs")`) both fell through to a 200 with arbitrary on-disk file bytes when a `<variant>.*` glob matched | `e121e39` (RED: 3 traversal tests) + `1135f61` (GREEN: `file_dir.resolve().relative_to(_attachments_root().resolve())` 400-on-escape) — RED→GREEN two-commit pattern |
| 2026-05-08 | atomic-write `.tmp` leak on `os.replace` failure | `_write_atomic` (preferences.py) and `_write_all` (bookmarks.py) didn't wrap the rename in try/finally; if `os.replace` raised, the `.tmp` was orphaned in the user's `~/.claude-explorer/` dir. No data corruption (the original file is preserved by `os.replace` atomicity) but disk leaked across failed writes | `0955f29` — try/except BaseException + `tmp.unlink()` cleanup (FileNotFoundError-tolerant) + re-raise. Test pattern: monkeypatch `os.replace` to raise OSError, assert `pytest.raises` + filesystem invariants (original byte-identical + no `*.tmp` glob) |
| 2026-05-08 | `DEFAULT_CREDENTIALS_PATH` value-imported in 4 modules, not 2 or 3 | `fetcher/credentials.py` defines it; `fetcher/bulk_fetch.py`, `backend/routers/fetch.py`, AND `backend/routers/orgs.py` each `from … import` it by value at module load. A test that only patches the canonical name leaves three handlers reading the user's real `~/.claude-explorer/credentials.json`. Discovered while implementing P4.2 (orgs corrupt-creds test) | `ea6781b` — conftest `_isolated_credentials_path` patches all 4 bindings; pattern documented in §5.1 ("constants imported by value need patching at every call site") |

Add to the appropriate sub-table when you ship a fix that surfaced a testing-discipline gap. The "class" column should name the FAILURE MODE, not the feature; the goal is to make the next agent recognize the same shape if it appears in a different feature.
