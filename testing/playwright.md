# Testing: Playwright and end-to-end tests

Part of [TESTING.md](../../TESTING.md). Read this file when: you write or debug an end-to-end spec.

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

## 5.15 · E2E tests MUST assert zero unexpected console errors / warnings

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

## 5.17 · Specs with a local `mockBackend(page)` MUST mock `/api/preferences`

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
