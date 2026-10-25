import { test, expect, makeSummary, makeMessage, makeDetail, PRIMARY_ORG, type Page, type Route, withNetRetry, waitForScrollQuiet, recordJumps, jumps, clearJumps, expectNoRevert } from './fixtures'
import type { Message } from '../src/lib/types'

/**
 * Regression spec for the 2026-05-24 "Cmd+G jumps then jumps back"
 * bug introduced by commit b6f9f70 (Settings flash-and-disappear fix).
 *
 * User-observable contract being pinned: when the user presses Cmd+G
 * or clicks a result card, the active-match indicator MUST advance to
 * the new hit AND STAY there. Pre-fix the indicator briefly showed the
 * new hit then reverted to match 1 within a few hundred ms.
 *
 * Why the existing search-* specs didn't catch it:
 *   - search-focus-model.spec.ts "Cmd+G moves focus to the matching
 *     bubble" asserts `await expect(bubble).toBeFocused()`. Playwright
 *     polls until the assertion passes — if focus briefly lands on
 *     msg-2 before being yanked back to msg-1, the toBeFocused() probe
 *     can hit during the brief window and pass even though the user
 *     sees a flash-and-disappear. The existing spec NEVER asserts
 *     "and STAYS focused for N ms".
 *   - search-auto-focus.spec.ts "does NOT yank user back" tests the
 *     two-match same-conv case and DOES `waitForTimeout(2500)`. That
 *     test passes today; the bug only repros when the URL gains a
 *     `?highlight=` param after navigation (SearchPinContext re-reads
 *     scope from URL on `location.search` change → new identity →
 *     SearchPanelContext scope memo identity churn → reset effect
 *     fires → activeMatchIndex back to -1 → auto-promote to 0). The
 *     existing test's match-2 click DOES go through the fast path
 *     (msg-2 already mounted), so the URL does NOT gain `highlight=`
 *     and the cascade never triggers.
 *
 * The reproducer below FORCES the URL fallback path (and therefore the
 * `?highlight=` URL mutation) by clicking a result card that targets
 * a DIFFERENT conversation. The cross-conv hop always takes the URL
 * fallback in navigateToMatch.ts, so the `?highlight=` lands in the
 * URL, SearchPinContext re-reads, and the jump-back race fires.
 */

const A = '00000000-0000-0000-0000-000000bb1100'
const B = '00000000-0000-0000-0000-000000bb2200'

const summaryA = makeSummary({
  uuid: A,
  source: 'CLAUDE_CODE',
  message_count: 1,
  name: 'Conv A',
})
const summaryB = makeSummary({
  uuid: B,
  source: 'CLAUDE_CODE',
  message_count: 1,
  name: 'Conv B',
})

const aMsg = makeMessage({
  uuid: 'a-msg-1',
  sender: 'human',
  text: 'needle one in A',
  content: [{ type: 'text', text: 'needle one in A' }],
} as Partial<Message> & { uuid: string })
const bMsg = makeMessage({
  uuid: 'b-msg-1',
  sender: 'assistant',
  text: 'needle two in B',
  content: [{ type: 'text', text: 'needle two in B' }],
} as Partial<Message> & { uuid: string })

const detailA = makeDetail(summaryA, [aMsg])
const detailB = makeDetail(summaryB, [bMsg])

async function mockSearchTwoConvs(page: Page) {
  await page.route('**/api/search**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        results: [
          {
            conversation_uuid: A,
            conversation_name: summaryA.name,
            conversation_updated_at: summaryA.updated_at,
            conversation_created_at: summaryA.created_at,
            project_name: null,
            matching_messages: [
              {
                message_uuid: 'a-msg-1',
                sender: 'human',
                snippet: 'needle one in A',
                match_start: 0,
                match_end: 6,
                created_at: aMsg.created_at,
              },
            ],
          },
          {
            conversation_uuid: B,
            conversation_name: summaryB.name,
            conversation_updated_at: summaryB.updated_at,
            conversation_created_at: summaryB.created_at,
            project_name: null,
            matching_messages: [
              {
                message_uuid: 'b-msg-1',
                sender: 'assistant',
                snippet: 'needle two in B',
                match_start: 0,
                match_end: 6,
                created_at: bMsg.created_at,
              },
            ],
          },
        ],
        total_messages_matched: 2,
        returned_messages: 2,
        truncated: false,
      }),
    })
  })
}

test.describe('Search — Cmd+G / card-click does NOT jump back (2026-05-24)', () => {
  test('observes URL stays on the user-targeted highlight (no auto-promote bounce)', async ({
    page,
    mockBackend,
  }) => {
    // Tightest possible repro: monitor every URL change. After Cmd+G,
    // there should be at most ONE URL transition. If a second one fires
    // (back to the first hit), that's the regression.
    //
    // CRITICAL: seed preferences with `organizationId` so the scope
    // memo in SearchPanelContext doesn't collapse to `undefined`. With
    // an org id, the memo returns `{organizationId: '...'}` — a NEW
    // object identity every time pinScope identity churns from the
    // SearchPinContext re-reading URL on `location.search` change.
    // Without an organizationId the bug is masked because `undefined`
    // is value-identical and the memo result is stable. Real users
    // always have an organizationId set (workspace dropdown defaults
    // to the primary org on first login).
    const C = '00000000-0000-0000-0000-000000bb4400'
    const N = 50
    const summaryC = makeSummary({ uuid: C, source: 'CLAUDE_CODE', message_count: N, name: 'Conv mon' })
    const cMsgs = Array.from({ length: N }, (_, i) =>
      makeMessage({
        uuid: `cm-${i}`,
        sender: i % 2 === 0 ? 'human' : 'assistant',
        text: i === 0 || i === 25 ? `needle hit ${i}` : `filler ${i} `.repeat(50),
        content: [],
      } as Partial<Message> & { uuid: string })
    )
    for (const m of cMsgs) m.content = [{ type: 'text', text: m.text }]
    const detailC = makeDetail(summaryC, cMsgs)
    await mockBackend({
      conversations: [summaryC],
      details: { [C]: detailC },
      preferences: {
        'claude-explorer.organizationFilter': PRIMARY_ORG,
      },
    })
    await page.route('**/api/search**', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          results: [{
            conversation_uuid: C,
            conversation_name: summaryC.name,
            conversation_updated_at: summaryC.updated_at,
            conversation_created_at: summaryC.created_at,
            project_name: null,
            matching_messages: [0, 25].map((i) => ({
              message_uuid: `cm-${i}`,
              sender: cMsgs[i].sender,
              snippet: `needle hit ${i}`,
              match_start: 0, match_end: 6,
              created_at: cMsgs[i].created_at,
            })),
          }],
          total_messages_matched: 2,
          returned_messages: 2,
          truncated: false,
        }),
      })
    })
    await page.setViewportSize({ width: 1024, height: 700 })

    await recordJumps(page)

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    await expect(page.locator('[data-message-uuid="cm-0"]')).toBeVisible()

    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible({ timeout: 3000 })
    await input.fill('needle')
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({ timeout: 5000 })

    const live = page.locator('[data-testid="search-match-aria-live"]')
    await expect(live).toContainText(/Match\s+1\s+of\s+2/i, { timeout: 5000 })

    // The auto-promote jump has settled; start the record just before Cmd+G.
    await waitForScrollQuiet(page)
    await clearJumps(page)

    await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')

    // The jump and its corrections are done. A bounce would run inside
    // that window: its effect chain is quicker than the correction chain
    // (2026-10-24: a 2 s sleep).
    await expect(live).toContainText(/Match\s+2\s+of\s+2/i)
    await waitForScrollQuiet(page)
    const { urls: distinctUrls, live: liveLog } = await jumps(page)
    expectNoRevert(liveLog, /Match\s+2\s+of\s+2/i)

    // We expect: URL changes ONCE to include highlight=cm-25, and that's it.
    // Pre-fix the URL goes highlight=cm-25, then the
    // SearchPinContext → SearchPanelContext scope cascade resets
    // activeMatchIndex back to -1, auto-promote forces it to 0, and
    // SearchPanel re-navigates to match 0 (same-conv, fast path, no
    // URL change but aria-live region reverts to "Match 1 of 2").
    const cm25Hits = distinctUrls.filter((u) => u.includes('highlight=cm-25')).length
    const cm0Hits = distinctUrls.filter((u) => u.includes('highlight=cm-0')).length
    expect(cm25Hits).toBeGreaterThan(0)
    // No bounce back to match 1: there must be NO transitions to cm-0
    // AFTER cm-25 lands. Detect by index ordering: last cm-25 index must
    // be the LAST distinct URL with a highlight= param.
    const lastIdxWithHighlight = (() => {
      for (let i = distinctUrls.length - 1; i >= 0; i--) {
        if (distinctUrls[i].includes('highlight=')) return i
      }
      return -1
    })()
    expect(lastIdxWithHighlight).toBeGreaterThan(-1)
    expect(distinctUrls[lastIdxWithHighlight]).toContain('highlight=cm-25')
    await expect(live).toContainText(/Match\s+2\s+of\s+2/i)
    // Negative: no cm-0 highlight after Cmd+G.
    expect(cm0Hits).toBe(0)
  })

  test('Cmd+G in LARGE virtualized conv (URL fallback path) advances and STAYS', async ({
    page,
    mockBackend,
  }) => {
    // The user-reported flow: a conversation with multiple search hits
    // spread across a LARGE conversation where the virtualizer only
    // mounts the visible window. Cmd+G to a non-mounted target hits
    // the URL fallback path in navigateToMatch (route gains
    // `?highlight=<msg>`). That URL mutation is what triggers the
    // SearchPinContext re-read of `scope` from `location.search`,
    // which produces a new identity-distinct `{kind:'none'}` and
    // (pre-fix) cascades through SearchPanelContext's `scope` useMemo
    // → activeMatchIndex reset effect → auto-promote → jumps user
    // back to match 1.
    //
    // Why same-conv 3-msg fixtures pass today (and missed the bug):
    // all 3 bubbles mount immediately on page load, so navigateToMatch's
    // fast path always succeeds and the URL never gains ?highlight=.
    // The SearchPin re-read never fires. Bug is hidden.
    const C = '00000000-0000-0000-0000-000000bb3300'
    const N = 50  // big enough that virtualizer doesn't mount all rows
    const summaryC = makeSummary({
      uuid: C,
      source: 'CLAUDE_CODE',
      message_count: N,
      name: 'Conv C (large)',
    })
    const cMsgs = Array.from({ length: N }, (_, i) =>
      makeMessage({
        uuid: `c-m${i}`,
        sender: i % 2 === 0 ? 'human' : 'assistant',
        text: i === 0 || i === 25 || i === 49
          ? `needle hit ${i}`
          : `filler message ${i} with enough text to take vertical space and force the virtualizer to leave unrelated bubbles unmounted on initial load. ` .repeat(3),
        content: [],
      } as Partial<Message> & { uuid: string })
    )
    // Backfill content for each msg from its text so the renderer has
    // something to display.
    for (const m of cMsgs) {
      m.content = [{ type: 'text', text: m.text }]
    }
    const detailC = makeDetail(summaryC, cMsgs)
    await mockBackend({
      conversations: [summaryC],
      details: { [C]: detailC },
      preferences: {
        'claude-explorer.organizationFilter': PRIMARY_ORG,
      },
    })
    await page.route('**/api/search**', async (route: Route) => {
      const hits = [0, 25, 49]
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          results: [{
            conversation_uuid: C,
            conversation_name: summaryC.name,
            conversation_updated_at: summaryC.updated_at,
            conversation_created_at: summaryC.created_at,
            project_name: null,
            matching_messages: hits.map((i) => ({
              message_uuid: `c-m${i}`,
              sender: cMsgs[i].sender,
              snippet: `needle hit ${i}`,
              match_start: 0,
              match_end: 6,
              created_at: cMsgs[i].created_at,
            })),
          }],
          total_messages_matched: 3,
          returned_messages: 3,
          truncated: false,
        }),
      })
    })
    await page.setViewportSize({ width: 1024, height: 800 })
    await recordJumps(page)
    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    await expect(page.locator('[data-message-uuid="c-m0"]')).toBeVisible()

    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible({ timeout: 3000 })
    await input.fill('needle')

    await expect(page.locator('text=/of\\s+3\\s+matches/')).toBeVisible({ timeout: 5000 })
    const live = page.locator('[data-testid="search-match-aria-live"]')
    await expect(live).toContainText(/Match\s+1\s+of\s+3/i, { timeout: 5000 })

    // Cmd+G → match 2 (c-m25, which is mid-conv and NOT mounted on
    // initial load — forces the URL fallback path).
    await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')

    // URL must gain `?highlight=c-m25` — this is the canary the
    // user sees as "jumped to next hit".
    await expect(page).toHaveURL(/highlight=c-m25/, { timeout: 3000 })
    await expect(live).toContainText(/Match\s+2\s+of\s+3/i, { timeout: 3000 })

    // CRITICAL CONTRACT: 1500ms later, the indicator must STILL say
    // "Match 2 of 3" AND the URL must STILL reference c-m25. Pre-fix:
    // the URL change triggers SearchPinContext to re-read scope (new
    // identity), which cascades through SearchPanelContext's scope
    // useMemo, fires the activeMatchIndex reset effect (deps include
    // scope), drops index back to -1, then auto-promote sets it to 0
    // → URL navigates back to c-m0 (the first match).
    await waitForScrollQuiet(page) // the jump and its corrections are done
    expectNoRevert((await jumps(page)).live, /Match\s+2\s+of\s+3/i)
    await expect(live).toContainText(/Match\s+2\s+of\s+3/i)
    await expect(page).toHaveURL(/highlight=c-m25/)

    // Cmd+G → match 3 (c-m49, also virt-unmounted).
    await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')
    await expect(page).toHaveURL(/highlight=c-m49/, { timeout: 3000 })
    await expect(live).toContainText(/Match\s+3\s+of\s+3/i, { timeout: 3000 })

    await waitForScrollQuiet(page)
    expectNoRevert((await jumps(page)).live, /Match\s+3\s+of\s+3/i)
    await expect(live).toContainText(/Match\s+3\s+of\s+3/i)
    await expect(page).toHaveURL(/highlight=c-m49/)
  })

  test('Cmd+G across conversations advances to match 2 AND STAYS', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summaryA, summaryB],
      details: { [A]: detailA, [B]: detailB },
      preferences: {
        'claude-explorer.organizationFilter': PRIMARY_ORG,
      },
    })
    await mockSearchTwoConvs(page)
    await page.setViewportSize({ width: 1024, height: 900 })

    await recordJumps(page)
    await withNetRetry(page, () => page.goto(`/conversations/${A}`))
    await expect(page.locator('[data-message-uuid="a-msg-1"]')).toBeVisible()

    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible({ timeout: 3000 })
    await input.fill('needle')

    // Auto-promote picks match 1 (a-msg-1 in conv A).
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({ timeout: 5000 })
    const live = page.locator('[data-testid="search-match-aria-live"]')
    await expect(live).toContainText(/Match\s+1\s+of\s+2/i, { timeout: 5000 })

    // Press Cmd+G — must advance to match 2 (b-msg-1 in conv B), which
    // requires a cross-conversation hop and therefore takes the URL
    // fallback path in navigateToMatch. The URL gains `?highlight=b-msg-1`.
    await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')

    // URL should change to conv B.
    await expect(page).toHaveURL(new RegExp(`/conversations/${B}`), { timeout: 3000 })
    await expect(live).toContainText(/Match\s+2\s+of\s+2/i, { timeout: 3000 })

    // CRITICAL: after a settle window, the active match MUST still be 2,
    // and we MUST still be on conv B. Pre-fix: SearchPinContext re-reads
    // scope from URL on `location.search` change (the highlight param
    // landing). The new pinScope identity propagates through
    // SearchPanelContext's `scope` useMemo. With an `organizationId`
    // set (real-user state — workspace dropdown), `scope` returns a
    // NEW identity-distinct `{organizationId: ...}` object on every
    // re-run, which fires the activeMatchIndex reset effect (deps
    // include `scope`), drops index back to -1, then auto-promote
    // sets it to 0 → user is yanked back to match 1.
    await expect(page.locator('[data-message-uuid="b-msg-1"]')).toBeVisible()
    await waitForScrollQuiet(page) // conv B's jump and its corrections are done
    expectNoRevert((await jumps(page)).live, /Match\s+2\s+of\s+2/i)
    await expect(live).toContainText(/Match\s+2\s+of\s+2/i)
    await expect(page).toHaveURL(new RegExp(`/conversations/${B}`))
  })

  test('clicking a cross-conv result card advances AND STAYS on the new hit', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summaryA, summaryB],
      details: { [A]: detailA, [B]: detailB },
      preferences: {
        'claude-explorer.organizationFilter': PRIMARY_ORG,
      },
    })
    await mockSearchTwoConvs(page)
    await page.setViewportSize({ width: 1024, height: 900 })

    await recordJumps(page)
    await withNetRetry(page, () => page.goto(`/conversations/${A}`))
    await expect(page.locator('[data-message-uuid="a-msg-1"]')).toBeVisible()

    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible({ timeout: 3000 })
    await input.fill('needle')

    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({ timeout: 5000 })
    const live = page.locator('[data-testid="search-match-aria-live"]')
    await expect(live).toContainText(/Match\s+1\s+of\s+2/i, { timeout: 5000 })

    // Click the result card for conv B (match 2). Cross-conv → URL
    // fallback → `?highlight=b-msg-1` lands in the URL.
    const cards = page.locator('[data-testid="search-result-card"]')
    await expect(cards).toHaveCount(2)
    await cards.nth(1).click()

    await expect(page).toHaveURL(new RegExp(`/conversations/${B}`), { timeout: 3000 })
    await expect(live).toContainText(/Match\s+2\s+of\s+2/i, { timeout: 3000 })

    // The jump-back race fires within ~50-200ms post-navigation. Wait
    // generously to catch it.
    await expect(page.locator('[data-message-uuid="b-msg-1"]')).toBeVisible()
    await waitForScrollQuiet(page) // conv B's jump and its corrections are done
    expectNoRevert((await jumps(page)).live, /Match\s+2\s+of\s+2/i)
    await expect(live).toContainText(/Match\s+2\s+of\s+2/i)
    await expect(page).toHaveURL(new RegExp(`/conversations/${B}`))
  })
})
