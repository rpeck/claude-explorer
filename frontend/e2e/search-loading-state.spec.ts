import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry, waitForKeyboardShortcuts } from './fixtures'
import type { SearchResult } from '../src/lib/types'

/**
 * Manual finding 2026-05-03 (Bug B): when search latency is high
 * (real backend takes 10-15s on a 600-conversation archive), the
 * SearchPanel shows "No matches" while the query is in flight, before
 * the response lands. It should say "Searching…" until results arrive.
 *
 * These tests reproduce by intentionally delaying the mocked
 * /api/search response. The bug is RED before the fix.
 */

const C = '00000000-0000-0000-0000-0000000000b1'

const summary = makeSummary({
  uuid: C,
  source: 'CLAUDE_AI',
  message_count: 1,
  name: 'Search loading fixture',
})
const m = makeMessage({
  uuid: 'sl-m1',
  sender: 'human',
  text: 'fixture body containing NEEDLE_LOAD',
  content: [{ type: 'text', text: 'fixture body containing NEEDLE_LOAD' }],
})
const detail = makeDetail(summary, [m])

const slowResults: SearchResult[] = [
  {
    conversation_uuid: C,
    conversation_name: summary.name,
    conversation_updated_at: summary.updated_at,
    conversation_created_at: summary.created_at,
    project_name: null,
    matching_messages: [
      {
        message_uuid: 'sl-m1',
        sender: 'human',
        snippet: 'fixture body containing NEEDLE_LOAD',
        match_start: 22,
        match_end: 33,
        created_at: m.created_at,
      },
    ],
  },
]

test.describe('Search panel loading state (Bug B)', () => {
  test('while a query is in flight, panel shows "Searching" not "No matches"', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [summary], details: { [C]: detail } })

    // Mocked /api/search holds the response for 1.5s — long enough for
    // the assertions below to inspect the in-flight state.
    let resolveSearch: (() => void) | null = null
    const releaseSearch = new Promise<void>((r) => {
      resolveSearch = r
    })
    let searchArrived = false
    await page.route('**/api/search**', async (route) => {
      searchArrived = true
      await releaseSearch
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          results: slowResults,
          total_messages_matched: slowResults.length,
          returned_messages: slowResults.length,
          truncated: false,
        }),
      })
    })

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.locator('main').click()
    await page.keyboard.press('Meta+k')
    const input = page.locator('input[placeholder="Search messages..."]')
    await expect(input).toBeVisible()
    await input.fill('NEEDLE_LOAD')

    // While the search is in flight, the panel MUST NOT say "No matches".
    // It SHOULD show some loading affordance ("Searching", spinner, etc.)
    const noMatches = page.getByText(/No matches/i)
    const loading = page.getByText(/Searching|Loading/i)

    // The request is in flight: the route holds it (2026-10-24: a fixed
    // 800 ms wait, which proved nothing about the request).
    await expect.poll(() => searchArrived).toBe(true)
    await expect(noMatches).toHaveCount(0)
    await expect(loading.first()).toBeVisible()

    // Now release the response and confirm results render.
    if (resolveSearch) (resolveSearch as () => void)()
    await expect(page.locator('[data-result-card]').first()).toBeVisible({ timeout: 5000 })
  })

  test('typing a new query while results from a previous query are visible shows loading, not stale "No matches"', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [summary], details: { [C]: detail } })

    let firstResponded = false
    let secondArrived = false
    let resolveSecond: (() => void) | null = null
    const releaseSecond = new Promise<void>((r) => {
      resolveSecond = r
    })

    await page.route('**/api/search**', async (route) => {
      const url = new URL(route.request().url())
      const q = url.searchParams.get('q') || ''
      if (q === 'NEEDLE_LOAD') {
        // First query responds immediately with a hit.
        firstResponded = true
        await route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({
            results: slowResults,
            total_messages_matched: slowResults.length,
            returned_messages: slowResults.length,
            truncated: false,
          }),
        })
      } else {
        // Second query (different text) is held until we release it.
        secondArrived = true
        await releaseSecond
        await route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({
            results: [],
            total_messages_matched: 0,
            returned_messages: 0,
            truncated: false,
          }),
        })
      }
    })

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.locator('main').click()
    await page.keyboard.press('Meta+k')
    const input = page.locator('input[placeholder="Search messages..."]')
    await expect(input).toBeVisible()

    await input.fill('NEEDLE_LOAD')
    await expect.poll(() => firstResponded).toBe(true)
    await expect(page.locator('[data-result-card]').first()).toBeVisible()

    // Now type a different query — second request goes in flight.
    await input.fill('UNRELATED_TOKEN_XYZ')
    await expect.poll(() => secondArrived).toBe(true)

    // While the second request is in flight, panel must NOT claim
    // "No matches" — that's the lying-empty-state bug.
    await expect(page.getByText(/No matches/i)).toHaveCount(0)
    await expect(page.getByText(/Searching|Loading/i).first()).toBeVisible()

    if (resolveSecond) (resolveSecond as () => void)()
  })
})
