import { test, expect, type Page, withNetRetry } from './fixtures';

/**
 * Build-1: Refresh-button toast notifications + credentials-expired handling.
 *
 * The Refresh button in the sidebar should fire toast notifications directly.
 * Success: auto-dismiss after 5s. Error: sticky (no auto-dismiss). The full
 * progress modal remains accessible via "Details" link inside the toast.
 */

async function clickRefresh(page: Page) {
  await page.locator('aside button[title="Refresh conversation list"]').click();
}

test.describe('Refresh toast', () => {
  test('shows in-progress toast immediately on click', async ({ page, mockBackend }) => {
    await mockBackend({});
    await page.route('**/api/fetch/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          has_credentials: true,
          credentials_path: '/tmp/c.json',
          output_dir: '/tmp/conv',
          existing_count: 5,
          credentials_age_days: 3,
        }),
      });
    });

    let sseClosed = false;
    await page.route('**/api/fetch/refresh*', async (route) => {
      sseClosed = true;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"start","message":"Fetching conversation list...","current":0,"total":0}\n\n' +
          'data: {"type":"complete","message":"Fetched 0 conversations successfully.","current":0,"total":0}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    await expect(page.locator('[data-sonner-toast]').first()).toBeVisible({ timeout: 5000 });
    expect(sseClosed).toBe(true);
  });

  test('shows sticky error toast on session-expired SSE event', async ({ page, mockBackend }) => {
    await mockBackend({});
    await page.route('**/api/fetch/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          has_credentials: true,
          credentials_path: '/tmp/c.json',
          output_dir: '/tmp/conv',
          existing_count: 5,
          credentials_age_days: 60,
        }),
      });
    });

    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"error","message":"Session expired or Cloudflare-blocked. Re-run claude-explorer capture to refresh credentials."}\n\n',
      });
    });

    // The page clock moves the toast's timer forward exactly (2026-10-24).
    await page.clock.install();
    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const errorToast = page.locator('[data-sonner-toast][data-type="error"]').first();
    await expect(errorToast).toBeVisible({ timeout: 5000 });
    await expect(errorToast).toContainText(/Session expired|Cloudflare/i);

    await page.clock.runFor(6000);
    await expect(errorToast).toBeVisible();
  });

  test('toast Details link opens full progress dialog', async ({ page, mockBackend }) => {
    await mockBackend({});
    await page.route('**/api/fetch/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          has_credentials: true,
          credentials_path: '/tmp/c.json',
          output_dir: '/tmp/conv',
          existing_count: 5,
          credentials_age_days: 3,
        }),
      });
    });

    await page.route('**/api/fetch/refresh*', async (route) => {
      // Emit start + progress + complete so the toast lands in a success
      // state (still carries the Details action). If we leave the stream
      // open without complete, sonner advances to a Retry-labelled error
      // toast and the click target text changes.
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"start","message":"Fetching conversation list...","current":0,"total":3}\n\n' +
          'data: {"type":"progress","message":"Fetching: alpha","current":1,"total":3}\n\n' +
          'data: {"type":"complete","message":"Fetched 3 conversations.","current":3,"total":3}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const toast = page.locator('[data-sonner-toast]').first();
    await expect(toast).toBeVisible({ timeout: 5000 });

    // Sonner renders the action label inside the toast root with
    // `data-action` and `data-button` attributes (see sonner index.mjs
    // line 813-815). The accessible-name path (getByRole) is unreliable
    // for *loading* toasts — error/success/default toasts work fine —
    // so target the action element by its sonner-stable data attribute.
    const detailsButton = toast.locator('[data-button][data-action]');
    await expect(detailsButton).toBeVisible({ timeout: 5000 });
    await expect(detailsButton).toHaveText(/Details/i);
    await detailsButton.click();

    await expect(
      page.getByRole('heading', { name: /Fetch Claude Desktop Conversations/i }),
    ).toBeVisible({ timeout: 10_000 });
  });
});
