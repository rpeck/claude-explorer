import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry } from './fixtures'
import type { SearchResult, Message } from '../src/lib/types'

/**
 * Search-hit scroll LANDING POSITION pin (2026-05-20, 3-model LLM
 * council G').
 *
 * Sibling to `search-hit-scroll.spec.ts`. That spec pins the
 * navigation PATH (fast-path vs URL fallback). This spec pins the
 * FINAL VIEWPORT POSITION of the target bubble — the gap the
 * previous council's smoke missed.
 *
 * Bug:
 *   `scrollIntoView({behavior:'smooth', block:'center'})` aims at the
 *   target's `offsetTop` at call time and animates ~500ms. During the
 *   animation, lazy-loaded images (loading="lazy", no intrinsic
 *   dimensions) above the target enter the swept region and decode,
 *   growing the document by thousands of pixels. The animation
 *   doesn't re-aim. Target lands far below viewport center.
 *
 * Fix:
 *   `src/lib/scrollBubbleIntoView.ts` — distance-gated: long hops
 *   (>1.5 viewports) use `behavior: 'auto'` to skip the swept region
 *   entirely; short hops keep `behavior: 'smooth'`. Both followed by a
 *   bounded 250ms post-settle correction.
 *
 * This spec exists because the previous spec's
 * `scrollTop > 1000` assertion was a rubber-stamp: it proved a
 * scroll happened, not that it landed in the right place. The bug
 * shipped through that gap.
 */

const CONV_UUID = '00000000-0000-0000-0000-00000000aa11'
// 600 messages × ~200-300px each (multi-paragraph filler) → ~150K px
// stream height. Target at idx 550 lands ~135K px below the start —
// guaranteed far below initial viewport (900px) even with the most
// aggressive deferred render. Same fixture shape as the sibling
// search-hit-scroll.spec.ts which already pins the navigation PATH.
const TOTAL_MESSAGES = 600
const LATE_TARGET_IDX = 550
const NEEDLE = 'unobtainium'

function makeFillerMessage(i: number): Message {
  const sender = i % 2 === 0 ? 'human' : 'assistant'
  // Filler bodies designed to give each bubble enough rendered height
  // (~200-300px) that the target at idx 550 lands far below the
  // initial viewport. ~20 sentences ≈ 1200 chars → 3-4 wrapped lines
  // per bubble at 1024px viewport width.
  const text = `Filler message ${i}. ${'Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. '.repeat(8)}`
  return makeMessage({
    uuid: `m-${String(i).padStart(4, '0')}`,
    sender,
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

const summary = makeSummary({
  uuid: CONV_UUID,
  name: 'Search hit landing-position pin (800 msgs)',
  message_count: TOTAL_MESSAGES,
})

const messages: Message[] = Array.from({ length: TOTAL_MESSAGES }, (_, i) =>
  i === LATE_TARGET_IDX ? makeTargetMessage(i) : makeFillerMessage(i),
)

const detail = makeDetail(summary, messages)

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

/**
 * Wait until no scroll correction is pending (`data-scroll-settling`,
 * set by `scrollBubbleIntoView`). A fixed delay raced the correction
 * chain on slow CI runners (2026-10-22).
 */
async function waitForScrollSettled(page: Page) {
  await expect(page.getByTestId('message-stream')).not.toHaveAttribute('data-scroll-settling', {
    timeout: 10000,
  })
}

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

test.describe('Search — clicking a hit LANDS the target bubble at viewport center', () => {
  // Rewritten 2026-05-25 for virtualization compatibility. The original
  // (2026-05-20) injected a phantom div as a SIBLING of the virtualizer's
  // spacer, which pushed bubbles out of viewport but didn't perturb the
  // virtualizer's coordinate space — the test became vacuous.
  //
  // The fix below grows an already-mounted bubble row ABOVE the target.
  // That's exactly what a lazy-image decode looks like to the virtualizer
  // in production: a bubble's content gets taller AFTER scroll-end,
  // ResizeObserver fires, `virtualizer.measureElement` updates the
  // row-height cache, the spacer height grows, sibling rows reposition,
  // and the target drifts below center. The
  // `scrollBubbleIntoView` post-settle correction chain (250/750/1250 ms)
  // must detect the drift and re-center.
  //
  // Two-step assertion makes the contract un-bypassable:
  //   1. After phantom injection AND before correction window closes,
  //      target IS visibly drifted (proves the injection actually
  //      perturbed the virtualizer — not a vacuous no-op).
  //   2. After the 1250 ms correction chain completes, target is back
  //      within ±100 px of viewport center.
  //
  // If step 1 fails the injection is silently broken (the §5.13 trap
  // the original test fell into). If step 2 fails the correction
  // is broken. Together they pin the contract.
  test('long-distance hit lands at center even when a row above grows mid-scroll (post-virt phantom-inside-row)', async ({
    page,
    mockBackend,
  }) => {
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
    await expect(hitCard).toBeVisible()
    const lateTarget = page.locator(`[data-message-uuid="${messages[LATE_TARGET_IDX].uuid}"]`)

    // The auto-promote scroll must finish before the reset below: on
    // slow CI runners a pending scroll moved back to the target after
    // the reset (2026-10-22), so the click had nothing to scroll and no
    // scrollend fired. The ring proves that scrollBubbleIntoView ran
    // (both navigation paths add it in the same task); then wait for
    // its correction chain to end.
    await expect(lateTarget).toHaveClass(/ring-yellow-400/, { timeout: 10000 })
    await waitForScrollSettled(page)

    // SearchPanel auto-promotes on type and already scrolled to the
    // target. Reset to top so the click path has real work to do.
    await page.evaluate(() => {
      const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
      if (stream) stream.scrollTo({ top: 0, behavior: 'auto' })
    })
    await expect(lateTarget).not.toBeInViewport()

    // ─── PHANTOM-INSIDE-ROW INJECTION ──────────────────────────────
    //
    // Schedule injection on `scrollend`. The injection picks a
    // currently-mounted bubble row whose data-index is LESS than the
    // target's index (the virtualizer mounts an overscan-window around
    // the scroll target — so after click-scroll to idx 550, rows
    // ~545-555 are mounted; we grow one of them above 550). Growing
    // the row triggers ResizeObserver → measureElement → spacer
    // height grows → siblings reposition → target drifts down.
    //
    // The injection scheduler is installed BEFORE the click so the
    // scrollend listener is in place.
    await page.evaluate(({ targetUuid, targetIdx }) => {
      const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
      if (!stream) return

      let injected = false
      const inject = () => {
        if (injected) return
        // Find any mounted bubble row whose index is BELOW the target's
        // and is the actual bubble content (not the wrapper). Each row
        // wrapper has `data-index="<N>"`; the bubble inside carries
        // `data-message-uuid`. Grow the BUBBLE so the wrapper's
        // ResizeObserver sees a height delta.
        const wrappers = Array.from(
          stream.querySelectorAll<HTMLElement>('[data-index]'),
        )
        let chosen: HTMLElement | null = null
        for (const w of wrappers) {
          const idx = Number(w.getAttribute('data-index'))
          if (idx < targetIdx) {
            const bubbleUuid = w
              .querySelector<HTMLElement>('[data-message-uuid]')
              ?.getAttribute('data-message-uuid')
            if (bubbleUuid && bubbleUuid !== targetUuid) {
              chosen = w
              break
            }
          }
        }
        if (!chosen) return
        injected = true
        const phantom = document.createElement('div')
        phantom.style.height = '4000px'
        phantom.style.background = 'transparent'
        phantom.setAttribute('data-test-phantom', 'mid-scroll-row-growth')
        // Append INSIDE the wrapper. measureElement observes the
        // wrapper's bounding-rect; appending here grows the row's
        // measured height — mirrors a lazy-image decode inside the
        // bubble in production.
        chosen.appendChild(phantom)
        ;(window as unknown as { __heightAtInjection?: number }).__heightAtInjection = stream.scrollHeight
        ;(window as unknown as { __phantomInjectedAt?: number }).__phantomInjectedAt = performance.now()
      }
      // Fire 80 ms after scrollend — well inside the 250 ms first
      // correction tick, but after the smooth-scroll animation has
      // finished settling so the drift it causes is the dominant
      // source of position error.
      stream.addEventListener('scrollend', () => {
        setTimeout(inject, 80)
      }, { once: true })
    }, { targetUuid: messages[LATE_TARGET_IDX].uuid, targetIdx: LATE_TARGET_IDX })

    await hitCard.click()

    // ─── ASSERTION 1: injection actually perturbed the virtualizer ──
    //
    // 350 ms after click: scroll has finished + injection has fired
    // (scrollend + 80 ms) + virtualizer's ResizeObserver has had time
    // to recompute. The first correction tick at 250 ms may have
    // already fired but the chain runs at 250/750/1250 cumulative —
    // 350 ms is BEFORE the second tick. The drift should be visible.
    //
    // If this assert fails: the injection is a no-op against the
    // virtualizer's coordinate space; the rest of the test would be
    // vacuous (the §5.13 trap). Loud failure is the right outcome.
    await page.waitForFunction(
      () => (window as unknown as { __phantomInjectedAt?: number }).__phantomInjectedAt !== undefined,
      undefined,
      { timeout: 5000 },
    )
    // Poll the condition itself (2026-10-24: a 50 ms sleep, then one look).
    await expect.poll(() => page.evaluate(() => {
      const phantom = document.querySelector('[data-test-phantom="mid-scroll-row-growth"]')
      return phantom !== null && (phantom as HTMLElement).offsetHeight === 4000
    }), {
      message:
      'phantom injection no-op: the 4000 px phantom is not present in the DOM, ' +
        'so the rest of the test would be vacuous. Likely the virtualizer mounted ' +
        'no rows below the target or the data-index discovery failed.',
    }).toBe(true)

    // ─── ASSERTION 2: the target ends within ±100 px of center ─────
    //
    // Rows are absolutely positioned, so the grown row moves the target
    // only after the virtualizer measures it again: wait for that (the
    // stream grows by the 4000 px phantom). Then the target must come
    // back to the center. Both waits check page state, not a fixed delay:
    // the correction chain and the virtualizer run late on slow runners.
    await page.waitForFunction(
      () => {
        const w = window as unknown as { __heightAtInjection?: number }
        const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
        return !!stream && w.__heightAtInjection !== undefined && stream.scrollHeight >= w.__heightAtInjection + 3000
      },
      undefined,
      { timeout: 10000 },
    )
    const landingDistancePx = () =>
      page.evaluate((uuid) => {
        const target = document.querySelector(`[data-message-uuid="${uuid}"]`) as HTMLElement | null
        const container = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
        if (!target || !container) return Number.POSITIVE_INFINITY
        const t = target.getBoundingClientRect()
        const c = container.getBoundingClientRect()
        return Math.abs((t.top + t.height / 2) - (c.top + c.height / 2))
      }, messages[LATE_TARGET_IDX].uuid)

    await expect
      .poll(landingDistancePx, {
        timeout: 10000,
        message:
          'target bubble must land within ±100 px of container center after the ' +
          'post-settle correction chain absorbs the mid-scroll row-growth',
      })
      .toBeLessThanOrEqual(100)

    await expect(lateTarget).toBeInViewport()
  })

  test('rapid Cmd+G (supersession): newer scroll cancels older correction', async ({
    page,
    mockBackend,
  }) => {
    // Two hits, late and early. Click late, then immediately click early
    // before the late's 250ms correction window closes. Late's correction
    // MUST NOT fire (would yank user back to late). Early must end up
    // centered.
    const earlyIdx = 50
    const twoHitMessages: Message[] = Array.from({ length: TOTAL_MESSAGES }, (_, i) => {
      if (i === earlyIdx) return makeTargetMessage(i)
      if (i === LATE_TARGET_IDX) return makeTargetMessage(i)
      return makeFillerMessage(i)
    })
    const twoHitDetail = makeDetail(summary, twoHitMessages)
    const twoHitResults: SearchResult[] = [{
      conversation_uuid: CONV_UUID,
      conversation_name: summary.name,
      conversation_updated_at: summary.updated_at,
      conversation_created_at: summary.created_at,
      project_name: null,
      matching_messages: [
        {
          message_uuid: twoHitMessages[earlyIdx].uuid,
          sender: twoHitMessages[earlyIdx].sender,
          snippet: `the ${NEEDLE} lives here.`,
          match_start: 4,
          match_end: 15,
          created_at: twoHitMessages[earlyIdx].created_at,
        },
        {
          message_uuid: twoHitMessages[LATE_TARGET_IDX].uuid,
          sender: twoHitMessages[LATE_TARGET_IDX].sender,
          snippet: `the ${NEEDLE} lives here.`,
          match_start: 4,
          match_end: 15,
          created_at: twoHitMessages[LATE_TARGET_IDX].created_at,
        },
      ],
    }]

    await mockBackend({ conversations: [summary], details: { [CONV_UUID]: twoHitDetail } })
    await mockSearch(page, twoHitResults)
    await page.setViewportSize({ width: 1024, height: 900 })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(NEEDLE)
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({ timeout: 10000 })

    // The auto-promote scroll must finish before the reset below: on
    // slow CI runners a pending scroll moved back to the target after
    // the reset (2026-10-22), so the click had nothing to scroll and no
    // scrollend fired. The ring proves that scrollBubbleIntoView ran
    // (both navigation paths add it in the same task); then wait for
    // its correction chain to end.
    await expect(page.locator(`[data-message-uuid="${twoHitMessages[earlyIdx].uuid}"]`)).toHaveClass(/ring-yellow-400/, { timeout: 10000 })
    await waitForScrollSettled(page)

    // Auto-navigate already moved us to the first hit. Reset to top so
    // the click path on the LATE hit has real distance to scroll.
    await page.evaluate(() => {
      const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
      if (stream) stream.scrollTo({ top: 0, behavior: 'auto' })
    })

    // Click late hit, then early hit ~50ms later (well before the
    // 250ms correction window closes).
    const hits = page.getByRole('button', { name: new RegExp(NEEDLE) })
    await hits.nth(1).click()  // late
    // The late jump's correction chain is pending: the case under test.
    // (2026-10-24: a 50 ms gap, which could also land after the window.)
    await expect(page.getByTestId('message-stream')).toHaveAttribute('data-scroll-settling', '')
    await hits.nth(0).click()  // early — supersedes late's pending correction

    // EARLY target must end within ±100px of center (no stale-correction
    // yank). Poll the page state instead of sampling after a fixed delay.
    await expect
      .poll(
        () =>
          page.evaluate((uuid) => {
            const target = document.querySelector(`[data-message-uuid="${uuid}"]`) as HTMLElement | null
            const container = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
            if (!target || !container) return Number.POSITIVE_INFINITY
            const t = target.getBoundingClientRect()
            const c = container.getBoundingClientRect()
            return Math.abs((t.top + t.height / 2) - (c.top + c.height / 2))
          }, twoHitMessages[earlyIdx].uuid),
        { timeout: 10000, message: 'early target must land within ±100px of center (no stale-correction yank)' },
      )
      .toBeLessThanOrEqual(100)
  })

  test('the newest jump wins on a slow machine (two hits 50 ms apart, 4x CPU)', async ({
    page,
    mockBackend,
  }) => {
    // 2026-10-23: the late click took the URL path (?highlight=late); the
    // early click 50 ms later took the fast path, which does not touch the
    // URL. On a slow machine, useScrollToHighlight processed ?highlight=late
    // after that and scrolled back to the older hit: 24 of 30 runs at 4x
    // CPU. Each jump now starts a search navigation, and a ?highlight= from
    // an older one is dropped (lib/searchNavigation.ts).
    const earlyIdx = 50
    const twoHitMessages: Message[] = Array.from({ length: TOTAL_MESSAGES }, (_, i) =>
      i === earlyIdx || i === LATE_TARGET_IDX ? makeTargetMessage(i) : makeFillerMessage(i),
    )
    const hit = (i: number) => ({
      message_uuid: twoHitMessages[i].uuid,
      sender: twoHitMessages[i].sender,
      snippet: `the ${NEEDLE} lives here.`,
      match_start: 4,
      match_end: 15,
      created_at: twoHitMessages[i].created_at,
    })
    await mockBackend({ conversations: [summary], details: { [CONV_UUID]: makeDetail(summary, twoHitMessages) } })
    await mockSearch(page, [{
      conversation_uuid: CONV_UUID,
      conversation_name: summary.name,
      conversation_updated_at: summary.updated_at,
      conversation_created_at: summary.created_at,
      project_name: null,
      matching_messages: [hit(earlyIdx), hit(LATE_TARGET_IDX)],
    }])
    await page.setViewportSize({ width: 1024, height: 900 })
    await (await page.context().newCDPSession(page)).send('Emulation.setCPUThrottlingRate', { rate: 4 })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()
    await page.keyboard.press('Meta+k')
    await page.locator('input[placeholder="Search messages..."]').fill(NEEDLE)
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({ timeout: 20000 })
    const early = page.locator(`[data-message-uuid="${twoHitMessages[earlyIdx].uuid}"]`)
    await expect(early).toHaveClass(/ring-yellow-400/, { timeout: 20000 })
    await waitForScrollSettled(page)
    await page.evaluate(() => {
      const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
      if (stream) stream.scrollTo({ top: 0, behavior: 'auto' })
    })

    const hits = page.getByRole('button', { name: new RegExp(NEEDLE) })
    await hits.nth(1).click() // late: URL path
    // The late jump is on its way: its ?highlight= is in the URL (2026-10-24:
    // a 50 ms gap instead of this state).
    await expect(page).toHaveURL(/highlight=/)
    await hits.nth(0).click() // early: the newer jump

    // End state: no jump is pending. Either the URL highlight was used and
    // cleared (after 2 s), or it was dropped as stale; and no correction
    // runs.
    await expect
      .poll(() => new URL(page.url()).searchParams.get('highlight'), { timeout: 20000 })
      .toBeNull()
    await waitForScrollSettled(page)
    await expect(early, 'the older, slower jump moved the view back to its hit').toBeInViewport()
  })
})
