import { test, expect, makeSummary, withNetRetry } from './fixtures'

/**
 * Stream C: Cmd+R must invoke the same Build-9 capture+fetch pipeline
 * the sidebar Refresh button runs, NOT a plain conversation-list
 * re-fetch.
 *
 * Article promise (articles/part_2_web_app.md, line ~154):
 *   "The UI also binds Cmd+R to the refresh action (the same one the
 *    sidebar button triggers) so you don't accidentally reload the
 *    single-page app and lose your place."
 *
 * Before this turn's work, Cmd+R did `queryClient.invalidateQueries`
 * — i.e. just re-listed conversations. The article's "same one the
 * sidebar button triggers" claim was false. This spec pins the
 * post-fix behavior so a future regression breaks loudly.
 *
 * Settle signals (per feedback_playwright_settle_signals):
 *   - We wait for `expect.poll(() => refreshRequests.length)` instead
 *     of a fixed timeout. The request fires synchronously from the
 *     keydown handler, but Playwright's network plumbing has a small
 *     async hop before page.on('request') sees it.
 *   - We wait for the toast text before asserting on it so a slow
 *     render doesn't false-positive the test.
 */

const SUMMARY = makeSummary({
  uuid: '11111111-1111-1111-1111-111111111111',
  name: 'Some conversation',
  message_count: 2,
  source: 'CLAUDE_AI',
})

test.describe('Stream C: Cmd+R triggers /api/fetch/refresh', () => {
  test('pressing Cmd+R hits /api/fetch/refresh (the sidebar Refresh pipeline)', async ({
    page,
    mockBackend,
  }) => {
    const refreshRequests: string[] = []
    page.on('request', (req) => {
      const url = req.url()
      if (url.includes('/api/fetch/refresh')) {
        refreshRequests.push(url)
      }
    })

    await mockBackend({
      conversations: [SUMMARY],
      extraRoutes: async (p) => {
        await p.route('**/api/fetch/refresh*', async (route) => {
          const body =
            'data: {"type":"start","message":"Fetching conversation list..."}\n\n' +
            'data: {"type":"complete","message":"Fetched 0 conversations successfully.","current":0,"total":0}\n\n'
          await route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body,
          })
        })
      },
    })

    await withNetRetry(page, () => page.goto('/'))

    // Focus the main pane so the keydown listener runs against the
    // app, not an input element. The handler in
    // useKeyboardShortcuts.ts checks isInputElement() for some
    // shortcuts, but Cmd+R is currently unconditional.
    await page.locator('main').click()

    // Fire Cmd+R. preventDefault() in the handler stops the browser
    // from reloading the page; if the binding regressed (e.g.
    // someone removed preventDefault), the navigation would
    // interrupt this test. So this spec also proves the
    // preventDefault stays put.
    await page.keyboard.press('Meta+r')

    // Settle signal: wait for the request to appear in our listener.
    // The handler calls startRefresh synchronously, but the network
    // round-trip through Playwright's interception layer is async.
    await expect
      .poll(() => refreshRequests.length, { timeout: 3000 })
      .toBeGreaterThan(0)
    expect(refreshRequests[0]).toContain('/api/fetch/refresh')
    // The hook calls startRefresh(true) which is the default; the
    // API client only appends an `incremental=false` query param
    // when the caller passes false. So the URL must NOT carry
    // `incremental=false` — proving we asked for the incremental
    // path, not the full-refresh path.
    expect(refreshRequests[0]).not.toContain('incremental=false')
  })

  test('pressing Ctrl+R (Windows / Linux) also hits /api/fetch/refresh', async ({
    page,
    mockBackend,
  }) => {
    // Ctrl+R is the native browser reload on Windows / Linux. Before the
    // 2026-05-29 fix the handler bound `e.metaKey && !e.ctrlKey`, so
    // Ctrl+R fell through to a page reload and the article's "the code
    // accepts both modifiers, so the shortcuts work everywhere" claim
    // was false off macOS. The handler now uses the shared cmdOrCtrl
    // modifier and calls preventDefault, so Ctrl+R must refresh in-app
    // instead of reloading. This is the regression guard for that fix.
    const refreshRequests: string[] = []
    page.on('request', (req) => {
      const url = req.url()
      if (url.includes('/api/fetch/refresh')) {
        refreshRequests.push(url)
      }
    })

    await mockBackend({
      conversations: [SUMMARY],
      extraRoutes: async (p) => {
        await p.route('**/api/fetch/refresh*', async (route) => {
          const body =
            'data: {"type":"start","message":"Fetching conversation list..."}\n\n' +
            'data: {"type":"complete","message":"Fetched 0 conversations successfully.","current":0,"total":0}\n\n'
          await route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body,
          })
        })
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    await page.locator('main').click()

    await page.keyboard.press('Control+r')

    await expect
      .poll(() => refreshRequests.length, { timeout: 3000 })
      .toBeGreaterThan(0)
    expect(refreshRequests[0]).toContain('/api/fetch/refresh')
    expect(refreshRequests[0]).not.toContain('incremental=false')
  })

  test('pressing Cmd+R while a refresh is already running does NOT fire a second request', async ({
    page,
    mockBackend,
  }) => {
    // Defense-in-depth check on the FetchPipelineContext.sourceRef
    // guard. The hook's isRefreshRunning flag short-circuits the
    // handler before startRefresh is ever called, and the context's
    // own sourceRef.current guard catches anything that races past
    // that. Both layers are tested by this single key-press scenario.
    const refreshRequests: string[] = []
    page.on('request', (req) => {
      const url = req.url()
      if (url.includes('/api/fetch/refresh')) {
        refreshRequests.push(url)
      }
    })

    await mockBackend({
      conversations: [SUMMARY],
      extraRoutes: async (p) => {
        // Slow SSE so the second Cmd+R lands while the first is
        // still "running". We never send the `complete` event.
        await p.route('**/api/fetch/refresh*', async (route) => {
          await new Promise((resolve) => setTimeout(resolve, 100))
          await route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body: 'data: {"type":"start","message":"Working..."}\n\n',
          })
        })
      },
    })

    // Count the refresh streams that the page opens. startRefresh creates
    // its EventSource synchronously in the key handler, so the count is
    // final when keyboard.press() returns (2026-10-24: a 300 ms sleep "for
    // a spurious second request").
    await page.addInitScript(() => {
      const Real = window.EventSource
      const w = window as unknown as { __refreshStreams: number }
      w.__refreshStreams = 0
      window.EventSource = class extends Real {
        constructor(url: string | URL, init?: EventSourceInit) {
          super(url, init)
          if (String(url).includes('/api/fetch/refresh')) w.__refreshStreams += 1
        }
      }
    })
    const refreshStreams = () =>
      page.evaluate(() => (window as unknown as { __refreshStreams: number }).__refreshStreams)

    await withNetRetry(page, () => page.goto('/'))
    await page.locator('main').click()

    await page.keyboard.press('Meta+r')
    expect(await refreshStreams()).toBe(1)

    // Settle signal: wait for the first request to land before
    // pressing again. Without this we'd race the keydown handler
    // against React batching.
    await expect
      .poll(() => refreshRequests.length, { timeout: 3000 })
      .toBe(1)

    // Now press again WHILE the first SSE is still running. Both
    // guards (hook's isRunning flag + context's sourceRef) must
    // prevent the second request from firing.
    await page.keyboard.press('Meta+r')

    expect(await refreshStreams(), 'the second Cmd+R opened another refresh stream').toBe(1)
    expect(refreshRequests.length).toBe(1)
  })
})
