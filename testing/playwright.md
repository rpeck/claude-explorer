# Testing: Playwright and end-to-end tests

Part of [TESTING.md](../TESTING.md). Read this file when you write or debug an end-to-end spec.

## 3 · Playwright-specific gotchas

These problems caused failures in this project. This section records them so that the next agent does not learn them again.

### `toBeVisible()` does not detect ancestor clipping

Playwright's `toBeVisible()` checks three conditions:

- The bounding box is not empty.
- `display !== 'none'`.
- `visibility !== 'hidden'`.

An ancestor with `overflow: hidden` does not change any of these conditions. The element's own box stays non-empty, and its computed style does not change.

`toBeInViewport()` has the same blind spot. It checks intersection with the **browser viewport**, not with an inner scroll container.

A row-anchored bounding-box check (button inside row) also does not help. If the same ancestor clips the row, the row and the button are both inside the row's logical box. The ancestor clips them together.

**Fix: use a helper that goes up to the nearest `overflow:hidden|auto|scroll|clip` ancestor and asserts containment.**

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

The reference implementation is in `frontend/e2e/spec-filters-trash-visible.spec.ts`. If you need it in more than one spec, move it into a shared helper at `frontend/e2e/helpers/clipAncestor.ts`.

### Add `.hover()` or `.click()` as an actionability cross-check

Playwright's actionability checks include "element is at the click point". A clipped element fails this check. Add a `.hover()` after the static assertions. The hover checks the "user can reach this" property end-to-end.

```ts
await deleteButton.hover({ timeout: 2000 })
```

Use this in every test that asserts "the user can interact with X". It is cheap, and it is independent of the static checks.

### shadcn `<Select>` quirks

- The Select trigger renders as `role="button"`, not as `role="combobox"`.
  - Prefer `getByLabel(/…/i)` for the trigger.
  - The only acceptable test-id fallback is `data-testid="active-filter-select"`. The spec names the picker structure unambiguously.
- The options are in a Portal with mount animations. Always use this sequence:
  ```ts
  await trigger.click()
  await expect(page.getByRole('option', { name: /…/i })).toBeVisible()
  await page.getByRole('option', { name: /…/i }).click()
  ```
  A bare `.click()` on an option races the mount.

### Radix `<ScrollArea>` quirks

- The Radix `<ScrollArea>` Viewport wraps its content in `style="display: table; min-width: 100%"`.
  - This wrapper sizes itself to the content width.
  - Rows can then overflow past the bounded width of the Viewport.
  - The outer `overflow: hidden` then clips the right end of the rows.
- Fix this problem at the use site. Append `[&>div>div]:!block` to the `className` of the ScrollArea.
  - This arbitrary-selector override forces the Radix wrapper to `display: block`.
  - The wrapper then inherits the bounded width of the Viewport.
- For the canonical application and its comment, see the ScrollArea in `ManageFiltersModal.tsx`.

### Radix `<RadioGroup>` `.check()` races controlled-component re-renders

- Playwright's `.check()` clicks the radio. Then it asserts `aria-checked="true"` before it returns.
- Radix `<RadioGroupItem>` changes `aria-checked` only after the `value` prop of the parent `<RadioGroup>` changes. That change needs three steps:
  1. The consumer's `onValueChange` handler fires.
  2. The React state setter runs.
  3. A re-render completes.
- If the setter goes through TanStack Query's `useMutation` (for example, `usePreferences`), the re-render occurs on a microtask. Under parallel-worker load, that microtask often loses the race against the post-assertion of `.check()`.
- Symptom: the test fails with `Error: locator.check: Clicking the checkbox did not
  change its state`.
  - The locator log shows `aria-checked="false" data-state="unchecked"` after the click action succeeded.
  - The test passes on retry, so the failure shows as "flaky", not as "failed".
  - This problem is easy to miss until the suite runs often enough to use up the retry budget.
- Fix: use `.click()`. Then verify the state through the durable side effect that you actually care about:
  - the PATCH body
  - a downstream DOM change
  - an aria-checked assertion after `waitForResponse`

  Pattern:
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
- The rule applies to every Radix primitive that wraps a controlled component with an asynchronous state setter:
  - `RadioGroup`
  - `Switch`
  - controlled `Checkbox`
- A native HTML `<input type="checkbox">` updates synchronously. `.check()` stays safe for it.
- If the radio state is in plain `useState` with no async mutation, `.check()` can work. But `.click()` plus a post-assertion is the safer default.
- These project files already use the fix:
  - `preferences-cross-context.spec.ts`
  - `settings.spec.ts`
  - `markdown-export-mode-unified.spec.ts`

### Strict-mode locator collisions

Playwright runs locators in strict mode by default. If a query matches more than one element, the query fails. These are the common problems:

- `getByText('Foo')` matches every visible occurrence. Use one of these fixes:
  - Use `.first()` deliberately.
  - Scope the query to a parent (`page.getByRole('dialog').getByText('Foo')`).
  - Add a more specific selector.
- `getByRole('combobox')` matches every `<Select>` trigger, every `<input role=combobox>`, and similar elements. Always pair it with `{ name: /…/i }`, or scope it to a parent.

### PATCH-spy ordering (LIFO route registration)

Some tests seed `mockBackend({ preferences: ... })` and also intercept later PATCH bodies. In these tests, register the `page.route('**/api/preferences')` spy after the seed.

- Playwright runs route handlers in LIFO order. The handler that registered last wins.
- If you register the spy before `mockBackend`, the seed mock catches the request. The spy then never fires.

```ts
await mockBackend({ preferences: seedBlob })
const patchBodies: any[] = []
await page.route('**/api/preferences', (route, req) => {
  if (req.method() === 'PATCH') patchBodies.push(JSON.parse(req.postData() ?? '{}'))
  route.continue()
})
```

## 5.15 · E2E tests MUST assert zero unexpected console errors / warnings

A Playwright e2e test that asserts only on DOM state is half-blind. The same is true for a Playwright MCP investigation.

- A page can have the right elements in the right place.
- At the same time, the page can emit red errors. These errors can:
  - crash a different code path
  - leak unhandled promises
  - fire React warnings that correlate with a real bug
- A test that passes on the DOM but fails on the console is exactly the failure mode that the user finds first in a manual test.

**Incident**: the 2026-05-24 settings-page flash-and-disappear regression.

- My Playwright check confirmed four things:
  - The URL stays at `/settings`.
  - The `[data-section="markdown-export"]` exists.
  - The new checkbox toggles.
  - `localStorage` updates.
- All four checks were green.
- The user opened the same page in their browser and reported "flashes on and disappears".
- The problem was visible on the first manual test, because the user's console had errors that my check never asserted on.

**Rule**: every Playwright `*.spec.ts` test must install a console-error capture in `beforeEach`. The test must assert in `afterEach` that the capture is empty. The only exceptions are the patterns in an explicit allowlist. Suggested fixture:

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

- After every navigation and after every meaningful action, call `mcp__playwright__browser_console_messages({ level: 'warning', all: true })`.
- Errors that fire during a navigation tell you which navigation broke. If you check only at the end, you lose the timeline.
- A clean `level: 'error'` response is not enough. React warnings often correlate with the bug under investigation. Examples of these warnings:
  - missing keys
  - missing `aria-describedby`
  - effect-dependency drift

**Allowlist hygiene**:

- The allowlist is explicit. It is not a blanket skip.
- Each pattern has a comment that names the source and the reason that the pattern is tolerated.
- A new pattern in the allowlist is a code-review checkpoint.

The framing of [§5.13](backend-contracts.md) and [§5.14](backend-contracts.md) also applies here:

- An assertion that "the DOM has X" pins an implementation rule.
- An assertion that "the console has no errors" pins the user-observable contract.
  - The developer who opens the browser dev tools is part of the contract.
  - Red text in the dev tools is a bug.
- Both assertions are required.

## 5.17 · Specs with a local `mockBackend(page)` MUST mock `/api/preferences`

Some specs define their own local `async function mockBackend(page)` helper. These specs do not use the shared `mockBackend` fixture from `frontend/e2e/fixtures.ts`. Each of these specs must also mock `**/api/preferences`.

If this mock is missing, these problems occur:

- GET/PATCH/PUT `/api/preferences` falls through the Vite dev-server proxy to any backend that runs on `:8765` at that time.
- Prefs persist across browser contexts and leak from one test into another. Examples of these prefs:
  - `rightPaneTab`
  - `searchPanel.isOpen`
  - `showCompactions`
  - `showToolCalls`

The 2026-06-01 recovery found this problem as the root cause of every flake that remained after the Tailscale fix. These specs had the flakes:

- bookmarks
- compact-markers
- cowork-multi-org
- force-refetch
- per-bubble-tools
- redownload-conversation
- url-navigation
- search-compact-auto-expand
- connection-status

The symptom has this shape:

- An assertion fails because a UI toggle or a panel state from a sibling test persists into the first render of the current test.
- The failure looks like a UI race. The root cause is server-side state.

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

The helper supports these methods:

- GET returns the current blob.
- PATCH merges the body into the blob.
- PUT overwrites the blob.
- All other methods return 405.

The helper keeps the status-quo behavior. Existing call sites have no migration cost.

**Rule for new specs**: if you write a local `mockBackend(page)`, audit your route mocks for `/api/preferences` coverage as part of the PR review. If `/api/preferences` is missing, the spec has a Vite-proxy leak. That leak can cause a flake in the next sibling test that toggles a pref.

The shared `mockBackend` fixture from `fixtures.ts` already sets up its own prefs mock with the same shape. That fixture accepts the `preferences` and `sharedPrefsState` options. Specs that use the shared fixture do not need this helper.
