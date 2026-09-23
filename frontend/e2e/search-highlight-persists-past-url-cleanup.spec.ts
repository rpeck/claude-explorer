import { test, expect, makeSummary, makeMessage, makeDetail, searchEnvelopeJson, type Page, type Route, withNetRetry } from './fixtures'
import type { Message } from '../src/lib/types'

/**
 * Regression (2026-05-24, user screenshot): search hits aren't yellow-
 * highlighted in the conversation panel anymore. The sidebar shows
 * matches correctly, but the in-bubble `<mark>` decoration is missing.
 *
 * Root cause: ConversationPage gated `searchQuery` on the URL `?highlight=`
 * param via `message.uuid === highlightMessageId`. The highlight effect's
 * 2 s cleanup timer clears that param, so AFTER 2 s the gate fails for
 * every bubble and no inline highlights render.
 *
 * Fix: gate on the search panel's `activeMatchUuid` (flatMatches at
 * activeMatchIndex), which is stable as long as the user hasn't
 * navigated to a different match. Yellow marks persist for the entire
 * time the user is reading the result.
 *
 * Per TESTING.md §5.13: the user-observable contract is
 * "matching tokens appear yellow in the bubble I'm looking at, and
 * stay yellow until I navigate elsewhere." Previous tests pinned
 * `<mark>` presence IMMEDIATELY after typing — they passed because the
 * URL param was still present during the assertion's polling window.
 * This test waits past the 2 s cleanup before asserting.
 */

const CONV = '00000000-0000-0000-0000-0000000abc123'
const NEEDLE = 'NEEDLE_TOKEN'

const summary = makeSummary({
  uuid: CONV,
  source: 'CLAUDE_CODE',
  name: 'Highlight persistence fixture',
})

const messages: Message[] = [
  makeMessage({
    uuid: 'msg-with-needle',
    sender: 'assistant',
    text: `Some text containing ${NEEDLE} in the middle and another ${NEEDLE} later`,
    content: [
      {
        type: 'text',
        text: `Some text containing ${NEEDLE} in the middle and another ${NEEDLE} later`,
      },
    ],
  }),
  makeMessage({
    uuid: 'msg-without',
    sender: 'human',
    text: 'unrelated content',
    content: [{ type: 'text', text: 'unrelated content' }],
  }),
]

const detail = makeDetail(summary, messages)

async function mockSearch(page: Page) {
  await page.route('**/api/search**', (route: Route) => {
    const url = new URL(route.request().url())
    const q = (url.searchParams.get('q') ?? '').toLowerCase()
    const results = q.includes(NEEDLE.toLowerCase())
      ? [
          {
            conversation_uuid: CONV,
            conversation_name: summary.name,
            conversation_updated_at: summary.updated_at,
            conversation_created_at: summary.created_at,
            project_name: 'fixture',
            matching_messages: [
              {
                message_uuid: 'msg-with-needle',
                sender: 'assistant',
                snippet: `Some text containing ${NEEDLE} in the middle`,
                match_start: 22,
                match_end: 22 + NEEDLE.length,
                created_at: messages[0].created_at,
              },
            ],
          },
        ]
      : []
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: searchEnvelopeJson(results),
    })
  })
}

test.describe('Search-hit highlights persist past the URL-cleanup timer (2026-05-24)', () => {
  test('inline <mark> stays on the active-match bubble after the 2s ?highlight= cleanup', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [CONV]: detail },
    })
    await mockSearch(page)
    await withNetRetry(page, () => page.goto(`/conversations/${CONV}`))

    // Open search panel, type the needle. Auto-promote should land on
    // msg-with-needle.
    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible({ timeout: 3000 })
    await input.fill(NEEDLE)

    // Wait for the sidebar match counter — proves search returned + auto-promote fired.
    await expect(page.getByText(/of\s+\d+\s+match/)).toBeVisible({ timeout: 5000 })

    // The bubble has yellow `<mark>` on each NEEDLE occurrence
    // IMMEDIATELY after results land.
    const bubble = page.locator('[data-message-uuid="msg-with-needle"]')
    await expect(bubble).toBeVisible()
    await expect(bubble.locator('mark')).toHaveCount(2)
    await expect(bubble.locator('mark').first()).toContainText(NEEDLE)

    // Wait LONGER than the 2 s highlight-effect URL cleanup. Without
    // the activeMatchUuid gate, the URL `?highlight=` param clears,
    // the bubble's `searchQuery` prop drops to `''`, and the `<mark>`
    // nodes disappear (the bug the user reported in the screenshot).
    await page.waitForTimeout(2500)

    // USER-OBSERVABLE CONTRACT: marks must still be there.
    await expect(bubble.locator('mark')).toHaveCount(2)
    await expect(bubble.locator('mark').first()).toContainText(NEEDLE)
  })

  test('non-active-match bubbles do NOT receive <mark> (perf gate preserved)', async ({
    page,
    mockBackend,
  }) => {
    // Bidirectional pair: the perf optimization that scopes searchQuery
    // to a single bubble must still hold. Without this, every bubble
    // re-renders per keystroke and the 16K-corpus typing-lag regression
    // returns.
    await mockBackend({
      conversations: [summary],
      details: { [CONV]: detail },
    })
    await mockSearch(page)
    await withNetRetry(page, () => page.goto(`/conversations/${CONV}`))

    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible({ timeout: 3000 })
    await input.fill(NEEDLE)
    await expect(page.getByText(/of\s+\d+\s+match/)).toBeVisible({ timeout: 5000 })

    // Active-match bubble has marks.
    await expect(
      page.locator('[data-message-uuid="msg-with-needle"]').locator('mark'),
    ).toHaveCount(2)

    // Non-match bubble has NO marks (would only have any if searchQuery
    // were threaded to all bubbles — the perf-regression failure mode).
    await expect(
      page.locator('[data-message-uuid="msg-without"]').locator('mark'),
    ).toHaveCount(0)
  })
})
