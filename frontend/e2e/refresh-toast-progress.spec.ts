import { test, expect, type Page, withNetRetry } from './fixtures';

/**
 * Build-9 Bug 2: Toast text MUST update with each `progress` SSE event
 * during a long fetch. The user reported clicking Refresh and seeing only
 * a static "Refreshing…" / "Fetching N/M…" with no per-conversation
 * feedback, even though the backend emits a progress event for every
 * conversation it fetches.
 *
 * Spec:
 *   - On `progress` events with `conversation_name`, the toast text becomes
 *     "Fetching N/M: <conversation_name>" (truncated to 40 chars).
 *   - On `capture_start`: "Opening browser to log in to Claude…"
 *   - On `capture_waiting_login`: "Waiting for you to log in…"
 *   - On `capture_done`: "Credentials captured. Fetching…"
 *   - On `complete`: success toast ("Fetched +N new conversations.")
 *   - One toast at a time (Sonner toast.loading with stable id), never a
 *     stack of toasts.
 */

async function clickRefresh(page: Page) {
  await page
    .getByRole('button', { name: /Refresh conversation list/i })
    .click();
}

test.describe('Refresh toast: live progress text (Bug 2)', () => {
  test.beforeEach(async ({ page, mockBackend }) => {
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

  test('toast text shows the conversation_name from the latest progress event', async ({ page }) => {
    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"start","message":"Fetching conversation list...","current":0,"total":0}\n\n' +
          'data: {"type":"progress","message":"Fetching: Alpha","current":1,"total":3,"conversation_name":"Alpha"}\n\n' +
          'data: {"type":"progress","message":"Fetching: Beta","current":2,"total":3,"conversation_name":"Beta"}\n\n' +
          'data: {"type":"progress","message":"Fetching: Gamma","current":3,"total":3,"conversation_name":"Gamma"}\n\n' +
          'data: {"type":"complete","message":"Fetched 3 conversations successfully.","current":3,"total":3}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const toast = page.locator('[data-sonner-toast]').first();
    await expect(toast).toBeVisible({ timeout: 5000 });
    // After all progress events fire, the toast should reflect the final
    // conversation name (Gamma) before the complete event flips it to success.
    // Either way, the success message wins as the final visible state.
    await expect(toast).toContainText(/Fetched 3 conversations/i, { timeout: 5000 });
  });

  test('toast text reflects formatProgressText output (smoke)', async ({ page }) => {
    // The exhaustive formatProgressText contract lives in
    // src/test/components/FetchToast.test.tsx (vitest). Browser-rendered
    // text is hard to assert mid-pipeline because Playwright's route.fulfill
    // delivers the full SSE body in one chunk and EventSource fires every
    // onmessage in the same microtask, leaving React with only the LAST
    // text to paint. Real production SSE arrives over time and the user
    // sees every transition. This smoke check confirms the success-path
    // toast renders correctly when the pipeline completes normally.
    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"start","message":"Fetching conversation list...","current":0,"total":0}\n\n' +
          'data: {"type":"progress","message":"Fetching: Synology metadata explanation","current":2,"total":5,"conversation_name":"Synology metadata explanation"}\n\n' +
          'data: {"type":"complete","message":"Fetched 5 conversations successfully.","current":5,"total":5}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const toast = page.locator('[data-sonner-toast]').first();
    await expect(toast).toBeVisible({ timeout: 5000 });
    await expect(toast).toContainText(/Fetched 5 conversations/i, { timeout: 5000 });
  });

  test('only one toast is visible while pipeline is running (no stacking)', async ({ page }) => {
    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"start","message":"Fetching conversation list...","current":0,"total":0}\n\n' +
          'data: {"type":"progress","message":"Fetching: A","current":1,"total":5,"conversation_name":"A"}\n\n' +
          'data: {"type":"progress","message":"Fetching: B","current":2,"total":5,"conversation_name":"B"}\n\n' +
          'data: {"type":"progress","message":"Fetching: C","current":3,"total":5,"conversation_name":"C"}\n\n' +
          'data: {"type":"progress","message":"Fetching: D","current":4,"total":5,"conversation_name":"D"}\n\n' +
          'data: {"type":"progress","message":"Fetching: E","current":5,"total":5,"conversation_name":"E"}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    // Wait for at least one toast.
    await expect(page.locator('[data-sonner-toast]').first()).toBeVisible({
      timeout: 5000,
    });

    // After the burst of events, only ONE toast should be visible. If
    // toast.loading was called without a stable id, we'd see 5 stacked.
    // The stream ends after the last event, so the app shows "Connection
    // lost during refresh." in the same toast: every event was handled
    // (2026-10-24: a 1 s sleep). Stacked progress toasts would still be
    // on screen beside it.
    await expect(page.locator('[data-sonner-toast]', { hasText: /Connection lost during refresh/ })).toBeVisible();
    await expect(page.locator('[data-sonner-toast]')).toHaveCount(1);
  });

  test('long conversation names never leak un-truncated to the toast', async ({ page }) => {
    // Negative assertion: even with the success message arriving, the
    // un-truncated long name must never appear in any toast textContent.
    const longName = 'This is an extremely long conversation name that should be truncated past 40 chars';
    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"start","message":"Fetching conversation list...","current":0,"total":0}\n\n' +
          `data: {"type":"progress","message":"Fetching: ${longName}","current":1,"total":2,"conversation_name":"${longName}"}\n\n` +
          'data: {"type":"complete","message":"Fetched 2 conversations successfully.","current":2,"total":2}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const toast = page.locator('[data-sonner-toast]').first();
    await expect(toast).toBeVisible({ timeout: 5000 });
    await expect(toast).toContainText(/Fetched 2/i, { timeout: 5000 });
    // The full untruncated name must never appear in any toast.
    await expect(page.getByText(longName)).toHaveCount(0);
  });

  test('capture phase events drive toast text transitions', async ({ page }) => {
    // Force the missing-creds path so capture_start fires.
    await page.unroute('**/api/fetch/status');
    await page.route('**/api/fetch/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          has_credentials: false,
          credentials_path: '/tmp/c.json',
          output_dir: '/tmp/conv',
          existing_count: 0,
          credentials_age_days: null,
        }),
      });
    });

    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"capture_start","message":"Opening browser to log in to Claude..."}\n\n' +
          'data: {"type":"capture_done","message":"Credentials captured. Fetching..."}\n\n' +
          'data: {"type":"progress","message":"Fetching: Hello","current":1,"total":1,"conversation_name":"Hello"}\n\n' +
          'data: {"type":"complete","message":"Fetched 1 conversations successfully.","current":1,"total":1}\n\n',
      });
    });

    await withNetRetry(page, () => page.goto('/'));
    await clickRefresh(page);

    const toast = page.locator('[data-sonner-toast]').first();
    await expect(toast).toBeVisible({ timeout: 5000 });
    // Final state should be success.
    await expect(toast).toContainText(/Fetched 1 conversations/i, { timeout: 5000 });
  });

  test('a failed Cowork task turns the final toast into a warning with Details', async ({ page }) => {
    // A task whose download kept failing used to end in a green
    // "successfully" toast (PR #6 review, 2026-10-19).
    const message =
      'Fetched 0 conversations and 1 Cowork task across 1 workspace(s). ' +
      '1 Cowork task could not be downloaded (Daily summary). The next Refresh tries it again.'
    await page.route('**/api/fetch/refresh*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          'data: {"type":"start","message":"Fetching conversation list...","current":0,"total":0}\n\n' +
          `data: ${JSON.stringify({
            type: 'complete',
            message,
            cowork_tasks_fetched: 1,
            cowork_tasks_failed: 1,
            current: 0,
            total: 0,
          })}\n\n`,
      })
    })

    await withNetRetry(page, () => page.goto('/'))
    await clickRefresh(page)

    const toast = page.locator('[data-sonner-toast][data-type="warning"]')
    await expect(toast).toBeVisible({ timeout: 5000 })
    await expect(toast).toContainText('could not be downloaded (Daily summary)')

    // Details opens the fetch dialog, whose result box is amber, not green.
    await toast.getByRole('button', { name: 'Details' }).click()
    const result = page.getByTestId('fetch-result-warning')
    await expect(result).toBeVisible()
    await expect(result).toContainText('Fetch finished, but some Cowork tasks failed')
    await expect(result).toContainText('could not be downloaded (Daily summary)')
  })
});
