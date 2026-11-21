import { test, expect, installLocalPrefsMock, withNetRetry, waitForScrollQuiet, type Page, type Route } from './fixtures'
import { pickOption, sidebar } from './sidebar-list-filters.helpers'

/**
 * Issue #80: the Search Pane finds a word in every session source, and the
 * empty result names the source filter that limits it.
 *
 * The user's demand (2026-11-20): "You MUST test search with ALL session
 * sources!" Every test runs against the REAL fixture backend
 * (playwright.config.ts). Each source has one session with a word that no
 * other session holds (backend/tests/fixtures/README.md):
 *
 *   - Claude Desktop: NEEDLE_HANDSHAKE
 *   - Claude Code: NEEDLE_CC
 *   - local Cowork: NEEDLE_COWORKLOCAL (fixtures/cowork/local-agent-mode-sessions/)
 *   - remote Cowork: NEEDLE_COWORKREMOTE (fixtures/desktop/cowork-remote/)
 *   - Codex: semicolon (fixtures/desktop/codex/)
 *
 * Only `/api/preferences` and `/api/bookmarks` are mocked: their real routes
 * write next to the fixtures or read the developer's own file. The console
 * gate of TESTING.md §5.15 runs for every test (`./fixtures`).
 */

interface ApiBlock {
  type: string
  text?: string | null
}
interface ApiDetail {
  uuid: string
  messages: { uuid: string; content: ApiBlock[] }[]
}

const SOURCES = [
  { id: 'desktop', word: 'NEEDLE_HANDSHAKE', uuid: '0f415a45-9c62-8671-d4ad-53b84acb7e1a', title: 'Phase 5 fixture: TLS handshakes (long)' },
  { id: 'claude-code', word: 'NEEDLE_CC', uuid: '435e56ed-50ba-21c0-3495-0e8749b20543', title: 'Hi! NEEDLE_CC — fixture session for Phase 5 tests.' },
  { id: 'cowork-local', word: 'NEEDLE_COWORKLOCAL', uuid: 'cccc2222-3333-4444-5555-666677778888', title: 'E2E fixture: local Cowork session' },
  { id: 'cowork-remote', word: 'NEEDLE_COWORKREMOTE', uuid: 'session_01E2EREMOTE00000000000001', title: 'E2E fixture: remote Cowork task' },
  { id: 'codex', word: 'semicolon', uuid: '019a0f20-0000-7000-8000-00000000b002', title: 'Why does the build fail? Here is the error.' },
] as const

const CC = SOURCES[1]

async function mockWriters(page: Page): Promise<void> {
  await installLocalPrefsMock(page)
  await page.route('**/api/bookmarks', (route: Route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ bookmarks: [] }) }),
  )
}

/** The uuid of the message that holds `word`, from the real backend. */
async function messageWith(page: Page, uuid: string, word: string): Promise<string> {
  const r = await page.request.get(`/api/conversations/${uuid}`)
  expect(r.status(), `GET /api/conversations/${uuid}`).toBe(200)
  const detail = (await r.json()) as ApiDetail
  const m = detail.messages.find((msg) => msg.content.some((b) => (b.text ?? '').includes(word)))
  expect(m, `a message of ${uuid} that holds ${word}`).toBeTruthy()
  return m!.uuid
}

async function openSearch(page: Page) {
  await page.keyboard.press('Meta+k')
  const input = page.getByPlaceholder('Search messages...')
  await expect(input).toBeVisible()
  return input
}

/** The result card of a message match (a title match has the file icon). */
function messageCard(page: Page, title: string) {
  return page
    .locator('[data-result-card]')
    .filter({ hasText: title })
    .filter({ hasNot: page.locator('svg.lucide-file-text') })
}

test.describe('Search Pane: every session source (#80)', () => {
  test.beforeEach(async ({ page }) => {
    await mockWriters(page)
  })

  for (const s of SOURCES) {
    test(`${s.id}: the search finds ${s.word}, and a click jumps to the message`, async ({ page }) => {
      const target = await messageWith(page, s.uuid, s.word)
      await withNetRetry(page, () => page.goto('/'))
      await expect(sidebar(page)).toBeVisible()

      const input = await openSearch(page)
      await input.fill(s.word)
      const card = messageCard(page, s.title)
      await expect(card.first()).toBeVisible({ timeout: 10_000 })
      // No other session holds the word.
      await expect(page.locator('[data-result-card]').filter({ hasNotText: s.title })).toHaveCount(0)
      await card.first().click()

      await expect(page).toHaveURL(new RegExp(`/conversations/${s.uuid}`))
      const bubble = page.locator(`[data-message-uuid="${target}"]`)
      await expect(bubble).toHaveClass(/ring-yellow-400/, { timeout: 5_000 })
      await waitForScrollQuiet(page)
      await expect(bubble).toBeInViewport()
    })
  }

  test('#80: under the Codex source filter, the empty result names it, and "Search all agents" finds the Claude Code hit', async ({ page }) => {
    await withNetRetry(page, () => page.goto('/'))
    await expect(sidebar(page)).toBeVisible()
    await pickOption(page, 'source-filter-select', 'Codex')
    await expect(page.getByTestId('source-filter-select')).toContainText('Codex')

    const input = await openSearch(page)
    await input.fill(CC.word)
    await expect(page.getByText(/No matches for/)).toBeVisible({ timeout: 10_000 })
    // Show Tools is off by default, so the line also names it (#84).
    await expect(page.getByTestId('search-empty-filters')).toHaveText('Search limited by source “Codex” and hidden tool calls.')
    const clear = page.getByTestId('search-empty-clear-source')
    await expect(clear).toHaveText('Search all agents')
    await expect(page.locator('[data-result-card]')).toHaveCount(0)

    await clear.click()

    // The Sidebar control shows the change. (The list is virtualized, so the
    // Claude Code row, the oldest one, can be out of view; the select is the check.)
    await expect(page.getByTestId('source-filter-select')).toHaveText('All Agents')
    // The search ran again without the limit.
    await expect(messageCard(page, CC.title).first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('search-empty-filters')).toHaveCount(0)
    await expect(input).toBeFocused()
  })
})
