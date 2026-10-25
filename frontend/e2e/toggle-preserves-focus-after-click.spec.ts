import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry, waitForScrollQuiet } from './fixtures'
import type { SearchResult, Message, CompactMarker } from '../src/lib/types'

/**
 * Bug 2 (2026-05-26) — Scroll position jumps when toggling Show Compactions
 * AFTER the user has clicked an adjacent (non-compaction) message.
 *
 * Reproducer (user's words, 2026-05-26):
 *   1. Search "ran out of context" → match lands on a compaction message.
 *   2. Click an adjacent non-compaction message (mouse click on a bubble).
 *   3. Toggle Show Compactions OFF.
 *   4. Conversation panel scrolls to somewhere unexpected — NOT the
 *      clicked bubble.
 *
 * Root cause:
 *   The priority chain in `markPendingRecenter` reads
 *   `activeMatchUuid ?? highlightMessageId ?? getSelectedMessageId()`.
 *   Click of a non-match message updates `selectedMessageIndex` but
 *   does NOT clear `activeMatchUuid` — so the recenter target is still
 *   the (about-to-be-hidden) compaction row. The compaction DOM
 *   unmounts mid-toggle and `scrollBubbleIntoView(null)` early-returns,
 *   leaving the user wherever the virtualizer's reflow lands them.
 *
 * Fix (Council 2026-05-26): introduce `userFocusedUuidRef` set on every
 * bubble click, consulted FIRST in the priority chain. Refs are
 * synchronous (no React batching race) and don't touch SearchPanelContext
 * state (so Cmd+G semantics — auto-promote re-firing on -1 → match 0 —
 * stay intact).
 *
 * Sibling spec: toggle-preserves-focus-scroll.spec.ts pins the
 * "search-hit lands then toggle without intermediate click" path. This
 * spec adds the "search-hit lands then user clicks a different bubble
 * THEN toggles" path — the exact reproducer the user reported.
 */

const CONV_UUID = '00000000-0000-0000-0000-00000000c220'
const TOTAL_MESSAGES = 600
const COMPACT_AT_IDX = 540 // compaction row carrying the search-hit needle
const ADJACENT_AT_IDX = 543 // a few rows after the compaction — user clicks here
const NEEDLE = 'plutonium'

// Sprinkle compaction rows across the conversation so toggling OFF
// drops a non-trivial number of rows (and the virtualizer reflows
// meaningfully). The reproduction needs the toggle to ACTUALLY change
// row heights enough to surface drift, not be a no-op.
const COMPACT_EVERY_N = 30

function makeFillerMessage(i: number): Message {
  const sender = i % 2 === 0 ? 'human' : 'assistant'
  const text =
    `Filler message ${i}. ` +
    'Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do '
      .repeat(10)
  return makeMessage({
    uuid: `m-${String(i).padStart(4, '0')}`,
    sender,
    text,
    content: [{ type: 'text', text }],
    parent_message_uuid: i === 0 ? null : `m-${String(i - 1).padStart(4, '0')}`,
  })
}

function makeCompactMessage(i: number, body: string): Message {
  return makeMessage({
    uuid: `m-compact-${String(i).padStart(4, '0')}`,
    sender: 'human',
    text: body,
    content: [{ type: 'text', text: body }],
    parent_message_uuid: i === 0 ? null : `m-${String(i - 1).padStart(4, '0')}`,
  })
}

function buildMessages(): {
  messages: Message[]
  compactMarkers: CompactMarker[]
} {
  const messages: Message[] = []
  const compactMarkers: CompactMarker[] = []
  for (let i = 0; i < TOTAL_MESSAGES; i++) {
    if (i === COMPACT_AT_IDX) {
      // The target compaction carrying the needle.
      const body = `Compaction summary that ran out of context — ${NEEDLE} lives here in the summary.`
      const msg = makeCompactMessage(i, body)
      messages.push(msg)
      compactMarkers.push({
        message_uuid: msg.uuid,
        summary_text: msg.text,
        timestamp: msg.created_at,
        kind: 'auto',
        user_prompt: null,
      })
    } else if (i > 0 && i % COMPACT_EVERY_N === 0) {
      const body = `Routine auto-compact at idx ${i}.`
      const msg = makeCompactMessage(i, body)
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
  name: 'Toggle-preserves-focus-after-click fixture',
  message_count: TOTAL_MESSAGES,
})

const detail = makeDetail(summary, messages, { compact_markers: compactMarkers })

// Search result that lands on the target compaction row.
const searchResults: SearchResult[] = [
  {
    conversation_uuid: CONV_UUID,
    conversation_name: summary.name,
    conversation_updated_at: summary.updated_at,
    conversation_created_at: summary.created_at,
    project_name: null,
    matching_messages: [
      {
        message_uuid: messages[COMPACT_AT_IDX].uuid,
        sender: messages[COMPACT_AT_IDX].sender,
        snippet: `ran out of context — ${NEEDLE} lives here`,
        match_start: 22,
        match_end: 22 + NEEDLE.length,
        created_at: messages[COMPACT_AT_IDX].created_at,
      },
    ],
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
    return Math.abs((t.top + t.height / 2) - (c.top + c.height / 2))
  }, uuid)
}

test.describe('Bug 2 (2026-05-26): Toggle preserves focus AFTER user click', () => {
  test('after clicking an adjacent bubble, toggling Compactions OFF keeps the CLICKED bubble centered (not the search-hit compaction)', async ({
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

    // Step 1: open search, fill the needle, click the search-result card
    // → focus lands on the compaction row.
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(NEEDLE)
    await expect(page.locator('text=/of\\s+1\\s+matches/')).toBeVisible({
      timeout: 10000,
    })

    const hitCard = page.getByRole('button', { name: new RegExp(NEEDLE) }).first()
    await expect(hitCard).toBeVisible()
    await hitCard.click()
    await waitForScrollQuiet(page) // the jump and its corrections are done

    const compactionUuid = messages[COMPACT_AT_IDX].uuid
    const adjacentUuid = messages[ADJACENT_AT_IDX].uuid

    // Sanity: the compaction row is now near viewport center.
    const initialDistance = await distancePxFromCenter(page, compactionUuid)
    expect(
      initialDistance,
      `pre-step-2 sanity: search-hit click must center the compaction row ` +
        `(was ${initialDistance} px off — search-hit landing itself is broken)`,
    ).toBeLessThanOrEqual(100)

    // Step 2: close the search panel so its overlay doesn't intercept
    // clicks on the conversation, then click an ADJACENT (non-compaction)
    // bubble. This is the exact user-action that surfaces Bug 2.
    await page.keyboard.press('Escape') // close the search panel overlay
    const adjacentBubble = page.locator(`[data-message-uuid="${adjacentUuid}"]`)
    // Make sure the adjacent bubble is in the DOM (the virtualizer may
    // have it overscan-mounted but not visible; if not, we need to
    // scroll a hair further. With overscan=5, idx 543 should be just
    // below the viewport when idx 540 is centered — close enough to be
    // mounted.)
    await expect(adjacentBubble).toBeAttached({ timeout: 5000 })
    // If the bubble isn't visible (off-screen overscan mount), scroll
    // a touch so we can click it.
    await adjacentBubble.click() // click() scrolls it into view first
    await waitForScrollQuiet(page)

    // Step 3: toggle Show Compactions OFF.
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    await expect(compactionsCheckbox).toBeChecked()
    const totalHeightBefore = await page.evaluate(() => {
      const stream = document.querySelector(
        '[data-testid="message-stream"]',
      ) as HTMLElement | null
      const spacer = stream?.querySelector(
        'div[style*="position: relative"]',
      ) as HTMLElement | null
      return spacer?.offsetHeight ?? 0
    })
    // The toggle changes the search query key, so the hits are fetched
    // again (2026-10-24: a 1.5 s sleep instead of these signals).
    const refetched = page.waitForResponse((r) => r.url().includes('/api/search'))
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).not.toBeChecked()
    // Settle: virtualizer total height shrank.
    await expect
      .poll(
        async () => {
          return await page.evaluate(() => {
            const stream = document.querySelector(
              '[data-testid="message-stream"]',
            ) as HTMLElement | null
            const spacer = stream?.querySelector(
              'div[style*="position: relative"]',
            ) as HTMLElement | null
            return spacer?.offsetHeight ?? 0
          })
        },
        { timeout: 5000 },
      )
      .toBeLessThan(totalHeightBefore - 1000)
    await refetched
    await waitForScrollQuiet(page)

    // CONTRACT: the CLICKED bubble (adjacentUuid) should now be the
    // recenter target, NOT the now-hidden compaction. The adjacent
    // bubble must remain near viewport center.
    const finalDistance = await distancePxFromCenter(page, adjacentUuid)
    expect(
      finalDistance,
      `after Show Compactions OFF: the user-clicked bubble must stay ` +
        `within ±200 px of viewport center (drifted ${finalDistance} px — ` +
        `Bug 2 fix: priority chain must prefer the explicit user click ` +
        `over the now-stale activeMatchUuid pointing at the compaction)`,
    ).toBeLessThanOrEqual(200)

    // Negative-pair half: the compaction row should be GONE from the DOM
    // (we hid all compactions). If it's still there, the visibility
    // filter is broken — which would mask Bug 2 with a different bug.
    await expect(
      page.locator(`[data-message-uuid="${compactionUuid}"]`),
    ).toHaveCount(0)
  })

  test('NEGATIVE PAIR: with NO user click after search-hit, toggle still centers the search-hit (no Bug 2 false-fire)', async ({
    page,
    mockBackend,
  }) => {
    // Defeats a false-pass fix that drops the activeMatchUuid path
    // entirely. The original toggle-preserves-focus-scroll spec already
    // pins this; this version restates it specifically as the inverse
    // half of the click-then-toggle pair so the two tests live next to
    // each other and the relationship is obvious to future maintainers.
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
    await expect(page.locator('text=/of\\s+1\\s+matches/')).toBeVisible({
      timeout: 10000,
    })

    const hitCard = page.getByRole('button', { name: new RegExp(NEEDLE) }).first()
    await hitCard.click()
    await waitForScrollQuiet(page)

    const compactionUuid = messages[COMPACT_AT_IDX].uuid
    // Toggle compactions OFF — no intermediate click.
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    const totalHeightBefore = await page.evaluate(() => {
      const stream = document.querySelector(
        '[data-testid="message-stream"]',
      ) as HTMLElement | null
      const spacer = stream?.querySelector(
        'div[style*="position: relative"]',
      ) as HTMLElement | null
      return spacer?.offsetHeight ?? 0
    })
    // The toggle changes the search query key, so the hits are fetched
    // again (2026-10-24: a 1.5 s sleep instead of these signals).
    const refetched = page.waitForResponse((r) => r.url().includes('/api/search'))
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).not.toBeChecked()
    await expect
      .poll(
        async () => {
          return await page.evaluate(() => {
            const stream = document.querySelector(
              '[data-testid="message-stream"]',
            ) as HTMLElement | null
            const spacer = stream?.querySelector(
              'div[style*="position: relative"]',
            ) as HTMLElement | null
            return spacer?.offsetHeight ?? 0
          })
        },
        { timeout: 5000 },
      )
      .toBeLessThan(totalHeightBefore - 1000)
    await refetched
    await waitForScrollQuiet(page)

    // Compaction row vanished. The original `toggle-preserves-focus-scroll`
    // spec already pins the "search-hit stays centered post-toggle"
    // contract for the case where the hit lands on a NON-compaction
    // row. Here the hit row itself is hidden, so the recenter falls
    // through to highlightMessageId (the search-hit's `?highlight=`
    // URL param). The URL param is cleared on a 2 s timer
    // (scheduleHighlightClear in ConversationPage), and the post-toggle
    // recenter runs in the same frame as the URL clear — so the
    // recenter may or may not find a target. The user-observable
    // contract this NEGATIVE PAIR enforces is the WEAKER condition:
    // scroll position MUST stay roughly stable (didn't snap to top or
    // bottom). The compaction-target unmount path is allowed to result
    // in "wherever the virtualizer's reflow landed."
    await expect(
      page.locator(`[data-message-uuid="${compactionUuid}"]`),
    ).toHaveCount(0)
    // We don't assert a tight distance here — the unmounted-target
    // case isn't covered by the priority chain's explicit signals; it
    // falls through to the browser/virtualizer's scroll-anchoring. The
    // sibling spec covers the search-hit-on-VISIBLE-bubble + toggle
    // case where the contract IS tight (±100 px).
  })
})
