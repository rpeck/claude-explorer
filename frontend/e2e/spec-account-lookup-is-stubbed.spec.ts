import { test, expect, withNetRetry } from './fixtures'

/**
 * PR #6 review (2026-10-19): a spec that does not use ``mockBackend`` must
 * still never reach the real ``/api/account``. That route makes the backend
 * call claude.ai with the session key on disk, and a local run reuses the
 * developer's own server. The ``stubAccountLookup`` auto fixture in
 * fixtures.ts answers it in the browser instead.
 */
test('a spec without mockBackend gets the stubbed account, not the real one', async ({ page }) => {
  const answered = page.waitForResponse((r) => r.url().endsWith('/api/account'))
  await withNetRetry(page, () => page.goto('/'))
  const response = await answered
  // Only the stub answers with this fixture account; the real backend has
  // no such credentials.
  expect(await response.json()).toMatchObject({ email: 'ada@example.com', authenticated: true })
})
