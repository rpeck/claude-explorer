import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry, waitForScrollQuiet } from './fixtures'
import type { SearchResult, Message, CompactMarker } from '../src/lib/types'

/**
 * Bug 3 (2026-05-26) — Auto-promote-on-refetch yanks the user back to the
 * first match after they have demonstrated focus (clicked a bubble OR
 * manually scrolled).
 *
 * Reproducer (user's words, 2026-05-26):
 *   1. Search "ran out of context" -> hits land, auto-promote scrolls to
 *      first match (correct).
 *   2. User clicks a bubble in the conversation pane (or wheel-scrolls).
 *   3. User toggles Show Compactions / Show Tools.
 *   4. The query-key flip refetches /api/search, results arrive, the
 *      auto-promote effect yanks the user back to the first match. UGH.
 *
 * The user-approved fix: introduce a "demonstrated focus" latch. Once
 * the user clicks a bubble or manually wheel/touch-scrolls, suppress
 * auto-promote until they explicitly re-engage navigation (Cmd+G,
 * type a new query, or change conversations).
 *
 * This spec is the multi-hit complement to
 * `toggle-preserves-focus-after-click.spec.ts` (single-hit fixture).
 * The toggle-preserves spec pins the recenter-target chain (the
 * userFocusedUuidRef -> markPendingRecenter path); this spec pins the
 * auto-promote suppression (the SearchPanelContext-side gate).
 *
 * Fixture: ~600 messages, FIVE distributed search hits (none on a
 * compaction summary so the toggle does not vanish the active hit row).
 * Tests cover:
 *   1. Click + toggle off -> sidebar shows "no active match", viewer stays.
 *   2. NEGATIVE PAIR — no click before toggle -> auto-promote DOES fire.
 *   3. Wheel scroll + toggle off -> viewer stays where user scrolled.
 *   4. Click + toggle off + Cmd+G -> sidebar moves to match 0, viewer
 *      scrolls there (Cmd+G clears demonstrated focus + advances).
 */

const CONV_UUID = '00000000-0000-0000-0000-00000000d330'
const TOTAL_MESSAGES = 600
const NEEDLE = 'tungsten'

// Five non-compaction hits sprinkled across the conversation. Pick
// indices clearly separated so toggling compactions on/off changes which
// rows are visible above each hit (the surface for the bug).
const HIT_INDICES = [80, 200, 320, 440, 560]

// Compactions every 30 rows EXCEPT on hit indices.
const COMPACT_EVERY_N = 30

function makeFillerMessage(i: number): Message {
  const sender = i % 2 === 0 ? 'human' : 'assistant'
  const text =
    `Filler message ${i}. ` +
    'Lorem ipsum dolor sit amet, consectetur adipiscing elit. '.repeat(8)
  return makeMessage({
    uuid: `m-${String(i).padStart(4, '0')}`,
    sender,
    text,
    content: [{ type: 'text', text }],
    parent_message_uuid: i === 0 ? null : `m-${String(i - 1).padStart(4, '0')}`,
  })
}

function makeHitMessage(i: number): Message {
  const sender = i % 2 === 0 ? 'human' : 'assistant'
  const text = `Hit message ${i}: the ${NEEDLE} lives here in body text.`
  return makeMessage({
    uuid: `m-${String(i).padStart(4, '0')}`,
    sender,
    text,
    content: [{ type: 'text', text }],
    parent_message_uuid: i === 0 ? null : `m-${String(i - 1).padStart(4, '0')}`,
  })
}

function makeCompactMessage(i: number): Message {
  const text = `Auto-compact summary at idx ${i}.`
  return makeMessage({
    uuid: `m-compact-${String(i).padStart(4, '0')}`,
    sender: 'human',
    text,
    content: [{ type: 'text', text }],
    parent_message_uuid: i === 0 ? null : `m-${String(i - 1).padStart(4, '0')}`,
  })
}

function buildMessages(): { messages: Message[]; compactMarkers: CompactMarker[] } {
  const messages: Message[] = []
  const compactMarkers: CompactMarker[] = []
  const hitSet = new Set(HIT_INDICES)
  for (let i = 0; i < TOTAL_MESSAGES; i++) {
    if (hitSet.has(i)) {
      messages.push(makeHitMessage(i))
    } else if (i > 0 && i % COMPACT_EVERY_N === 0) {
      const msg = makeCompactMessage(i)
      messages.push(msg)
      compactMarkers.push({
        message_uuid: msg.uuid,
        summary_text: msg.text,
        timestamp: msg.created_at,
        kind: 'auto',
        user_prompt: null,
      })
    } else {
      messages.push(makeFillerMessage(i))
    }
  }
  return { messages, compactMarkers }
}

const { messages, compactMarkers } = buildMessages()

const summary = makeSummary({
  uuid: CONV_UUID,
  source: 'CLAUDE_CODE',
  name: 'Auto-promote-demonstrated-focus fixture',
  message_count: TOTAL_MESSAGES,
})

const detail = makeDetail(summary, messages, { compact_markers: compactMarkers })

// Search returns five hits in chronological (idx) order. The conv has
// only one entry in `results[]`; the matching_messages array carries all
// five.
const searchResults: SearchResult[] = [
  {
    conversation_uuid: CONV_UUID,
    conversation_name: summary.name,
    conversation_updated_at: summary.updated_at,
    conversation_created_at: summary.created_at,
    project_name: null,
    matching_messages: HIT_INDICES.map((idx) => ({
      message_uuid: messages[idx].uuid,
      sender: messages[idx].sender,
      snippet: `Hit message ${idx}: the ${NEEDLE} lives here in body text.`,
      match_start: `Hit message ${idx}: the `.length,
      match_end: `Hit message ${idx}: the `.length + NEEDLE.length,
      created_at: messages[idx].created_at,
    })),
  },
]

async function mockSearch(page: Page, results: SearchResult[]) {
  await page.route('**/api/search**', (route) => {
    const total = results.reduce(
      (acc, r) => acc + r.matching_messages.length,
      0,
    )
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        results,
        total_messages_matched: total,
        returned_messages: total,
        truncated: false,
      }),
    })
  })
}

async function distancePxFromCenter(page: Page, uuid: string): Promise<number> {
  return await page.evaluate((u) => {
    const target = document.querySelector(
      `[data-message-uuid="${u}"]`,
    ) as HTMLElement | null
    const container = document.querySelector(
      '[data-testid="message-stream"]',
    ) as HTMLElement | null
    if (!target || !container) return Number.POSITIVE_INFINITY
    const t = target.getBoundingClientRect()
    const c = container.getBoundingClientRect()
    return Math.abs(t.top + t.height / 2 - (c.top + c.height / 2))
  }, uuid)
}

async function streamScrollTop(page: Page): Promise<number> {
  return await page.evaluate(() => {
    const stream = document.querySelector(
      '[data-testid="message-stream"]',
    ) as HTMLElement | null
    return stream?.scrollTop ?? 0
  })
}

async function waitForHeightShrink(
  page: Page,
  heightBefore: number,
  shrinkBy: number,
): Promise<void> {
  await expect
    .poll(
      async () =>
        await page.evaluate(() => {
          const stream = document.querySelector(
            '[data-testid="message-stream"]',
          ) as HTMLElement | null
          const spacer = stream?.querySelector(
            'div[style*="position: relative"]',
          ) as HTMLElement | null
          return spacer?.offsetHeight ?? 0
        }),
      { timeout: 5000 },
    )
    .toBeLessThan(heightBefore - shrinkBy)
}

async function streamTotalHeight(page: Page): Promise<number> {
  return await page.evaluate(() => {
    const stream = document.querySelector(
      '[data-testid="message-stream"]',
    ) as HTMLElement | null
    const spacer = stream?.querySelector(
      'div[style*="position: relative"]',
    ) as HTMLElement | null
    return spacer?.offsetHeight ?? 0
  })
}

test.describe('Bug 3 (2026-05-26): auto-promote respects demonstrated focus', () => {
  test('a bubble click right after a search jump is not undone by a pending correction', async ({
    page,
    mockBackend,
  }) => {
    // 2026-10-23: the jump's scrollBubbleIntoView schedules corrections
    // (250/500/500 ms) that re-center the hit. A click on another bubble
    // inside that window scrolled the clicked bubble into view, and then a
    // correction scrolled back to the hit, about 1000 px away. This was
    // the cause of the full-suite flake of the next test. A click
    // demonstrates focus, so it now cancels the pending corrections.
    await mockBackend({ conversations: [summary], details: { [CONV_UUID]: detail } })
    await mockSearch(page, searchResults)
    await page.setViewportSize({ width: 1024, height: 900 })
    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await searchInput.fill(NEEDLE)
    const firstHit = page.locator(`[data-message-uuid="${messages[HIT_INDICES[0]].uuid}"]`)
    // The ring appears in the same task as scrollBubbleIntoView: the
    // correction chain has just started.
    await expect(firstHit).toHaveClass(/ring-yellow-400/, { timeout: 10000 })

    // Act inside the correction window: no wait.
    await page.keyboard.press('Escape')
    const clickedUuid = 'm-0085'
    const clicked = page.locator(`[data-message-uuid="${clickedUuid}"]`)
    await clicked.click()

    // Every pending correction has run or was cancelled.
    await expect(page.getByTestId('message-stream')).not.toHaveAttribute('data-scroll-settling', {
      timeout: 10000,
    })
    await expect(clicked).toBeInViewport()
    expect(
      await distancePxFromCenter(page, clickedUuid),
      'the clicked bubble must stay near the center: a pending correction scrolled back to the search hit',
    ).toBeLessThan(800)
  })

  test('after bubble click, toggling Show Compactions OFF does NOT yank viewer to first match', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [CONV_UUID]: detail },
    })
    await mockSearch(page, searchResults)
    await page.setViewportSize({ width: 1024, height: 900 })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    // Open search, type the needle, wait for the 5 matches to appear.
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(NEEDLE)
    await expect(page.locator('text=/of\\s+5\\s+matches/')).toBeVisible({
      timeout: 10000,
    })

    // Auto-promote should have landed us on the first match (m-0080).
    // Wait for the yellow ring to confirm the landing happened.
    const firstHitUuid = messages[HIT_INDICES[0]].uuid
    const firstHit = page.locator(`[data-message-uuid="${firstHitUuid}"]`)
    await expect(firstHit).toHaveClass(/ring-yellow-400/, { timeout: 5000 })

    // Close the search panel so its overlay does not intercept clicks.
    await page.keyboard.press('Escape')

    // Click a non-hit bubble in the same area. m-0085 is filler near the
    // first hit; the virtualizer keeps it mounted via overscan.
    const clickedUuid = 'm-0085'
    const clickedBubble = page.locator(`[data-message-uuid="${clickedUuid}"]`)
    await expect(clickedBubble).toBeAttached({ timeout: 5000 })
    await clickedBubble.click() // click() scrolls it into view first
    await waitForScrollQuiet(page)

    const distanceBeforeToggle = await distancePxFromCenter(page, clickedUuid)
    // Sanity: the clicked bubble was near viewport (else the test setup
    // itself is broken).
    expect(distanceBeforeToggle).toBeLessThan(800)

    // Capture the scroll position BEFORE the toggle so the contract
    // can be expressed as "scroll position does not yank to the first-
    // match's scrollTop." This avoids tight viewport-distance asserts
    // that conflate two effects: (a) auto-promote firing (the
    // regression we want to catch), and (b) recenter scrollBubbleIntoView
    // settling on the clicked bubble (an orthogonal contract pinned by
    // toggle-preserves-focus-after-click.spec.ts).
    //
    // The PRIMARY contract for this spec is the SIDEBAR-side gate:
    // activeMatchIndex stays at -1, no result card is marked active.
    // The viewer-side stability is a SECONDARY assertion bounded loosely
    // (within 5 viewports) — if auto-promote fires, the viewer snaps to
    // first-hit's scrollTop which is many viewports away from the
    // clicked bubble's scrollTop.
    const scrollTopBeforeToggle = await streamScrollTop(page)

    // Toggle Show Compactions OFF.
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    await expect(compactionsCheckbox).toBeChecked()
    const heightBefore = await streamTotalHeight(page)
    // The toggle changes the search query key: the app fetches the hits
    // again, and auto-promote reacts to that answer (2026-10-24: a 1.5 s
    // sleep instead of these two signals).
    const refetched = page.waitForResponse((r) => r.url().includes('/api/search'))
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).not.toBeChecked()
    await waitForHeightShrink(page, heightBefore, 1000)
    await refetched
    await waitForScrollQuiet(page)

    // CONTRACT 1: the sidebar's active-match counter must show "no
    // active match" (the "—" placeholder in SearchPanel.tsx:460).
    // Open the panel back up to inspect the counter.
    await page.keyboard.press('Meta+k')
    await expect(searchInput).toBeVisible()
    await expect(page.locator('text=/of\\s+5\\s+matches/')).toBeVisible({
      timeout: 5000,
    })
    // The active-match position should be "—" (the literal em-dash
    // placeholder rendered when activeMatchIndex < 0).
    const counter = page.locator('text=/—\\s+of\\s+5\\s+matches/')
    await expect(counter).toBeVisible({ timeout: 5000 })

    // CONTRACT 2: no result card carries the "active" ring class. The
    // active card gets `ring-blue-500` in SearchPanel.tsx:662 when
    // isActive is true; if no card is active, NO result card has that
    // class.
    const activeCardCount = await page
      .locator('[data-result-card].ring-blue-500')
      .count()
    expect(
      activeCardCount,
      'after demonstrated-focus toggle: NO result card should be marked active ' +
        '(activeMatchIndex must stay at -1)',
    ).toBe(0)

    // CONTRACT 3 (secondary): the conversation viewer is NOT yanked to
    // the first-match's scroll position. Allow up to 5 viewports of
    // natural reflow (virtualizer measurement settles can move scrollTop
    // by a few hundred px when row heights change). Anything more means
    // auto-promote fired.
    await page.keyboard.press('Escape') // close search panel
    await waitForScrollQuiet(page)
    const scrollTopAfterToggle = await streamScrollTop(page)
    const delta = Math.abs(scrollTopAfterToggle - scrollTopBeforeToggle)
    expect(
      delta,
      `after demonstrated-focus toggle: scrollTop must NOT yank to first ` +
        `match (drifted ${delta} px from ${scrollTopBeforeToggle} to ` +
        `${scrollTopAfterToggle}; if close to ~16K, auto-promote fired ` +
        `despite demonstrated focus)`,
    ).toBeLessThan(5000)

    // And the first match must NOT have the yellow ring (it would mean
    // auto-promote fired).
    await expect(firstHit).not.toHaveClass(/ring-yellow-400/)
  })

  test('NEGATIVE PAIR: with NO bubble click and NO manual scroll, toggling DOES auto-promote to first match', async ({
    page,
    mockBackend,
  }) => {
    // Defeats a false-pass fix that drops the auto-promote effect entirely.
    // When the user has NOT demonstrated focus, the existing "results
    // refresh -> jump to first hit" UX must still fire.
    await mockBackend({
      conversations: [summary],
      details: { [CONV_UUID]: detail },
    })
    await mockSearch(page, searchResults)
    await page.setViewportSize({ width: 1024, height: 900 })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await searchInput.fill(NEEDLE)
    await expect(page.locator('text=/of\\s+5\\s+matches/')).toBeVisible({
      timeout: 10000,
    })

    const firstHitUuid = messages[HIT_INDICES[0]].uuid
    const firstHit = page.locator(`[data-message-uuid="${firstHitUuid}"]`)
    await expect(firstHit).toHaveClass(/ring-yellow-400/, { timeout: 5000 })

    // No click, no scroll. Toggle Show Compactions OFF.
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    const heightBefore = await streamTotalHeight(page)
    // The toggle changes the search query key: the app fetches the hits
    // again, and auto-promote reacts to that answer (2026-10-24: a 1.5 s
    // sleep instead of these two signals).
    const refetched = page.waitForResponse((r) => r.url().includes('/api/search'))
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).not.toBeChecked()
    await waitForHeightShrink(page, heightBefore, 1000)
    await refetched
    await waitForScrollQuiet(page)

    // Auto-promote should fire: sidebar lands on match 1 (the first
    // matching message in the refreshed result set, which is still
    // m-0080). The yellow ring lands on it (may have aged out by 2 s
    // timeout, but the counter is authoritative).
    await expect(page.locator('text=/1\\s+of\\s+5\\s+matches/')).toBeVisible({
      timeout: 5000,
    })

    // The first card should be marked active.
    const firstCard = page.locator('[data-result-card]').first()
    await expect(firstCard).toHaveClass(/ring-blue-500/, { timeout: 5000 })
  })

  test('after manual wheel scroll, toggling Show Compactions OFF preserves scroll position', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [CONV_UUID]: detail },
    })
    await mockSearch(page, searchResults)
    await page.setViewportSize({ width: 1024, height: 900 })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    // Trigger the search so a match exists for auto-promote to fight
    // over.
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await searchInput.fill(NEEDLE)
    await expect(page.locator('text=/of\\s+5\\s+matches/')).toBeVisible({
      timeout: 10000,
    })
    const firstHitUuid = messages[HIT_INDICES[0]].uuid
    await expect(
      page.locator(`[data-message-uuid="${firstHitUuid}"]`),
    ).toHaveClass(/ring-yellow-400/, { timeout: 5000 })

    // Close search panel.
    await page.keyboard.press('Escape')

    // Fire a real wheel event on the message stream. The deltaY signals
    // user-initiated scroll (programmatic scroll does NOT fire wheel
    // events — that's the disambiguation that lets us treat wheel as
    // demonstrated focus without false-positives).
    const stream = page.locator('[data-testid="message-stream"]')
    await stream.dispatchEvent('wheel', {
      deltaY: -2000,
      deltaMode: 0,
      bubbles: true,
    })
    // The dispatchEvent fires the synthetic React handler but does NOT
    // actually scroll the container (Playwright's dispatchEvent path
    // bypasses the native scroll). So we also manually adjust scrollTop
    // to simulate the visible-position change a real wheel would cause.
    await page.evaluate(() => {
      const el = document.querySelector(
        '[data-testid="message-stream"]',
      ) as HTMLElement | null
      if (el) el.scrollTop = Math.max(0, el.scrollTop - 2000)
    })
    await waitForScrollQuiet(page)

    const scrollTopBeforeToggle = await streamScrollTop(page)

    // Toggle Show Compactions OFF.
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    await expect(compactionsCheckbox).toBeChecked()
    const heightBefore = await streamTotalHeight(page)
    // The toggle changes the search query key: the app fetches the hits
    // again, and auto-promote reacts to that answer (2026-10-24: a 1.5 s
    // sleep instead of these two signals).
    const refetched = page.waitForResponse((r) => r.url().includes('/api/search'))
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).not.toBeChecked()
    await waitForHeightShrink(page, heightBefore, 1000)
    await refetched
    await waitForScrollQuiet(page)

    void scrollTopBeforeToggle

    // PRIMARY contract: the sidebar's active-match counter shows "no
    // active match" (the "—" placeholder rendered when activeMatchIndex
    // is < 0). This is the authoritative signal that auto-promote did
    // NOT fire — independent of the (flaky) ring-class timeout dance.
    await page.keyboard.press('Meta+k')
    await expect(searchInput).toBeVisible()
    const counter = page.locator('text=/—\\s+of\\s+5\\s+matches/')
    await expect(
      counter,
      `after manual wheel scroll + toggle: activeMatchIndex must stay ` +
        `at -1 (the "—" placeholder in the counter); if instead the ` +
        `counter reads "1 of 5", auto-promote fired despite the wheel ` +
        `event marking demonstrated focus`,
    ).toBeVisible({ timeout: 5000 })

    // SECONDARY contract: no result card is marked active.
    const activeCardCount = await page
      .locator('[data-result-card].ring-blue-500')
      .count()
    expect(
      activeCardCount,
      `after manual wheel + toggle: NO result card should be active ` +
        `(activeMatchIndex must stay at -1)`,
    ).toBe(0)
  })

  test('after demonstrated focus, Cmd+G clears the latch and navigates to match 0', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [CONV_UUID]: detail },
    })
    await mockSearch(page, searchResults)
    await page.setViewportSize({ width: 1024, height: 900 })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await searchInput.fill(NEEDLE)
    await expect(page.locator('text=/of\\s+5\\s+matches/')).toBeVisible({
      timeout: 10000,
    })

    const firstHitUuid = messages[HIT_INDICES[0]].uuid
    const firstHit = page.locator(`[data-message-uuid="${firstHitUuid}"]`)
    await expect(firstHit).toHaveClass(/ring-yellow-400/, { timeout: 5000 })

    // Close search panel, click a bubble (demonstrated focus).
    await page.keyboard.press('Escape')
    const clickedUuid = 'm-0085'
    const clickedBubble = page.locator(`[data-message-uuid="${clickedUuid}"]`)
    await clickedBubble.click() // click() scrolls it into view first
    await waitForScrollQuiet(page)

    // Toggle Show Compactions OFF — demonstrated focus suppresses auto-promote.
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    const heightBefore = await streamTotalHeight(page)
    // The toggle changes the search query key: the app fetches the hits
    // again, and auto-promote reacts to that answer (2026-10-24: a 1.5 s
    // sleep instead of these two signals).
    const refetched = page.waitForResponse((r) => r.url().includes('/api/search'))
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).not.toBeChecked()
    await waitForHeightShrink(page, heightBefore, 1000)
    await refetched
    await waitForScrollQuiet(page)

    // Confirm: sidebar shows no active match.
    await page.keyboard.press('Meta+k')
    const counter = page.locator('text=/—\\s+of\\s+5\\s+matches/')
    await expect(counter).toBeVisible({ timeout: 5000 })

    // Press Cmd+G. This is the user's "navigate me to next match"
    // gesture, which (per the user-approved design) clears the
    // demonstrated-focus latch AND advances activeMatchIndex.
    await page.keyboard.press('Meta+g')

    // Sidebar should now show "1 of 5 matches" (Cmd+G advances from
    // -1 to 0; the SearchPanel.tsx counter shows index+1).
    await expect(page.locator('text=/1\\s+of\\s+5\\s+matches/')).toBeVisible({
      timeout: 5000,
    })

    // First card must be active.
    const firstCard = page.locator('[data-result-card]').first()
    await expect(firstCard).toHaveClass(/ring-blue-500/, { timeout: 5000 })

    // Viewer scrolls to first hit (the yellow ring lands again).
    await expect(firstHit).toHaveClass(/ring-yellow-400/, { timeout: 5000 })
  })
})
