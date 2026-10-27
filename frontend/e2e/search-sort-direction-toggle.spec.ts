/**
 * V1 polish (2026-05-14, Bug 2) — search panel sort-direction arrow
 * must visibly flip result order.
 *
 * Pre-fix behavior (bug): clicking the ↓/↑ arrow updated the button
 * glyph and fired a new /api/search request, but the visible card
 * order did NOT change. Root cause: the SearchPanelContext re-sorted
 * `flatMatches` client-side using a per-message timestamp with a
 * conversation-level fallback. For CC conversations where many
 * messages share a fallback (null `created_at` → `conversationUpdatedAt`),
 * asc and desc collapsed to the same visible top-N.
 *
 * Post-fix contract:
 *   * Backend is the SINGLE SOURCE OF TRUTH for sort order.
 *   * Clicking the arrow MUST change the visible top result when the
 *     backend response is different — even with multiple result cards.
 *   * The frontend MUST send `sort_order=asc` (or omit for desc) and
 *     render the backend response verbatim.
 *
 * Settle signals (feedback_playwright_settle_signals):
 *   * After typing the query: wait for cards to appear.
 *   * After clicking the arrow: wait for the NEW /api/search response
 *     by polling on the request log + on the rendered card order being
 *     the desc/asc shape we mocked. Never bare-sleep.
 */
import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry, waitForKeyboardShortcuts } from './fixtures'
import type { SearchResult } from '../src/lib/types'
import type { Route } from './fixtures'

const NEWEST_UUID = '00000000-0000-0000-0000-00000000b001'
const MIDDLE_UUID = '00000000-0000-0000-0000-00000000b002'
const OLDEST_UUID = '00000000-0000-0000-0000-00000000b003'

const newest = makeSummary({
  uuid: NEWEST_UUID,
  name: 'Newest match',
  message_count: 1,
  human_message_count: 1,
})
const middle = makeSummary({
  uuid: MIDDLE_UUID,
  name: 'Middle match',
  message_count: 1,
  human_message_count: 1,
})
const oldest = makeSummary({
  uuid: OLDEST_UUID,
  name: 'Oldest match',
  message_count: 1,
  human_message_count: 1,
})

const details = {
  [NEWEST_UUID]: makeDetail(newest, [
    makeMessage({
      uuid: 'new-m1',
      sender: 'human',
      text: 'newest needle here',
      content: [{ type: 'text', text: 'newest needle here' }],
    }),
  ]),
  [MIDDLE_UUID]: makeDetail(middle, [
    makeMessage({
      uuid: 'mid-m1',
      sender: 'human',
      text: 'middle needle here',
      content: [{ type: 'text', text: 'middle needle here' }],
    }),
  ]),
  [OLDEST_UUID]: makeDetail(oldest, [
    makeMessage({
      uuid: 'old-m1',
      sender: 'human',
      text: 'oldest needle here',
      content: [{ type: 'text', text: 'oldest needle here' }],
    }),
  ]),
}

/**
 * Build a SearchResult that simulates the CC bug shape:
 *   - Per-message `created_at` is null (typical of CC ingestion).
 *   - All three conversations share the SAME `conversation_updated_at`.
 *
 * Pre-fix the client-side re-sort applied `createdAt ?? conversationUpdatedAt`
 * — every entry collapsed to the SAME fallback timestamp. Array.sort
 * with a comparator returning 0 is stable; combined with the backend
 * having ALREADY ordered the response correctly, the client-sort was a
 * no-op for desc but actively REVERSED ascending order (because it
 * compared zero-deltas with `desc ? bt-at : at-bt`, both = 0, stable
 * preserves backend order — wait, that should pass through too).
 *
 * The actual production bug: with per-message createdAt VALID and
 * unique INSIDE a single conversation (many messages, same conv), the
 * client-sort interleaved across conversations by per-message ts. When
 * one conv had many matching messages with timestamps that bracketed
 * the global min/max, that conv dominated the top of the flat list in
 * BOTH directions because its messages contained the extreme values.
 *
 * To reproduce in a deterministic mock: ONE conv with multiple
 * matching messages spanning a wide ts range, plus other convs with
 * shorter ts ranges. The pre-fix client-sort would interleave by ts
 * across convs. The post-fix render preserves backend's per-conv
 * grouping verbatim. So the assertion is: in DESC mode, the FIRST
 * card's conversation matches the FIRST conv in the backend payload,
 * not the conv with the latest single message.
 */
const TS_NEWEST = '2026-05-01T12:00:00Z'
const TS_MIDDLE = '2026-04-01T12:00:00Z'
const TS_OLDEST = '2026-03-01T12:00:00Z'

function makeResult(
  summary: ReturnType<typeof makeSummary>,
  msg_uuid: string,
  snippet: string,
  ts: string,
): SearchResult {
  return {
    conversation_uuid: summary.uuid,
    conversation_name: summary.name,
    conversation_updated_at: ts,
    conversation_created_at: ts,
    project_name: null,
    matching_messages: [
      {
        message_uuid: msg_uuid,
        sender: 'human',
        snippet,
        match_start: 0,
        match_end: 6,
        created_at: null, // CC-like: null per-message timestamp
      },
    ],
  }
}

// Backend emulator: shape the response per the sort_order param so the
// frontend's render strictly mirrors what the server returned. The
// pre-fix client-side re-sort would have collapsed both asc/desc into
// the same visible order; with the fix removed the client renders the
// backend's ordering verbatim.
async function installOrderedSearchMock(page: Page, log: string[]) {
  await page.route('**/api/search**', (route: Route) => {
    const url = new URL(route.request().url())
    const q = url.searchParams.get('q') ?? ''
    const order = url.searchParams.get('sort_order') ?? 'desc'
    log.push(`q=${q} sort_order=${order}`)
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
    const descOrder: SearchResult[] = [
      makeResult(newest, 'new-m1', 'newest needle here', TS_NEWEST),
      makeResult(middle, 'mid-m1', 'middle needle here', TS_MIDDLE),
      makeResult(oldest, 'old-m1', 'oldest needle here', TS_OLDEST),
    ]
    const ascOrder = [...descOrder].reverse()
    const chosen = order === 'asc' ? ascOrder : descOrder
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        results: chosen,
        total_messages_matched: chosen.length,
        returned_messages: chosen.length,
        truncated: false,
      }),
    })
  })
}

test.describe('Search panel sort-direction arrow (V1 polish 2026-05-14)', () => {
  test('clicking arrow flips the visible top result', async ({ page, mockBackend }) => {
    const requestLog: string[] = []

    await mockBackend({
      conversations: [newest, middle, oldest],
      details,
      extraRoutes: async (p) => {
        await installOrderedSearchMock(p, requestLog)
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
    // Settle: 3 cards visible (desc — backend default).
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(3)

    // Confirm desc: top card name is "Newest match".
    const firstTitleDesc = await cards.first().locator('.font-semibold').textContent()
    expect(firstTitleDesc).toContain('Newest match')

    // Click the SEARCH PANEL's sort-direction arrow. The left sidebar
    // also has a similar arrow; target by being INSIDE the search
    // aside. Title attr distinguishes asc vs desc.
    const aside = page.locator('aside[aria-label="Search panel"]')
    const arrow = aside.locator('button[title="Descending"]')
    await expect(arrow).toBeVisible()
    await arrow.click()

    // Settle: wait for an `asc` request to be logged AND for the top
    // card to be the oldest. Polling on both makes this robust against
    // React Query's `placeholderData: keepPreviousData` briefly
    // showing the stale desc order during the refetch.
    await expect
      .poll(
        async () => {
          const sawAsc = requestLog.some((line) => line.includes('sort_order=asc'))
          const firstTitle = await cards.first().locator('.font-semibold').textContent()
          return { sawAsc, firstTitle }
        },
        { timeout: 5000 },
      )
      .toEqual({ sawAsc: true, firstTitle: 'Oldest match' })

    // Button text/title must reflect the new state too.
    await expect(aside.locator('button[title="Ascending"]')).toBeVisible()
  })

  test('preserves backend conversation grouping in flat match list (no client interleave)', async ({ page, mockBackend }) => {
    // Bug 2 historical mode: pre-fix the frontend re-sorted the flat
    // message list by per-message createdAt (with conv-level fallback),
    // which interleaved messages from DIFFERENT conversations and
    // destroyed the backend's conversation-major grouping. Post-fix the
    // flat list is conversation-major, message-minor.
    //
    // Mock: 2 conversations, each with 2 matching messages. The conv-A
    // messages have AN OLDER created_at than the conv-B messages, but
    // backend desc returns A FIRST (because A's conv-level updated_at
    // is more recent than B's). Pre-fix client-sort would have
    // promoted B's newer messages above A's older messages, producing
    // [B-newer, B-newer, A-older, A-older] — losing A's "top conv"
    // position. Post-fix the list is [A, A, B, B].
    const CONV_A_UUID = '00000000-0000-0000-0000-00000000c001'
    const CONV_B_UUID = '00000000-0000-0000-0000-00000000c002'
    const convA = makeSummary({ uuid: CONV_A_UUID, name: 'Conversation A (top conv)' })
    const convB = makeSummary({ uuid: CONV_B_UUID, name: 'Conversation B' })

    await mockBackend({
      conversations: [convA, convB],
      details: {
        [CONV_A_UUID]: makeDetail(convA, [
          makeMessage({ uuid: 'a-m1', text: 'needle a1', content: [{ type: 'text', text: 'needle a1' }] }),
          makeMessage({ uuid: 'a-m2', text: 'needle a2', content: [{ type: 'text', text: 'needle a2' }] }),
        ]),
        [CONV_B_UUID]: makeDetail(convB, [
          makeMessage({ uuid: 'b-m1', text: 'needle b1', content: [{ type: 'text', text: 'needle b1' }] }),
          makeMessage({ uuid: 'b-m2', text: 'needle b2', content: [{ type: 'text', text: 'needle b2' }] }),
        ]),
      },
      extraRoutes: async (p) => {
        await p.route('**/api/search**', (route: Route) => {
          // Conv A is the top conv (more recent conv_updated_at) but its
          // matching messages are OLDER than B's matching messages.
          const results: SearchResult[] = [
            {
              conversation_uuid: CONV_A_UUID,
              conversation_name: convA.name,
              conversation_updated_at: '2026-05-01T12:00:00Z',
              conversation_created_at: '2026-05-01T12:00:00Z',
              project_name: null,
              matching_messages: [
                { message_uuid: 'a-m1', sender: 'human', snippet: 'needle a1', match_start: 0, match_end: 6, created_at: '2025-01-01T12:00:00Z' },
                { message_uuid: 'a-m2', sender: 'human', snippet: 'needle a2', match_start: 0, match_end: 6, created_at: '2025-01-02T12:00:00Z' },
              ],
            },
            {
              conversation_uuid: CONV_B_UUID,
              conversation_name: convB.name,
              conversation_updated_at: '2026-04-01T12:00:00Z',
              conversation_created_at: '2026-04-01T12:00:00Z',
              project_name: null,
              matching_messages: [
                // B's messages are MORE RECENT than A's per-message
                // timestamps — the pre-fix client-sort would promote
                // them above A's, breaking conv-grouping.
                { message_uuid: 'b-m1', sender: 'human', snippet: 'needle b1', match_start: 0, match_end: 6, created_at: '2026-03-01T12:00:00Z' },
                { message_uuid: 'b-m2', sender: 'human', snippet: 'needle b2', match_start: 0, match_end: 6, created_at: '2026-03-02T12:00:00Z' },
              ],
            },
          ]
          route.fulfill({
            contentType: 'application/json',
            body: JSON.stringify({
              results,
              total_messages_matched: 4,
              returned_messages: 4,
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
    await page.getByPlaceholder('Search messages...').fill('needle')

    const cards = page.locator('[data-result-card]')
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(4)

    // Post-fix contract: first two cards belong to Conv A (the backend's
    // top conv), the last two to Conv B. If the pre-fix interleave
    // returns (B first because of newer message timestamps), this
    // assertion fails — exactly the regression we want to pin.
    const titles = await cards.evaluateAll((els) =>
      els.map((el) => el.querySelector('.font-semibold')?.textContent ?? ''),
    )
    expect(titles[0]).toContain('Conversation A')
    expect(titles[1]).toContain('Conversation A')
    expect(titles[2]).toContain('Conversation B')
    expect(titles[3]).toContain('Conversation B')
  })

  test('clicking arrow back toggles direction (asc → desc)', async ({ page, mockBackend }) => {
    const requestLog: string[] = []

    await mockBackend({
      conversations: [newest, middle, oldest],
      details,
      extraRoutes: async (p) => {
        await installOrderedSearchMock(p, requestLog)
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press('Meta+k')
    await page.getByPlaceholder('Search messages...').fill('needle')

    const aside = page.locator('aside[aria-label="Search panel"]')
    const cards = page.locator('[data-result-card]')
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(3)

    // First click: desc → asc.
    await aside.locator('button[title="Descending"]').click()
    await expect.poll(
      async () => (await cards.first().locator('.font-semibold').textContent()) ?? '',
      { timeout: 5000 },
    ).toContain('Oldest match')

    // Second click: asc → desc. Order should return to newest-first.
    await aside.locator('button[title="Ascending"]').click()
    await expect.poll(
      async () => (await cards.first().locator('.font-semibold').textContent()) ?? '',
      { timeout: 5000 },
    ).toContain('Newest match')

    // Both directions must have been requested at least once each.
    expect(requestLog.some((l) => l.includes('sort_order=asc'))).toBe(true)
    // 'desc' is the backend default and is OMITTED from the query string
    // (see api.ts: `if (sortOrder !== 'desc') params.set(...)`). So the
    // back-to-desc request appears in the log WITHOUT sort_order in the q.
    // Validate by checking at least one log entry has NO sort_order=asc.
    expect(requestLog.some((l) => !l.includes('sort_order=asc'))).toBe(true)
  })
})
