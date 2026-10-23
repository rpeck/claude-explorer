import { test, expect } from './fixtures'

/**
 * `mockBackend` registers its catch-all 500 route first and then about 20
 * specific routes, one `await page.route(...)` at a time. A page that is
 * already open keeps sending requests during that time. Before 2026-10-23,
 * such a request reached the catch-all before its own route existed and got
 * a 500, and the console gate failed the test ("Failed to load resource:
 * ... status of 500"). keyboard-nav-smoke.spec.ts failed this way in about
 * 1 of 4 runs at 8 workers.
 *
 * This test holds the registration open after the catch-all, sends a
 * request from the page, and then lets the registration finish.
 */
test('a request sent while mockBackend registers its routes does not get the catch-all 500', async ({
  page,
  mockBackend,
}) => {
  await page.goto('/')

  const originalRoute = page.route.bind(page)
  let registered = 0
  let release!: () => void
  const held = new Promise<void>((resolve) => (release = resolve))
  let signalHeld!: () => void
  const isHeld = new Promise<void>((resolve) => (signalHeld = resolve))
  page.route = (async (...args: Parameters<typeof page.route>) => {
    const result = await originalRoute(...args)
    registered += 1
    if (registered === 1) {
      signalHeld()
      await held
    }
    return result
  }) as typeof page.route

  const registration = mockBackend({ conversations: [] })
  await isHeld
  const status = await page.evaluate(() => fetch('/api/config').then((r) => r.status))
  release()
  await registration
  page.route = originalRoute

  expect(status).toBe(200)
})
