import { test, expect, type Page, withNetRetry } from './fixtures';

/**
 * Bug C: Error toasts must remain visible long enough for the user to
 * actually read them.
 *
 * Real-world failure mode reported: the user clicked Refresh and an error
 * toast appeared and disappeared so quickly they didn't see it. Sonner's
 * default `duration` for error toasts can be as short as 4s in some
 * configs; we want a minimum 8s for non-sticky errors and Infinity for
 * sticky/terminal errors.
 *
 * SSE error events now carry a `kind` field:
 *   AUTH      -> sticky toast, no Retry button
 *   TRANSIENT -> >=8s toast with a Retry button
 *   TERMINAL  -> sticky toast
 *
 * The legacy event shape (no `kind`) is treated as TERMINAL (sticky) for
 * backwards compat with Build-1's tests.
 */

async function clickRefresh(page: Page) {
  await page.locator('aside button[title="Refresh conversation list"]').click();
}

test.describe('Refresh error toast duration (Bug C)', () => {
  test.beforeEach(async ({ page, mockBackend }) => {
    // The page clock: a test moves the toasts' timers forward exactly,
    // instead of waiting 5-10 s of real time (2026-10-24).
    await page.clock.install();
    await mockBackend({});
    await page.route('**/api/fetch/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          has_credentials: true,
          credentials_path: '/tmp/c.json',
          output_dir: '/tmp/conv',
          existing_count: 0,
          credentials_age_days: 2,
        }),
      });
    });
  });

  test('TRANSIENT error toast is still visible 5s after appearing', async ({
    page,
  }) => {
    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"error","kind":"TRANSIENT","retryable":true,"message":"Network problem reaching claude.ai. Retry?"}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const toast = page.locator('[data-sonner-toast][data-type="error"]').first();
    await expect(toast).toBeVisible({ timeout: 3000 });
    await expect(toast).toContainText(/Network problem/i);

    // After 5s the toast must still be visible (not auto-dismissed).
    await page.clock.runFor(5000);
    await expect(toast).toBeVisible();
  });

  test('TRANSIENT error toast exposes a Retry action', async ({ page }) => {
    let calls = 0;
    await page.route('**/api/fetch/refresh*', async (route) => {
      calls += 1;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"error","kind":"TRANSIENT","retryable":true,"message":"Network problem reaching claude.ai. Retry?"}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const toast = page.locator('[data-sonner-toast][data-type="error"]').first();
    await expect(toast).toBeVisible({ timeout: 3000 });
    const retry = toast.getByRole('button', { name: /Retry/i });
    await expect(retry).toBeVisible();
    await retry.click();

    // Clicking Retry must re-issue the refresh.
    await expect.poll(() => calls).toBeGreaterThanOrEqual(2);
  });

  test('TERMINAL error toast is still visible 10s after appearing (sticky)', async ({
    page,
  }) => {
    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"error","kind":"TERMINAL","retryable":false,"message":"Fetch failed: schema mismatch"}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const toast = page.locator('[data-sonner-toast][data-type="error"]').first();
    await expect(toast).toBeVisible({ timeout: 3000 });

    await page.clock.runFor(10000);
    await expect(toast).toBeVisible();
  });

  test('Legacy error event (no kind) is treated as TERMINAL and stays visible 10s', async ({
    page,
  }) => {
    // Backwards compat: errors that lack the `kind` field (Build-1/Build-9
    // shape) must be treated as TERMINAL/sticky so the user never sees a
    // <5s toast they can't read.
    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"error","message":"Fetch failed: weird raw error"}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const toast = page.locator('[data-sonner-toast][data-type="error"]').first();
    await expect(toast).toBeVisible({ timeout: 3000 });

    await page.clock.runFor(10000);
    await expect(toast).toBeVisible();
  });

  test('AUTH error toast is sticky (no Retry, stays visible)', async ({
    page,
  }) => {
    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"error","kind":"AUTH","retryable":false,"message":"Session expired or Cloudflare-blocked. Re-run claude-explorer capture to refresh credentials."}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const toast = page.locator('[data-sonner-toast][data-type="error"]').first();
    await expect(toast).toBeVisible({ timeout: 3000 });
    await expect(toast).toContainText(/Session expired/i);

    // Sticky: still there after 8s.
    await page.clock.runFor(8000);
    await expect(toast).toBeVisible();
  });
});
