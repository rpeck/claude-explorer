/**
 * P3d — SourceFilterContext + showPhantomSessions dual-read/dual-write
 * migration via usePreferences.
 *
 * After this commit:
 *   - SourceFilterContext.organizationId persists via usePreferences
 *     under the EXISTING legacy key 'claude-explorer.organizationFilter'
 *     (no key rename — keeps existing browser sessions working).
 *   - SourceFilterContext.sourceFilter ('all' | 'CLAUDE_AI' | 'CLAUDE_CODE')
 *     is newly persisted under 'sourceFilter'.
 *   - SettingsContext.showPhantomSessions is newly persisted under
 *     'showPhantomSessions' (was ephemeral useState before).
 *
 * Tests assert the dual side: server PATCH + local mirror.
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

test.describe('SourceFilterContext + showPhantomSessions preferences migration (P3d)', () => {
  test('sourceFilter change PATCHes server preferences and mirrors localStorage', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    const { patches } = await installPrefsRoute(page, {})

    await withNetRetry(page, () => page.goto('/'))
    await page.evaluate(() => localStorage.clear())
    await withNetRetry(page, () => page.reload())

    // Open the source-filter Select and click Claude Code.
    // CF1: the active-filter picker is now the first combobox in the
    // sidebar, so we filter to the source-filter's content text.
    await page
      .getByRole('combobox')
      .filter({ hasText: /All Conversations|Claude Desktop|Claude Code/ })
      .first()
      .click()
    await page.getByRole('option', { name: /Claude Code/ }).click()

    await expect
      .poll(() => patches.bodies.length, { timeout: 5_000 })
      .toBeGreaterThan(0)
    const sawSourcePatch = patches.bodies.some(
      (b) => (b as Record<string, unknown>)['sourceFilter'] === 'CLAUDE_CODE',
    )
    expect(sawSourcePatch).toBe(true)

    const stored = await page.evaluate(() => localStorage.getItem('sourceFilter'))
    expect(stored).toBe(JSON.stringify('CLAUDE_CODE'))
  })

  test('sourceFilter restored from server preferences on first mount', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    await installPrefsRoute(page, { sourceFilter: 'CLAUDE_CODE' })

    await withNetRetry(page, () => page.goto('/'))
    await page.evaluate(() => localStorage.clear())
    await withNetRetry(page, () => page.reload())

    // SelectTrigger renders the current value as text content.
    // CF1: filter by content to avoid matching the active-filter picker.
    const trigger = page
      .getByRole('combobox')
      .filter({ hasText: /All Conversations|Claude Desktop|Claude Code/ })
      .first()
    await expect(trigger).toContainText(/Claude Code/i, { timeout: 5_000 })
  })

  test('showPhantomSessions toggle PATCHes server preferences', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    const { patches } = await installPrefsRoute(page, {})

    await withNetRetry(page, () => page.goto('/'))
    await page.evaluate(() => localStorage.clear())
    await withNetRetry(page, () => page.reload())

    // Sidebar "Empty" checkbox toggles showPhantomSessions.
    const toggle = page.getByTestId('show-phantom-sessions-toggle')
    await expect(toggle).toBeVisible({ timeout: 5_000 })
    await toggle.click()

    await expect
      .poll(() => patches.bodies.length, { timeout: 5_000 })
      .toBeGreaterThan(0)
    const sawPatch = patches.bodies.some(
      (b) => (b as Record<string, unknown>)['showPhantomSessions'] === true,
    )
    expect(sawPatch).toBe(true)

    const stored = await page.evaluate(() =>
      localStorage.getItem('showPhantomSessions'),
    )
    expect(stored).toBe(JSON.stringify(true))
  })
})
