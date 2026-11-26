import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'
import type { SearchResult, CompactMarker } from '../src/lib/types'

/**
 * Bug 1 (2026-05-26) — Search must respect the "Show Compactions" toggle.
 *
 * The user-visible contract: when "Show Compactions" in the conversation
 * header is OFF, the FTS search panel must not return hits whose match
 * falls inside a compaction-summary (``isCompactSummary``) row body.
 * Clicking such a hit would land the user on a hidden marker — the
 * sidebar/viewer mismatch this spec exists to prevent.
 *
 * Documented in `articles/part_2_web_app.md` line 252: same contract as
 * the Tools toggle. This spec is the bidirectional pair for
 * `search-match-focus-mismatch.spec.ts` (the Tools-toggle version).
 *
 * Architecture pinned (Council 2026-05-26): the frontend sends
 * `include_compactions=false` on the wire when `hideCompactMarkers=true`.
 * The mock server here mimics the backend's filter: it returns the
 * compaction-row match ONLY when `include_compactions=true`.
 */

const TM = '00000000-0000-0000-0000-00000000c401'

const summary = makeSummary({
  uuid: TM,
  source: 'CLAUDE_CODE',
  name: 'Compactions-filter fixture',
  message_count: 3,
})

const SUMMARY_NEEDLE = 'plutonium'

// Three messages: a regular pre-compaction message, an isCompactSummary
// row carrying the needle in its summary body, and a post-compaction
// regular message. The compaction summary is the ONLY message containing
// the needle, so under include_compactions=false the search must return
// zero hits for this conversation.
const messages = [
  makeMessage({
    uuid: 'pre-msg',
    sender: 'human',
    text: 'Pre-compaction message, no needle.',
    content: [{ type: 'text', text: 'Pre-compaction message, no needle.' }],
  }),
  makeMessage({
    uuid: 'compact-msg',
    sender: 'human', // CC emits the synthetic row as a user message
    text: `Compaction summary mentioning ${SUMMARY_NEEDLE} keyword.`,
    content: [{
      type: 'text',
      text: `Compaction summary mentioning ${SUMMARY_NEEDLE} keyword.`,
    }],
    parent_message_uuid: 'pre-msg',
  }),
  makeMessage({
    uuid: 'post-msg',
    sender: 'assistant',
    text: 'Post-compaction reply, no needle.',
    content: [{ type: 'text', text: 'Post-compaction reply, no needle.' }],
    parent_message_uuid: 'compact-msg',
  }),
]

const compactMarkers: CompactMarker[] = [
  {
    message_uuid: 'compact-msg',
    summary_text: messages[1].text,
    timestamp: messages[1].created_at,
    kind: 'auto',
    user_prompt: null,
  },
]

const detail = makeDetail(summary, messages, { compact_markers: compactMarkers })

// Server payloads for the two toggle states.
const compactionIncludedResults: SearchResult[] = [{
  conversation_uuid: TM,
  conversation_name: summary.name,
  conversation_updated_at: summary.updated_at,
  conversation_created_at: summary.created_at,
  project_name: null,
  matching_messages: [
    {
      message_uuid: 'compact-msg',
      sender: 'human',
      snippet: `Compaction summary mentioning ${SUMMARY_NEEDLE} keyword.`,
      match_start: 28,
      match_end: 28 + SUMMARY_NEEDLE.length,
      created_at: messages[1].created_at,
    },
  ],
}]

// With include_compactions=false the server returns zero hits for this
// conversation (the only match was inside the compaction row, which the
// SQL filter dropped).
const compactionExcludedResults: SearchResult[] = []

/**
 * Stand up a search route that mimics the real backend's
 * include_compactions filter AND records every observed value of the
 * include_compactions param so tests can assert the toggle reached
 * the network layer.
 */
async function mountSearchRouteAware(page: import('@playwright/test').Page) {
  const seen: Array<{ q: string; includeCompactions: 'true' | 'false' }> = []
  await page.route('**/api/search**', async (route) => {
    let q = ''
    let param: string | null = null
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() ?? '{}')
      q = body.q ?? ''
      // POST body uses the canonical boolean.
      const v = body.include_compactions
      param = v === false ? 'false' : v === true ? 'true' : null
    } else {
      const url = new URL(route.request().url())
      q = url.searchParams.get('q') ?? ''
      param = url.searchParams.get('include_compactions')
    }
    // Backend default is true (no param sent on the URL means true).
    const includeCompactions: 'true' | 'false' = param === 'false' ? 'false' : 'true'
    seen.push({ q, includeCompactions })
    const body =
      includeCompactions === 'false'
        ? compactionExcludedResults
        : compactionIncludedResults
    const total = body.reduce(
      (acc, r) => acc + r.matching_messages.length,
      0,
    )
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        results: body,
        total_messages_matched: total,
        returned_messages: total,
        truncated: false,
      }),
    })
  })
  return seen
}

test.describe('Search — include_compactions filter (Bug 1 fix 2026-05-26)', () => {
  test('with Show Compactions ON, hits inside isCompactSummary rows surface in the sidebar', async ({
    page,
    mockBackend,
  }) => {
    // Show Compactions defaults to ON (hideCompactMarkers=false on a fresh
    // preferences load), so this is the default state.
    await mockBackend({
      conversations: [summary],
      details: { [TM]: detail },
    })
    const seen = await mountSearchRouteAware(page)
    await page.setViewportSize({ width: 1024, height: 1200 })

    await withNetRetry(page, () => page.goto(`/conversations/${TM}`))
    await expect(page.locator('[data-message-uuid="pre-msg"]')).toBeVisible()

    // Show Compactions checkbox must be CHECKED by default.
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    await expect(compactionsCheckbox).toBeChecked()

    // Open search, type the needle.
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(SUMMARY_NEEDLE)

    // Wait for the result envelope to update.
    await expect(page.locator('text=/of\\s+1\\s+match\\b/')).toBeVisible({ timeout: 10000 })

    // Sidebar shows ONE match (the compaction hit) — the network layer
    // received `include_compactions=true` (or didn't set the param at
    // all, which the backend treats as true).
    expect(
      seen.some((s) => s.q === SUMMARY_NEEDLE && s.includeCompactions === 'true'),
      `expected at least one /api/search request with q="${SUMMARY_NEEDLE}" ` +
        `and include_compactions=true; got ${JSON.stringify(seen)}`,
    ).toBe(true)
  })

  test('with Show Compactions OFF, hits inside isCompactSummary rows are filtered out', async ({
    page,
    mockBackend,
  }) => {
    // Bidirectional pair: same conversation, same query, toggle OFF →
    // zero hits. The network call must carry include_compactions=false.
    await mockBackend({
      conversations: [summary],
      details: { [TM]: detail },
    })
    const seen = await mountSearchRouteAware(page)
    await page.setViewportSize({ width: 1024, height: 1200 })

    await withNetRetry(page, () => page.goto(`/conversations/${TM}`))
    await expect(page.locator('[data-message-uuid="pre-msg"]')).toBeVisible()

    // Click the Show Compactions checkbox OFF.
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    await expect(compactionsCheckbox).toBeChecked()
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).not.toBeChecked()

    // Open search, type the needle.
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(SUMMARY_NEEDLE)

    // Settle: wait for the network call carrying include_compactions=false
    // to be observed by the route handler. We poll the `seen` array
    // because the queryKey-driven re-fetch is async.
    await expect
      .poll(
        () =>
          seen.some(
            (s) => s.q === SUMMARY_NEEDLE && s.includeCompactions === 'false',
          ),
        {
          timeout: 5000,
          message:
            'expected /api/search request with include_compactions=false; ' +
            `seen requests: ${JSON.stringify(seen)}`,
        },
      )
      .toBe(true)

    // No matches surface in the sidebar. Use the "No matches" empty state
    // text or absence of any result card as the assertion.
    await expect(page.locator('[data-result-card]')).toHaveCount(0, {
      timeout: 5000,
    })
  })

  test('toggling Show Compactions OFF then ON re-includes compaction hits (no stale cache)', async ({
    page,
    mockBackend,
  }) => {
    // Bidirectional pair, closing the loop: a user who hides compactions,
    // searches, then re-enables compactions MUST see the hit reappear.
    // React Query's cache must NOT serve a stale "no results" payload —
    // the queryKey includes include_compactions so the second toggle
    // re-fires the request.
    await mockBackend({
      conversations: [summary],
      details: { [TM]: detail },
    })
    const seen = await mountSearchRouteAware(page)
    await page.setViewportSize({ width: 1024, height: 1200 })

    await withNetRetry(page, () => page.goto(`/conversations/${TM}`))
    await expect(page.locator('[data-message-uuid="pre-msg"]')).toBeVisible()

    // Toggle OFF.
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).not.toBeChecked()

    // Search.
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await searchInput.fill(SUMMARY_NEEDLE)
    await expect
      .poll(
        () =>
          seen.some(
            (s) => s.q === SUMMARY_NEEDLE && s.includeCompactions === 'false',
          ),
        { timeout: 5000 },
      )
      .toBe(true)
    await expect(page.locator('[data-result-card]')).toHaveCount(0, {
      timeout: 5000,
    })

    // Toggle back ON.
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).toBeChecked()

    // The queryKey changed → React Query fires a fresh request with
    // include_compactions=true → match reappears.
    await expect
      .poll(
        () =>
          seen.some(
            (s) => s.q === SUMMARY_NEEDLE && s.includeCompactions === 'true',
          ),
        { timeout: 5000 },
      )
      .toBe(true)
    await expect(page.locator('text=/of\\s+1\\s+match\\b/')).toBeVisible({ timeout: 10000 })
  })
})
