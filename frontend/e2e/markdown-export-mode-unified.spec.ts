/**
 * markdown-export-mode unification — the Settings page Export section and
 * the conversation header's Markdown dialog must share a SINGLE pref key
 * (`markdownExportMode`). Before unification the Settings page wrote
 * `markdownBundleImages` + `markdownDialect`, which nothing else read,
 * so the user's Settings choice never reached the dialog or the export.
 *
 * Contract proven by this file (bidirectional):
 *   1. Setting the radio in Settings PATCHes `markdownExportMode` (single
 *      key) and the same value pre-selects the dialog's radio.
 *   2. "Save as default" in the dialog PATCHes `markdownExportMode` and
 *      the new value is reflected back in the Settings page radio on
 *      next mount.
 *   3. The orphan keys `markdownBundleImages` + `markdownDialect` are
 *      never PATCHed (regression guard against the dead-write bug).
 */
import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry } from './fixtures'
import type { Route } from './fixtures'

const ME = '00000000-0000-0000-0000-0000000000d8'

const summary = makeSummary({
  uuid: ME,
  name: 'Markdown unified fixture',
  message_count: 1,
  source: 'CLAUDE_CODE',
})
const detail = makeDetail(summary, [
  makeMessage({ uuid: 'm1', sender: 'human', text: 'Hi', content: [{ type: 'text', text: 'Hi' }] }),
])

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
  const state: PrefsState = { data: { ...initial } }
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

test.describe('Markdown export mode unification', () => {
  test('Settings page → dialog: choosing Bundle Obsidian in Settings pre-selects same radio in dialog', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [summary], details: { [ME]: detail } })
    const { state, patches } = await installPrefsRoute(page, {})

    await withNetRetry(page, () => page.goto('/settings'))

    // Three options live under the Export section now: Inline /
    // Bundle CommonMark / Bundle Obsidian. Choose Bundle Obsidian.
    // Use .click() rather than .check(): Playwright's .check() asserts
    // aria-checked="true" before returning, which races our Radix +
    // usePreferences controlled-component pipeline (onValueChange →
    // setMarkdownExportMode → useMutation → next render flips
    // aria-checked). See TESTING.md §3 "Radix RadioGroup .check()
    // races controlled-component re-renders". We verify the post-click
    // state via the PATCH log below.
    const settingsExport = page.locator('[data-section="markdown-export"]')
    await expect(settingsExport).toBeVisible()
    await settingsExport.getByRole('radio', { name: 'Bundle Obsidian' }).click()

    // 1) PATCH lands with the canonical single key.
    await expect.poll(() => patches.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0)
    const sawModePatch = patches.bodies.some(
      (b) => (b as Record<string, unknown>).markdownExportMode === 'bundle-obsidian',
    )
    expect(sawModePatch).toBe(true)

    // 2) Regression guard: orphan keys must NEVER be written.
    const orphanWrites = patches.bodies.filter(
      (b) =>
        'markdownBundleImages' in (b as Record<string, unknown>) ||
        'markdownDialect' in (b as Record<string, unknown>),
    )
    expect(orphanWrites).toEqual([])
    expect(state.data.markdownBundleImages).toBeUndefined()
    expect(state.data.markdownDialect).toBeUndefined()

    // 3) Open the dialog from a conversation page — its radio must
    // reflect the choice made in Settings (same key).
    await withNetRetry(page, () => page.goto(`/conversations/${ME}`))
    await page.getByRole('button', { name: 'Markdown', exact: true }).click()
    const dialog = page.getByTestId('markdown-export-dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('radio', { name: 'Bundle Obsidian' })).toBeChecked()
  })

  test('Dialog Save-as-default → Settings page reflects the new mode on next mount', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [summary], details: { [ME]: detail } })
    const { patches } = await installPrefsRoute(page, {})

    await withNetRetry(page, () => page.goto(`/conversations/${ME}`))
    await page.getByRole('button', { name: 'Markdown', exact: true }).click()

    const dialog = page.getByTestId('markdown-export-dialog')
    await expect(dialog).toBeVisible()
    // .click() over .check() for the Radix radio — same controlled-
    // component race as Settings page above. The "Save as default"
    // checkbox is a native <input type="checkbox">, which updates
    // synchronously and stays on .check().
    await dialog.getByRole('radio', { name: 'Bundle CommonMark' }).click()
    await dialog.getByLabel('Save as default').check()

    // Wait for the PATCH so the next nav reads stable state.
    const patchSettled = page.waitForResponse(
      (r) => r.url().endsWith('/api/preferences') && r.request().method() === 'PATCH',
    )
    await dialog.getByRole('button', { name: 'Download' }).click()
    await patchSettled

    const sawModePatch = patches.bodies.some(
      (b) => (b as Record<string, unknown>).markdownExportMode === 'bundle-commonmark',
    )
    expect(sawModePatch).toBe(true)

    // Settings page reads the same key.
    await withNetRetry(page, () => page.goto('/settings'))
    const settingsExport = page.locator('[data-section="markdown-export"]')
    await expect(settingsExport).toBeVisible()
    await expect(
      settingsExport.getByRole('radio', { name: 'Bundle CommonMark' }),
    ).toBeChecked()
  })

  test('Settings page default radio reflects server-stored markdownExportMode', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [summary], details: { [ME]: detail } })
    await installPrefsRoute(page, { markdownExportMode: 'bundle-obsidian' })

    await withNetRetry(page, () => page.goto('/settings'))
    const settingsExport = page.locator('[data-section="markdown-export"]')
    await expect(settingsExport).toBeVisible()
    await expect(
      settingsExport.getByRole('radio', { name: 'Bundle Obsidian' }),
    ).toBeChecked()
  })
})
