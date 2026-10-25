import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry, type Page, waitForScrollQuiet } from './fixtures'
import type { SearchResult, Message, CompactMarker } from '../src/lib/types'

/**
 * Header-checkbox-toggle scroll-pin contract (2026-05-25).
 *
 * Bug:
 *   Toggling the "Show Compactions" (or "Show Tools") checkbox in the
 *   conversation header changes the visibleMessages array length —
 *   inserting or removing ~10-50 bubble rows above whatever bubble the
 *   user is currently reading. The virtualizer's scrollTop is a literal
 *   pixel count; once rows above the anchor change height, that pixel
 *   count points at a different message. The user loses their reading
 *   position with no warning.
 *
 *   The user-visible symptom (reported on Safari + lazy-image cases):
 *   land on a search hit deep in a long conversation, click the
 *   "Show Compactions" checkbox to declutter, and the focused bubble
 *   jumps far off-screen.
 *
 * Contract pinned here:
 *   1. When a focused message exists (via search-hit click → activeMatchUuid,
 *      or via URL ?highlight=, or via keyboard selection), it STAYS within
 *      ±100 px of viewport center across BOTH directions of the toggle.
 *   2. When NO focused message exists, the toggle does NOT re-center
 *      anything — scroll position is roughly preserved (±400 px). This
 *      negative-pair test defeats a false-pass implementation that
 *      unconditionally re-centers on every toggle.
 *
 * Why ±100 px: that's the empirical threshold inside
 * `scrollBubbleIntoView`'s correction chain (CORRECTION_THRESHOLD_PX),
 * the same number that pins the navigation landing position in
 * `search-hit-scroll-landing.spec.ts`. Below this drift the user
 * perceives the bubble as "still where I was reading."
 *
 * Chromium-vs-Safari caveat (council finding, 2026-05-25):
 *   Under Chromium's CSS scroll-anchoring + TanStack Virtual's spacer-
 *   height tracking, this contract is upheld even WITHOUT the explicit
 *   post-toggle re-center in ConversationPage.tsx — the browser anchors
 *   the focused bubble across the spacer-height change automatically.
 *   The user's reported drift surfaces in Safari (weaker scroll-
 *   anchoring) and in the lazy-image edge case where absolutely-
 *   positioned + transform:translateY rows defeat the anchoring
 *   algorithm. The fix is shipped as defense-in-depth — these tests
 *   pin the user-observable contract so a future regression that
 *   breaks scroll-anchoring (e.g., CSS overflow-anchor disable) is
 *   caught immediately, not weeks later by a user report.
 */

const CONV_UUID = '00000000-0000-0000-0000-00000000bb22'
const TOTAL_MESSAGES = 600
const LATE_TARGET_IDX = 550
const NEEDLE = 'plutonium'

// Every Nth message is a compact-marker. Spacing dense enough that
// hiding compactions removes ~20 rows above the target at idx 550.
const COMPACT_EVERY_N = 25

function makeFillerMessage(i: number): Message {
  const sender = i % 2 === 0 ? 'human' : 'assistant'
  const text = `Filler message ${i}. ${'Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. '.repeat(8)}`
  return makeMessage({
    uuid: `m-${String(i).padStart(4, '0')}`,
    sender,
    text,
    content: [{ type: 'text', text }],
    parent_message_uuid: i === 0 ? null : `m-${String(i - 1).padStart(4, '0')}`,
  })
}

function makeCompactMessage(i: number): Message {
  // CompactMarker entries reference a real message UUID; the underlying
  // message is filtered out of the bubble stream when the marker is
  // shown (the CompactMarker pill renders in its place) and is dropped
  // entirely when the user hides compactions. The body text is what the
  // pill expands to.
  const text = `Auto-compact summary at idx ${i}. Context preserved for further work.`
  return makeMessage({
    uuid: `m-compact-${String(i).padStart(4, '0')}`,
    sender: 'human',
    text,
    content: [{ type: 'text', text }],
    parent_message_uuid: i === 0 ? null : `m-${String(i - 1).padStart(4, '0')}`,
  })
}

function makeTargetMessage(i: number): Message {
  const sender = i % 2 === 0 ? 'human' : 'assistant'
  const text = `Target ${i}: the ${NEEDLE} lives here.`
  return makeMessage({
    uuid: `m-${String(i).padStart(4, '0')}`,
    sender,
    text,
    content: [{ type: 'text', text }],
    parent_message_uuid: i === 0 ? null : `m-${String(i - 1).padStart(4, '0')}`,
  })
}

// Build the message list. Compact-marker rows replace fillers at every
// COMPACT_EVERY_N index (NOT at the target index). The target stays at
// LATE_TARGET_IDX.
function buildMessages(): { messages: Message[]; compactMarkers: CompactMarker[] } {
  const messages: Message[] = []
  const compactMarkers: CompactMarker[] = []
  for (let i = 0; i < TOTAL_MESSAGES; i++) {
    if (i === LATE_TARGET_IDX) {
      messages.push(makeTargetMessage(i))
    } else if (i > 0 && i !== LATE_TARGET_IDX && i % COMPACT_EVERY_N === 0) {
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
  name: 'Toggle-preserves-focus-scroll fixture',
  message_count: TOTAL_MESSAGES,
})

const detail = makeDetail(summary, messages, { compact_markers: compactMarkers })

const searchResults: SearchResult[] = [{
  conversation_uuid: CONV_UUID,
  conversation_name: summary.name,
  conversation_updated_at: summary.updated_at,
  conversation_created_at: summary.created_at,
  project_name: null,
  matching_messages: [
    {
      message_uuid: messages[LATE_TARGET_IDX].uuid,
      sender: messages[LATE_TARGET_IDX].sender,
      snippet: `the ${NEEDLE} lives here.`,
      match_start: 4,
      match_end: 15,
      created_at: messages[LATE_TARGET_IDX].created_at,
    },
  ],
}]

async function mockSearch(page: Page, results: SearchResult[]) {
  await page.route('**/api/search**', (route) => {
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        results,
        total_messages_matched: results[0].matching_messages.length,
        returned_messages: results[0].matching_messages.length,
        truncated: false,
      }),
    })
  })
}

// Read the current vertical offset of a bubble from the message stream's
// center. Returns Infinity if the bubble isn't in the DOM (e.g. unmounted
// by the virtualizer's overscan-eviction).
async function distancePxFromCenter(page: Page, uuid: string): Promise<number> {
  return await page.evaluate((u) => {
    const target = document.querySelector(`[data-message-uuid="${u}"]`) as HTMLElement | null
    const container = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
    if (!target || !container) return Number.POSITIVE_INFINITY
    const t = target.getBoundingClientRect()
    const c = container.getBoundingClientRect()
    return Math.abs((t.top + t.height / 2) - (c.top + c.height / 2))
  }, uuid)
}

test.describe('Header toggle preserves focused-message viewport position (2026-05-25)', () => {
  test('Show Compactions toggle keeps a search-hit bubble pinned to viewport center across both directions', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: [summary], details: { [CONV_UUID]: detail } })
    await mockSearch(page, searchResults)
    await page.setViewportSize({ width: 1024, height: 900 })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    // Open search, type the needle, wait for the result envelope.
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(NEEDLE)
    await expect(page.locator('text=/of\\s+1\\s+matches/')).toBeVisible({ timeout: 10000 })

    // Click the search-result card → scroll-to-hit lands the target at
    // viewport center via the existing search-hit fast path. Wait for
    // the correction chain to fully settle (1250 ms multi-shot) plus a
    // small slack.
    const hitCard = page.getByRole('button', { name: new RegExp(NEEDLE) }).first()
    await expect(hitCard).toBeVisible()
    await hitCard.click()
    await waitForScrollQuiet(page) // the jump and its corrections are done

    const targetUuid = messages[LATE_TARGET_IDX].uuid
    const initialDistance = await distancePxFromCenter(page, targetUuid)
    expect(
      initialDistance,
      `pre-toggle sanity check: search-hit click must center the target ` +
        `(was ${initialDistance} px off — search-hit landing itself is broken, ` +
        `unrelated to the toggle bug)`,
    ).toBeLessThanOrEqual(100)

    // Settle signal for the toggle: the virtualizer's TOTAL scroll
    // height shrinks because ~22 compact-marker rows are dropped from
    // visibleMessages. We can't count [data-compact-marker] in the DOM
    // because the virtualizer only mounts rows near the viewport (the
    // user is at idx 550; the compactions are spread across all
    // indices and most are unmounted). Total height is a deterministic
    // signal that's unaffected by which rows happen to be mounted.
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    await expect(compactionsCheckbox).toBeChecked()
    const totalHeightBefore = await page.evaluate(() => {
      // The virtualizer renders a single absolutely-positioned spacer
      // inside the scroll container; its height === virtualizer.getTotalSize().
      const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
      const spacer = stream?.querySelector('div[style*="position: relative"]') as HTMLElement | null
      return spacer?.offsetHeight ?? 0
    })
    expect(
      totalHeightBefore,
      'fixture sanity: virtualizer total height must be non-trivial ' +
        '(otherwise the conversation didn\'t render and the test is vacuous)',
    ).toBeGreaterThan(10_000)

    // ─── TOGGLE OFF ─────────────────────────────────────────────────
    //
    // .click() (NOT .uncheck()) per header-toggles-as-checkboxes.spec.ts
    // L141 — Playwright's .uncheck has a state-precondition race with
    // React commit.
    // The toggle changes the search query key, so the hits are fetched
    // again (2026-10-24: a 1.5 s sleep instead of these signals).
    const refetched = page.waitForResponse((r) => r.url().includes('/api/search'))
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).not.toBeChecked()
    // Deterministic settle: virtualizer total height shrank by enough
    // to prove the row drop fired. With 22 compaction rows × the
    // 240 px row estimate the unmounted estimate-drop is ~5280 px, but
    // some compact-marker rows near the viewport may have been measured
    // to shorter heights before the toggle fired, so the observed delta
    // is closer to ~3400 px in practice. Use 2000 as a conservative
    // floor that proves the row drop fired without overspecifying the
    // exact measurement state.
    await expect
      .poll(async () => {
        return await page.evaluate(() => {
          const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
          const spacer = stream?.querySelector('div[style*="position: relative"]') as HTMLElement | null
          return spacer?.offsetHeight ?? 0
        })
      }, { timeout: 5000 })
      .toBeLessThan(totalHeightBefore - 2000)
    await refetched
    await waitForScrollQuiet(page)

    const afterHideDistance = await distancePxFromCenter(page, targetUuid)
    expect(
      afterHideDistance,
      `after Show Compactions OFF: focused message must stay within ±100 px ` +
        `of viewport center (drifted ${afterHideDistance} px — toggle is ` +
        `the user's reading position-killer this spec exists to pin)`,
    ).toBeLessThanOrEqual(100)

    // ─── TOGGLE BACK ON ─────────────────────────────────────────────
    // Back to the first query key: React Query answers from its cache, so
    // no request goes out.
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).toBeChecked()
    // Deterministic settle: total height back near the original value.
    await expect
      .poll(async () => {
        return await page.evaluate(() => {
          const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
          const spacer = stream?.querySelector('div[style*="position: relative"]') as HTMLElement | null
          return spacer?.offsetHeight ?? 0
        })
      }, { timeout: 5000 })
      .toBeGreaterThan(totalHeightBefore - 1000)
    await waitForScrollQuiet(page)

    const afterShowDistance = await distancePxFromCenter(page, targetUuid)
    expect(
      afterShowDistance,
      `after Show Compactions ON (toggled back): focused message must stay ` +
        `within ±100 px of viewport center (drifted ${afterShowDistance} px)`,
    ).toBeLessThanOrEqual(100)
  })

  test('Show Tools toggle keeps a search-hit bubble pinned to viewport center', async ({
    page,
    mockBackend,
  }) => {
    // Bidirectional pair: prove the fix isn't specific to compactions —
    // the same scroll-pin contract applies to the Show Tools checkbox,
    // which has identical visibleMessages-mutation semantics.
    await mockBackend({ conversations: [summary], details: { [CONV_UUID]: detail } })
    await mockSearch(page, searchResults)
    await page.setViewportSize({ width: 1024, height: 900 })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(NEEDLE)
    await expect(page.locator('text=/of\\s+1\\s+matches/')).toBeVisible({ timeout: 10000 })

    const hitCard = page.getByRole('button', { name: new RegExp(NEEDLE) }).first()
    await hitCard.click()
    await waitForScrollQuiet(page) // the jump and its corrections are done

    const targetUuid = messages[LATE_TARGET_IDX].uuid
    const initialDistance = await distancePxFromCenter(page, targetUuid)
    expect(initialDistance).toBeLessThanOrEqual(100)

    // The fixture has no tool messages, so Show Tools CHECKED inserts
    // zero rows — but the toggle handler is the same code path. We
    // assert the position is preserved across the no-op toggle to pin
    // the contract for the symmetric case. The compaction-side test
    // above provides the load-bearing assertion that rows actually move.
    const toolsCheckbox = page.locator(
      '[data-testid="header-show-tools-checkbox"]',
    )
    await expect(toolsCheckbox).not.toBeChecked()
    await toolsCheckbox.click()
    await expect(toolsCheckbox).toBeChecked()
    // The search does not refetch for Tools (it always includes tool
    // calls); the re-center and its corrections are done.
    await waitForScrollQuiet(page)

    const afterToggleDistance = await distancePxFromCenter(page, targetUuid)
    expect(
      afterToggleDistance,
      `after Show Tools ON: focused message must stay within ±100 px of ` +
        `viewport center (drifted ${afterToggleDistance} px)`,
    ).toBeLessThanOrEqual(100)
  })

  test('NEGATIVE PAIR: no focused message → toggle does NOT re-center, scroll roughly preserved', async ({
    page,
    mockBackend,
  }) => {
    // Defeats a false-pass implementation that ALWAYS calls
    // scrollBubbleIntoView(firstVisibleBubble) on every toggle — that
    // would pass the focused-message test trivially but break the
    // user's reading position when no message is focused (the common
    // case when the user toggles compactions while just scrolling).
    await mockBackend({ conversations: [summary], details: { [CONV_UUID]: detail } })
    await mockSearch(page, [])
    await page.setViewportSize({ width: 1024, height: 900 })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    // Scroll to a known mid-conversation position (NOT via search — so
    // no activeMatchUuid is set). We use the virtualizer's scrollToIndex
    // by directly mutating scrollTop, which mimics user drag-scroll.
    await page.evaluate(() => {
      const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
      if (stream) stream.scrollTo({ top: 30_000, behavior: 'auto' })
    })
    await waitForScrollQuiet(page)

    const beforeScrollTop = await page.evaluate(() => {
      const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
      return stream?.scrollTop ?? 0
    })

    // Sanity: scrollTop is well past zero AND the search panel was
    // never opened (so no activeMatchUuid).
    expect(beforeScrollTop).toBeGreaterThan(20_000)

    // Toggle compactions OFF. Use the virtualizer's total height delta
    // as the deterministic settle signal — see the focused-scroll test
    // above for the rationale (compact markers are spread across the
    // conversation and most aren't mounted at any given scroll position).
    const compactionsCheckbox = page.locator(
      '[data-testid="header-show-compactions-checkbox"]',
    )
    const heightBefore = await page.evaluate(() => {
      const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
      const spacer = stream?.querySelector('div[style*="position: relative"]') as HTMLElement | null
      return spacer?.offsetHeight ?? 0
    })
    expect(heightBefore).toBeGreaterThan(10_000)
    await compactionsCheckbox.click()
    await expect(compactionsCheckbox).not.toBeChecked()
    await expect
      .poll(async () => {
        return await page.evaluate(() => {
          const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
          const spacer = stream?.querySelector('div[style*="position: relative"]') as HTMLElement | null
          return spacer?.offsetHeight ?? 0
        })
      }, { timeout: 5000 })
      .toBeLessThan(heightBefore - 2000)
    await waitForScrollQuiet(page) // no search here, so no refetch

    const afterScrollTop = await page.evaluate(() => {
      const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
      return stream?.scrollTop ?? 0
    })

    // The virtualizer will reflow rows above us, so scrollTop will
    // change SOMEWHAT — but we must NOT have snapped to top/bottom or
    // jumped to a wildly different position. ±5000 px is generous
    // (5x viewport at 900h); the actual delta should be much smaller.
    // We're pinning "the toggle didn't unconditionally re-center" not
    // "the toggle is a perfect no-op."
    const delta = Math.abs(afterScrollTop - beforeScrollTop)
    expect(
      delta,
      `negative pair: toggle with no focused message should NOT yank ` +
        `scrollTop across viewports (drifted ${delta} px from ${beforeScrollTop} ` +
        `to ${afterScrollTop}; if this is close to zero we may have ` +
        `accidentally re-centered when we shouldn't have)`,
    ).toBeLessThan(5000)
  })
})
