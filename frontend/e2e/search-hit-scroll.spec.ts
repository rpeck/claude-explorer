import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry, waitForScrollQuiet, recordHistory, historyRecord } from './fixtures'
import type { SearchResult, Message } from '../src/lib/types'

/**
 * Bug pin (2026-05-20): clicking a search-result snippet card on a
 * large conversation silently failed. The fast path in
 * `frontend/src/components/search/navigateToMatch.ts` ran
 * `document.querySelector('[data-message-uuid=...]')` synchronously
 * immediately after queueing React state updates; on a 15K-message
 * conversation the bubble wasn't yet committed to the DOM at that
 * synchronous moment (React 19 concurrent render starvation), so
 * `querySelector` returned `null` AND the fast path silently
 * `return`ed without falling through to the URL-based navigate branch
 * that would have worked. Result: user clicks a hit, page does
 * nothing.
 *
 * Council A1 fix (3-persona unanimous after a re-vote): keep the
 * fast-path scrollIntoView for the common case (bubble already
 * mounted) — preserves the "no URL pollution" property AND the
 * pinned-by-other-tests "focus stays on the search input after Cmd+G"
 * behavior — and fall through to `navigate('?highlight=<uuid>')` when
 * the bubble isn't in the DOM, so `ConversationPage`'s highlight
 * effect (which has a 100 ms settle + its own querySelector) takes
 * over.
 *
 * This spec pins the contract bidirectionally:
 *
 *   POSITIVE (Test 1, fast-path lives): bubble IS in DOM at click
 *     time → click scrolls the bubble into view AND the URL stays at
 *     `/conversations/<uuid>` (no `?highlight=` flicker).
 *
 *   POSITIVE (Test 2, THE BUG PIN — RED on pre-fix code): bubble is
 *     NOT in DOM at click time → click navigates to
 *     `?highlight=<uuid>` and the highlight effect lands the bubble
 *     in view. Pre-fix, this was a silent no-op.
 *
 * Bubble-not-in-DOM simulation: we transiently strip
 * `data-message-uuid` from the target via `queueMicrotask` right
 * before the click. The click handler's synchronous querySelector
 * sees the missing attribute; the 100 ms later highlight effect
 * sees it restored. This is a deterministic, in-test-only proxy for
 * the React 19 concurrent-render race that produced the original
 * user-reported bug. Without this simulation, all 600 bubbles are in
 * DOM and the fast path always succeeds — the bug wouldn't reproduce
 * and the test would falsely pass on pre-fix code.
 */

const CONV_UUID = '00000000-0000-0000-0000-00000000ee15'
const TOTAL_MESSAGES = 600
// Hit #1 — early in the stream, naturally visible without scrolling.
// Tests that the fast path still scrolls/highlights cleanly when the
// bubble is mounted (the 99% case).
const EARLY_TARGET_IDX = 2
// Hit #2 — deep in the stream, well below the initial viewport.
// Combined with the bubble-attribute strip below, this is the
// "fast-path fails → fall through to URL" test.
const LATE_TARGET_IDX = 550

const NEEDLE = 'aardvark'

function makeFillerMessage(i: number): Message {
  // Alternate sender so the conversation looks plausibly real. Pad
  // text body with enough content that bubble heights vary (mimicking
  // real conversations) but no message contains the needle except the
  // two intentional targets below.
  const sender = i % 2 === 0 ? 'human' : 'assistant'
  const text = `Filler message ${i}. Lorem ipsum dolor sit amet, consectetur adipiscing elit.`
  return makeMessage({
    uuid: `m-${String(i).padStart(4, '0')}`,
    sender,
    text,
    content: [{ type: 'text', text }],
    parent_message_uuid: i === 0 ? null : `m-${String(i - 1).padStart(4, '0')}`,
  })
}

function makeTargetMessage(i: number, marker: string): Message {
  const sender = i % 2 === 0 ? 'human' : 'assistant'
  const text = `Target message ${i} — the ${NEEDLE} is here. Marker: ${marker}.`
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
  name: 'Search scroll bug pin (600 messages, two targets)',
  message_count: TOTAL_MESSAGES,
})

const messages: Message[] = Array.from({ length: TOTAL_MESSAGES }, (_, i) => {
  if (i === EARLY_TARGET_IDX) return makeTargetMessage(i, 'early')
  if (i === LATE_TARGET_IDX) return makeTargetMessage(i, 'late')
  return makeFillerMessage(i)
})

const detail = makeDetail(summary, messages)

// Two SearchResult rows so the sidebar renders TWO distinguishable
// cards (early-target with marker 'early', late-target with marker
// 'late'). The router responds with both rows so the user can click
// either.
const searchResults: SearchResult[] = [{
  conversation_uuid: CONV_UUID,
  conversation_name: summary.name,
  conversation_updated_at: summary.updated_at,
  conversation_created_at: summary.created_at,
  project_name: null,
  matching_messages: [
    {
      message_uuid: messages[EARLY_TARGET_IDX].uuid,
      sender: messages[EARLY_TARGET_IDX].sender,
      snippet: `the ${NEEDLE} is here. Marker: early.`,
      match_start: 4,
      match_end: 12,
      created_at: messages[EARLY_TARGET_IDX].created_at,
    },
    {
      message_uuid: messages[LATE_TARGET_IDX].uuid,
      sender: messages[LATE_TARGET_IDX].sender,
      snippet: `the ${NEEDLE} is here. Marker: late.`,
      match_start: 4,
      match_end: 12,
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

test.describe('Search — clicking a hit scrolls its target bubble (A1 two-tier fix)', () => {
  // ─────────────────────────────────────────────────────────────────────
  // Test 1 — Fast path stays alive. Bubble IS in DOM → click scrolls
  // in place, no URL change, ring-yellow flashes.
  // ─────────────────────────────────────────────────────────────────────
  test('fast path: bubble mounted → scrolls in place, no URL change', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: [summary], details: { [CONV_UUID]: detail } })
    await mockSearch(page, searchResults)
    // Modest viewport so even the "early" target needs SOME scroll to
    // be centered.
    await page.setViewportSize({ width: 1024, height: 900 })

    await recordHistory(page)
    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(NEEDLE)
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({ timeout: 10000 })

    // Click the EARLY result card. The early target is in DOM, so the
    // fast path takes it.
    const earlyCard = page.getByRole('button', { name: /Marker: early/i }).first()
    await expect(earlyCard).toBeVisible()
    await earlyCard.click()

    // POSITIVE: fast path applied the ring-yellow class directly via
    // `element.classList.add(...)` inside navigateToMatch.
    const earlyTarget = page.locator(`[data-message-uuid="${messages[EARLY_TARGET_IDX].uuid}"]`)
    await expect(earlyTarget).toHaveClass(/ring-yellow-400/, { timeout: 2000 })

    // NEGATIVE bidirectional pin: fast path must NOT have changed the
    // URL. If the URL acquires `?highlight=`, it means we regressed to
    // the URL-only path (A2) — that would break the
    // search-focus-model + search-auto-focus tests downstream. The jump
    // has settled, and the in-page history record holds every URL the
    // page had (2026-10-24: a 500 ms sleep, then one look).
    await waitForScrollQuiet(page)
    expect((await historyRecord(page)).filter((u) => u.includes('highlight='))).toEqual([])
    expect(new URL(page.url()).searchParams.get('highlight')).toBeNull()
  })

  // ─────────────────────────────────────────────────────────────────────
  // Test 2 — THE BUG PIN. Bubble NOT in DOM at click time → fast path
  // can't run, falls through to URL navigation. Pre-fix code went
  // silent-no-op here; post-fix the URL navigation kicks in.
  // ─────────────────────────────────────────────────────────────────────
  test('fall-through: bubble missing at click time → navigates to ?highlight= (15K-msg bug pin)', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: [summary], details: { [CONV_UUID]: detail } })
    await mockSearch(page, searchResults)
    await page.setViewportSize({ width: 1024, height: 900 })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_UUID}`))
    await expect(page.locator('[data-message-uuid="m-0000"]')).toBeVisible()

    // Pre-flight bidirectional invariant: the LATE target must NOT be
    // in viewport before the click. This proves the test isn't trivially
    // passing because "the bubble was always on screen."
    const lateTarget = page.locator(`[data-message-uuid="${messages[LATE_TARGET_IDX].uuid}"]`)
    await expect(lateTarget).not.toBeInViewport()

    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(NEEDLE)
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({ timeout: 10000 })

    // ─── BUG REPRODUCTION (deterministic proxy for the React 19 race)
    //
    // The user's live smoke on a 15K-msg conv showed `querySelector`
    // returning `null` from inside navigateToMatch (the bubble had
    // not yet been committed to the DOM at the synchronous moment).
    // We can't trigger a 15K React render in e2e — too slow, and
    // playwright budgets don't tolerate it — so we simulate the race
    // by intercepting `document.querySelector` ONCE for the late
    // target's selector and returning `null` (mimicking "not in DOM
    // yet"). After that single call, we restore the original
    // implementation so ConversationPage's highlight-effect
    // querySelector (running 100 ms later) finds the real bubble
    // and lands the scroll.
    //
    // This is the deterministic equivalent of "the React render
    // hadn't committed yet" — the exact failure mode the user
    // reported. The OLD fast path's null result silently bailed
    // (silent no-op for the user). The A1 fix falls through to
    // navigate(), which is what we assert below.
    await page.evaluate((targetUuid) => {
      const original = Document.prototype.querySelector
      const targetSelector = `[data-message-uuid="${targetUuid}"]`
      let firstHitConsumed = false
      Document.prototype.querySelector = function (selector: string) {
        if (!firstHitConsumed && selector === targetSelector) {
          firstHitConsumed = true
          // Restore on next microtask so the highlight-effect's
          // querySelector (100 ms later) sees the real DOM.
          queueMicrotask(() => {
            Document.prototype.querySelector = original
          })
          return null
        }
        return original.call(this, selector)
      }
    }, messages[LATE_TARGET_IDX].uuid)

    // Click the LATE result card.
    const lateCard = page.getByRole('button', { name: /Marker: late/i }).first()
    await expect(lateCard).toBeVisible()
    await lateCard.click()

    // ─── A1 fall-through contract pin (RED on pre-fix code) ──────────
    //
    // Decisive observable trace that the fall-through fired: the URL
    // acquires `?highlight=<late-uuid>`. The OLD fast path silently
    // exited at navigateToMatch.ts:56 when querySelector returned
    // null — never called navigate(). With the fix, the missing
    // element falls through to the navigate branch.
    //
    // 1-second window leaves margin around ConversationPage's 2 s
    // scheduleHighlightClear cleanup.
    await expect.poll(
      () => new URL(page.url()).searchParams.get('highlight'),
      {
        timeout: 1000,
        message: 'A1 fall-through contract: bubble missing at click time → navigate to ?highlight=<uuid>',
      },
    ).toBe(messages[LATE_TARGET_IDX].uuid)

    // User-visible outcome: scroll happened. The inner scroll
    // container's `scrollTop` moved away from 0 — the late target
    // at idx 550/600 can't be brought into view from scrollTop=0
    // without scrolling. We assert against scrollTop (not
    // `toBeInViewport`) because the scroll target is the inner
    // `overflow-y-auto` container, not the window viewport, and
    // Playwright's viewport assertions can race the smooth-scroll
    // animation.
    await expect.poll(
      async () => {
        return await page.evaluate(() => {
          const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
          return stream?.scrollTop ?? 0
        })
      },
      {
        timeout: 5000,
        message: 'Fall-through path must scroll the message-stream container',
      },
    ).toBeGreaterThan(1000) // m-0550 is far below; scrollTop must be substantial.

    // And the ring-flash from ConversationPage's highlight effect
    // landed on the target (proves the effect ran end-to-end).
    await expect(lateTarget).toHaveClass(/ring-yellow-400/, { timeout: 2000 })
  })
})
