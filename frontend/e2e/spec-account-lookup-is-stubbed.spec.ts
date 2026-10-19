import { test, expect, withNetRetry } from './fixtures'

/**
 * PR #6 review (2026-10-19): a spec that does not use ``mockBackend`` must
 * still never reach the real ``/api/account``. That route makes the backend
 * call claude.ai with the session key on disk, and a local run reuses the
 * developer's own server. The ``stubAccountLookup`` auto fixture in
 * fixtures.ts answers it in the browser instead.
 *
 * Checked through the badge, not the network response: Chromium may drop a
 * response body before the test reads it ("No data found for resource"),
 * which made the first version of this spec flaky.
 */
test('a spec without mockBackend gets the stubbed account, not the real one', async ({ page }) => {
  await withNetRetry(page, () => page.goto('/'))
  // Only the stub knows this account; the real backend has no such credentials.
  const badge = page.getByRole('region', { name: 'Signed-in account' })
  await expect(badge.getByText(/ada@example\.com/)).toBeVisible()
})
