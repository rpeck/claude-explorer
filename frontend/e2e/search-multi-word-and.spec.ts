/**
 * V1 polish (2026-05-14, Bug 1) — multi-word search must AND tokens.
 *
 * Pins the contract:
 *   * Unquoted multi-word query → backend receives the raw query string;
 *     UI does not OR-split it client-side or fall back to single-word
 *     filtering.
 *   * Result count comes from the backend payload verbatim; the client
 *     filter does NOT drop scattered-token results.
 *
 * Settle signals used (per feedback_playwright_settle_signals): we wait
 * on the deterministic DOM signal that the search results have settled
 * (`[data-result-card]` count stabilization, post-debounce). We do NOT
 * use bare `waitForTimeout` — the debounce window is 200 ms but a stale
 * placeholderData payload can still be visible during the refetch, so
 * we poll on the visible card count.
 */
import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry, waitForKeyboardShortcuts } from './fixtures'
import type { SearchResult } from '../src/lib/types'
import type { Route } from './fixtures'

const ADJACENT_UUID = '00000000-0000-0000-0000-000000000a01'
const SCATTERED_UUID = '00000000-0000-0000-0000-000000000a02'
const SINGLE_UUID = '00000000-0000-0000-0000-000000000a03'

const adjacentSummary = makeSummary({
  uuid: ADJACENT_UUID,
  name: 'All adjacent phrase',
  message_count: 1,
  human_message_count: 1,
})
const scatteredSummary = makeSummary({
  uuid: SCATTERED_UUID,
  name: 'Scattered tokens',
  message_count: 1,
  human_message_count: 1,
})
const singleSummary = makeSummary({
  uuid: SINGLE_UUID,
  name: 'Only one token',
  message_count: 1,
  human_message_count: 1,
})

const adjacentDetail = makeDetail(adjacentSummary, [
  makeMessage({
    uuid: 'adj-m1',
    sender: 'human',
    text: 'please write a comprehensive medium article about FTS5',
    content: [{ type: 'text', text: 'please write a comprehensive medium article about FTS5' }],
  }),
])
const scatteredDetail = makeDetail(scatteredSummary, [
  makeMessage({
    uuid: 'scat-m1',
    sender: 'human',
    text: 'medium-format piece. deeper article. make it comprehensive.',
    content: [{ type: 'text', text: 'medium-format piece. deeper article. make it comprehensive.' }],
  }),
])
const singleDetail = makeDetail(singleSummary, [
  makeMessage({
    uuid: 'one-m1',
    sender: 'human',
    text: 'only the word comprehensive lives here',
    content: [{ type: 'text', text: 'only the word comprehensive lives here' }],
  }),
])

/**
 * Backend-emulating search router. The TEST is a black-box contract on
 * the frontend's QUERY shape and RENDERING — not the FTS5 logic itself
 * (covered by backend pytests). We model the backend's NEW contract:
 *   - "comprehensive medium article"      → returns adjacent + scattered
 *   - "\"comprehensive medium article\""  → returns adjacent only
 *   - "comprehensive"                     → returns all three
 *   - anything else                       → []
 *
 * The mock returns the same JSON shape the real backend would. If the
 * frontend silently OR-splits the query and re-fires single-token
 * searches, this mock would NOT receive the multi-word query and the
 * test would fail at the route assertion.
 */
async function installMultiWordSearchMock(page: Page) {
  await page.route('**/api/search**', (route: Route) => {
    const url = new URL(route.request().url())
    const q = url.searchParams.get('q') ?? ''
    let results: SearchResult[] = []
    if (q === 'comprehensive medium article') {
      results = [
        {
          conversation_uuid: ADJACENT_UUID,
          conversation_name: adjacentSummary.name,
          conversation_updated_at: adjacentSummary.updated_at,
          conversation_created_at: adjacentSummary.created_at,
          project_name: null,
          matching_messages: [
            {
              message_uuid: 'adj-m1',
              sender: 'human',
              snippet: 'please write a comprehensive medium article about FTS5',
              match_start: 15,
              match_end: 28, // 'comprehensive'
              created_at: '2026-05-01T12:00:00Z',
            },
          ],
        },
        {
          conversation_uuid: SCATTERED_UUID,
          conversation_name: scatteredSummary.name,
          conversation_updated_at: scatteredSummary.updated_at,
          conversation_created_at: scatteredSummary.created_at,
          project_name: null,
          matching_messages: [
            {
              message_uuid: 'scat-m1',
              sender: 'human',
              // Snippet contains all 3 tokens scattered; the highlight
              // (match_start/match_end) lands on the first token the
              // backend regex found ("medium").
              snippet: 'medium-format piece. deeper article. make it comprehensive.',
              match_start: 0,
              match_end: 6, // 'medium'
              created_at: '2026-05-01T12:00:00Z',
            },
          ],
        },
      ]
    } else if (q === '"comprehensive medium article"') {
      results = [
        {
          conversation_uuid: ADJACENT_UUID,
          conversation_name: adjacentSummary.name,
          conversation_updated_at: adjacentSummary.updated_at,
          conversation_created_at: adjacentSummary.created_at,
          project_name: null,
          matching_messages: [
            {
              message_uuid: 'adj-m1',
              sender: 'human',
              snippet: 'please write a comprehensive medium article about FTS5',
              match_start: 15,
              match_end: 43, // 'comprehensive medium article'
              created_at: '2026-05-01T12:00:00Z',
            },
          ],
        },
      ]
    } else if (q === 'comprehensive') {
      // single-token: all three convs hit
      results = [
        ...['adj', 'scat', 'one'].map((tag) => {
          const summary = tag === 'adj' ? adjacentSummary : tag === 'scat' ? scatteredSummary : singleSummary
          return {
            conversation_uuid: summary.uuid,
            conversation_name: summary.name,
            conversation_updated_at: summary.updated_at,
            conversation_created_at: summary.created_at,
            project_name: null,
            matching_messages: [
              {
                message_uuid: `${tag}-m1`,
                sender: 'human',
                snippet: 'snippet containing comprehensive here',
                match_start: 19,
                match_end: 32,
                created_at: '2026-05-01T12:00:00Z',
              },
            ],
          } as SearchResult
        }),
      ]
    }
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        results,
        total_messages_matched: results.length,
        returned_messages: results.length,
        truncated: false,
      }),
    })
  })
}

test.describe('Multi-word search AND semantics (V1 polish 2026-05-14)', () => {
  test('unquoted 3-word query passes raw string to backend (not OR-split)', async ({
    page,
    mockBackend,
  }) => {
    const capturedQueries: string[] = []

    await mockBackend({
      conversations: [adjacentSummary, scatteredSummary, singleSummary],
      details: {
        [ADJACENT_UUID]: adjacentDetail,
        [SCATTERED_UUID]: scatteredDetail,
        [SINGLE_UUID]: singleDetail,
      },
      extraRoutes: async (p) => {
        await p.route('**/api/search**', (route: Route) => {
          const q = new URL(route.request().url()).searchParams.get('q') ?? ''
          capturedQueries.push(q)
          // Re-use the multi-word mock body logic inline so we can also
          // assert the captured query shape.
          let results: SearchResult[] = []
          if (q === 'comprehensive medium article') {
            results = [
              {
                conversation_uuid: ADJACENT_UUID,
                conversation_name: adjacentSummary.name,
                conversation_updated_at: adjacentSummary.updated_at,
                conversation_created_at: adjacentSummary.created_at,
                project_name: null,
                matching_messages: [
                  {
                    message_uuid: 'adj-m1',
                    sender: 'human',
                    snippet: 'please write a comprehensive medium article about FTS5',
                    match_start: 15,
                    match_end: 28,
                    created_at: '2026-05-01T12:00:00Z',
                  },
                ],
              },
              {
                conversation_uuid: SCATTERED_UUID,
                conversation_name: scatteredSummary.name,
                conversation_updated_at: scatteredSummary.updated_at,
                conversation_created_at: scatteredSummary.created_at,
                project_name: null,
                matching_messages: [
                  {
                    message_uuid: 'scat-m1',
                    sender: 'human',
                    snippet: 'medium-format piece. deeper article. make it comprehensive.',
                    match_start: 0,
                    match_end: 6,
                    created_at: '2026-05-01T12:00:00Z',
                  },
                ],
              },
            ]
          }
          route.fulfill({
            contentType: 'application/json',
            body: JSON.stringify({
              results,
              total_messages_matched: results.length,
              returned_messages: results.length,
              truncated: false,
            }),
          })
        })
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press('Meta+k')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible()
    await input.fill('comprehensive medium article')

    // Settle on the cards: wait for both expected results to render.
    const cards = page.locator('[data-result-card]')
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(2)

    // Contract: backend received the FULL query string with whitespace,
    // not three separate single-token requests. (If the frontend
    // OR-splits, we'd see ['comprehensive', 'medium', 'article'] here.)
    const multiWordHits = capturedQueries.filter(
      (q) => q === 'comprehensive medium article',
    )
    expect(multiWordHits.length).toBeGreaterThan(0)
    expect(capturedQueries).not.toContain('medium')
    expect(capturedQueries).not.toContain('article')
  })

  test('scattered-token result is rendered (not filtered out by client)', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [adjacentSummary, scatteredSummary, singleSummary],
      details: {
        [ADJACENT_UUID]: adjacentDetail,
        [SCATTERED_UUID]: scatteredDetail,
        [SINGLE_UUID]: singleDetail,
      },
      extraRoutes: async (p) => {
        await installMultiWordSearchMock(p)
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press('Meta+k')
    await page.getByPlaceholder('Search messages...').fill('comprehensive medium article')

    const cards = page.locator('[data-result-card]')
    // Settle: both convs must appear. The CLIENT-side filter (which we
    // also fixed in this PR) used to require literal substring
    // "comprehensive medium article" in each snippet — that would have
    // dropped the scattered card here even though the backend included
    // it. So this test pins both contracts simultaneously.
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(2)

    // Verify both conversations are present in the rendered cards by
    // looking up each card's conv title.
    const titles = await cards.evaluateAll((els) =>
      els.map((el) => el.querySelector('.font-semibold')?.textContent ?? ''),
    )
    expect(titles.some((t) => t.includes('All adjacent'))).toBe(true)
    expect(titles.some((t) => t.includes('Scattered tokens'))).toBe(true)
  })

  test('quoted phrase narrows to adjacent-only', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: [adjacentSummary, scatteredSummary, singleSummary],
      details: {
        [ADJACENT_UUID]: adjacentDetail,
        [SCATTERED_UUID]: scatteredDetail,
        [SINGLE_UUID]: singleDetail,
      },
      extraRoutes: async (p) => {
        await installMultiWordSearchMock(p)
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press('Meta+k')
    await page
      .getByPlaceholder('Search messages...')
      .fill('"comprehensive medium article"')

    const cards = page.locator('[data-result-card]')
    // Phrase mode: only the adjacent conv survives.
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(1)
    const title = await cards.first().locator('.font-semibold').textContent()
    expect(title).toContain('All adjacent')
  })
})
