/**
 * Truncation footer — Playwright RED phase.
 *
 * The /api/search response is now a wrapped envelope:
 *   { results, total_messages_matched, returned_messages, truncated }
 *
 * When `truncated === true`, the SearchPanel renders a small muted
 * footer below the results list that tells the user how many matches
 * exist and that refining the query will surface the rest.
 *
 * Per the plan (PLANS/SEARCH_TOOL_AWARENESS_AND_LIMIT_DISCLOSURE.md §B):
 *   "Showing first {returned_messages} of {total_messages_matched}
 *   message matches. Refine your query to see the rest."
 *
 * Style: active voice, no em-dash, muted, beneath the results list.
 */

import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry, waitForKeyboardShortcuts } from './fixtures'
import type { Route } from './fixtures'

const NEEDLE = 'truncationcanary'
const CONV_UUID = '11111111-2222-3333-4444-555555555555'

function fixtureConversation() {
  const summary = makeSummary({
    uuid: CONV_UUID,
    name: 'Truncation fixture conv',
    message_count: 1,
    human_message_count: 1,
  })
  const messages = [
    makeMessage({
      uuid: 'trunc-m1',
      sender: 'human',
      text: `Earlier we discussed ${NEEDLE} at length.`,
    }),
  ]
  return { summary, detail: makeDetail(summary, messages) }
}

function envelopeResponse(opts: {
  total: number
  returned: number
  truncated: boolean
}) {
  const { summary } = fixtureConversation()
  // One representative result card. The interesting assertions are on
  // the envelope-driven footer, not on the per-card render.
  return {
    results: [
      {
        conversation_uuid: CONV_UUID,
        conversation_name: summary.name,
        conversation_updated_at: summary.updated_at,
        conversation_created_at: summary.created_at,
        project_name: null,
        matching_messages: [
          {
            message_uuid: 'trunc-m1',
            sender: 'human',
            snippet: `Earlier we discussed ${NEEDLE} at length.`,
            match_start: 21,
            match_end: 21 + NEEDLE.length,
            created_at: '2026-04-01T10:00:00Z',
            fragments: null,
          },
        ],
      },
    ],
    total_messages_matched: opts.total,
    returned_messages: opts.returned,
    truncated: opts.truncated,
  }
}

test.describe('Search truncation footer', () => {
  test('renders when total_messages_matched exceeds returned_messages', async ({
    page,
    mockBackend,
  }) => {
    const { summary, detail } = fixtureConversation()
    await mockBackend({
      conversations: [summary],
      details: { [CONV_UUID]: detail },
      extraRoutes: async (p) => {
        await p.route('**/api/search**', (route: Route) => {
          route.fulfill({
            contentType: 'application/json',
            body: JSON.stringify(
              envelopeResponse({ total: 5000, returned: 1000, truncated: true }),
            ),
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
    await input.fill(NEEDLE)

    // Wait for the result card to appear so we know the response was applied.
    await expect(page.locator('[data-result-card]').first()).toBeVisible()

    // Footer text. Active voice; no em-dash. Numbers locale-formatted
    // ("1,000 of 5,000") match the plan's example.
    const footer = page.getByTestId('search-truncation-footer')
    await expect(footer).toBeVisible()
    await expect(footer).toContainText(/Showing first 1,000 of 5,000/i)
    await expect(footer).toContainText(/Refine your query to see the rest/i)
  })

  test('absent when total_messages_matched equals returned_messages', async ({
    page,
    mockBackend,
  }) => {
    const { summary, detail } = fixtureConversation()
    await mockBackend({
      conversations: [summary],
      details: { [CONV_UUID]: detail },
      extraRoutes: async (p) => {
        await p.route('**/api/search**', (route: Route) => {
          route.fulfill({
            contentType: 'application/json',
            body: JSON.stringify(
              envelopeResponse({ total: 1, returned: 1, truncated: false }),
            ),
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
    await input.fill(NEEDLE)

    await expect(page.locator('[data-result-card]').first()).toBeVisible()

    // Footer must NOT render under truncated=false. Bidirectional pair
    // with the previous test — if the impl always rendered (or never
    // did), one of these two tests would fail.
    await expect(page.getByTestId('search-truncation-footer')).toHaveCount(0)
  })
})
