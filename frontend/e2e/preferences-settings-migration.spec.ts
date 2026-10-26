/**
 * P3c — SettingsContext dual-read/dual-write migration via usePreferences.
 *
 * After this commit, every persistent SettingsContext key (theme,
 * keyboardMode, sortField, sortOrder, groupByProject, hideCompactMarkers,
 * rightPaneTab, markdownExportMode) must:
 *
 *   1. PATCH `/api/preferences` when the user changes it (the body
 *      contains the changed key under `data`).
 *   2. Mirror the new value into `localStorage` under the EXACT same
 *      string key the legacy code used (so existing browser sessions
 *      keep working — no key renames).
 *   3. Read from the server envelope on first mount when the server has
 *      a value for that key (server beats local; local beats fallback).
 *
 * Local mirror writes are NOT removed in this commit — that comes after
 * a soak window. Tests assert the dual side: server PATCH + local mirror.
 */

import { test, expect, withNetRetry, E2E_DEFAULT_PREFS } from './fixtures'
import type { Route } from './fixtures'

interface PrefsState {
  data: Record<string, unknown>
}

interface PatchLog {
  bodies: Array<Record<string, unknown>>
}

/**
 * Install a route handler for `/api/preferences` that holds a mutable
 * in-memory store. GET returns the current envelope; PATCH merges the
 * supplied `data` into the store and records the patch body.
 */
async function installPrefsRoute(
  page: import('@playwright/test').Page,
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

test.describe('SettingsContext preferences migration (P3c)', () => {
  test('writes to BOTH server and localStorage on theme change', async ({ page, mockBackend }) => {
    await mockBackend({})
    const { patches } = await installPrefsRoute(page, {})

    await withNetRetry(page, () => page.goto('/'))
    await page.evaluate(() => localStorage.clear())

    await withNetRetry(page, () => page.goto('/settings'))

    // Click the Dark theme radio. SettingsPage renders a RadioGroup of
    // Light/Dark/System labels.
    await page.locator('label:has-text("Dark")').click()

    // 1) PATCH must have been sent with theme=dark in the body.
    await expect.poll(() => patches.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0)
    const sawThemePatch = patches.bodies.some(
      (b) => (b as Record<string, unknown>).theme === 'dark',
    )
    expect(sawThemePatch).toBe(true)

    // 2) localStorage mirror must contain the new value under the legacy key.
    const stored = await page.evaluate(() => localStorage.getItem('theme'))
    expect(stored).toBe(JSON.stringify('dark'))

    // 3) The migration marker is set.
    const marker = await page.evaluate(() => localStorage.getItem('prefs_migrated_v1'))
    expect(marker).toBe('true')
  })

  test('reads theme from server preferences on first mount', async ({ page, mockBackend }) => {
    await mockBackend({})
    await installPrefsRoute(page, { theme: 'dark' })

    // Make sure we are not relying on a stale local value.
    await withNetRetry(page, () => page.goto('/'))
    await page.evaluate(() => localStorage.clear())
    await withNetRetry(page, () => page.reload())

    // After mount the server says theme=dark, so <html> must carry the
    // `dark` class regardless of the browser color-scheme media.
    await page.emulateMedia({ colorScheme: 'light' })
    await expect(page.locator('html')).toHaveClass(/dark/)
  })
})
