import {
  test,
  expect,
  makeSummary,
  withNetRetry,
  expectNetworkError,
  watchConnectionDialog,
  connectionDialogSeen,
  type Page,
  type Route,
} from './fixtures'

/**
 * Manual finding 2026-05-03 (Bug A): on a reload against a slow but
 * healthy backend (~2.3 s /api/config on a 660-conversation archive), the
 * ConnectionStatus dialog cycled through "Attempt N of 5" before it
 * settled.
 *
 * The dialog opens once two attempts in a row failed (retryCount >= 2);
 * retries wait 4 s, then 8 s, 10 s, ...; each attempt aborts after 5 s.
 *
 * 2026-10-24 rewrite. The old tests checked nothing:
 * - They registered their slow /api/config route BEFORE mockBackend, and
 *   Playwright runs the route registered last, so mockBackend answered
 *   every call at once: the delays never happened, and the call counter
 *   stayed at 0 (so "at most 4 calls" always passed).
 * - They watched for 6-7 s of real time, but two failed attempts take
 *   about 14 s.
 * Now the test's route comes after mockBackend, each test asserts that
 * its route answered, the page clock moves the app's timers (retry
 * delays and the 5 s abort) forward exactly, and a MutationObserver
 * records every appearance of the dialog.
 */

const CONFIG = JSON.stringify({ data_dir: '/tmp', conversation_count: 660 })

/** A /api/config route whose calls the test answers by hand. */
async function holdConfig(page: Page) {
  const held: Route[] = []
  let calls = 0
  await page.route('**/api/config', (route) => {
    calls += 1
    held.push(route)
  })
  return {
    calls: () => calls,
    held: () => held.length,
    /** Answer every call that waits, and let later calls through at once. */
    releaseAll: async () => {
      await page.unroute('**/api/config')
      await page.route('**/api/config', (route) => {
        calls += 1
        return route.fulfill({ contentType: 'application/json', body: CONFIG })
      })
      for (const route of held.splice(0)) {
        await route.fulfill({ contentType: 'application/json', body: CONFIG }).catch(() => undefined)
      }
    },
  }
}

test.describe('ConnectionStatus on reload (Bug A)', () => {
  test.beforeEach(async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [makeSummary({ uuid: 'a', name: 'Conv A' })] })
    await watchConnectionDialog(page)
    await page.clock.install()
  })

  test('a slow but successful /api/config shows no retry dialog', async ({ page }) => {
    const config = await holdConfig(page)
    await withNetRetry(page, () => page.goto('/'))
    await expect.poll(() => config.held()).toBeGreaterThan(0)

    await page.clock.runFor(2500) // 2.5 s: slow, but under the 5 s abort
    await config.releaseAll()
    await expect(page.getByText('Conv A')).toBeVisible()
    await page.clock.runFor(45_000) // past every retry window

    expect(await connectionDialogSeen(page)).toEqual([])
  })

  test('the app does not fire /api/config in a loop (StrictMode safe)', async ({ page }) => {
    let calls = 0
    await page.route('**/api/config', (route) => {
      calls += 1
      return route.fulfill({ contentType: 'application/json', body: CONFIG })
    })
    await withNetRetry(page, () => page.goto('/'))
    await expect(page.getByText('Conv A')).toBeVisible()
    await page.clock.runFor(45_000) // past every retry window

    // StrictMode mounts twice in dev; ConnectionStatus and useConfig each
    // fetch once per mount: at most 4. A retry loop would go far higher.
    expect(calls).toBeGreaterThan(0)
    expect(calls).toBeLessThanOrEqual(4)
  })

  test('one call past the 5 s timeout shows no dialog', async ({ page, consoleAssertions }) => {
    consoleAssertions.allowlist.push(/signal is aborted|AbortError|aborted/i)
    const config = await holdConfig(page)
    await withNetRetry(page, () => page.goto('/'))
    await expect.poll(() => config.held()).toBeGreaterThan(0)
    const firstCalls = config.calls()

    await page.clock.runFor(5000) // the first attempt aborts ...
    // ... which ConnectionStatus counts (its DOM signal of the retry state).
    await expect(page.locator('html[data-connection-retries="1"]')).toHaveCount(1)
    await config.releaseAll() // from now on the backend answers at once
    await page.clock.runFor(4000) // the first retry runs, and succeeds
    await expect(page.getByText('Conv A')).toBeVisible()
    await page.clock.runFor(45_000)

    expect(config.calls(), 'the retry after the abort did not happen').toBeGreaterThan(firstCalls)
    expect(await connectionDialogSeen(page)).toEqual([])
  })

  test('two failed attempts in a row do open the dialog', async ({ page, consoleAssertions }) => {
    // The opposite case: proves the observer sees the dialog, and pins the
    // threshold of two failures.
    expectNetworkError(consoleAssertions, 'connectionrefused')
    await page.route('**/api/config', (route) => route.abort('connectionrefused'))
    await withNetRetry(page, () => page.goto('/'))
    await page.clock.runFor(4000) // the first retry runs, and fails too

    await expect(page.getByText(/Attempt 2 of 5/)).toBeVisible()
    expect((await connectionDialogSeen(page)).join(' ')).toMatch(/Attempt 2 of 5/)
  })
})
