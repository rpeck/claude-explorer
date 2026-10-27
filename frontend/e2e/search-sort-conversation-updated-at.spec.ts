/**
 * V1 polish (2026-05-14, Bug B second fix) — search result list must
 * sort by `conversation_updated_at` (matching the date column shown
 * in each result card), NOT by the max matched-message timestamp.
 *
 * Live API repro (curl on 8765, BEFORE fix):
 *   Position 4 had conversation_updated_at=2026-05-14 but sat BELOW
 *   position 3 with conversation_updated_at=2026-05-01. Position 11
 *   had conv_updated_at=2026-05-13 sitting below March/April convs.
 *   Cause: backend sorted by max(matched_msg.created_at).
 *
 * UI-level proof: fixture three conversations whose conv_updated_at
 * and matched-msg created_at deliberately invert. Under the bug, the
 * sort key (max msg time) gives:
 *   "Oldest by conv" (msg=05-13) → "Middle" (msg=04-15) → "Newest by conv" (msg=03-01)
 * The user sees "May 1" labeled card at TOP and "May 14" labeled card
 * at BOTTOM — the broken behavior.
 *
 * After fix, sort by conv_updated_at gives:
 *   "Newest by conv" → "Middle" → "Oldest by conv"
 * And date labels read newest → oldest top-to-bottom — sane UX.
 *
 * Bidirectional: flip sort_order to asc → inverse order.
 *
 * Settle signal: poll card-count + card-title sequence rather than
 * waitForTimeout.
 */
import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry, waitForKeyboardShortcuts } from './fixtures'
import type { SearchResult } from '../src/lib/types'
import type { Route } from './fixtures'

const A_NEW = '00000000-0000-0000-0000-0000000000d1'
const B_MID = '00000000-0000-0000-0000-0000000000d2'
const C_OLD = '00000000-0000-0000-0000-0000000000d3'

// `updated_at` field on the summary is what would normally be the
// conv-level updated time. Spread across May 1 → May 7 → May 14.
const aSummary = makeSummary({
  uuid: A_NEW,
  name: 'A newest conversation',
  message_count: 1,
  human_message_count: 1,
  updated_at: '2026-05-14T22:00:00Z',
  created_at: '2026-05-14T22:00:00Z',
})
const bSummary = makeSummary({
  uuid: B_MID,
  name: 'B middle conversation',
  message_count: 1,
  human_message_count: 1,
  updated_at: '2026-05-07T15:00:00Z',
  created_at: '2026-05-07T15:00:00Z',
})
const cSummary = makeSummary({
  uuid: C_OLD,
  name: 'C oldest conversation',
  message_count: 1,
  human_message_count: 1,
  updated_at: '2026-05-01T09:00:00Z',
  created_at: '2026-05-01T09:00:00Z',
})

const aDetail = makeDetail(aSummary, [
  makeMessage({
    uuid: 'a-m1',
    sender: 'assistant',
    text: 'this conversation contains a needle deep inside',
    content: [
      { type: 'text', text: 'this conversation contains a needle deep inside' },
    ],
  }),
])
const bDetail = makeDetail(bSummary, [
  makeMessage({
    uuid: 'b-m1',
    sender: 'assistant',
    text: 'middle needle here',
    content: [{ type: 'text', text: 'middle needle here' }],
  }),
])
const cDetail = makeDetail(cSummary, [
  makeMessage({
    uuid: 'c-m1',
    sender: 'assistant',
    text: 'oldest conv has a needle too',
    content: [{ type: 'text', text: 'oldest conv has a needle too' }],
  }),
])

function searchResultFor(
  summary: typeof aSummary,
  msgUuid: string,
  snippet: string,
  msgCreatedAt: string,
): SearchResult {
  const matchStart = snippet.indexOf('needle')
  return {
    conversation_uuid: summary.uuid,
    conversation_name: summary.name,
    conversation_updated_at: summary.updated_at,
    conversation_created_at: summary.created_at,
    project_name: null,
    matching_messages: [
      {
        message_uuid: msgUuid,
        sender: 'assistant',
        snippet,
        match_start: matchStart,
        match_end: matchStart + 'needle'.length,
        created_at: msgCreatedAt,
      },
    ],
  }
}

async function installSearchMock(page: Page) {
  // The mock backend returns results in the order the BACKEND would
  // emit them — i.e. it RESPECTS the sort_order parameter. The bug we
  // are pinning is whether the backend returns the right order; we
  // mimic the FIXED behavior here and verify the UI renders them in
  // the order returned. This catches a separate regression: if a
  // future contributor adds client-side re-sort that re-introduces
  // the inversion, this test trips.
  await page.route('**/api/search**', (route: Route) => {
    const url = new URL(route.request().url())
    const q = url.searchParams.get('q') ?? ''
    const sort = url.searchParams.get('sort') ?? 'updated_at'
    const sortOrder = url.searchParams.get('sort_order') ?? 'desc'
    if (q !== 'needle') {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          results: [],
          total_messages_matched: 0,
          returned_messages: 0,
          truncated: false,
        }),
      })
      return
    }
    // Build per-conv results. Per-message created_at is DELIBERATELY
    // inverse to conv_updated_at so the test would FAIL if any layer
    // (frontend or backend mock) sorts by msg time.
    const all: SearchResult[] = [
      searchResultFor(
        aSummary,
        'a-m1',
        'this conversation contains a needle deep inside',
        '2026-03-01T10:00:00Z', // OLDEST msg, NEWEST conv
      ),
      searchResultFor(
        bSummary,
        'b-m1',
        'middle needle here',
        '2026-04-15T10:00:00Z',
      ),
      searchResultFor(
        cSummary,
        'c-m1',
        'oldest conv has a needle too',
        '2026-05-13T17:00:00Z', // NEWEST msg, OLDEST conv
      ),
    ]
    let ordered: SearchResult[]
    if (sort === 'updated_at') {
      ordered = [...all].sort((x, y) => {
        const cmp = x.conversation_updated_at.localeCompare(
          y.conversation_updated_at,
        )
        return sortOrder === 'desc' ? -cmp : cmp
      })
    } else {
      ordered = all
    }
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        results: ordered,
        total_messages_matched: ordered.length,
        returned_messages: ordered.length,
        truncated: false,
      }),
    })
  })
}

test.describe('Search sort — Bug B v2: conversation_updated_at, not max msg', () => {
  test('sort=updated_at desc → conv_updated_at desc (newest conv at top)', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [aSummary, bSummary, cSummary],
      details: { [A_NEW]: aDetail, [B_MID]: bDetail, [C_OLD]: cDetail },
      extraRoutes: async (p) => {
        await installSearchMock(p)
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press('Meta+k')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible()
    await input.fill('needle')

    const cards = page.locator('[data-result-card]')
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(3)

    // Settle on the title sequence — that's the DOM signal proving
    // the search request resolved AND the cards rendered.
    await expect
      .poll(
        async () => {
          const titles = await cards.evaluateAll((els) =>
            els.map(
              (el) =>
                el.querySelector('.text-xs.font-semibold')?.textContent ?? '',
            ),
          )
          return titles.join('|')
        },
        { timeout: 5000 },
      )
      .toBe('A newest conversation|B middle conversation|C oldest conversation')
  })

  test('sort=updated_at asc → conv_updated_at asc (oldest conv at top)', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [aSummary, bSummary, cSummary],
      details: { [A_NEW]: aDetail, [B_MID]: bDetail, [C_OLD]: cDetail },
      extraRoutes: async (p) => {
        await installSearchMock(p)
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press('Meta+k')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible()
    await input.fill('needle')

    // Wait for initial (desc) results, then flip sort direction.
    const cards = page.locator('[data-result-card]')
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(3)

    // Click the asc/desc toggle button (↑/↓ label next to sort select).
    // The search-panel-specific button is the one inside the Search panel
    // aside; scope to it to avoid ambiguity with the sidebar's own
    // identically-titled toggle.
    const searchPanel = page.getByRole('complementary', { name: 'Search panel' })
    const sortToggle = searchPanel.getByTitle('Descending')
    await sortToggle.click()
    // After flip, title becomes "Ascending"; wait for that DOM signal.
    await expect(searchPanel.getByTitle('Ascending')).toBeVisible({
      timeout: 5000,
    })

    await expect
      .poll(
        async () => {
          const titles = await cards.evaluateAll((els) =>
            els.map(
              (el) =>
                el.querySelector('.text-xs.font-semibold')?.textContent ?? '',
            ),
          )
          return titles.join('|')
        },
        { timeout: 5000 },
      )
      .toBe('C oldest conversation|B middle conversation|A newest conversation')
  })
})
