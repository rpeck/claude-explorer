import { test, expect, withNetRetry, expectNetworkError } from './fixtures'
import type { Page, Route } from '@playwright/test'

/**
 * Spec-driven tests for UX.md → "Quit (power button)".
 *
 * Written from UX.md, e2e/fixtures.ts and src/lib/types.ts only
 * (TESTING.md §1, spec-driven test files).
 */

const quitButton = (page: Page) =>
  page.locator('aside').first().getByRole('button', { name: 'Quit Claude Explorer' })

async function routeShutdown(
  page: Page,
  calls: { method: string }[],
  status = 202,
) {
  await page.route('**/api/shutdown', (route: Route) => {
    calls.push({ method: route.request().method() })
    route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(status < 300 ? { status: 'stopping' } : { detail: 'refused by test' }),
    })
  })
}

/** True for the conversation-list request (not detail or export paths). */
const isListUrl = (url: string) => new URL(url).pathname === '/api/conversations'

/**
 * Record every `/api/` fetch the page starts, in the page itself. A fetch
 * that a timer or event handler starts is recorded synchronously, so the
 * record does not depend on when Playwright delivers its `request` event.
 */
async function recordApiFetches(page: Page) {
  await page.evaluate(() => {
    const w = window as unknown as { __apiFetches: string[] }
    w.__apiFetches = []
    const realFetch = window.fetch.bind(window)
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (url.includes('/api/')) w.__apiFetches.push(new URL(url, location.href).href)
      return realFetch(input, init)
    }
  })
}

/**
 * Return and clear the recorded fetches. It first waits for one real
 * macrotask (a MessageChannel round trip, which the fake clock does not
 * control), so every microtask that a signal queued has run.
 */
async function takeApiFetches(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    await new Promise<void>((resolve) => {
      const channel = new MessageChannel()
      channel.port1.onmessage = () => resolve()
      channel.port2.postMessage(null)
    })
    const w = window as unknown as { __apiFetches: string[] }
    const taken = w.__apiFetches
    w.__apiFetches = []
    return taken
  })
}

/**
 * The refetch-on-focus signal that TanStack Query v5 listens to. Its
 * focusManager listens for `visibilitychange`, not `focus`, so a `focus`
 * event proves nothing.
 */
async function sendFocusSignal(page: Page) {
  await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')))
}

test.describe('Quit (power button)', () => {
  test('sits in the sidebar footer, below the account row', async ({ page, mockBackend }) => {
    await mockBackend()
    await withNetRetry(page, () => page.goto('/'))
    const button = quitButton(page)
    await expect(button).toBeVisible()
    const account = page.getByRole('region', { name: 'Signed-in account' })
    const [b, a] = [await button.boundingBox(), await account.boundingBox()]
    expect(b && a && b.y > a.y).toBeTruthy()
  })

  test('Cancel closes the dialog without stopping anything', async ({ page, mockBackend }) => {
    const calls: { method: string }[] = []
    await mockBackend({ extraRoutes: (p) => routeShutdown(p, calls) })
    await withNetRetry(page, () => page.goto('/'))

    await quitButton(page).click()
    const dialog = page.getByRole('dialog', { name: 'Quit Claude Explorer?' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText(
      'This stops the Claude Explorer server. Open Claude Explorer again to restart it.',
    )
    await expect(dialog).not.toContainText('A Refresh is running')
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toBeHidden()

    await quitButton(page).click()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toBeHidden()

    expect(calls).toEqual([])
    await expect(page.locator('aside').first()).toBeVisible()
  })

  test('Quit stops the server and replaces the page with the stopped screen', async ({ page, mockBackend }) => {
    const calls: { method: string }[] = []
    await page.clock.install()
    await mockBackend({ extraRoutes: (p) => routeShutdown(p, calls) })
    const listLoaded = page.waitForResponse((r) => isListUrl(r.url()))
    await withNetRetry(page, () => page.goto('/'))
    await listLoaded
    await recordApiFetches(page)

    // Control: while the app runs, the focus signal refetches stale
    // queries. Without this, the check after Quit could pass because
    // the signal does nothing.
    await page.clock.fastForward('01:00') // past the list's 30 s staleTime
    await takeApiFetches(page)
    const refetched = page.waitForResponse((r) => isListUrl(r.url()))
    await sendFocusSignal(page)
    expect((await takeApiFetches(page)).some(isListUrl)).toBe(true)
    await refetched

    await quitButton(page).click()
    await page.getByRole('dialog', { name: 'Quit Claude Explorer?' })
      .getByRole('button', { name: 'Quit' }).click()

    await expect(page.getByRole('heading', { name: 'Claude Explorer has stopped' })).toBeVisible()
    await expect(page.getByText('You can close this tab.')).toBeVisible()
    expect(calls).toEqual([{ method: 'POST' }])
    await expect(page.locator('aside')).toHaveCount(0)

    // The stopped page sends no more requests, even after polls would
    // fire and after the same focus signal that refetched above.
    await takeApiFetches(page)
    await page.clock.fastForward('05:00')
    expect(await takeApiFetches(page)).toEqual([])
    await sendFocusSignal(page)
    expect(await takeApiFetches(page)).toEqual([])
    await expect(page.getByRole('heading', { name: 'Claude Explorer has stopped' })).toBeVisible()
  })

  test('a running Refresh is called out in the dialog', async ({ page, mockBackend }) => {
    await mockBackend({
      extraRoutes: async (p) => {
        // Never answer: the pipeline stays running.
        await p.route('**/api/fetch/refresh*', () => {})
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    await page.getByRole('button', { name: /refresh conversation list/i }).click()

    await quitButton(page).click()
    await expect(page.getByRole('dialog', { name: 'Quit Claude Explorer?' })).toContainText(
      'A Refresh is running. Quitting interrupts it; the next Refresh picks up where it stopped.',
    )
  })

  test('a failed shutdown keeps the dialog open with the reason, and the app working', async ({ page, mockBackend, consoleAssertions }) => {
    const calls: { method: string }[] = []
    expectNetworkError(consoleAssertions, 403)
    await mockBackend({ extraRoutes: (p) => routeShutdown(p, calls, 403) })
    await withNetRetry(page, () => page.goto('/'))

    await quitButton(page).click()
    const dialog = page.getByRole('dialog', { name: 'Quit Claude Explorer?' })
    await dialog.getByRole('button', { name: 'Quit' }).click()

    await expect(dialog).toContainText('Could not stop the server.')
    await expect(dialog).toContainText('refused by test')
    await expect(page.getByRole('heading', { name: 'Claude Explorer has stopped' })).toHaveCount(0)
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.locator('aside').first()).toBeVisible()
  })
})
