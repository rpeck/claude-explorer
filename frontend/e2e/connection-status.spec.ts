import { test, expect, withNetRetry, expectNetworkError, watchConnectionDialog, connectionDialogSeen } from './fixtures';

// M5.5: converted to `./fixtures`. Tests 1-7 deliberately block all
// `/api/**` to exercise the offline-backend UX, so they don't need
// mockBackend at all. Test 8 ("dialog closes automatically") needs the
// mocked backend to be reachable AFTER the initial block lifts —
// previously it used `route.continue()`, which would leak to the live
// backend on :8765. With mockBackend installed first, the per-test
// `/api` route can use `route.fallback()` to delegate to the fixture
// mocks once `blockRequests=false`.
//
// V1 polish (2026-05-09): the dialog now opens only once retryCount≥2
// (suppresses dialog flash on a single transient blip) and the first
// retry waits 4s instead of 2s (lets a healthy backend's --reload
// cold-start finish). Schedule: 4s, 8s, 10s, 10s, 10s = 42s to terminal.

test.describe('Connection Status', () => {
  test('shows connecting dialog when backend is unavailable', async ({ page, consoleAssertions }) => {
    // §5.15: deliberately aborting every /api/** with connectionrefused
    // makes Chromium log net::ERR_CONNECTION_REFUSED at the network
    // layer. That noise is expected for this offline-backend test; every
    // other console error or warning still fails the test.
    expectNetworkError(consoleAssertions, 'connectionrefused')
    // Block all API requests to simulate backend down
    await page.route('**/api/**', (route) => {
      route.abort('connectionrefused');
    });

    await withNetRetry(page, () => page.goto('/'));

    // Dialog opens at retry 2 (4s + initial check). 15s timeout > the 12s
    // worst case (4s first retry + check timeout slack).
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Connecting to Backend')).toBeVisible();
    await expect(page.getByText(/Attempt \d+ of \d+/)).toBeVisible();
  });

  test('shows retry counter incrementing', async ({ page, consoleAssertions }) => {
    expectNetworkError(consoleAssertions, 'connectionrefused')
    // Block API requests
    await page.route('**/api/**', (route) => {
      route.abort('connectionrefused');
    });

    await withNetRetry(page, () => page.goto('/'));

    // Wait for dialog (opens at retry 2 — see V1 polish note above).
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15000 });

    // Dialog opens showing "Attempt 2" (the first attempt visible to user
    // under the V1 dialog-suppression policy).
    await expect(page.getByText('Attempt 2 of 5')).toBeVisible();

    // Wait for retry 3 (8s after retry 2).
    await expect(page.getByText('Attempt 3 of 5')).toBeVisible({ timeout: 10000 });
  });

  test('Retry Now button triggers immediate retry', async ({ page, consoleAssertions }) => {
    expectNetworkError(consoleAssertions, 'connectionrefused')
    let requestCount = 0;

    // Block API requests and count them
    await page.route('**/api/config', (route) => {
      requestCount++;
      route.abort('connectionrefused');
    });

    await withNetRetry(page, () => page.goto('/'));

    // Wait for dialog (opens at retry 2 — V1 polish).
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15000 });

    // Get current request count
    const initialCount = requestCount;

    // Click Retry Now
    await page.getByRole('button', { name: 'Retry Now' }).click();

    // Should trigger a new request immediately
    await expect.poll(() => requestCount).toBeGreaterThan(initialCount);
  });

  test('shows Connection Failed dialog after max retries', async ({ page, consoleAssertions }) => {
    expectNetworkError(consoleAssertions, 'connectionrefused')
    // Block API requests
    await page.route('**/api/**', (route) => {
      route.abort('connectionrefused');
    });

    await withNetRetry(page, () => page.goto('/'));

    // Wait for dialog (opens at retry 2 — V1 polish).
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15000 });

    // Wait for all 5 retries to exhaust. Schedule: 4 + 8 + 10 + 10 + 10 = 42s.
    // Plus initial check + 5x 5s API timeout slack. Use 60s timeout.
    await expect(page.getByText('Connection Failed')).toBeVisible({ timeout: 60000 });

    // Should show the failure message
    await expect(page.getByText(/Unable to connect.*after 5 attempts/)).toBeVisible();

    // Should show the help text
    await expect(page.getByText('claude-explorer serve')).toBeVisible();

    // Should have both Dismiss and Try Again buttons
    await expect(page.getByRole('button', { name: 'Dismiss' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Try Again' })).toBeVisible();
  });

  test('Try Again button restarts retry process', async ({ page, consoleAssertions }) => {
    expectNetworkError(consoleAssertions, 'connectionrefused')
    // Block API requests
    await page.route('**/api/**', (route) => {
      route.abort('connectionrefused');
    });

    await withNetRetry(page, () => page.goto('/'));

    // Wait for Connection Failed (V1 polish: 60s for 4+8+10+10+10 = 42s schedule).
    await expect(page.getByText('Connection Failed')).toBeVisible({ timeout: 60000 });

    // Click Try Again. handleReconnect() clears showDialog, so the
    // dialog closes briefly and re-opens when retryCount reaches 2 again
    // (V1 polish: dialog suppressed until retry≥2). Wait up to 15s for
    // the re-open (initial check + 4s first retry).
    await page.getByRole('button', { name: 'Try Again' }).click();
    await expect(page.getByText('Connecting to Backend')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/Attempt [23] of 5/)).toBeVisible();
  });

  test('Dismiss button closes the dialog', async ({ page, consoleAssertions }) => {
    expectNetworkError(consoleAssertions, 'connectionrefused')
    // Block API requests
    await page.route('**/api/**', (route) => {
      route.abort('connectionrefused');
    });

    await withNetRetry(page, () => page.goto('/'));

    // Wait for Connection Failed (V1 polish: 60s for the 42s retry schedule).
    await expect(page.getByText('Connection Failed')).toBeVisible({ timeout: 60000 });

    // Click Dismiss
    await page.getByRole('button', { name: 'Dismiss' }).click();

    // Dialog should close
    await expect(page.getByRole('dialog')).not.toBeVisible();
  });

  test('dialog closes automatically when backend becomes available', async ({ page, mockBackend, consoleAssertions }) => {
    // The pre-recovery block phase aborts with connectionrefused, which
    // Chromium logs at the network layer. Allowlist just that shape.
    expectNetworkError(consoleAssertions, 'connectionrefused')
    // Install the mocked backend FIRST so its routes exist when we lift
    // the per-test block. Once `blockRequests=false`, the per-test
    // `**/api/**` handler falls through (via `route.fallback()`) to the
    // mockBackend defaults — never to the live :8765 backend.
    await mockBackend({});

    let blockRequests = true;

    // Initially block API requests. Registered AFTER mockBackend so this
    // handler runs first (LIFO); when unblocked, falls through.
    await page.route('**/api/**', async (route) => {
      if (blockRequests) {
        await route.abort('connectionrefused');
      } else {
        await route.fallback();
      }
    });

    await withNetRetry(page, () => page.goto('/'));

    // Wait for dialog to appear (V1 polish: opens at retry 2, 4s+ wait).
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Connecting to Backend')).toBeVisible();

    // Unblock requests (simulate backend coming up)
    blockRequests = false;

    // Click Retry Now to trigger reconnection
    await page.getByRole('button', { name: 'Retry Now' }).click();

    // Dialog should close when connection succeeds
    await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 10000 });

    // App should load normally
    await expect(page.getByText('Claude Explorer')).toBeVisible();
  });

  test('shows spinning icon during connection attempts', async ({ page, consoleAssertions }) => {
    expectNetworkError(consoleAssertions, 'connectionrefused')
    // Block API requests
    await page.route('**/api/**', (route) => {
      route.abort('connectionrefused');
    });

    await withNetRetry(page, () => page.goto('/'));

    // Wait for dialog (V1 polish: opens at retry 2, 4s+ wait).
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15000 });

    // Should show spinning refresh icon (has animate-spin class)
    const spinningIcon = page.locator('.animate-spin');
    await expect(spinningIcon).toBeVisible();
  });

  // V1 polish (2026-05-09): the three new behaviors get explicit
  // regression tests so a future refactor can't quietly drop them.

  test('does NOT show "Last error" while still connecting (V1 polish)', async ({ page, consoleAssertions }) => {
    expectNetworkError(consoleAssertions, 'connectionrefused')
    // Block API to force `connecting` state with a recorded lastError.
    await page.route('**/api/**', (route) => {
      route.abort('connectionrefused');
    });

    await withNetRetry(page, () => page.goto('/'));

    // Dialog opens at retry 2 — by then lastError is set ("Failed to fetch")
    // but we should NOT render the red "Last error: …" line; the spinner
    // already conveys "we're trying", so a red error line on top of an
    // active retry is the user's UGH.
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Connecting to Backend')).toBeVisible();
    await expect(page.getByText(/Last error/)).toHaveCount(0);
  });

  test('first retry waits ~4 seconds (V1 polish)', async ({ page, consoleAssertions }) => {
    expectNetworkError(consoleAssertions, 'connectionrefused')
    const requestTimes: number[] = [];
    await page.route('**/api/config', (route) => {
      requestTimes.push(Date.now());
      route.abort('connectionrefused');
    });

    await withNetRetry(page, () => page.goto('/'));

    // The previous shape (poll for length ≥ 3 then read gaps) returned
    // immediately after the StrictMode-doubled mount fired ~2-4 nearly
    // simultaneous requests, BEFORE the 4s scheduled retry got a chance
    // to fire. That made maxGap = ~30ms regardless of the production
    // schedule, defeating the regression check. (Baseline 2026-06-01.)
    //
    // The `/api/config` endpoint is hit by TWO independent retry
    // schedules: ConnectionStatus's own loop AND react-query's default
    // for the `['config']` query (`queryClient.ts:42`, 1s/2s/4s/...).
    // Observed timestamps after 8s wall-clock (one representative run):
    //
    //   t:    [0, 4, 5, 40,  1027, 3031, 4029, 4033, 7035]
    //   gaps: [4, 1, 35, 1027, 2004,  998,   4, 3002]
    //
    // The 4s ConnectionStatus retry shows as the 3002ms gap at the end
    // of that 8s window; with only 5-6s of wall-clock we instead lock
    // in the 2004ms react-query gap and miss the 4s one. Wait ≥ 7s so
    // the 4s ConnectionStatus retry has landed AND the next 2s
    // react-query backoff has put a `/api/config` after it, surfacing
    // the ≥ 3s gap.
    await expect.poll(() => requestTimes.length, { timeout: 10000 }).toBeGreaterThanOrEqual(1);
    const first = requestTimes[0];
    await expect
      .poll(() => Date.now() - first, { timeout: 12000, intervals: [500, 500, 1000] })
      .toBeGreaterThanOrEqual(7500);

    // Find the largest gap between consecutive timestamps. Under the
    // 2s baseline this is ~2000ms; under the 4s V1-polish schedule it
    // should be ~4000ms (actually surfaces as ~3000ms because the
    // react-query retries land in between).
    //
    // The threshold sits halfway between the two schedules (2500ms), not
    // at 3000ms. The 4s schedule measures ~3000ms by itself (3002ms in the
    // run above), so a 3000ms threshold failed on a few ms of jitter: the
    // first attempt failed in 3 of 4 main runs (2026-10-13/14), and PR #6
    // measured 2978-2984ms on all three attempts. 2500ms still rejects the
    // pre-polish 2s schedule (~2000ms) with the same 500ms margin.
    expect(requestTimes.length).toBeGreaterThanOrEqual(2);
    const gaps = requestTimes.slice(1).map((t, i) => t - requestTimes[i]);
    const maxGap = Math.max(...gaps);
    expect(maxGap).toBeGreaterThanOrEqual(2500);
  });

  test('does NOT flash dialog after a single transient (V1 polish)', async ({ page, mockBackend, consoleAssertions }) => {
    // §5.15: the test deliberately aborts the first /api/config with
    // connectionrefused; Chromium logs the network-layer line. This
    // wasn't catching in the original baseline because the abort fired
    // before the App tree had wired up its console listener, but it
    // surfaced as a flake on retry runs (2026-06-01). Allowlist the
    // expected shape; the actual contract (no dialog ever visible) is
    // still asserted below.
    expectNetworkError(consoleAssertions, 'connectionrefused')
    // mockBackend installed first so the app loads.
    await mockBackend({});

    // Every /api/config call fails until the first attempt has failed:
    // ConnectionStatus and useConfig send the same request, so failing
    // only the first call could hit useConfig's and leave ConnectionStatus
    // untested (it did before 2026-10-24).
    let failing = true;
    let calls = 0;
    await page.route('**/api/config', async (route) => {
      calls += 1;
      if (failing) {
        await route.abort('connectionrefused');
        return;
      }
      await route.fallback();
    });
    // A MutationObserver records every appearance of the dialog, and the
    // page clock runs the retry timers exactly. Before, the test waited
    // 6 s of real time and then looked once.
    await watchConnectionDialog(page);
    await page.clock.install();

    await withNetRetry(page, () => page.goto('/'));

    // The first attempt failed (a DOM signal of ConnectionStatus) ...
    await expect(page.locator('html[data-connection-retries="1"]')).toHaveCount(1);
    failing = false;
    // ... and the retry 4 s later succeeds, before a second failure could
    // open the dialog. The dialog must NEVER become visible.
    await page.clock.runFor(4000);
    await expect(page.locator('html[data-connection-state="connected"]')).toHaveCount(1);
    expect(calls).toBeGreaterThan(1);
    await page.clock.runFor(45_000); // past every retry window
    expect(await connectionDialogSeen(page)).toEqual([]);
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });
});
