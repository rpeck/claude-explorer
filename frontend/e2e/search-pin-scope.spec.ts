import { test, expect, makeSummary, makeMessage, makeDetail, type Page, type Route, withNetRetry, waitForKeyboardShortcuts } from './fixtures'
import type { Message } from '../src/lib/types'

/**
 * Manual finding 2026-05-04: search scope pin.
 *
 *   - Default scope is global. Pin button on the conversation header
 *     lets the user constrain search to (a) this conversation OR
 *     (b) this project.
 *   - Pin is encoded in the URL as ?pin=conv:<uuid> or
 *     ?pin=project:<path>; durable across reload.
 *   - Sidebar dim: out-of-scope rows get opacity-60.
 *   - SearchPanel header chip mirrors the pin and offers an X to clear.
 *   - Empty state in scoped search shows "Unpin and search all →".
 *   - Sidebar title-search typing clears the pin (title search is
 *     global by construction; signals broadening intent).
 */

const A = '00000000-0000-0000-0000-000000a1a1a1'
const B = '00000000-0000-0000-0000-000000b2b2b2'
const C = '00000000-0000-0000-0000-000000c3c3c3'

const summaries = [
  makeSummary({
    uuid: A,
    name: 'ProjectA session 1',
    source: 'CLAUDE_CODE',
    project_path: '/work/projectA',
    project_name: 'projectA',
  }),
  makeSummary({
    uuid: B,
    name: 'ProjectA session 2',
    source: 'CLAUDE_CODE',
    project_path: '/work/projectA',
    project_name: 'projectA',
  }),
  makeSummary({
    uuid: C,
    name: 'ProjectB session',
    source: 'CLAUDE_CODE',
    project_path: '/work/projectB',
    project_name: 'projectB',
  }),
]

function detailFor(uuid: string, name: string, projectPath: string) {
  const m = makeMessage({
    uuid: `${uuid}-m1`,
    sender: 'human',
    text: 'needle text',
    content: [{ type: 'text', text: 'needle text' }],
  } as Partial<Message> & { uuid: string })
  return makeDetail(
    makeSummary({ uuid, name, source: 'CLAUDE_CODE', project_path: projectPath, project_name: projectPath.split('/').pop() }),
    [m],
  )
}

const details: Record<string, ReturnType<typeof detailFor>> = {
  [A]: detailFor(A, 'ProjectA session 1', '/work/projectA'),
  [B]: detailFor(B, 'ProjectA session 2', '/work/projectA'),
  [C]: detailFor(C, 'ProjectB session', '/work/projectB'),
}

async function mockSearch(page: Page, returnFor: (params: URLSearchParams) => unknown[]) {
  await page.route('**/api/search**', (route: Route) => {
    const url = new URL(route.request().url())
    const results = returnFor(url.searchParams)
    const body = JSON.stringify({
      results,
      total_messages_matched: results.length,
      returned_messages: results.length,
      truncated: false,
    })
    route.fulfill({ status: 200, contentType: 'application/json', body })
  })
}

test.describe('Search pin scope (manual finding 2026-05-04)', () => {
  test('Pin button appears on conversation header with conv + project options', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: summaries, details })
    await withNetRetry(page, () => page.goto(`/conversations/${A}`))

    const pin = page.getByTestId('pin-scope-button')
    await expect(pin).toBeVisible({ timeout: 5000 })
    await pin.click()

    const menu = page.getByTestId('pin-scope-menu')
    await expect(menu).toBeVisible()
    await expect(menu.getByTestId('pin-this-conversation')).toBeVisible()
    await expect(menu.getByTestId('pin-this-project')).toBeVisible()
  })

  test('Pinning conversation writes ?pin=conv:<uuid> and dims out-of-scope sidebar rows', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: summaries, details })
    await withNetRetry(page, () => page.goto(`/conversations/${A}`))

    await page.getByTestId('pin-scope-button').click()
    await page.getByTestId('pin-this-conversation').click()

    await expect(page).toHaveURL(new RegExp(`pin=conv(?::|%3A)${A}`))

    const rowB = page.locator(`[data-out-of-scope="true"]`).filter({ hasText: 'ProjectA session 2' }).first()
    await expect(rowB).toBeVisible({ timeout: 3000 })

    // P1.1: dim level must be 0.4 exactly (was 0.6 — not visually distinct
    // enough). toBeCloseTo with precision=1 accepts anything in the band
    // [0.35, 0.45) — exact 0.4 plus float rounding, but NOT 0.6 or 0.3.
    const opacity = await rowB.evaluate((el) => window.getComputedStyle(el).opacity)
    expect(parseFloat(opacity)).toBeCloseTo(0.4, 1)
  })

  test('Pinning project dims sessions outside the project', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: summaries, details })
    await withNetRetry(page, () => page.goto(`/conversations/${A}`))

    await page.getByTestId('pin-scope-button').click()
    await page.getByTestId('pin-this-project').click()

    await expect(page).toHaveURL(/pin=project%3A/)

    // ProjectB session should be dimmed; ProjectA siblings should NOT.
    const rowC = page.locator('[data-out-of-scope="true"]').filter({ hasText: 'ProjectB session' }).first()
    await expect(rowC).toBeVisible({ timeout: 3000 })
    const rowA2 = page.locator('[data-out-of-scope="false"]').filter({ hasText: 'ProjectA session 2' }).first()
    await expect(rowA2).toBeVisible({ timeout: 3000 })
  })

  test('SearchPanel header shows scope chip mirroring the pin', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: summaries, details })
    await withNetRetry(page, () => page.goto(`/conversations/${A}`))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')

    await page.getByTestId('pin-scope-button').click()
    await page.getByTestId('pin-this-conversation').click()

    // Open SearchPanel via Cmd+F (or Cmd+K).
    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')

    const chip = page.getByTestId('search-scope-chip')
    await expect(chip).toBeVisible({ timeout: 3000 })
    await expect(chip).toContainText('ProjectA session 1')
  })

  test('Search request includes conversation_uuid when pinned to a conversation', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: summaries, details })
    const observedConvUuids: (string | null)[] = []
    const allRequests: string[] = []

    page.on('request', (req) => {
      const u = req.url()
      if (u.includes('search')) allRequests.push(u)
    })

    await page.route('**/api/search**', (route: Route) => {
      const url = new URL(route.request().url())
      observedConvUuids.push(url.searchParams.get('conversation_uuid'))
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          results: [],
          total_messages_matched: 0,
          returned_messages: 0,
          truncated: false,
        }),
      })
    })

    await withNetRetry(page, () => page.goto(`/conversations/${A}`))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.getByTestId('pin-scope-button').click()
    await page.getByTestId('pin-this-conversation').click()

    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible({ timeout: 3000 })
    await input.click()
    await input.fill('needle')

    void allRequests
    await expect
      .poll(() => observedConvUuids.includes(A), { timeout: 7000 })
      .toBe(true)
  })

  test('Empty results in scoped search show "Unpin and search all →" CTA', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: summaries, details })
    await mockSearch(page, () => [])
    await withNetRetry(page, () => page.goto(`/conversations/${A}`))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.getByTestId('pin-scope-button').click()
    await page.getByTestId('pin-this-conversation').click()

    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await input.fill('zzzznever')

    const cta = page.getByTestId('search-unpin-and-search-all')
    await expect(cta).toBeVisible({ timeout: 3000 })

    await cta.click()
    // Pin should clear → URL no longer has ?pin=
    await expect(page).not.toHaveURL(/[?&]pin=/)
  })

  test('Sidebar title-search typing clears the pin', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: summaries, details })
    await withNetRetry(page, () => page.goto(`/conversations/${A}`))
    await page.getByTestId('pin-scope-button').click()
    await page.getByTestId('pin-this-conversation').click()
    await expect(page).toHaveURL(new RegExp(`pin=conv(?::|%3A)${A}`))

    const sidebar = page.getByTestId('sidebar-title-search')
    await sidebar.click()
    await sidebar.pressSequentially('foo', { delay: 20 })
    await expect(page).not.toHaveURL(/[?&]pin=/, { timeout: 3000 })
  })

  test('Unpin item is always visible in dropdown (disabled when no pin)', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: summaries, details })
    await withNetRetry(page, () => page.goto(`/conversations/${A}`))
    // Open dropdown WITHOUT pinning anything.
    await page.getByTestId('pin-scope-button').click()
    await expect(page.getByTestId('pin-scope-menu')).toBeVisible()
    const unpin = page.getByTestId('pin-unpin')
    await expect(unpin).toBeVisible()
    await expect(unpin).toBeDisabled()
  })

  test('Pin survives reload via URL param', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: summaries, details })
    await withNetRetry(page, () => page.goto(`/conversations/${A}?pin=conv:${A}`))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')

    // Chip visible after Cmd+F without re-pinning.
    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')

    const chip = page.getByTestId('search-scope-chip')
    await expect(chip).toBeVisible({ timeout: 3000 })
  })
})
