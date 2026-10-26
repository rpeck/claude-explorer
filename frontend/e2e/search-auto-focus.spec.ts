import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry, waitForScrollQuiet, recordJumps, jumps, expectNoRevert, waitForKeyboardShortcuts } from './fixtures'
import type { SearchResult } from '../src/lib/types'

/**
 * V1 polish — when full-text search results land for a fresh query,
 * auto-focus the FIRST matching message: scroll the conversation panel
 * to it AND apply the yellow ring class added by navigateToMatch
 * (frontend/src/components/search/navigateToMatch.ts:46-54).
 *
 * Pre-fix: the user types "needle", sees results in the sidebar, but
 * the conversation panel doesn't move — they have to press Cmd+G or
 * click a card to bootstrap navigation. UGH.
 *
 * The auto-promote effect lives in
 * frontend/src/contexts/SearchPanelContext.tsx (added directly after
 * the activeMatchIndex reset effect). It runs once per stable-query
 * cycle, gated on:
 *   - !isSearching (debounce + network settled)
 *   - flatMatches.length > 0 (don't waste re-renders on empty)
 *   - activeMatchIndex === -1 (don't overwrite Cmd+G navigation)
 */

const SM = '00000000-0000-0000-0000-0000000000a7'

const summary = makeSummary({
  uuid: SM,
  name: 'Auto-focus fixture',
  message_count: 4,
})

const messages = [
  makeMessage({
    uuid: 'af-m1',
    sender: 'human',
    text: 'first message at top',
    content: [{ type: 'text', text: 'first message at top' }],
  }),
  makeMessage({
    uuid: 'af-m2',
    sender: 'assistant',
    text: 'unrelated middle 1',
    content: [{ type: 'text', text: 'unrelated middle 1' }],
    parent_message_uuid: 'af-m1',
  }),
  makeMessage({
    uuid: 'af-m3',
    sender: 'human',
    text: 'unrelated middle 2',
    content: [{ type: 'text', text: 'unrelated middle 2' }],
    parent_message_uuid: 'af-m2',
  }),
  makeMessage({
    uuid: 'af-target',
    sender: 'assistant',
    text: 'TARGET message contains needle keyword',
    content: [{ type: 'text', text: 'TARGET message contains needle keyword' }],
    parent_message_uuid: 'af-m3',
  }),
]

const detail = makeDetail(summary, messages)

const searchResults: SearchResult[] = [
  {
    conversation_uuid: SM,
    conversation_name: summary.name,
    conversation_updated_at: summary.updated_at,
    conversation_created_at: summary.created_at,
    project_name: null,
    matching_messages: [
      {
        message_uuid: 'af-target',
        sender: 'assistant',
        snippet: 'TARGET message contains needle keyword',
        match_start: 24,
        match_end: 30,
        created_at: messages[3].created_at,
      },
    ],
  },
]

async function mockSearch(page: Page, results: SearchResult[]) {
  await page.route('**/api/search**', (route) => {
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

test.describe('Search — auto-focus first match on results land (V1 polish)', () => {
  test('typing a query auto-scrolls to first match + applies ring class', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [summary], details: { [SM]: detail } })
    await mockSearch(page, searchResults)
    // Tall viewport so all 4 messages fit; we want to detect the scroll
    // by ring presence + visibility, not by viewport mathematics.
    await page.setViewportSize({ width: 1024, height: 900 })

    await withNetRetry(page, () => page.goto(`/conversations/${SM}`))
    await expect(page.locator('[data-message-uuid="af-m1"]')).toBeVisible()

    // Open the search panel.
    await page.locator('main').click()
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()

    // Type the query and wait for results to land.
    await searchInput.fill('needle')
    await expect(page.locator('text=/of\\s+1\\s+match/')).toBeVisible({ timeout: 10000 })

    // The auto-focus effect should fire WITHOUT any click on a result.
    // Assert the YELLOW ring class lands on the target message bubble.
    // (We assert `ring-yellow-400` specifically, not `ring-2`, because
    // `ring-blue-500` is a different state — keyboard-nav selection
    // ring — and matching `/ring-2/` would let that regress unnoticed.)
    const target = page.locator('[data-message-uuid="af-target"]')
    await expect(target).toBeVisible()
    await expect(target).toHaveClass(/ring-yellow-400/, { timeout: 5000 })
    await expect(target).toBeInViewport()
  })

  test('does NOT yank the user back to first match after Cmd+G navigation (V1 polish)', async ({ page, mockBackend }) => {
    // Two matches in the same conversation. After landing, pressing
    // Cmd+G moves to match 2. The auto-focus effect must NOT then fire
    // again and yank the user back to match 1.
    const twoMatchResults: SearchResult[] = [
      {
        conversation_uuid: SM,
        conversation_name: summary.name,
        conversation_updated_at: summary.updated_at,
        conversation_created_at: summary.created_at,
        project_name: null,
        matching_messages: [
          {
            message_uuid: 'af-m1',
            sender: 'human',
            snippet: 'first message at top — needle here',
            match_start: 22,
            match_end: 28,
            created_at: messages[0].created_at,
          },
          {
            message_uuid: 'af-target',
            sender: 'assistant',
            snippet: 'TARGET message contains needle keyword',
            match_start: 24,
            match_end: 30,
            created_at: messages[3].created_at,
          },
        ],
      },
    ]
    await mockBackend({ conversations: [summary], details: { [SM]: detail } })
    await mockSearch(page, twoMatchResults)
    await page.setViewportSize({ width: 1024, height: 900 })
    await recordJumps(page)

    await withNetRetry(page, () => page.goto(`/conversations/${SM}`))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.locator('main').click()
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await searchInput.fill('needle')
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({ timeout: 10000 })

    // Auto-focus picks match 1 (af-m1). Wait for the ring on it, then
    // wait it back out so the second navigateToMatch can re-apply.
    // Assert the YELLOW search ring, not the generic ring-2 (which would
    // accept the blue keyboard-nav ring and miss real regressions).
    const m1 = page.locator('[data-message-uuid="af-m1"]')
    await expect(m1).toHaveClass(/ring-yellow-400/, { timeout: 5000 })

    // Press Cmd+G to advance to match 2. activeMatchIndex goes 0 → 1.
    await page.keyboard.press('Meta+g')

    // The target message gets the ring; the user is now on match 2.
    const target = page.locator('[data-message-uuid="af-target"]')
    await expect(target).toHaveClass(/ring-yellow-400/, { timeout: 5000 })

    // Crucial: the auto-focus effect must NOT fire again here. It was
    // gated on activeMatchIndex === -1; once we set 0 and then 1 it
    // shouldn't snap back. The Cmd+G jump has settled, so a spurious
    // effect would have run, and the live-region record shows any
    // snap back, even a short one (2026-10-24: a 2.5 s sleep).
    await waitForScrollQuiet(page)
    expectNoRevert((await jumps(page)).live, /Match\s+2\s+of\s+2/i)
    // The target's ring may have aged out (2s timeout). Either way,
    // m1 must NOT have the yellow ring (which would mean the effect
    // yanked us back).
    await expect(m1).not.toHaveClass(/ring-yellow-400/)
  })
})
