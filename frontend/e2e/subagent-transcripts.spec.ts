import { test, expect, installLocalPrefsMock, withNetRetry, waitForScrollQuiet, type Page, type Route } from './fixtures'

/**
 * Issue #96: open and search subagent transcripts, on the REAL fixture
 * backend (playwright.config.ts). `/api/conversations*` and `/api/search*`
 * are never mocked. Only the writers (`/api/preferences`, `/api/bookmarks`)
 * and the PDF download are mocked.
 *
 * The fixture sessions (scripts/generate_e2e_fixtures.py,
 * backend/tests/fixtures/README.md):
 *
 *   - The real recording 2339aab4… (Claude Code 2.1.331) with its subagent
 *     a47f122549f03fce6. Its meta file gives "List src directory". Only the
 *     subagent's Code Mode script holds `nothrow`.
 *   - A synthetic session 5a6e…0096 with a current-layout agent ("Synthetic
 *     search agent") and a legacy agent (no meta file). Each word is in one
 *     place only: zqagenttext (agent reply), zqagenttool (agent tool
 *     output), zqshared (session reply and agent reply), zqlegacyagent
 *     (legacy agent reply), zqsessiontext (session prompt).
 *
 * The console gate of TESTING.md §5.15 runs for every test (`./fixtures`).
 */

const REAL_SESSION = '2339aab4-744c-4556-95b8-5ee5552c1b85'
const REAL_AGENT = 'a47f122549f03fce6'
const REAL_DESCRIPTION = 'List src directory'

const SESSION = '5a6e0000-0000-4000-8000-000000000096'
const AGENT = 'a5a0000000000096c'
const AGENT_DESCRIPTION = 'Synthetic search agent'
const LEGACY_AGENT = '5a1e9ac'
const AGENT_REPLY = 'sa-t1'
const SESSION_REPLY = '0096-t1'

const W = {
  text: 'zqagenttext',
  tool: 'zqagenttool',
  shared: 'zqshared',
  legacy: 'zqlegacyagent',
  session: 'zqsessiontext',
} as const

const agentUrl = (s: string, a: string) => `/conversations/${s}/agents/${a}`

async function mockWriters(page: Page): Promise<void> {
  await installLocalPrefsMock(page)
  await page.route('**/api/bookmarks', (route: Route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ bookmarks: [] }) }),
  )
}

async function openSearch(page: Page) {
  await page.keyboard.press('Meta+k')
  const input = page.getByPlaceholder('Search messages...')
  await expect(input).toBeVisible()
  return input
}

const breadcrumb = (page: Page) => page.getByRole('navigation', { name: 'Breadcrumb' })

test.describe('Subagent transcripts (#96)', () => {
  test.beforeEach(async ({ page }) => {
    await mockWriters(page)
  })

  test('the agent page shows the agent transcript and a breadcrumb back to the session', async ({ page }) => {
    await withNetRetry(page, () => page.goto(agentUrl(SESSION, AGENT)))
    await expect(page.locator(`[data-message-uuid="${AGENT_REPLY}"]`)).toContainText(W.text)
    // The session's own messages are not on the agent page (its title is in the list).
    await expect(page.locator('[data-message-uuid]').filter({ hasText: W.session })).toHaveCount(0)

    const nav = breadcrumb(page)
    await expect(nav).toContainText(AGENT_DESCRIPTION)
    await nav.getByRole('link', { name: 'Session' }).click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${SESSION}$`))
    await expect(page.locator(`[data-message-uuid="${SESSION_REPLY}"]`)).toBeVisible()
  })

  test('the breadcrumb of an agent without a description shows its id', async ({ page }) => {
    await withNetRetry(page, () => page.goto(agentUrl(SESSION, LEGACY_AGENT)))
    await expect(page.getByText(W.legacy)).toBeVisible()
    await expect(breadcrumb(page)).toContainText(LEGACY_AGENT)
  })

  test('the session Details list each agent, and a link opens it', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${SESSION}`))
    const row = page.getByTestId('details-subagents')
    await expect(row).toContainText('Subagents:')
    await expect(row.getByRole('link', { name: LEGACY_AGENT })).toHaveAttribute('href', agentUrl(SESSION, LEGACY_AGENT))
    await row.getByRole('link', { name: AGENT_DESCRIPTION }).click()
    await expect(page).toHaveURL(new RegExp(`${agentUrl(SESSION, AGENT)}$`))
    await expect(page.getByText(W.text)).toBeVisible()
  })

  test('the real subagent opens with its description, and Show Code Mode shows its run', async ({ page }) => {
    await withNetRetry(page, () => page.goto(agentUrl(REAL_SESSION, REAL_AGENT)))
    await expect(breadcrumb(page)).toContainText(REAL_DESCRIPTION)
    await expect(page.getByTestId('code-run')).toHaveCount(0)
    await page.getByTestId('header-show-code-mode-checkbox').check()
    await expect(page.getByTestId('code-run')).toHaveCount(1)
    await expect(page.getByTestId('code-run-script')).toContainText('nothrow')
  })

  test('a search hit inside an agent shows the description, and a click opens the agent at the message', async ({ page }) => {
    await withNetRetry(page, () => page.goto('/'))
    const input = await openSearch(page)
    await input.fill(W.text)
    const card = page.locator('[data-result-card]').filter({ has: page.getByTestId('search-hit-agent') })
    await expect(card).toHaveCount(1, { timeout: 10_000 })
    await expect(card.getByTestId('search-hit-agent')).toHaveText(AGENT_DESCRIPTION)
    await expect(page.locator('[data-result-card]')).toHaveCount(1)
    await card.click()

    await expect(page).toHaveURL(new RegExp(agentUrl(SESSION, AGENT)))
    const bubble = page.locator(`[data-message-uuid="${AGENT_REPLY}"]`)
    await expect(bubble).toHaveClass(/ring-yellow-400/, { timeout: 5_000 })
    await waitForScrollQuiet(page)
    await expect(bubble).toBeInViewport()
  })

  test('Show Tools applies to an agent hit', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${SESSION}`))
    const input = await openSearch(page)
    await input.fill(W.tool)
    await expect(page.getByText(/No matches for/)).toBeVisible({ timeout: 10_000 })
    await page.getByTestId('header-show-tools-checkbox').check()
    const card = page.locator('[data-result-card]').filter({ has: page.getByTestId('search-hit-agent') })
    await expect(card).toHaveCount(1, { timeout: 10_000 })
  })

  test('Show Code Mode applies to an agent hit in the real recording', async ({ page }) => {
    await withNetRetry(page, () => page.goto(agentUrl(REAL_SESSION, REAL_AGENT)))
    await expect(breadcrumb(page)).toBeVisible()
    const input = await openSearch(page)
    await input.fill('nothrow')
    await expect(page.getByText(/No matches for/)).toBeVisible({ timeout: 10_000 })
    await page.getByTestId('header-show-code-mode-checkbox').check()
    const card = page.locator('[data-result-card]').filter({ has: page.getByTestId('search-hit-agent') })
    await expect(card.getByTestId('search-hit-agent')).toHaveText(REAL_DESCRIPTION, { timeout: 10_000 })
  })

  test('Cmd+G moves between a session hit and an agent hit', async ({ page }) => {
    await withNetRetry(page, () => page.goto('/'))
    const input = await openSearch(page)
    await input.fill(W.shared)
    await expect(page.locator('[data-result-card]')).toHaveCount(2, { timeout: 10_000 })
    const isMac = process.platform === 'darwin'
    const seen = new Set<string>()
    for (let i = 0; i < 2; i++) {
      await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')
      await expect(page).toHaveURL(new RegExp(`/conversations/${SESSION}`))
      await expect(page.locator(`[data-message-uuid="${AGENT_REPLY}"], [data-message-uuid="${SESSION_REPLY}"]`)).toHaveClass(/ring-yellow-400/, { timeout: 5_000 })
      seen.add(new URL(page.url()).pathname)
    }
    expect([...seen].sort()).toEqual([`/conversations/${SESSION}`, agentUrl(SESSION, AGENT)].sort())
  })

  test('Copy as Markdown on the agent page copies the agent transcript', async ({ page }) => {
    await withNetRetry(page, () => page.goto(agentUrl(SESSION, AGENT)))
    await expect(page.getByText(W.text)).toBeVisible()
    await page.getByRole('button', { name: 'Copy as Markdown' }).click()
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain(W.text)
    const text = await page.evaluate(() => navigator.clipboard.readText())
    expect(text).not.toContain(W.session)
  })

  test('the PDF export on the agent page names the agent', async ({ page }) => {
    const urls: string[] = []
    await page.route('**/export/pdf**', (route: Route) => {
      urls.push(route.request().url())
      return route.fulfill({ contentType: 'application/pdf', body: '%PDF-1.4\n%%EOF\n' })
    })
    await withNetRetry(page, () => page.goto(agentUrl(SESSION, AGENT)))
    await expect(page.getByText(W.text)).toBeVisible()
    await page.getByRole('button', { name: 'PDF' }).click()
    await expect.poll(() => urls.length).toBe(1)
    expect(urls[0]).toContain(`/conversations/${SESSION}/export/pdf`)
    expect(urls[0]).toContain(`agent_id=${AGENT}`)
  })
})
