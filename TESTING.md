# Testing — Claude Explorer

This is the single home for testing in this project. Every agent and every
contributor starts here, whatever tool they use.

## Contents

- §0 Running the tests, and trusting the result
- §1 to §6 Writing tests: rules earned from real bugs
- §7 What CI proves
- §8 Verifying each platform by hand
- §9 Protecting credentials on every platform
- §10 What cannot be automated, and why
- Reference incidents

Sections §1 to §6 keep their numbers. Test docstrings across the codebase
cite them as `TESTING.md §5.14` and similar.

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
order-dependent failures. Two such bugs passed under pytest-xdist and failed
only serially before they were fixed. On 2026-09-23 the suite passed in three
orders: parallel, serial, and reversed.

### Test-execution integrity (hard invariant)

**Test-execution integrity (HARD invariant — earned 2026-06-01, when a confident "the suite passes" was reported while 13 spec files weren't even running).** A run is not "green" until you have verified it actually executed, using the runner's *real* exit code:

1. **Never pipe a test / type-check / lint command through `tail` / `head` / `grep` when pass/fail matters.** A shell pipeline's exit status is the LAST stage's, so `pytest … | tail` (or a backgrounded `playwright test … | tail`) returns `tail`'s `0` even when the runner failed. Run it bare and read the output, redirect full output to a file and read the file, or force the status to survive (`set -o pipefail`, `${PIPESTATUS[0]}` in bash, `$pipestatus[1]` in zsh).
2. **"0 failed" is not "green" — verify the COUNT.** A parse/import/collection error or an empty filter makes a suite "succeed" while testing nothing (on 2026-06-01, 13 Playwright specs threw `SyntaxError` at parse time and the run still "completed"). Confirm the runner's pass count is at/above the known baseline — Python pytest **1299 passed / 2 skipped** (re-measured 2026-09-23), vitest **538 passed / 67 files**, Playwright **~441 tests** — and grep the output for `SyntaxError` / `Error:` / `no tests ran` / `collected 0` before reporting green. A count that *dropped* is a failure signal, not "tests were removed." **Then run the baseline-free file-count check: independently count the test files on disk and confirm the runner collected exactly that many.** A parse/collection error drops a whole file silently, so disk-file-count > collected is the direct tell. Examples (numbers current 2026-06-01) — vitest: `find frontend/src \( -name '*.test.ts' -o -name '*.test.tsx' \) | wc -l` (= 67) must equal the `Test Files N passed (N)` count; Playwright: `find frontend/e2e -name '*.spec.ts' | wc -l` (= 113) must equal the `M` in `npx playwright test --list`'s `Total: N tests in M files` footer (`--list` errors outright on a parse-broken file); pytest: `find backend fetcher mcp_server -name 'test_*.py' | wc -l` (= 173, 2026-09-23) vs the unique files from `uv run pytest --collect-only -q -n 0` (= 172), where the **expected** delta is the deliberately-deselected serial benchmark (`-m 'not serial'` drops `backend/tests/test_search_index_benchmark.py`). Investigate every mismatch and confirm each excluded file is intentional; an unexplained drop means files never ran — not green.
3. **Report only verified results.** Never tell the user the suite passes from a background "exit 0" or a truncated tail; read the summary line and the real exit code first. A green claim that turns out false is a falsification event — correct it loudly and immediately. Full detail: [TESTING.md §5.16](./TESTING.md).

---

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

---

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

---

## 3 · Playwright-specific gotchas

These bit us. Encode them now so the next agent doesn't relearn them.

### `toBeVisible()` does NOT detect ancestor clipping

Playwright's `toBeVisible()` definition: non-empty bounding box +
`display !== 'none'` + `visibility !== 'hidden'`. An ancestor's
`overflow: hidden` doesn't change any of those — the element's own box
remains non-empty and its computed style is unchanged.

`toBeInViewport()` checks intersection with the **browser viewport**,
not an inner scroll container. Same blind spot.

A row-anchored bounding-box check (button-inside-row) doesn't help
either: when the row itself is clipped by the same ancestor, both row
and button are inside the row's logical box but both are clipped
together.

**Fix: use a helper that walks up to the nearest
`overflow:hidden|auto|scroll|clip` ancestor and asserts containment.**

```ts
async function expectInsideClipAncestor(target: Locator, label: string) {
  const result = await target.evaluate((el) => {
    const t = el.getBoundingClientRect()
    let n: Element | null = el.parentElement
    while (n) {
      const cs = getComputedStyle(n)
      const isClippy = (v: string) =>
        v === 'hidden' || v === 'auto' || v === 'scroll' || v === 'clip'
      if (isClippy(cs.overflowX) || isClippy(cs.overflowY)) {
        const r = n.getBoundingClientRect()
        return { t: { x: t.left, y: t.top, w: t.width, h: t.height },
                 a: { x: r.left, y: r.top, w: r.width, h: r.height,
                      tag: n.tagName,
                      cls: typeof n.className === 'string' ? n.className.slice(0, 80) : '',
                      ox: cs.overflowX, oy: cs.overflowY } }
      }
      n = n.parentElement
    }
    return { t: { x: t.left, y: t.top, w: t.width, h: t.height }, a: null }
  })
  expect(result.a, `${label}: no overflow-clipping ancestor found`).not.toBeNull()
  const t = result.t, a = result.a!, eps = 1
  expect(t.x, `${label}: clipped on the left by ${a.tag}.${a.cls}`).toBeGreaterThanOrEqual(a.x - eps)
  expect(t.x + t.w, `${label}: clipped on the right by ${a.tag}.${a.cls} (overflow-x: ${a.ox})`).toBeLessThanOrEqual(a.x + a.w + eps)
  expect(t.y, `${label}: clipped on the top by ${a.tag}`).toBeGreaterThanOrEqual(a.y - eps)
  expect(t.y + t.h, `${label}: clipped on the bottom by ${a.tag} (overflow-y: ${a.oy})`).toBeLessThanOrEqual(a.y + a.h + eps)
}
```

The reference implementation lives in
`frontend/e2e/spec-filters-trash-visible.spec.ts`. If you need it in
multiple specs, factor it into a shared helper at `frontend/e2e/helpers/clipAncestor.ts`.

### Add `.hover()` or `.click()` for actionability cross-checks

Playwright's actionability includes "element is at the click point".
A clipped element fails this. Adding a `.hover()` after the static
assertions catches the "user can reach this" property end-to-end.

```ts
await deleteButton.hover({ timeout: 2000 })
```

Use this on every test that asserts "the user can interact with X".
It's cheap and orthogonal to the static checks.

### shadcn `<Select>` quirks

- The Select trigger renders as `role="button"`, NOT `role="combobox"`.
  Prefer `getByLabel(/…/i)` for the trigger. The
  `data-testid="active-filter-select"` is the only acceptable test-id
  fallback (the spec names the picker structure unambiguously).
- Options live in a Portal with mount animations. Always:
  ```ts
  await trigger.click()
  await expect(page.getByRole('option', { name: /…/i })).toBeVisible()
  await page.getByRole('option', { name: /…/i }).click()
  ```
  Bare `.click()` on options races the mount.

### Radix `<ScrollArea>` quirks

- Radix `<ScrollArea>` Viewport wraps content in
  `style="display: table; min-width: 100%"` which auto-sizes to
  content width and lets rows overflow past the Viewport's bounded
  width. The outer `overflow: hidden` then clips the right end.
- Fix at the use site: append `[&>div>div]:!block` to the
  ScrollArea's `className`. The arbitrary-selector override forces
  the Radix wrapper to `display: block` so it inherits the Viewport's
  bounded width.
- See the `ManageFiltersModal.tsx` ScrollArea for the canonical
  application + comment.

### Radix `<RadioGroup>` `.check()` races controlled-component re-renders

- Playwright's `.check()` clicks the radio AND asserts
  `aria-checked="true"` before returning. Radix `<RadioGroupItem>`
  flips `aria-checked` only once the parent `<RadioGroup>`'s `value`
  prop changes, which requires the consumer's `onValueChange` handler
  to fire, the React state setter to run, and a re-render to land.
  When the setter routes through TanStack Query's `useMutation` (e.g.
  `usePreferences`), the re-render lands on a microtask that often
  loses the race against `.check()`'s post-assertion under
  parallel-worker load.
- Symptom: `Error: locator.check: Clicking the checkbox did not
  change its state`, with the locator log showing
  `aria-checked="false" data-state="unchecked"` AFTER the click
  action succeeded. The test passes on retry, so it surfaces as
  "flaky" not "failed". Easy to miss until the suite runs
  often enough that the retry budget runs out.
- Fix: use `.click()` and verify state via the durable side effect
  you actually care about (the PATCH body, a downstream DOM change,
  or a post-`waitForResponse` aria-checked assertion). Pattern:
  ```ts
  // WRONG: races the controlled-component update
  await radioGroup.getByRole('radio', { name: 'Bundle Obsidian' }).check()

  // RIGHT: click, then verify via the durable signal
  const patch = page.waitForResponse((r) =>
    r.url().endsWith('/api/preferences') && r.request().method() === 'PATCH'
  )
  await radioGroup.getByRole('radio', { name: 'Bundle Obsidian' }).click()
  await patch
  await expect(radioGroup.getByRole('radio', { name: 'Bundle Obsidian' })).toBeChecked()
  ```
- The rule applies to every Radix primitive wrapping a controlled
  component whose state setter runs asynchronously (`RadioGroup`,
  `Switch`, controlled `Checkbox`). Native HTML
  `<input type="checkbox">` updates synchronously and stays safe with
  `.check()`. When the radio's state lives in plain `useState` with
  no async mutation, `.check()` may work, but `.click()` plus a
  post-assertion is the lower-foot-gun default.
- Project sites already on the fix: `preferences-cross-context.spec.ts`,
  `settings.spec.ts`, `markdown-export-mode-unified.spec.ts`.

### Strict-mode locator collisions

Playwright runs locators in strict mode by default; if a query matches
more than one element, it fails. Common pitfalls:

- `getByText('Foo')` matches every visible occurrence — use
  `.first()` deliberately, OR scope to a parent
  (`page.getByRole('dialog').getByText('Foo')`), OR add a more
  specific selector.
- `getByRole('combobox')` matches every `<Select>` trigger, every
  `<input role=combobox>`, etc. Always pair with `{ name: /…/i }` or
  scope to a parent.

### PATCH-spy ordering (LIFO route registration)

When a test seeds `mockBackend({ preferences: ... })` AND wants to
intercept later PATCH bodies, the `page.route('**/api/preferences')`
spy must register AFTER the seed. Playwright runs route handlers in
LIFO order; the latest-registered wins. If the spy is registered
before `mockBackend`, the seed mock catches the request and the spy
never fires.

```ts
await mockBackend({ preferences: seedBlob })
const patchBodies: any[] = []
await page.route('**/api/preferences', (route, req) => {
  if (req.method() === 'PATCH') patchBodies.push(JSON.parse(req.postData() ?? '{}'))
  route.continue()
})
```

---

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

---

## 5 · Backend test discipline (pytest, FastAPI, async)

The Playwright lessons from sections 1–4 transpose cleanly to pytest:
write tests against the contract, falsify them, build realistic
fixtures, beware of clip-ancestor-style false-positives. The shape of
the false-positives is different on the backend, but the discipline is
the same. The 11 sub-sections below are concrete failure modes we've
shipped or nearly shipped.

### 5.1 · Test isolation: lru_cache, env vars, module singletons, time

Backend false-pass class #1: a test passes because it's actually
running against state from a *previous* test.

**`get_settings()` is `@lru_cache`d.** If your test does
`monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))` but
doesn't clear the cache, every subsequent `get_settings()` call
returns the FIRST test's settings. `tmp_path` from this test is never
read. Fixture template:

```python
@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    from backend import config
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()  # don't leak this test's settings into the next
```

**Module-level singletons need explicit reset.** Examples in this
codebase: `_refresh_in_progress` flag in `backend/routers/fetch.py`,
the `_seen` set in `backend/cc_watcher.py`, the in-memory cache
in `backend/cache.py`. Each has a test-only `reset_for_tests()`
helper or equivalent — call it from a fixture.

**Time-dependent tests need `freezegun` or `monkeypatch`.** `migrate_to_v2`'s
sentinel uses `datetime.now()`; tests that race the sentinel can flake
on slow CI. `monkeypatch.setattr("backend.foo.datetime", FakeDatetime)`
or use `freezegun.freeze_time(...)`.

**`tmp_path` is per-test by default** but `tmp_path_factory` is
session-scoped and shared. Don't write user data to `tmp_path_factory`
unless you reset it.

**Constants imported by value need patching at every call site.**
`fetcher/credentials.py` defines `DEFAULT_CREDENTIALS_PATH = ...`, and
*three* other modules import the constant by value at module-load time:
`fetcher/bulk_fetch.py`, `backend/routers/fetch.py`, and re-imports in
tests. `monkeypatch.setattr("fetcher.credentials.DEFAULT_CREDENTIALS_PATH",
new)` ONLY rebinds the canonical name — the three by-value copies still
point at `~/.claude-explorer/credentials.json`. The fixture must patch
all four:

```python
@pytest.fixture
def _isolated_credentials_path(tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    for target in (
        "fetcher.credentials.DEFAULT_CREDENTIALS_PATH",
        "fetcher.bulk_fetch.DEFAULT_CREDENTIALS_PATH",
        "backend.routers.fetch.DEFAULT_CREDENTIALS_PATH",
    ):
        monkeypatch.setattr(target, creds)
    yield creds
```

Same pattern applies to any module that does
`from foo import CONSTANT` rather than `from foo import bar; bar.CONSTANT`.
Grep for the constant name globally; if it appears as a bare-name import
anywhere, patch each binding.

**`CLAUDE_DIR` and `CLAUDE_EXPLORER_DATA_DIR` are different knobs.**
`CLAUDE_DIR` controls where `~/.claude-explorer/` itself resolves
(used by capture, credentials, and the orgs router);
`CLAUDE_EXPLORER_DATA_DIR` controls where `conversations/` lives.
A test that only pins `CLAUDE_EXPLORER_DATA_DIR` can still scribble
into the user's real `~/.claude-explorer/credentials.json` if the
code under test goes through the credentials path. Pin both unless
you've verified the call graph never touches credentials.

**`isolated_data_dir` must be a SUBDIRECTORY of `tmp_path`, not
`tmp_path` itself.** `_resolve_path` uses
`data_dir.parent / "preferences.json"`, so `preferences.json` lives one
level up from the data dir. If the fixture uses `tmp_path` directly,
`preferences.json` lands in the pytest tmp root and bleeds across tests
on the same worker. The reference fixture uses `<tmp_path>/data` — `data/`
is the data dir, `<tmp_path>/preferences.json` is the prefs file.

**`real_async_client` is orthogonal to data isolation.** The `httpx.AsyncClient`
+ `ASGITransport(app=...)` fixture used for SSE/concurrency tests does NOT
imply isolated disk. Compose explicitly: a test that streams over real ASGI
AND touches preferences/credentials must use `real_async_client` PLUS
`isolated_data_dir` PLUS (if creds are involved) `_isolated_credentials_path`.
Don't fold them; an SSE test for a read-only endpoint shouldn't pay the
disk-isolation cost it doesn't need.

**Do not compute a home-relative path at import time.** A module
constant such as `Path.home() / "Library" / ...` freezes the developer's
real home before any test runs. `patch_home` (in
`backend/tests/_platform_home.py`) changes `HOME` later, so it cannot
redirect that constant.

- **What happened:** on 2026-09-23 a corrupt-config test ran
  `install-watcher --uninstall` under a patched home. The plist path
  was a module constant, so every local test run deleted the
  maintainer's real launchd watcher.
- **The rule:** make the path a function that reads `Path.home()` when
  it is called. `cli/watcher.py` now does this.
- **The guard:** `test_watcher_paths_follow_home.py`. It asserts that
  each path follows the patched home, and that no subprocess argument
  leaves it.

**Lifecycle tests must be order-independent.** Don't rely on file
collection order (`test_zz_step1_set_flag`, `test_zz_step2_observe_flag`);
pytest-randomly and pytest-xdist will reorder or split them across workers
and the second test will see uninitialized state. Pattern: extract the
fixture body into a plain helper (`def _reset_refresh_flag_body(...): ...`)
and have BOTH the fixture and any lifecycle test call the helper directly.
The test asserts on observable state after each helper invocation in the
same function body.

### 5.2 · Mock at the boundary, not the nesting

Backend false-pass class #2: the test mocks so much of the
implementation that the real bug never runs.

**Rule.** Mock at the HTTP boundary (outbound calls to claude.ai), or
at the filesystem boundary in the rare case where `tmp_path` won't
work. Let everything else run for real.

**Don't mock:** Pydantic models, serializers, migration code, the
prefs reader/writer, the store layer, the route handlers, the SSE
generators. They're cheap and they're where the bugs live.

**Counter-example.** The `/api/preferences` PATCH deep-merge contract
(`{savedFilters: null, activeFilterIds: null}` must explicitly null
legacy keys for the per-key overwrite to clear them). A test that
mocks `_write_atomic` and asserts "yes, _write_atomic was called with
the right body" passes — but the real bug is what lands on disk after
the round trip through `_read_blob() → merge → _write_atomic →
_read_blob()`. Only a real-`tmp_path` test catches it.

```python
# WRONG: mocks too much
def test_patch_merges(monkeypatch):
    seen = {}
    monkeypatch.setattr("backend.routers.preferences._write_atomic",
                        lambda p, d: seen.update(json.loads(d)))
    client.patch("/api/preferences", json={"data": {"theme": "dark"}})
    assert seen["data"]["theme"] == "dark"  # passes; doesn't test merge

# RIGHT: round-trip through real disk
def test_patch_merges(isolated_data_dir, client):
    # seed
    client.put("/api/preferences", json={"data": {"theme": "light", "lang": "en"}})
    # patch
    client.patch("/api/preferences", json={"data": {"theme": "dark"}})
    # round-trip read
    final = client.get("/api/preferences").json()["data"]
    assert final["theme"] == "dark"
    assert final["lang"] == "en"  # NEGATIVE-SPACE: must not be wiped
```

### 5.3 · Strong assertions, not "field exists"

Backend false-pass class #3: the assertion checks structure but not
semantic value. The field could be hardcoded to 0, an empty array,
`None`, or last-write-wins junk and the test still passes.

**Examples.**

- `assert "conversation_count" in data` — passed for weeks while
  `/api/config` returned a hardcoded `0`. The right test asserts
  against a value computed from a known fixture: with 3 conversation
  files in `tmp_path`, `/api/config/stats` returns `3`.
- `assert response.json()["bookmarks"]` — Python truthy. `[]` is
  falsy, `[None]` is truthy. Assert `assert response.json()["bookmarks"]
  == [{...expected...}]`.
- `assert response.status_code == 200` — most route bugs corrupt the
  body, not the status. Always also assert the body shape and key
  values.

**For PDF / image / binary outputs:** assert against a known fixture
byte signature, NOT just "≥1 image stream". WeasyPrint emits valid
streams for broken-image icons; "stream count" can't tell broken from
fixed. The P5 test (`backend/tests/test_export_pdf_images.py`) decodes
the FlateDecode XObject and matches a deterministic 6-byte RGB
sequence in the fixture image. Bytes-in, bytes-out.

### 5.4 · Negative-space assertions

Don't only assert what should change. Also assert what should NOT
change. This catches the entire class of "endpoint clobbers
unrelated state" bugs.

**Concrete patterns.**

- After a PATCH: GET back the resource and assert untouched fields.
- After a migration: assert the keys you didn't migrate are still
  there, and the values are byte-identical (`.read_bytes() ==
  expected_bytes` if it's a file).
- After copying to a cache: assert the source file is unchanged
  (mtime + bytes).
- After a delete: assert siblings/parents are unchanged.

**Fenced-block strip incident (2026-05-05 P1.3, council caught).** The
TOOL_PLACEHOLDER regex stripped placeholder text *inside* fenced code
blocks, killing the friendly badge. A "strip works" test passes
trivially. The real test is two-pronged: stripped *outside* fences;
*preserved* inside fences. Negative-space assertion as a first-class
test, not an afterthought.

```python
def test_tool_placeholder_strip_outside_fence_only():
    md = "before\n\nTOOL_PLACEHOLDER_TEXT here\n\n```\nTOOL_PLACEHOLDER_TEXT inside\n```\nafter"
    out = filter_tool_placeholders(md)
    assert "TOOL_PLACEHOLDER_TEXT here" not in out                  # stripped outside
    assert "TOOL_PLACEHOLDER_TEXT inside" in out                    # PRESERVED inside fence
```

### 5.5 · Migration tests MUST seed the legacy shape

Backend false-pass class #4 (and the most common): tests seed the new
schema, the migration code never runs, and the test happily verifies
the new schema is still the new schema.

**Rule.** Migration tests seed the on-disk shape USERS WILL HAVE
(legacy), then run the migration, then assert the post-migration
shape AND the full contract of what the migration was supposed to do
(tombstone keys, sentinel flags, side effects).

**v1 → v2 filter migration template.**

```python
def test_v1_to_v2_atom_polarity_promotes_to_behavior(isolated_data_dir, client):
    prefs = isolated_data_dir / "preferences.json"
    prefs.write_text(json.dumps({
        "version": 1,
        "data": {
            "filters": {
                "nodes": {
                    "atom-x": {
                        "id": "atom-x", "type": "atom", "name": "X",
                        "enabled": True,
                        "polarity": "exclude",   # legacy v1
                        # NO 'behavior' key
                        "patterns": ["*X*"], "mode": "glob", "target": "title",
                    },
                },
                "activeId": "atom-x",
                "_migratedV1": True,
                # NO _migratedV2
            },
        },
    }))
    # Trigger the migration via the normal path (a GET that the app uses
    # on first mount). Don't reach into private migration functions —
    # tests should exercise the public surface.
    client.get("/api/preferences")
    final = json.loads(prefs.read_text())["data"]["filters"]
    atom = final["nodes"]["atom-x"]
    assert atom["behavior"] == "hide"        # promoted
    assert "polarity" not in atom             # legacy stripped
    assert final["_migratedV2"] is True       # sentinel set
    assert final["activeId"] == "atom-x"      # active preserved
```

**Idempotency.** Run the migration twice. Assert the second run is a
no-op (no PATCH, no on-disk diff). The 2026-05-05 P3a fix uses a
sentinel for exactly this; if the sentinel can be bypassed, the
migration runs every page load and silently rewrites user state.

**Tombstone keys.** When a migration is supposed to clear legacy keys
(via the per-key-overwrite PATCH path), assert they're EXPLICITLY
nulled in the request body OR absent from the post-migration GET.
Omitting them from the PATCH leaves them on disk — that's exactly the
bug Gemini's council review caught in CFR1.

### 5.6 · SSE streaming tests

`/api/fetch/refresh`, `/api/fetch/start`, and any future SSE endpoint
have a contract that's ENTIRELY about the event stream. A test that
asserts `status_code == 200` proves none of it.

**The full SSE contract: event order, event types, payload shape per
event, termination.**

```python
@pytest.mark.asyncio
async def test_refresh_emits_start_progress_complete(client_with_real_app):
    events: list[tuple[str, dict]] = []
    async with client_with_real_app.stream("GET", "/api/fetch/refresh?incremental=true") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        current_event: str | None = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                current_event = line.removeprefix("event:").strip()
            elif line.startswith("data:") and current_event:
                payload = json.loads(line.removeprefix("data:").strip())
                events.append((current_event, payload))
                if current_event in ("complete", "error"):
                    break

    # Order: start, then ≥1 progress, then complete (or error — assert which).
    kinds = [k for k, _ in events]
    assert kinds[0] == "start"
    assert "progress" in kinds
    assert kinds[-1] == "complete"            # NOT error in the happy path
    # Payload shape per event:
    start_payload = next(p for k, p in events if k == "start")
    assert "total" in start_payload
```

**Termination.** Every SSE stream must reach `complete` OR `error`.
Tests should assert the terminator and that the stream actually
closes (no hang). Use `asyncio.wait_for(..., timeout=5)` on the
`async for` loop.

**Reconnection.** If the impl supports SSE retry (`retry: N`), a test
should assert the retry directive is emitted and respected.

**Cancellation.** Disconnect mid-stream and assert the server-side
generator cleans up (no leaked threads, no half-written file). For
the cc-image watcher: assert the polling loop cancels cleanly when
the lifespan teardown fires.

### 5.7 · Realistic data sizes

Backend equivalent of the Playwright "long names" rule. Bugs that
only appear at scale:

- **Search / scoring loops** — fixtures with 1 message don't test
  per-message sort, dedup, or pagination boundaries. Build a fixture
  with at least 50 messages and a known token in only one of them.
- **Filesystem walks** — `discover_jsonl_files` paginates / dedups
  across orgs. With 1 file, you don't test the dedup. With 50 files
  spanning 3 orgs, you do.
- **Memory limits** — large attachments (multi-MB images) don't fit
  in a 1×1 PNG fixture. PDF export with 10+ images can hit
  WeasyPrint memory pressure; include at least one such test.
- **UUID / off-by-one bugs** — sequential UUIDs hide collisions and
  off-by-one errors. Use `uuid.uuid4()` in fixtures, not
  `f"uuid-{i}"`.
- **Long content** — message text > 100kB exercises the streaming-
  tokenizer code paths. Title/name strings ≥ 30 chars test the
  truncation paths the UI relies on.

**Fixture helper template.**

```python
def make_realistic_conversation(uuid: str, *, message_count: int = 50,
                                  needle_index: int | None = None) -> dict:
    """Build a fixture conversation with realistic structure.

    needle_index: if set, the message at this index contains the literal
    string 'NEEDLE_TOKEN' (for search/sort tests). Use a non-zero index
    so 'first match wins' bugs surface.
    """
    msgs = []
    for i in range(message_count):
        text = f"Message {i} body with some realistic content."
        if i == needle_index:
            text += " NEEDLE_TOKEN here."
        msgs.append({
            "uuid": str(uuid_lib.uuid4()),
            "sender": "human" if i % 2 == 0 else "assistant",
            "text": text,
            "content": [{"type": "text", "text": text}],
            "created_at": (BASE_TIME + timedelta(seconds=i)).isoformat(),
            "updated_at": (BASE_TIME + timedelta(seconds=i)).isoformat(),
            "files": [],
            "files_v2": [],
            "attachments": [],
        })
    return {
        "uuid": uuid,
        "name": "Realistic conversation with a long enough title to truncate",
        "model": "claude-opus-4-7",
        "created_at": BASE_TIME.isoformat(),
        "updated_at": (BASE_TIME + timedelta(seconds=message_count)).isoformat(),
        "chat_messages": msgs,
        "current_leaf_message_uuid": msgs[-1]["uuid"],
        ...
    }
```

### 5.8 · Concurrency and atomic-op tests

Endpoints that use locks, atomic ops, or shared state need explicit
race tests. The contract is "lock holds under contention" — and the
only way to exercise that is to actually contend.

**Lock under contention.** `/api/fetch/refresh` is serialized via
`asyncio.Lock` + `_refresh_in_progress`. The test fires concurrent
requests:

```python
@pytest.mark.asyncio
async def test_refresh_serialized(real_async_client):
    # Start two refreshes "simultaneously"; one must 409.
    r1, r2 = await asyncio.gather(
        real_async_client.get("/api/fetch/refresh"),
        real_async_client.get("/api/fetch/refresh"),
        return_exceptions=False,
    )
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 409]
```

**Atomic write under crash.** When the impl uses `tmp + os.replace`,
inject a failure between write and replace. Assert (a) the original
file is intact and (b) the temp file is cleaned up.

```python
def test_atomic_write_recovers_from_replace_failure(isolated_data_dir, monkeypatch):
    target = isolated_data_dir / "preferences.json"
    target.write_text(json.dumps({"version": 1, "data": {"theme": "light"}}))
    original_bytes = target.read_bytes()

    # Force os.replace to fail.
    def boom(*a, **k): raise OSError("simulated rename failure")
    monkeypatch.setattr("os.replace", boom)

    with pytest.raises(OSError):
        write_preferences({"version": 1, "data": {"theme": "dark"}})

    # Original survived.
    assert target.read_bytes() == original_bytes
    # No temp leaked.
    assert not list(isolated_data_dir.glob("preferences.json.tmp*"))
```

**Filesystem ordering in migrations.** What happens if the user kills
the process mid-migration? Test the partial states. If migration
writes files A, B, C in order, simulate a crash after each and assert
recovery on next mount.

**SQLite WAL contention.** If we ever use SQLite, test concurrent
readers + a writer; assert no `database is locked` errors leak to the
client. (Currently no SQLite — but the cache.db hint suggests it
might be relevant; flag if so.)

### 5.9 · Security-adjacent inputs

Every route that takes a path / URL / pattern / external input needs
explicit malicious-input tests. The test passes when the route
*refuses* the input (4xx with no leakage), not when it serves
something.

**Path traversal.** `/api/cc-image?path=../../../etc/passwd` — assert
403 or 400, not 200 with /etc/passwd content. Same for
`/api/attachments/<conv>/<file>/<variant>`. Real pattern: the route
must `Path(...).resolve(strict=True).relative_to(allowed_root)` and
404 on `ValueError`.

**Symlink resolution.** Place a symlink in `tmp_path` pointing
outside the data dir. Assert the route doesn't follow it.

**Permission bits.** After writing `~/.claude-explorer/credentials.json`
or `preferences.json`, assert `os.stat(p).st_mode & 0o777 == 0o600`.
The atomic-write path is what writes mode bits; if it
`os.replace()`s a `tmp` file with `0o644`, the permission slips. We
have this test for credentials but not preferences — write it.

**Regex DoS.** If the user can supply regex patterns
(`AtomFilter.mode == 'regex'`), a pathological pattern like
`(a+)+$` with a long input can hang. Assert the matcher terminates
within a small time budget OR validates pattern complexity.

**Auth headers.** Routes that expect headers (X-Org-ID,
Authorization, etc.) should 401 on missing headers, 403 on
malformed. Don't rely on FastAPI's default behavior; explicit tests
prevent regressions.

**Header / form smuggling.** Tests that supply unexpected
content-type, oversized JSON, or duplicate headers should produce
4xx with a useful detail body, not 500.

### 5.10 · Async / await pitfalls

Backend false-pass class #5: a coroutine is created but not awaited.
The test happily passes; the assertion runs against the coroutine
object instead of its resolved value.

**Concrete trap.**

```python
# WRONG — silent pass
def test_get_config(client):
    response = client.get("/api/config")  # if `client` is AsyncClient, returns a coroutine
    assert response.status_code == 200    # `response` is a coroutine; status_code attribute access throws AttributeError
                                           # ...but if you got the imports wrong AsyncClient might be a sync mock,
                                           # silently passing.
```

**Discipline.**

1. `pyproject.toml` sets `asyncio_mode = "auto"` so all `async def`
   tests run via `pytest-asyncio` automatically. Or use
   `asyncio_mode = "strict"` and decorate explicitly with
   `@pytest.mark.asyncio`. Don't mix.
2. CI runs with `-W error::RuntimeWarning` so "coroutine was never
   awaited" is a test failure, not a silent warning.
3. For the simple HTTP tests, use FastAPI's `TestClient` (sync) — it
   wraps `httpx.AsyncClient` internally and you write plain
   `def test_…`. For SSE / streaming / explicit async behavior, use
   `httpx.AsyncClient` + `async def test_…`.
4. Never `asyncio.run()` inside a test; always let `pytest-asyncio`
   manage the loop.

**Warning hygiene.** `filterwarnings` in `pyproject.toml` should NOT
contain a blanket `ignore::DeprecationWarning`. Real deprecations
from third-party libs are how we learn about upgrade requirements.
Filter only the specific warnings you've consciously decided to live
with, with a comment explaining why.

### 5.11 · Pydantic / FastAPI specifics

**Strict input validation.** Input models should declare
`model_config = ConfigDict(extra='forbid')` so unknown fields produce
422, not silent acceptance. Tests should send a payload with one
extra field and assert 422 with a useful detail.

**Edge cases for every input model.**

- empty list, empty dict, empty string for required-non-empty fields
- `null` for required fields → 422
- Type coercion: `"1"` (string) where `int` is required — assert the
  coercion happens AND the right cases reject (e.g. `"abc"` → 422).
- Float / int boundary: `1.0` for `int` field; `2**53 + 1` for large
  ints (JSON precision loss).
- Datetime: ISO-8601 with and without timezone; assert tz handling.

**Response model coercion only runs through HTTP.** Calling a route
handler directly skips `response_model`. Always test via
`httpx.AsyncClient`/`TestClient`, not by importing the handler.

**Schema migration tests.** When you add a response field, write a
test that consumes the OLD response shape and adapts (proves
backwards compat). When you remove a field, write a test that the
new response does NOT contain it (proves you actually removed it,
didn't accidentally keep it for one extra release).

**`Depends()` overrides.** Use `app.dependency_overrides[get_settings]
= lambda: TestSettings()` for unit testing. Do NOT monkeypatch
`get_settings` globally — that breaks lru_cache discipline (5.1).

**Status codes are part of the contract.** A 200/201/204/404/422 etc
distinction matters to clients. Tests should assert the *exact* code,
not "≥ 200 and < 300".

**Test the error path.** For every route, assert at least one error
case explicitly: missing data → 404; bad input → 422; conflict → 409;
internal failure → 500 with a sanitized detail (no traceback in body
for production responses).

### 5.12 · Monkeypatching: prefer attribute-patch over value-binding

The way a test rebinds a symbol determines whether the module under
test can be safely refactored. Two distinct idioms:

```python
# ✓ ATTRIBUTE PATCH (refactor-safe)
from backend.routers import fetch as fetch_router
monkeypatch.setattr(fetch_router, "save_credentials", fake_save)

# ✗ VALUE BINDING (refactor-fragile)
from backend.routers.fetch import save_credentials
monkeypatch.setattr("backend.routers.fetch.save_credentials", fake_save)
# Or worse, capturing the value at import time:
saved_real = save_credentials
monkeypatch.setattr(saved_real, "__call__", fake_save)  # doesn't do what you think
```

**Why this matters.** When the module under test imports a helper from
elsewhere (`from .helpers import save_credentials`), the helper is bound
to the local module's namespace AT IMPORT TIME. A test that
attribute-patches the local namespace (`fetch_router.save_credentials =
...`) reaches through to the late-bound runtime call. A test that
value-binds a snapshot of the function won't see updates.

**The refactor-safety consequence.** If you extract `save_credentials`
out of `fetch.py` into a new `fetch_pipeline.py` module:

- Tests that use **attribute-patch on `fetch_router`** keep working iff
  `fetch_router` still has `save_credentials` as a top-level attribute
  (i.e., it's re-imported at the top of `fetch.py`). Mass refactors
  that move helpers out without re-importing them break these tests
  silently — the patch lands on a module that no longer routes the
  call through.

- Tests that use **value-binding** (`from backend.routers.fetch import
  save_credentials; ... = fake`) only patch the test's local symbol —
  the route's call goes through to the real `save_credentials`. These
  tests are vacuously green and DON'T catch the bug they should.

**Incident**: the 2026-05-21 A2 refactor of `routers/fetch.py`
surfaced this. The Engineer council persona (gpt-5.2-pro) caught that
23+ tests used the attribute-patch idiom against `fetch_router`, which
forced the council to ship a CONSERVATIVE split (preserve top-level
attributes on `fetch_router`) instead of the aggressive split the
Architect originally proposed. Detailed in
`PLANS/CODE-REVIEW-BACKEND.md`.

**Rule**: prefer attribute-patch via `monkeypatch.setattr(module,
"name", fake)`. Avoid value-binding via `from module import name` in
test files — it makes future refactors strictly harder. When
refactoring a module that has heavy test coverage, run this grep
FIRST to surface the landmine count:

```bash
grep -rnE 'monkeypatch\.setattr\(|patch\.object\(|patch\(["\'][^"\']*<module>' \
  backend/tests/ fetcher/tests/ | grep -E '<module>' | wc -l
```

If the count is > 0, the refactor must either (a) preserve top-level
attributes on the original module via re-export, or (b) migrate the
test sites in lockstep.

### 5.13 · User-observable contracts over implementation-pinned rules

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

### 5.15 · E2E tests MUST assert zero unexpected console errors / warnings

A Playwright e2e (or Playwright MCP investigation) that asserts only
on DOM state is half-blind. A page can have the right elements in the
right place AND simultaneously emit red errors that crash a different
code path, leak unhandled promises, or fire React warnings that
correlate with a real bug. DOM-passing + console-failing is exactly
the failure mode the user finds first on manual test.

**Incident**: 2026-05-24 settings-page flash-and-disappear regression.
My Playwright check confirmed URL stays at `/settings`, the
`[data-section="markdown-export"]` exists, the new checkbox toggles,
and `localStorage` updates. All green. The user opened the same page
in their browser and reported "flashes on and disappears" — visible
on first manual test because their console had errors mine never
asserted on.

**Rule**: every Playwright `*.spec.ts` test MUST install a
console-error capture in `beforeEach` and assert empty in `afterEach`,
modulo an explicit allowlist. Suggested fixture:

```typescript
import { test as base } from '@playwright/test'

type ConsoleCapture = { errors: string[]; warnings: string[] }

const ALLOWED_NOISE = [
  /\[vite\] (connecting|connected)/,
  /Download the React DevTools/,
  // Each addition needs a comment naming the source + reason it's tolerated.
]

export const test = base.extend<{ consoleCapture: ConsoleCapture }>({
  consoleCapture: async ({ page }, use) => {
    const cap: ConsoleCapture = { errors: [], warnings: [] }
    page.on('pageerror', e => cap.errors.push(`pageerror: ${e.message}`))
    page.on('console', m => {
      const text = m.text()
      if (ALLOWED_NOISE.some(rx => rx.test(text))) return
      if (m.type() === 'error') cap.errors.push(text)
      else if (m.type() === 'warning') cap.warnings.push(text)
    })
    await use(cap)
    if (cap.errors.length > 0) {
      throw new Error(`Unexpected console errors:\n  ${cap.errors.join('\n  ')}`)
    }
    if (cap.warnings.length > 0) {
      throw new Error(`Unexpected console warnings:\n  ${cap.warnings.join('\n  ')}`)
    }
  },
})
```

**Rule for Playwright MCP investigation** (interactive debugging):
after EVERY navigation and after EVERY meaningful action, call
`mcp__playwright__browser_console_messages({ level: 'warning', all: true })`.
Errors that fire during a navigation tell you which navigation broke;
checking only at the end loses the timeline. A clean `level: 'error'`
response is NOT enough — React warnings (missing keys, missing
`aria-describedby`, effect-dependency drift) often correlate with
the bug under investigation.

**Allowlist hygiene**: the allowlist is explicit (each pattern has a
comment naming the source and why it's tolerated), not a blanket
skip. A new pattern in the allowlist is a code-review checkpoint.

The §5.13/§5.14 framing extends here: asserting "the DOM has X" pins
an implementation rule; asserting "the console has no errors" pins
the user-observable contract (the developer opening the browser dev
tools is part of the contract — red text there IS a bug). Both are
required.

### 5.16 · A "passing" run must PROVE it executed (no piped exit codes, no silent non-execution)

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

---

### 5.17 · Specs with a local `mockBackend(page)` MUST mock `/api/preferences`

Any spec that defines its OWN local `async function mockBackend(page)`
helper (i.e. does NOT use the shared `mockBackend` fixture from
`frontend/e2e/fixtures.ts`) must also mock `**/api/preferences`. Without
the mock, GET/PATCH/PUT `/api/preferences` falls through Vite's dev-
server proxy to whatever backend happens to be running on `:8765`, so
prefs like `rightPaneTab`, `searchPanel.isOpen`, `showCompactions`, and
`showToolCalls` persist across browser contexts and bleed between
tests.

The 2026-06-01 recovery surfaced this as the root cause of every
remaining post-Tailscale-fix flake (bookmarks, compact-markers,
cowork-multi-org, force-refetch, per-bubble-tools, redownload-
conversation, url-navigation, search-compact-auto-expand,
connection-status). Symptom shape: an assertion fails because a UI
toggle / panel state set by a sibling test persists into the current
test's first render. The failure looks like a UI race; the root cause
is server-side state.

Use the shared helper:

```ts
import { installLocalPrefsMock } from './fixtures'

async function mockBackend(page: Page) {
  // ... other route mocks ...
  await installLocalPrefsMock(page)                            // empty prefs
  await installLocalPrefsMock(page, { rightPaneTab: 'search' }) // seeded
  // ... rest of the mocks ...
}
```

The helper supports GET (returns the current blob), PATCH (merges the
body into the blob), PUT (overwrites the blob), and 405 for everything
else. Status-quo behavior; no migration cost for existing call sites.

**Rule for new specs**: if you write a local `mockBackend(page)`, audit
your route mocks for `/api/preferences` coverage AS PART OF THE PR
review. If `/api/preferences` is missing, that's a Vite-proxy leak
waiting to flake the next sibling test that toggles a pref.

The shared `mockBackend` fixture from `fixtures.ts` already wires its
own prefs mock with the same shape (accepting `preferences` /
`sharedPrefsState` options). Specs using the shared fixture do NOT
need this helper.

---

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

---

## 7 · What CI proves

Two workflows run automatically.

| Workflow | Runners | What it proves |
|---|---|---|
| `test.yml` | `ubuntu-latest` | The Python suite, and Playwright in fixture mode |
| `install-matrix.yml` | Six runners, listed below | The documented install, the CLI, Chromium, `serve`, the watcher, and the Python suite |

### The install matrix

`install-matrix.yml` covers every platform that the README supports. All six
runners are free for public repositories.

| Platform | Runner | Watcher supervisor |
|---|---|---|
| macOS arm64 | `macos-latest` | launchd |
| macOS x86_64 | `macos-15-intel` | launchd |
| Linux x86_64 | `ubuntu-latest` | systemd user unit |
| Linux arm64 | `ubuntu-24.04-arm` | systemd user unit |
| Windows x86_64 | `windows-latest` | Task Scheduler |
| Windows ARM64 | `windows-11-arm` | Task Scheduler |

**When it runs:**

- On a push to `main` or to any `ci/**` branch.
- On a pull request.
- By manual dispatch. The `source` input selects the checkout or the
  published PyPI package.

The push and pull-request triggers apply only when a change touches the
Python code, `pyproject.toml`, `uv.lock`, `README.md`, or the workflow file.

**The `install` job runs the README commands themselves.** So the
documentation is under test, not only the code. On each runner it checks
these items:

- The documented install command works. On Windows ARM64 this includes the
  x86_64-Python pin. The step prints the interpreter, which must report
  `AMD64` on an `ARM64` machine. That proves the Prism translation path.
- All five subcommands appear in `--help`.
- The Chromium build installs and launches. On Linux the install uses
  `--with-deps`, which adds the system libraries.
- `serve` answers `/api/config` with HTTP 200.
- The watcher installs, and its supervisor reports it:
  - macOS: `launchctl list` shows the job.
  - Linux: `systemctl --user is-active` reports the unit active. The job
    first enables lingering, so the runner has a user session bus.
  - Windows: `schtasks` finds the task.
- The uninstall removes the watcher, and a second query confirms it is gone.

**The `pytest` job runs the full suite on the same six runners.** It uses
xdist on macOS and Linux. On Windows it runs serially with `-n 0`. There the
xdist controller hung at shutdown, and parallel runs hid order-dependent
failures.

## 8 · Verifying each platform by hand

CI cannot reach three things: the native Claude Desktop app, operating-system
certificate trust, and an interactive login. Section 10 explains why. Verify
those by hand before a release, on every platform that supports them.

### What each platform supports

| Check | macOS | Linux | Windows x64 | Windows ARM64 |
|---|---|---|---|---|
| Web UI | Yes | Yes | Yes | Yes |
| MCP server (stdio) | Yes | Yes | Yes | Yes |
| MCPB bundle in Claude Desktop | Yes | No: no Claude Desktop | Yes | Yes |
| Browser capture (default) | Yes | Yes | Yes | Yes |
| Proxy capture (`--proxy`) | Yes | No: no Claude Desktop | Yes | **No:** no mitmproxy wheels |
| PDF export | Needs Pango | Needs Pango | Needs GTK3 runtime | Needs GTK3 runtime |

Claude Desktop is not available for Linux, so the two checks that depend on
it do not apply there.

### Test machines

- **macOS:** the development machine.
- **Linux:** the `claude-explorer-ubuntu` UTM VM (Ubuntu 24.04, ARM64).
- **Windows ARM64:** the `claude-explorer-windows` UTM VM (Windows 11,
  QEMU with HVF, 8 GB RAM).
- **Windows x64:** CI only. On Apple Silicon an x86_64 guest runs under full
  emulation, which is too slow for interactive work. For hands-on x86_64
  testing, use a cloud Windows VM.

The host runs UTM 4.7.5. It offers no snapshots for hardware-accelerated
guests, so restore a VM by deleting it and cloning its `.clean` baseline.
Maintainer notes live in `~/.claude-explorer/vm-fleet.md`.

**Free disk space before you boot a VM.** A 37 GB image needs room for its
write overlay, and a full disk can corrupt a `qcow2` image.

### Step 1. Install the build under test

On every platform, install from the current `main`:

- macOS and Linux: `uv tool install --force claude-explorer`
- Windows x64: the same command.
- Windows ARM64:
  `uv tool install --force claude-explorer --python cpython-3.13-windows-x86_64-none`

A VM that you set up earlier carries whatever build was current then.

### Step 2. The web UI (every platform)

1. Run `claude-explorer serve`.
2. Open `http://localhost:8765` in the platform's default browser: Safari
   on macOS, Firefox on Linux, Edge on Windows.
3. Confirm that the conversation list renders.
4. Open one conversation. Confirm that its messages render.
5. Run a search. Confirm that results appear.
6. Confirm that the source filter offers All, Desktop, Code, and Cowork.

**Pass:** all six render, with no error in the browser console.

### Step 3. The MCPB bundle (macOS and Windows)

1. Download the `.mcpb` from the latest GitHub Release.
2. On Windows, **record the exact SmartScreen text** and the path you took.
   On macOS, record any Gatekeeper prompt the same way.
3. Drag the file into Claude Desktop, then Settings, then Extensions.
4. Accept the install dialog.
5. Confirm that the five tools appear.

**Pass:** the five tools are listed, and one call returns data.

### Step 4. PDF export (every platform)

1. Install the graphics libraries WeasyPrint needs:
   - macOS: `brew install pango cairo libffi`
   - Linux: the distribution's `pango`, `cairo`, and `libffi` packages.
   - Windows: the GTK3 runtime. Follow the WeasyPrint Windows instructions.
2. Export any conversation as PDF from the web UI.

**Pass:** the file opens, and it starts with the `%PDF` magic bytes.

A user who needs only Markdown can skip this. Markdown export needs no extra
libraries, and `serve` starts normally without them.

### Step 5. Credential capture and fetch

**Browser capture works on every platform.** Run it first:

1. Run `claude-explorer capture`.
2. Log in to Claude in the window that opens.
3. Run `claude-explorer fetch`.

**Pass:** `captured_at` in `~/.claude-explorer/credentials.json` carries
today's date, and `fetch` reports conversations.

**Proxy capture** applies only on macOS and Windows x64. It serves users who
cannot log in on the web but still have a working Claude Desktop session.
Do it last, because it carries the most risk:

1. Start the proxy: `claude-explorer capture --proxy`.
2. Stop it once. The certificate file appears only after the first run.
3. Trust the mitmproxy certificate authority. The CLI prints the command for
   your platform. Do this **before** you launch Claude Desktop:
   `--ignore-certificate-errors` covers only Chromium's own requests, not
   the Electron main process.
4. On Windows, confirm the install path:
   `Get-Process Claude | Select-Object Path`.
5. Launch Claude Desktop through the proxy. The CLI prints the command.
6. Click around in Claude Desktop for a minute.

**Pass:** the addon reports that it captured the credentials.

#### If proxy capture captures nothing on Windows

A Claude Desktop installed from the **Microsoft Store** runs sandboxed from a
protected `C:\Program Files\WindowsApps\` folder. It may ignore the proxy
flags, so its traffic never reaches mitmproxy and nothing is captured. This
is a prediction from the June test run; it is not yet confirmed.

**What the user sees:** `capture --proxy` starts, the user clicks around in
Claude Desktop, and no credentials ever appear. Nothing fails loudly.

**Who it affects:** only users who need proxy capture, meaning people who
cannot log in on the web but are still logged in to Claude Desktop.
Everyone else uses browser capture and never touches the proxy.

**Why it matters for that group:** proxy capture is their only route back
to their archive. A Store-installed Claude Desktop may therefore leave them
with no working recovery path in this tool today.

**What to record:** whether the flags were honoured, and the install path
from step 4. A path under `WindowsApps` identifies a Store install.

## 9 · Protecting credentials on every platform

`~/.claude-explorer/credentials.json` holds the session key. Anyone who can
read it can act as the user on claude.ai. The app therefore restricts the
file, and its `.bak` copy, to the current user on every platform.

Each platform needs a different mechanism, because each stores permissions
differently. `harden_path_permissions` in `fetcher/credentials.py` applies
the right one.

| Platform | Mechanism | What the app does |
|---|---|---|
| macOS | POSIX mode bits | Sets mode `0600` (owner read and write only) |
| Linux | POSIX mode bits | Sets mode `0600` (owner read and write only) |
| Windows | NTFS access control list | Removes inherited entries, then grants the current user alone |

Windows ignores POSIX mode bits entirely. Before 2026-09-22 the app set
`0600` on Windows too, which did nothing, and the file stayed readable by
every account the inherited access list allowed. The fix gives Windows the
same protection as macOS and Linux, through the mechanism Windows actually
uses.

### Verify it

- **macOS:** `stat -f '%Sp' ~/.claude-explorer/credentials.json`.
  Expect `-rw-------`.
- **Linux:** `stat -c '%A' ~/.claude-explorer/credentials.json`.
  Expect `-rw-------`.
- **Windows:** in PowerShell,
  `icacls "$env:USERPROFILE\.claude-explorer\credentials.json"`.
  Expect only your own account. `Everyone`, `BUILTIN\Users`, and
  `Authenticated Users` must not appear.

The protection is best effort on every platform. If the operating system
refuses, the app logs a warning and keeps the credential rather than fail
the capture. A user who reports a permission problem should run the check
for their platform.

## 10 · What cannot be automated, and why

**Browser automation drives web pages.** That covers Playwright, and Claude
in Chrome. Of the manual checks, only the web UI in step 2 is a web page:

- The web UI **can** be automated. CI already proves `serve` answers with
  HTTP 200. A Playwright screenshot pass would add the visual check.

The rest is not browser work, so browser automation cannot reach it:

- **The MCPB drag-drop** is a native Claude Desktop dialog. A browser tool
  cannot see another application's windows.
- **Certificate trust** for proxy capture is an operating-system security
  decision, made outside any browser.
- **The real login** is interactive by design. Scripting a login through a
  proxy is exactly the flow the security design makes hard.

**Desktop automation** is a separate technology. It controls the whole
screen, mouse, and keyboard, and it could in principle drive the MCPB
drag-drop. Three things argue against it here:

- It runs against a VM's display, so it is slow and breaks when a dialog
  moves or changes its wording.
- Proxy capture needs the maintainer's real account. That login must not be
  handed to an automated tool.
- Each check runs once per release, so the manual cost is small.

Keep steps 3 and 5 manual. Automate the visual half of step 2 when a
screenshot pass is added to CI.

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
