import { test, expect, withNetRetry } from './fixtures'
import type { Browser, BrowserContext, Page, Route } from './fixtures'

/**
 * G1 audit — cross-browser-context preference persistence.
 *
 * Goal: prove that prefs PATCHed in context A are visible to a freshly
 * mounted context B. This simulates "user changes settings in one
 * browser window, opens the app in a second window, expects the same
 * values". The on-disk shape is already covered by
 * `backend/tests/test_preferences.py` — what this test adds is the
 * frontend's read-on-mount behavior across an independent context.
 *
 * Design (per LLM-council G1 resolution): two BrowserContexts in a
 * single test share an in-memory prefs map via closure capture. The
 * route handler is installed separately on each context but both close
 * over the same `prefsState` object — so a PATCH on context A's page
 * mutates the same data that context B's GET reads back.
 *
 * Why not lift `prefsState` to a worker-scoped fixture? Worker-scoped
 * shared state would cross-contaminate every preference-touching test
 * in the suite. Multi-context-in-a-single-test gives us the persistence
 * proof without that blast radius (see decision_record in the audit
 * commit message).
 *
 * Coverage targets (the three §16.1 prefs — unified Markdown mode
 * replaces the legacy bundleImages + dialect pair as of 2026-05-29):
 *   - theme
 *   - keyboardMode
 *   - markdownExportMode
 */

interface PrefsState {
  data: Record<string, unknown>
}

type Fulfill = Parameters<Route['fulfill']>[0]

/**
 * Install the minimum mocks a page needs to load (`/`, `/settings`)
 * plus the stateful `/api/preferences` handler keyed off `prefsState`.
 *
 * Mirrors fixtures.ts's `mockBackend` exactly for the routes the
 * Settings page reads. We don't reuse the fixture's `mockBackend`
 * because that one is bound to a single page; here we need to wire two
 * pages from two different contexts to the SAME prefsState.
 */
async function installSharedMocks(
  context: BrowserContext,
  prefsState: PrefsState,
): Promise<void> {
  const fulfill = (route: Route, payload: Fulfill) => {
    void route.fulfill(payload)
  }

  // Catch-all leakage guard (registered FIRST so LIFO order runs it
  // LAST). Same shape as fixtures.ts.
  await context.route('**/api/**', (route) => {
    const req = route.request()
    console.error(
      `[preferences-cross-context] Unmocked API call leaked: ${req.method()} ${req.url()}`,
    )
    fulfill(route, {
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({
        error: 'Unmocked API route hit in test',
        method: req.method(),
        url: req.url(),
      }),
    })
  })

  await context.route('**/api/config', (route) => {
    fulfill(route, {
      contentType: 'application/json',
      body: JSON.stringify({ data_dir: '/tmp' }),
    })
  })
  await context.route('**/api/config/stats', (route) => {
    fulfill(route, {
      contentType: 'application/json',
      body: JSON.stringify({ data_dir: '/tmp', conversation_count: 0 }),
    })
  })
  await context.route('**/api/orgs', (route) => {
    fulfill(route, {
      contentType: 'application/json',
      body: JSON.stringify({
        authenticated: true,
        orgs: [{ org_id: 'org-1', name: 'Personal', is_primary: true }],
      }),
    })
  })
  await context.route('**/api/account', (route) => {
    fulfill(route, {
      contentType: 'application/json',
      body: JSON.stringify({
        authenticated: true,
        account_uuid: null,
        email: 'ada@example.com',
        full_name: 'Ada Lovelace',
        display_name: 'Ada Lovelace',
        org_name: 'Personal',
        error: null,
      }),
    })
  })
  await context.route('**/api/conversations**', (route) => {
    fulfill(route, { contentType: 'application/json', body: '[]' })
  })
  await context.route('**/api/search**', (route) => {
    fulfill(route, {
      contentType: 'application/json',
      body: JSON.stringify({
        results: [],
        total_messages_matched: 0,
        returned_messages: 0,
        truncated: false,
      }),
    })
  })
  await context.route('**/api/bookmarks', (route) => {
    fulfill(route, {
      contentType: 'application/json',
      body: JSON.stringify({ bookmarks: [] }),
    })
  })
  await context.route('**/api/fetch/status', (route) => {
    fulfill(route, {
      contentType: 'application/json',
      body: JSON.stringify({
        has_credentials: false,
        credentials_path: '/tmp/credentials.json',
        output_dir: '/tmp/conversations',
        existing_count: 0,
        credentials_age_days: null,
      }),
    })
  })

  // /api/preferences — stateful GET/PATCH/PUT, shared across contexts.
  await context.route('**/api/preferences', (route) => {
    const req = route.request()
    const method = req.method()
    if (method === 'PATCH' || method === 'PUT') {
      let body: { data?: Record<string, unknown> } = {}
      try {
        body = JSON.parse(req.postData() ?? '{}') as { data?: Record<string, unknown> }
      } catch {
        body = {}
      }
      const incoming = body.data ?? {}
      if (method === 'PUT') {
        prefsState.data = { ...incoming }
      } else {
        Object.assign(prefsState.data, incoming)
      }
    }
    fulfill(route, {
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ version: 1, data: prefsState.data }),
    })
  })
}

async function waitForPrefsPatch(page: Page): Promise<void> {
  await page.waitForResponse(
    (r) => r.url().endsWith('/api/preferences') && r.request().method() === 'PATCH',
  )
}

async function newPage(browser: Browser, prefsState: PrefsState): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext()
  await installSharedMocks(context, prefsState)
  const page = await context.newPage()
  return { context, page }
}

test.describe('G1 — preferences persist across browser contexts', () => {
  test('all three §16.1 prefs changed in context A are visible in context B', async ({ browser }) => {
    // Shared state — both contexts mutate / read from this object.
    const prefsState: PrefsState = { data: {} }

    // Context A — make all three pref changes.
    const a = await newPage(browser, prefsState)
    try {
      await withNetRetry(a.page, () => a.page.goto('/settings'))

      const exportSection = a.page.locator('[data-section="markdown-export"]')
      await expect(exportSection).toBeVisible()

      // 1. Theme → Dark. Wait for the PATCH AND for prefsState to
      // actually contain the new value before moving on — this absorbs
      // the case where the response handler is slower than the
      // waitForResponse resolver.
      let patch = waitForPrefsPatch(a.page)
      await a.page.locator('label:has-text("Dark")').click()
      await patch
      await expect.poll(() => prefsState.data.theme, { timeout: 3000 }).toBe('dark')

      // 2. Keyboard → Vim.
      patch = waitForPrefsPatch(a.page)
      await a.page.locator('label:has-text("Vim")').click()
      await patch
      await expect.poll(() => prefsState.data.keyboardMode, { timeout: 3000 }).toBe('vim')

      // 3. Markdown export mode → Bundle Obsidian (unified key as of
      // 2026-05-29). Scope to the Export section because "Obsidian"
      // appears in the radio label. Use .click() instead of .check()
      // because Playwright's .check() asserts aria-checked='true' on the
      // SAME tick as the click, which races our controlled-component
      // pipeline (Radix onValueChange → setMarkdownExportMode →
      // mutation.mutate → next render flips aria-checked). We verify
      // the post-click state via the PATCH log + expect.poll below.
      patch = waitForPrefsPatch(a.page)
      await exportSection.getByRole('radio', { name: 'Bundle Obsidian' }).click()
      await patch
      await expect.poll(() => prefsState.data.markdownExportMode, { timeout: 3000 }).toBe('bundle-obsidian')
    } finally {
      await a.context.close()
    }

    // Context B — fresh browser context, same in-memory prefs map.
    // The only thing that survives between contexts is the shared
    // prefsState (since localStorage is per-context). Anything B reads
    // back must come from the /api/preferences GET — which is the
    // contract we're proving.
    const b = await newPage(browser, prefsState)
    try {
      await withNetRetry(b.page, () => b.page.goto('/settings'))

      // Theme: dark class on <html>.
      await expect(b.page.locator('html')).toHaveClass(/dark/)
      // Keyboard: vim radio is checked.
      await expect(
        b.page.locator('button[role="radio"][value="vim"]'),
      ).toHaveAttribute('data-state', 'checked')
      // Markdown export mode: Bundle Obsidian radio is checked.
      await expect(
        b.page
          .locator('[data-section="markdown-export"]')
          .getByRole('radio', { name: 'Bundle Obsidian' }),
      ).toBeChecked()
    } finally {
      await b.context.close()
    }
  })

  test('context B does NOT see prefs when there is no shared state (negative control)', async ({ browser }) => {
    // Bidirectional check: this test installs TWO INDEPENDENT prefsState
    // maps — one per context — so a regression where the fixture
    // accidentally bridged state would FAIL this test. The user-facing
    // promise is "shared backend = shared prefs"; we want the dual
    // promise enforced too: "isolated backend = isolated prefs".
    const stateA: PrefsState = { data: {} }
    const stateB: PrefsState = { data: {} }

    const a = await newPage(browser, stateA)
    try {
      await withNetRetry(a.page, () => a.page.goto('/settings'))
      const patch = waitForPrefsPatch(a.page)
      await a.page.locator('label:has-text("Dark")').click()
      await patch
      expect(stateA.data.theme).toBe('dark')
    } finally {
      await a.context.close()
    }

    const b = await newPage(browser, stateB)
    try {
      await withNetRetry(b.page, () => b.page.goto('/settings'))
      // <html> must NOT have the dark class because stateB never
      // received a PATCH.
      const htmlClass = (await b.page.locator('html').getAttribute('class')) ?? ''
      expect(htmlClass).not.toMatch(/\bdark\b/)
      expect(stateB.data.theme).toBeUndefined()
    } finally {
      await b.context.close()
    }
  })
})
