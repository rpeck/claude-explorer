import { test, expect, withNetRetry, waitForKeyboardShortcuts } from './fixtures'

/**
 * M5.5: converted to `./fixtures` `mockBackend`. Without mocked
 * `/api/config` the ConnectionStatus modal's overlay intercepts pointer
 * events on the sidebar Refresh button, timing the test out at 60s.
 * The per-test `/api/fetch/refresh*` mock stays authoritative via
 * `extraRoutes` (LIFO).
 */

test.describe('Toast position must not be occluded by the search panel', () => {
  test('toast remains visible when the right-side search panel is open', async ({ page, mockBackend }) => {
    await mockBackend({
      extraRoutes: async (p) => {
        await p.route('**/api/fetch/refresh*', async (route) => {
          const body =
            'data: {"type":"start","message":"Fetching conversation list..."}\n\n' +
            'data: {"type":"error","kind":"TRANSIENT","retryable":true,"message":"Network problem reaching claude.ai. Retry?"}\n\n'
          await route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body,
          })
        })
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press('Meta+k')
    const searchPanel = page.locator('[data-testid="search-panel"], aside[aria-label*="earch"]').first()
    // The panel is open (2026-10-24: a 300 ms sleep, and the overlap check
    // below ran only `if` the panel happened to be visible).
    await expect(searchPanel).toBeVisible()

    const headerRefresh = page.locator('aside button[title="Refresh conversation list"]')
    await headerRefresh.click()

    const toast = page.locator('[data-sonner-toast]', {
      hasText: /Network problem reaching claude\.ai/i,
    })
    await expect(toast).toBeVisible({ timeout: 3000 })

    const toastBox = await toast.boundingBox()
    expect(toastBox).not.toBeNull()
    if (!toastBox) throw new Error('toast box missing')

    const viewport = page.viewportSize()
    if (!viewport) throw new Error('viewport missing')

    await expect(searchPanel).toBeVisible()
    const panelBox = await searchPanel.boundingBox()
    if (!panelBox) throw new Error('search panel box missing')
    const overlapX =
      Math.max(0, Math.min(toastBox.x + toastBox.width, panelBox.x + panelBox.width) - Math.max(toastBox.x, panelBox.x))
    const overlapY =
      Math.max(0, Math.min(toastBox.y + toastBox.height, panelBox.y + panelBox.height) - Math.max(toastBox.y, panelBox.y))
    const overlapArea = overlapX * overlapY
    const toastArea = toastBox.width * toastBox.height
    expect(overlapArea / toastArea).toBeLessThan(0.25)
  })
})
