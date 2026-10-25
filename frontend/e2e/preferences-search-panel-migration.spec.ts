/**
 * P3e — SearchPanelContext dual-read/dual-write migration via usePreferences.
 *
 * After this commit, the four persistent SearchPanelContext keys
 * (searchPanel.isOpen, searchPanel.contextSize, searchPanel.sortField,
 * searchPanel.sortOrder) must:
 *
 *   1. PATCH /api/preferences when the user changes them, with the
 *      changed key under `data` using the EXACT legacy key string.
 *   2. Mirror the new value into localStorage under the same legacy key
 *      (no key renames — keeps existing browser sessions working).
 *   3. Read from the server envelope on first mount when the server has
 *      a value for that key (server beats local; local beats fallback).
 *
 * Local mirror writes are NOT removed in this commit — that comes after
 * a soak window. Tests assert the dual side: server PATCH + local mirror.
 */

import { test, expect, withNetRetry, E2E_DEFAULT_PREFS } from './fixtures'
import type { Route, Page } from './fixtures'

interface PrefsState {
  data: Record<string, unknown>
}

interface PatchLog {
  bodies: Array<Record<string, unknown>>
}

async function installPrefsRoute(
  page: Page,
  initial: Record<string, unknown> = {},
): Promise<{ state: PrefsState; patches: PatchLog }> {
  // E2E_DEFAULT_PREFS: the recent-sessions filter is off (fixture dates are in the past).
  const state: PrefsState = { data: { ...E2E_DEFAULT_PREFS, ...initial } }
  const patches: PatchLog = { bodies: [] }

  await page.route('**/api/preferences', (route: Route) => {
    const req = route.request()
    if (req.method() === 'PATCH') {
      let body: { data?: Record<string, unknown> } = {}
      try {
        body = JSON.parse(req.postData() ?? '{}')
      } catch {
        body = {}
      }
      const patchData = body.data ?? {}
      patches.bodies.push(patchData)
      Object.assign(state.data, patchData)
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ version: 1, data: state.data }),
      })
      return
    }
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ version: 1, data: state.data }),
    })
  })

  return { state, patches }
}

test.describe('SearchPanelContext preferences migration (P3e)', () => {
  test('SearchPanel contextSize toggle PATCHes server preferences', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    const { patches } = await installPrefsRoute(page, {})

    await withNetRetry(page, () => page.goto('/conversations'))
    await page.evaluate(() => localStorage.clear())
    await withNetRetry(page, () => page.reload())

    // Open the search panel via Cmd+F (works on both mac/linux due to
    // the cmdOrCtrl branch in useKeyboardShortcuts).
    await page.keyboard.press('Meta+f')

    // Click the "Full" radio in the snippet/full segmented control.
    const fullRadio = page.getByRole('radio', { name: 'Full' })
    await expect(fullRadio).toBeVisible()
    await fullRadio.click()

    // 1) PATCH must have been sent with searchPanel.contextSize='full'.
    await expect
      .poll(() => patches.bodies.length, { timeout: 5_000 })
      .toBeGreaterThan(0)
    const sawContextSizePatch = patches.bodies.some(
      (b) => (b as Record<string, unknown>)['searchPanel.contextSize'] === 'full',
    )
    expect(sawContextSizePatch).toBe(true)

    // 2) localStorage mirror must contain the new value under the legacy key.
    const stored = await page.evaluate(() =>
      localStorage.getItem('searchPanel.contextSize'),
    )
    expect(stored).toBe(JSON.stringify('full'))

    // 3) Migration marker is set.
    const marker = await page.evaluate(() =>
      localStorage.getItem('prefs_migrated_v1'),
    )
    expect(marker).toBe('true')
  })

  test('searchPanel.isOpen restored from server preferences', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    await installPrefsRoute(page, { 'searchPanel.isOpen': true })

    // Make sure we are not relying on a stale local value.
    await withNetRetry(page, () => page.goto('/'))
    await page.evaluate(() => localStorage.clear())

    await withNetRetry(page, () => page.goto('/conversations'))

    // After mount the server says isOpen=true, so the panel must be
    // rendered with aria-hidden=false on first paint.
    const panel = page.getByRole('complementary', { name: 'Search panel' })
    await expect(panel).toHaveAttribute('aria-hidden', 'false')
  })
})
