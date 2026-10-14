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
    await withNetRetry(page, () => page.goto('/'))

    await quitButton(page).click()
    await page.getByRole('dialog', { name: 'Quit Claude Explorer?' })
      .getByRole('button', { name: 'Quit' }).click()

    await expect(page.getByRole('heading', { name: 'Claude Explorer has stopped' })).toBeVisible()
    await expect(page.getByText('You can close this tab.')).toBeVisible()
    expect(calls).toEqual([{ method: 'POST' }])
    await expect(page.locator('aside')).toHaveCount(0)

    // The stopped page sends no more requests, even after polls would fire.
    const later: string[] = []
    page.on('request', (r) => {
      if (r.url().includes('/api/')) later.push(r.url())
    })
    await page.clock.fastForward('05:00')
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await page.waitForTimeout(300)
    expect(later).toEqual([])
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
