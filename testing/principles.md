# Testing: Principles, fixtures, and review

Part of [TESTING.md](../TESTING.md). Read this file when you write or review a test.

## 1 · Black-box, spec-driven discipline

If the same session writes a feature and its tests, the tests often record the quirks of the implementation. They do not verify the contract. This codebase has two real examples.

- **CFR1 (filter v2 redesign).**
  - The tests that the session wrote together with the implementation used `data-testid` in all assertions.
  - The shipped implementation showed the Behavior, Mode, and Match radios as `<button aria-pressed>`, not as `role="radio"`.
  - The tests from the same agent passed because they used the test IDs.
  - An a11y-aware contract test fails on this implementation.
- **2026-05-07 trash-icon regression.**
  - The original canary asserted `toBeVisible()` and a row-anchored bounding-box check.
  - Both assertions passed when an ancestor with `overflow: hidden` clipped the trash button.
  - The test checked that "the impl renders something". It did not check that "the user can see/click it".

### The contract

Tests verify the contract. They do not verify the implementation. The contract comes from these sources:

- The UI contract is in `UX.md`.
- The API contract is in the Pydantic models in `backend/models.py` and in the FastAPI route signatures.
- The backend test contract also includes the OpenAPI shape that each route produces.

### Selector priority (UI tests)

Use the selectors in this order:

1. `getByRole('button', { name: /…/i })`
2. `getByLabel(/…/i)`
3. `getByPlaceholder(/…/i)`
4. `getByText(/…/i)` *(prefer the selectors above; this selector finds non-interactive elements)*
5. `data-testid="…"`: use it ONLY when the spec dictates a test ID.

`getByRole` is important because it forces the implementation to be accessible.

- If `getByRole` "doesn't work" and you want to use `data-testid`, report this as a finding.
- The cause is probably one of these implementation errors:
  - A div with an onclick handler, where the spec wanted a real button.
  - A `<button aria-pressed>`, where the spec said "radio".
- Do not silently work around the problem. Report it.

### Spec-driven test files

For non-trivial features, write a small set of `spec-*.spec.ts` tests.

- Write these tests from the spec **alone**.
- Do not read the implementation while you write them.
- These tests sit next to the implementation-coupled tests.
- They find contract drift that the implementation-coupled tests cannot see.

These files in this codebase use this pattern:

- `frontend/e2e/spec-filters-*.spec.ts` (54 tests; covers the "Composable filters (named title filters)" section of UX.md).

Add new `spec-*.spec.ts` files for new features.

The "no app code reads" rule is a discipline. No tool enforces it. Keep an explicit allowlist of the files that you can read while you write the spec test. The usual allowlist is:

- `UX.md`
- the relevant plan doc
- `frontend/e2e/fixtures.ts`
- `frontend/src/lib/types.ts`

Read no other files.

## 2 · Bidirectional verification

A new test MUST show both of these results:

1. It passes against the correct implementation.
2. It FAILS against a deliberately broken implementation.

If the test does not fail when you revert the fix, the test asserts something that the bug does not violate.

The 2026-05-07 trash canary "passed on first run". That result was a red flag: the assertions were too lax. Four separate assertions passed while the trash button was visually clipped:

- `toHaveCount`
- `toBeVisible`
- `toBeInViewport`
- a row-anchored bounding-box check

The fix:

- Rewrote the canary with a real clip-ancestor check.
- Verified the canary bidirectionally. The canary passed against the fix. It FAILED against the reverted-fix state.

### Workflow for bug-fix commits

1. **Reproduce the bug live first.**
   - Take a screenshot.
   - Record the actual broken state. Do not record what you assume the bug is.
2. **Write the failing test FIRST.**
   - Run the test. Verify that it fails.
   - Verify that it fails *for the right reason*. Read the failure message.
   - If the message is "selector not found" but the bug is "selector clipped", the test targets the wrong thing.
3. **Fix the code.** Run the test. Verify that it passes.
4. **Revert the fix temporarily** with `git stash` or `git revert
   --no-commit`.
   - Run the test again.
   - Confirm that it fails again with the informative message that you want to see in the future.
   - Apply the fix again.

For changes that are not bug fixes, use this order:

1. Write the test against the spec FIRST.
2. Fix any spec drift that the test shows.
3. THEN ship.

The same bidirectional rule applies.

### "Tests pass" proves nothing on its own

Always pair a green run with at least one falsification. Use one of these methods:

- Run the test in isolation against a known-broken state.
- Make the test fail in CI on a parallel branch that intentionally regressed the behavior.

If the test never fails, it never tested anything.

## 4 · Test fixture design

Use realistic edge-case data. Do not use minimal happy-path data. Make each fixture answer this question: "what is the most likely thing that the user has, which breaks the layout or the logic?"

### Long strings

Some UI shows text that the user entered. Examples:

- filter names
- conversation titles
- project paths
- attachment names

For each such UI, include at least one fixture with a long string. The string must be long enough to cause truncation, overflow, or wrap. A short name does not reproduce layout failures.

The 2026-05-07 row-clip bug shipped because the canary used a short name.

- The canary used `"Foo filter"` (12 chars).
- It did not use a longer name such as `"automated run of a scheduled task"` (33 chars).
- The Radix `display: table; min-width: 100%` wrapper grew past 100% only when the content forced it.
- Short names never caused the wrapper to overflow.

If you are not sure, include a name of ≥30 characters. If the implementation uses `truncate`, long strings probably exist in real user data. Include them in tests.

### Many items

Some UI shows a list, a scroll area, or a quantifier. Examples:

- group members
- conversations
- search hits

For this UI, include enough items to start the scroll, pagination, or virtualization code paths. Two items do not test overflow. Ten or fifty items often do.

### Empty state

Every list and every dependent input has an empty case. Test it.

The spec-driven sweep added the "Manage filters with zero filters" test (in `spec-filters-active-picker.spec.ts`). The sweep added this test because this case was easy to forget.

### Migration / legacy state

When you ship a schema migration, seed the fixture with the on-disk shape that USERS HAVE. Do not seed it with the new shape. If you use the new shape, the migration code never runs in the test.

For the v1→v2 filter migration, use this fixture:

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

Then assert that a PATCH sent the post-migration shape back to the server. The post-migration shape has `behavior: 'hide'` and `_migratedV2: true`.

### Special characters

Test names and patterns that contain these characters:

- spaces
- `*`
- regex metacharacters
- Unicode
- line breaks
- leading or trailing whitespace

Pattern-matching code is the first place where these characters cause failures. UI rendering is the second place.

For the `name` field, include a fixture with `*` in the name.

- The metachar-strip step of the auto-fill rule otherwise removes this character.
- This fixture is useful to test that the strip step behaves as documented.

### Fixture seeding rule

Build the smallest fixture that reproduces the failure mode that you test.

- Do not import a fixture from another spec.
- An import ties the definitions of two tests together. It also makes failures harder to read.
- Build clean fixtures from the spec.

## 6 · Test review checklist

Before you decide that a new test is sufficient, confirm these items.

### Universal (UI + backend)

- [ ] Bidirectional verification: the test fails when you revert the fix, and the error message is informative. ("Test passes" proves nothing. Can you make it fail?)
- [ ] The test name names the contract, not the implementation.
  - Good name: "Manage Filters modal: every row exposes a visible, in-viewport, NOT-clipped delete affordance".
  - Bad name: "trash icon visible".
- [ ] At least one fixture tests an edge case, not only the happy path. Examples: a long string, many items, special characters.
- [ ] The spec docs match any new contract that the test asserts.
  - For UI, the spec doc is `UX.md`.
  - For backend, the spec doc is the relevant model or route docstring.
- [ ] If the contract has a negative-space assertion, the test includes it. Assert what should NOT change, not only what should change.

### UI / Playwright

- [ ] The selector uses `getByRole` or `getByLabel` first. It uses `data-testid` only where the spec dictates.
- [ ] If the assertion is "user can see this", the visibility test uses `expectInsideClipAncestor` (or an equivalent).
- [ ] Where reachability matters, an actionability check (`hover`/`click`) also tests it.
- [ ] Strict-mode locator: every `getBy*` query is unambiguous, OR it has an explicit scope or `.first()`.
- [ ] Register PATCH and route spies AFTER `mockBackend`. This order gives them LIFO precedence.

### Backend / pytest

- [ ] When the test covers migration code, it seeds the LEGACY shape (what users have on disk), not the new shape. Otherwise the migration code never runs.
- [ ] Use a real `tmp_path` for filesystem operations.
  - Do not mock the store, writer, or serializer layer.
  - Mock at the HTTP boundary or at the filesystem boundary, not between them.
- [ ] The test makes a strong value assertion, not only a "field exists" check. If the code hardcodes a field by design, the test asserts the meaningful expected value that comes from a known fixture.
- [ ] An async test uses `async def` and `await`. The pytest config also reports "coroutine was never awaited" as a failure (`-W error::RuntimeWarning`).
- [ ] Call `lru_cache.cache_clear()` after `monkeypatch.setenv` for any cached settings or config function.
- [ ] A fixture resets module-level singletons for each test. Examples: `_refresh_in_progress`, `_seen` sets, in-memory caches.
- [ ] A migration test asserts these results:
  - (a) the post-migration on-disk shape;
  - (b) tombstone keys explicitly set to null;
  - (c) idempotency (a second run is a no-op);
  - (d) the sentinel flag is set.
- [ ] SSE tests assert event ORDER, types, payload shape, and termination. They never assert only `status_code == 200`.
- [ ] Include a concurrency test where a lock or an atomic operation is part of the contract.
- [ ] Every route that takes a path, URL, pattern, or other external input has a security-adjacent input test. Examples: path traversal, symlinks, permission bits, regex DoS.
- [ ] For PDF, image, or binary output, assert against the byte signature of a known fixture. Do not assert only "≥1 stream present".
- [ ] Assert the status code EXACTLY, not "2xx". Test at least one error path explicitly.

## Reference incidents

These bugs produced this document. Before you add a new section, read the commits in the Fix column.

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
| 2026-05-08 | `DEFAULT_CREDENTIALS_PATH` value-imported in 4 modules, not 2 or 3 | `fetcher/credentials.py` defines it; `fetcher/bulk_fetch.py`, `backend/routers/fetch.py`, AND `backend/routers/orgs.py` each `from … import` it by value at module load. A test that only patches the canonical name leaves three handlers reading the user's real `~/.claude-explorer/credentials.json`. Discovered while implementing P4.2 (orgs corrupt-creds test) | `ea6781b` — conftest `_isolated_credentials_path` patches all 4 bindings; pattern documented in [§5.1](backend-isolation.md) ("constants imported by value need patching at every call site") |

When you ship a fix for a bug that showed a gap in testing discipline, add the bug to the correct sub-table.

- In the "class" column, name the FAILURE MODE, not the feature.
- The goal: the next agent recognizes the same failure shape when it appears in a different feature.
