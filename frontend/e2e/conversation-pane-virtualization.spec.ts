import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'
import type { Message } from '../src/lib/types'

/**
 * Spec-driven user-observable pin for ConversationPage virtualization
 * (2026-05-23) — implements the primary budget guard for the
 * 4051-bubble warm-switch regression documented in
 * `PLANS/PERFORMANCE_BASELINE_2026-05-23.md`.
 *
 * Contract:
 *
 *   1. With N visible messages where N is large (we use N=600 below),
 *      the rendered `[data-message-uuid]` count in the DOM MUST be
 *      substantially smaller than N. The pre-fix shape rendered all N;
 *      a windowed render mounts only the visible portion plus a small
 *      overscan band (~10 rows).
 *
 *   2. Scrolling far down the stream MUST mount messages from near the
 *      end of the dataset AND unmount messages from the beginning. This
 *      is the load-bearing virtualization signal: if every row stayed
 *      in the DOM the first message would still be visible after
 *      scrolling.
 *
 *   3. A row at the tail of the dataset MUST NOT be in the DOM on
 *      initial page load. Pre-fix this WAS in the DOM (the whole list
 *      was rendered eagerly).
 *
 * Why spec-driven (per TESTING.md §1): the implementation uses
 * `@tanstack/react-virtual` (same lib as the sidebar; see
 * `frontend/e2e/spec-conversation-list-virtualized.spec.ts` for the
 * sibling sidebar spec). If a future swap goes to react-virtuoso or
 * back to eager rendering, only the test invariants matter — the
 * library is an implementation detail. The contract is "do not put N
 * rows in the DOM when N is large."
 */

const CONV_UUID = 'cccccccc-cccc-cccc-cccc-000000000001'
const TOTAL_MESSAGES = 600
// A unique needle text we can locate at the tail of the dataset
// without depending on incidental copy of other messages.
const TAIL_NEEDLE = 'unique_virtualization_needle_late_in_dataset'
// Place the needle within the LAST visible window after scroll-to-bottom.
// Virtualizer mounts ~5-10 trailing rows + overscan; idx 597 of 600 keeps
// the needle robustly inside that final window across viewport sizes.
const TAIL_IDX = TOTAL_MESSAGES - 3

function makeFillerMessage(i: number): Message {
  const sender = i % 2 === 0 ? 'human' : 'assistant'
  // Filler bodies long enough that no two messages collapse to the
  // same vertical position (forces the virtualizer to honor variable
  // heights). ~6 sentences ≈ 600-900 chars per bubble at 1024px width.
  const text = `Filler message ${i}. ${'Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. '.repeat(6)}`
  return makeMessage({
    uuid: `vmsg-${String(i).padStart(4, '0')}`,
    sender,
    text,
    content: [{ type: 'text', text }],
    parent_message_uuid: i === 0 ? null : `vmsg-${String(i - 1).padStart(4, '0')}`,
  })
}

function makeTailNeedleMessage(i: number): Message {
  return makeMessage({
    uuid: `vmsg-${String(i).padStart(4, '0')}`,
    sender: 'assistant',
    text: `Tail message ${i}: ${TAIL_NEEDLE} lives here.`,
    content: [{ type: 'text', text: `Tail message ${i}: ${TAIL_NEEDLE} lives here.` }],
    parent_message_uuid: i === 0 ? null : `vmsg-${String(i - 1).padStart(4, '0')}`,
  })
}

const summary = makeSummary({
  uuid: CONV_UUID,
  name: 'Virtualization budget pin (600 msgs)',
  message_count: TOTAL_MESSAGES,
})

const messages: Message[] = Array.from({ length: TOTAL_MESSAGES }, (_, i) =>
  i === TAIL_IDX ? makeTailNeedleMessage(i) : makeFillerMessage(i),
)

const detail = makeDetail(summary, messages)

test.describe('ConversationPage — bubble list is virtualized (user-observable budget)', () => {
  test.beforeEach(async ({ mockBackend, page }) => {
    await mockBackend({ conversations: [summary], details: { [CONV_UUID]: detail } })
    await page.setViewportSize({ width: 1280, height: 900 })
  })

  test('renders FAR fewer DOM rows than the dataset size', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    // Wait for the first message to appear so we know mount finished.
    await expect(page.locator('[data-message-uuid="vmsg-0000"]')).toBeVisible({ timeout: 15000 })

    // Generous ceiling so overscan tuning has headroom. Pre-fix this
    // would be 600. With virtualization at viewport 900px and
    // ~200-300px per bubble, expect roughly 5-10 visible + ~5 overscan.
    const renderedCount = await page
      .locator('[data-testid="message-stream"] [data-message-uuid]')
      .count()
    expect(
      renderedCount,
      `expected ≪${TOTAL_MESSAGES} bubbles in the DOM, got ${renderedCount} — ` +
        `if this is close to ${TOTAL_MESSAGES} the virtualizer is not active`,
    ).toBeGreaterThan(0)
    expect(renderedCount).toBeLessThan(50)
  })

  test('tail-end message is NOT in the DOM until scrolled to', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="vmsg-0000"]')).toBeVisible({ timeout: 15000 })

    // The needle is at idx 590 of 600 — far below the initial viewport
    // even at maximum overscan. THIS is the load-bearing assertion that
    // virtualization is doing real work: a non-virtualized list would
    // have every row mounted from the start.
    await expect(
      page.locator('[data-testid="message-stream"]').getByText(TAIL_NEEDLE),
    ).toHaveCount(0)
  })

  test('scroll-to-bottom mounts tail messages AND unmounts head messages', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="vmsg-0000"]')).toBeVisible({ timeout: 15000 })

    // Variable-height virtualizers correct their total scroll height as
    // rows mount and measure. A single `scrollTop = scrollHeight` snaps
    // to the CURRENT estimated total — which may grow as new rows come
    // into the viewport and measureElement reports their real height.
    // So scroll-to-bottom in a tight loop until we settle at the bottom
    // (scrollHeight stops growing AND scrollTop is pinned to bottom).
    await page.evaluate(async () => {
      const STREAM = '[data-testid="message-stream"]'
      const stream = document.querySelector(STREAM) as HTMLElement | null
      if (!stream) return
      let lastHeight = -1
      for (let i = 0; i < 60; i++) {
        stream.scrollTop = stream.scrollHeight
        await new Promise((r) => setTimeout(r, 100))
        const atBottom =
          stream.scrollTop + stream.clientHeight >= stream.scrollHeight - 2
        if (stream.scrollHeight === lastHeight && atBottom) return
        lastHeight = stream.scrollHeight
      }
    })

    // After scroll: the FIRST message must be unmounted (this is the
    // load-bearing virtualization signal). A non-virtualized list would
    // leave it in the DOM even though it's off-screen.
    await expect(page.locator('[data-message-uuid="vmsg-0000"]')).toHaveCount(0, { timeout: 5000 })

    // AND a tail-end message must now be visible.
    await expect(
      page.locator('[data-testid="message-stream"]').getByText(TAIL_NEEDLE),
    ).toBeVisible({ timeout: 5000 })
  })
})
