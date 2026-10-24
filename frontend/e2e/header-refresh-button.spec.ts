import { test, expect, withNetRetry } from './fixtures'

/**
 * M5.5: converted to `./fixtures` `mockBackend`. Without mocked
 * `/api/config` the ConnectionStatus modal pops up and intercepts
 * pointer events on the sidebar Refresh button, so every test in this
 * file failed under the Python-free run. The per-test
 * `/api/fetch/refresh*` mocks remain authoritative — they're registered
 * after `mockBackend` (via `extraRoutes`) so LIFO grants them priority
 * over the fixture's default refresh handler.
 */

test.describe('Header Refresh button (Sidebar)', () => {
  test('clicking the header Refresh button hits the /api/fetch/refresh SSE pipeline', async ({ page, mockBackend }) => {
    const refreshRequests: string[] = []
    page.on('request', (req) => {
      const url = req.url()
      if (url.includes('/api/fetch/refresh')) {
        refreshRequests.push(url)
      }
    })

    await mockBackend({
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
    const headerRefresh = page.locator('aside button[title="Refresh conversation list"]')
    await expect(headerRefresh).toBeVisible()
    await headerRefresh.click()

    await expect.poll(() => refreshRequests.length).toBeGreaterThan(0)
    expect(refreshRequests[0]).toContain('/api/fetch/refresh')
  })

  // B5 — the header Refresh button is the unified "rebuild the corpus"
  // action: backend's /api/fetch/refresh SSE pipeline runs the Desktop
  // fetch AND triggers a re-list (which re-scans Claude Code JSONL files
  // since CC is read at request time, not cached). After the SSE completes,
  // the conversation-list query is invalidated so React Query refetches
  // /api/conversations — this is what surfaces both newly-fetched Desktop
  // sessions and any newly-discovered CC sessions in one visible action.
  test('header Refresh re-lists conversations after the SSE pipeline completes (B5)', async ({ page, mockBackend }) => {
    let listRequestCount = 0
    await mockBackend({
      extraRoutes: async (p) => {
        await p.route('**/api/conversations*', (route) => {
          const url = new URL(route.request().url())
          // Only count list requests, not detail/tree. Non-list requests
          // fall back to mockBackend's defaults so future detail/tree
          // assertions can't get a malformed `[]` body.
          if (!/\/api\/conversations\/[^/?]+/.test(url.pathname)) {
            listRequestCount += 1
            route.fulfill({ contentType: 'application/json', body: '[]' })
          } else {
            route.fallback()
          }
        })
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
    // Wait for the initial list load.
    await expect.poll(() => listRequestCount).toBeGreaterThan(0)
    const before = listRequestCount

    const headerRefresh = page.locator('aside button[title="Refresh conversation list"]')
    await headerRefresh.click()

    // After SSE completes the React Query cache invalidates and the list
    // is re-fetched — this is the unified "+ Desktop fetch + CC re-scan"
    // outcome the article promises.
    await expect.poll(() => listRequestCount).toBeGreaterThan(before)
  })

  test('clicking the header Refresh button shows an error toast for at least 5 seconds when the SSE errors', async ({ page, mockBackend }) => {
    await mockBackend({
      extraRoutes: async (p) => {
        await p.route('**/api/fetch/refresh*', async (route) => {
          const body =
            'data: {"type":"start","message":"Fetching conversation list..."}\n\n' +
            'data: {"type":"error","message":"Network problem reaching claude.ai. Retry?","kind":"TRANSIENT","retryable":true}\n\n'
          await route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body,
          })
        })
      },
    })

    // The page clock moves the toast's timer forward exactly (2026-10-24).
    await page.clock.install()
    await withNetRetry(page, () => page.goto('/'))
    const headerRefresh = page.locator('aside button[title="Refresh conversation list"]')
    await headerRefresh.click()

    const toast = page.locator('[data-sonner-toast]', {
      hasText: /Network problem reaching claude\.ai/i,
    })
    await expect(toast).toBeVisible({ timeout: 3000 })

    await page.clock.runFor(5000)
    await expect(toast).toBeVisible()
  })
})
