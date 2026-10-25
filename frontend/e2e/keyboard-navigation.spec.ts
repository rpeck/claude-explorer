import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry, waitForKeyboardShortcuts } from './fixtures';
import type { Page } from './fixtures';
import type { ConversationSummary, ConversationDetail } from '../src/lib/types';

/**
 * Keyboard / accessibility coverage. Drives the SPA off a small TS-built
 * fixture set so the tests no longer depend on a live backend or on
 * fixture-mode JSON files on disk.
 *
 * The TLS fixture below mirrors the long TLS conversation that the old
 * fixture-mode backend used to serve, so the "Enter selects focused
 * conversation" test still has a deterministic row to focus by name.
 */

const TLS_TITLE = 'Phase 5 fixture: TLS handshakes (long)';
const TLS_UUID = '0f415a45-9c62-8671-d4ad-53b84acb7e1a';

function buildFixtures(): {
  conversations: ConversationSummary[];
  details: Record<string, ConversationDetail>;
} {
  const tlsSummary = makeSummary({
    uuid: TLS_UUID,
    name: TLS_TITLE,
    message_count: 30,
    human_message_count: 15,
  });
  const tlsMessages = [];
  let prev: string | null = null;
  for (let i = 0; i < 30; i++) {
    const uuid = `tls-${i.toString().padStart(2, '0')}`;
    const sender = i % 2 === 0 ? 'human' : 'assistant';
    const text =
      i === 0
        ? "Hi! Let's talk about TLS. NEEDLE_HANDSHAKE"
        : sender === 'human'
          ? `Follow-up question ${i} about TLS handshakes.`
          : `Answer ${i}: more on TLS handshakes.`;
    tlsMessages.push(
      makeMessage({ uuid, sender, text, parent_message_uuid: prev }),
    );
    prev = uuid;
  }
  const tlsDetail = makeDetail(tlsSummary, tlsMessages);
  return {
    conversations: [tlsSummary],
    details: { [TLS_UUID]: tlsDetail },
  };
}

test.describe('Keyboard Navigation', () => {
  test.beforeEach(async ({ page, mockBackend }) => {
    const { conversations, details } = buildFixtures();
    await mockBackend({ conversations, details });
    await withNetRetry(page, () => page.goto('/'));
    // Wait for conversations to load
    await expect(page.locator('[role="button"]').first()).toBeVisible({
      timeout: 10000,
    });
  });

  test('pressing ? opens help modal', async ({ page }) => {
    // Click on the main content area first to ensure focus (not in an input)
    await page.locator('main').click();
    // Type ? character directly using keyboard.type
    await page.keyboard.type('?');

    // Help modal should be visible
    await expect(page.locator('text=Keyboard Shortcuts')).toBeVisible({ timeout: 5000 });
  });

  test('help modal shows current keyboard mode', async ({ page }) => {
    await page.locator('main').click();
    await page.keyboard.type('?');

    // Should show Emacs mode by default
    await expect(page.locator('text=Emacs Mode')).toBeVisible();
  });

  test('help modal can be closed', async ({ page }) => {
    await page.locator('main').click();
    await page.keyboard.type('?');
    await expect(page.locator('text=Keyboard Shortcuts')).toBeVisible();

    // Click close button
    await page.click('button:has-text("Close")');

    // Modal should be gone
    await expect(page.locator('text=Keyboard Shortcuts')).not.toBeVisible();
  });

  test('help modal links to settings', async ({ page }) => {
    await page.locator('main').click();
    await page.keyboard.type('?');

    // Click the settings link
    await page.click('a:has-text("Change keyboard mode")');

    // Should navigate to settings
    await expect(page).toHaveURL(/\/settings/);
  });

  test('Tab navigates through interactive elements', async ({ page }) => {
    // Click body first to ensure focus starts somewhere predictable.
    await page.locator('body').click({ position: { x: 1, y: 1 } });
    await page.keyboard.press('Tab');

    // Should focus on an interactive element (input/button/anchor).
    const focused = await page.evaluate(() => document.activeElement?.tagName);
    expect(['INPUT', 'BUTTON', 'A']).toContain(focused);
  });

  test('keyboard does not trigger in input fields', async ({ page }) => {
    // Focus the sidebar search input by exact placeholder (avoids
    // ambiguity with the SearchPanel input which uses "Search messages...").
    const searchInput = page.getByPlaceholder('Search titles and projects');
    await searchInput.focus();

    // Type '?' - should go into input, not open help modal
    await page.keyboard.type('?');

    // Input should contain '?'
    await expect(searchInput).toHaveValue('?');

    // Help modal should NOT be visible
    await expect(page.locator('text=Keyboard Shortcuts')).not.toBeVisible();
  });

  test('Enter selects focused conversation', async ({ page }) => {
    // Click on conversation list to focus a row. In fixture mode the
    // first deterministic row to look for is the long TLS conversation.
    const row = page.getByText(TLS_TITLE);
    await expect(row).toBeVisible({ timeout: 10000 });
    await row.focus();

    // Press Enter
    await page.keyboard.press('Enter');

    // URL should update to show conversation
    await expect(page).toHaveURL(/\/conversations\/[a-f0-9-]+/);
  });

  test('search input accepts text', async ({ page }) => {
    const searchInput = page.getByPlaceholder('Search titles and projects');

    // Type something
    await searchInput.fill('test query');
    expect(await searchInput.inputValue()).toBe('test query');

    // Clear manually
    await searchInput.fill('');
    expect(await searchInput.inputValue()).toBe('');
  });

  test('clicking on sidebar search focuses input', async ({ page }) => {
    const searchInput = page.getByPlaceholder('Search titles and projects');

    // Click on search input
    await searchInput.click();

    // Should be focused
    await expect(searchInput).toBeFocused();
  });
});

/**
 * Choose a keyboard mode in Settings, and wait until it is in effect and saved.
 *
 * Signals, not timers (2026-10-24): before, the setup waited a fixed 500 ms,
 * and the test pressed its first key before the listener existed.
 * - The shortcuts listen in every route, so the new mode shows on this page
 *   at once (localStorage is written in the same click).
 * - A real change also PATCHes /api/preferences; wait for it, so that the
 *   next navigation does not cut it off. Clicking the active mode sends none.
 */
async function chooseKeyboardMode(page: Page, mode: 'vim' | 'emacs'): Promise<void> {
  await withNetRetry(page, () => page.goto('/settings'));
  const label = page.locator(`label:has-text("${mode === 'vim' ? 'Vim' : 'Emacs'}")`);
  await expect(label).toBeVisible({ timeout: 10000 });
  // The shortcuts listen (in some mode) before we read which mode is active.
  await expect(page.locator('html[data-keyboard-shortcuts]')).toHaveCount(1);
  const current = await page.locator('html').getAttribute('data-keyboard-shortcuts');
  const saved =
    current === mode
      ? null
      : page.waitForResponse((r) => r.url().includes('/api/preferences') && r.request().method() === 'PATCH');
  await label.click();
  await waitForKeyboardShortcuts(page, mode);
  if (saved) expect((await saved).ok()).toBe(true);
}

test.describe('Vim Mode', () => {
  test.beforeEach(async ({ page, mockBackend }) => {
    const { conversations, details } = buildFixtures();
    await mockBackend({ conversations, details });
    await chooseKeyboardMode(page, 'vim');
    // 'commit' returns before React mounts: the strictest start for the
    // test's first key. It must still pass, because it waits for the signal.
    await withNetRetry(page, () => page.goto('/', { waitUntil: 'commit' }));
    await waitForKeyboardShortcuts(page, 'vim');
  });

  test('help modal shows Vim shortcuts', async ({ page }) => {
    // Press ? to open help modal (Shift+/ on US keyboard)
    await page.locator('main').click();
    await page.keyboard.type('?');

    // Should show the help modal with Vim mode
    await expect(page.locator('text=Keyboard Shortcuts')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=Vim Mode')).toBeVisible();
    // Should show vim-specific keys. Multiple <kbd>j</kbd> tags appear
    // (one per binding row); .first() avoids the strict-mode violation.
    await expect(page.getByText('j', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('k', { exact: true }).first()).toBeVisible();
  });

  test('/ focuses search', async ({ page }) => {
    await page.keyboard.press('/');

    // Search input should be focused
    const searchInput = page.getByPlaceholder('Search titles and projects');
    await expect(searchInput).toBeFocused();
  });
});

test.describe('Emacs Mode', () => {
  test.beforeEach(async ({ page, mockBackend }) => {
    const { conversations, details } = buildFixtures();
    await mockBackend({ conversations, details });
    await chooseKeyboardMode(page, 'emacs');
    await withNetRetry(page, () => page.goto('/', { waitUntil: 'commit' }));
    await waitForKeyboardShortcuts(page, 'emacs');
  });

  test('help modal shows Emacs shortcuts', async ({ page }) => {
    // Press ? to open help modal (Shift+/ on US keyboard)
    await page.locator('main').click();
    await page.keyboard.type('?');

    // Should show the help modal with Emacs mode
    await expect(page.locator('text=Keyboard Shortcuts')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=Emacs Mode')).toBeVisible();
    // Should show emacs-specific keys (use first() since multiple Ctrl keys exist)
    await expect(page.getByText('Ctrl').first()).toBeVisible();
  });

  test('Ctrl+S focuses search', async ({ page }) => {
    await page.keyboard.press('Control+s');

    // Search input should be focused
    const searchInput = page.getByPlaceholder('Search titles and projects');
    await expect(searchInput).toBeFocused();
  });
});

test.describe('Accessibility', () => {
  test('page has proper heading structure', async ({ page, mockBackend }) => {
    const { conversations, details } = buildFixtures();
    await mockBackend({ conversations, details });
    await withNetRetry(page, () => page.goto('/'));

    // Wait for page to load
    await expect(page.getByRole('button', { name: /\d+ msgs/ }).first()).toBeVisible({
      timeout: 10000,
    });

    // Select a conversation
    await page.getByRole('button', { name: /\d+ msgs/ }).first().click();

    // Wait for conversation detail
    await page.waitForSelector('text=/^(You|Claude)$/');

    // Should have at least one heading
    const headings = await page.locator('h1, h2, h3, h4, h5, h6').count();
    expect(headings).toBeGreaterThan(0);
  });

  test('interactive elements are keyboard accessible', async ({ page, mockBackend }) => {
    const { conversations, details } = buildFixtures();
    await mockBackend({ conversations, details });
    await withNetRetry(page, () => page.goto('/'));

    // Wait for page to load
    await expect(page.getByRole('button', { name: /\d+ msgs/ }).first()).toBeVisible({
      timeout: 10000,
    });

    // All buttons should be tabbable
    const buttons = await page.locator('button').all();
    for (const button of buttons.slice(0, 5)) {
      // Should not have tabindex="-1" (unless it's intentionally hidden)
      const tabindex = await button.getAttribute('tabindex');
      if (tabindex !== '-1') {
        // Should be focusable
        await button.focus();
        await expect(button).toBeFocused();
      }
    }
  });

  test('conversation list items have accessible names', async ({ page, mockBackend }) => {
    const { conversations, details } = buildFixtures();
    await mockBackend({ conversations, details });
    await withNetRetry(page, () => page.goto('/'));

    // Wait for conversations
    await expect(page.getByRole('button', { name: /\d+ msgs/ }).first()).toBeVisible({
      timeout: 10000,
    });

    // Each conversation item should have meaningful text
    const conversationItems = page.getByRole('button', { name: /\d+ msgs/ });
    const count = await conversationItems.count();

    for (let i = 0; i < Math.min(count, 5); i++) {
      const text = await conversationItems.nth(i).textContent();
      expect(text?.length).toBeGreaterThan(5); // Should have meaningful content
    }
  });

  test('form inputs have associated labels or placeholders', async ({ page, mockBackend }) => {
    // 2026-05-18 council audit: the prior assertion called
    // `getByPlaceholder('Search titles and projects')` then asserted
    // `toBeTruthy()` on the result of `getAttribute('placeholder')`.
    // That second assertion was tautological: `getByPlaceholder()`
    // already throws if the placeholder doesn't exactly match, so the
    // `toBeTruthy()` would never see a falsy value.
    //
    // Replaced with an exact-value assertion AND a check that the
    // input is reachable by keyboard (its accessible name is the
    // placeholder, by convention; this proves a screen reader user
    // hears the same label the visual user sees).
    await mockBackend({});
    await withNetRetry(page, () => page.goto('/'));

    const searchInput = page.getByPlaceholder('Search titles and projects');
    await expect(searchInput).toBeVisible();
    // Pin the EXACT placeholder copy. A regression that renamed the
    // placeholder ("Search conversations…" was the legacy copy) would
    // fail here loudly instead of silently passing.
    await expect(searchInput).toHaveAttribute(
      'placeholder',
      'Search titles and projects',
    );
    // Element is focusable — keyboard users can reach it.
    await searchInput.focus();
    await expect(searchInput).toBeFocused();
  });

  // B9 — Cmd+R (or Ctrl+R) is intercepted: it triggers a React Query
  // invalidation of the conversation list rather than a full browser
  // reload. The article calls this out (line ~154) because losing the
  // single-page state on every refresh is the classic SPA gotcha.
  //
  // V1 update (Stream C, 2026-05-11): Cmd+R is no longer a plain
  // queryClient.invalidateQueries() — it now fires the same Build-9
  // capture+fetch SSE pipeline as the sidebar Refresh button (the
  // article promised "the same one the sidebar button triggers", and
  // the binding now matches the promise). The B9 test was updated to
  // pin the new behavior: Cmd+R hits /api/fetch/refresh, the SSE
  // completes, and the list-invalidation that follows still re-fetches
  // /api/conversations. The no-browser-reload sentinel still holds.
  // Detailed behavior is covered by cmd-r-refresh-pipeline.spec.ts.
  test('Cmd+R invalidates the conversation list (via the refresh pipeline) without a browser reload (B9)', async ({ page, mockBackend }) => {
    let listRequestCount = 0
    await mockBackend({
      extraRoutes: async (p) => {
        await p.route('**/api/conversations*', (route) => {
          const url = new URL(route.request().url())
          if (!/\/api\/conversations\/[^/?]+/.test(url.pathname)) {
            listRequestCount += 1
            route.fulfill({ contentType: 'application/json', body: '[]' })
          } else {
            route.fallback()
          }
        })
        // Mock the refresh SSE so the pipeline completes promptly
        // and the post-complete invalidateQueries() runs.
        await p.route('**/api/fetch/refresh*', async (route) => {
          await route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body:
              'data: {"type":"start","message":"Fetching..."}\n\n' +
              'data: {"type":"complete","message":"Fetched 0 conversations.","current":0,"total":0}\n\n',
          })
        })
      },
    });

    await withNetRetry(page, () => page.goto('/'))
    await expect.poll(() => listRequestCount).toBeGreaterThan(0)

    // Tag the window so we can detect a true browser reload (which would
    // wipe this property).
    await page.evaluate(() => {
      ;(window as unknown as { __noReloadSentinel: boolean }).__noReloadSentinel = true
    })

    const before = listRequestCount
    await page.locator('main').click()
    // Use Meta+R on macOS-style keyboards; Playwright accepts the alias.
    await page.keyboard.press('Meta+r')

    // After the refresh SSE 'complete' event, the pipeline calls
    // queryClient.invalidateQueries({ queryKey: ['conversations'] }),
    // so the list re-fetches. This poll is the settle signal.
    await expect.poll(() => listRequestCount).toBeGreaterThan(before)
    // Sentinel survived → no browser reload.
    const sentinel = await page.evaluate(() => {
      return (window as unknown as { __noReloadSentinel?: boolean }).__noReloadSentinel === true
    })
    expect(sentinel).toBe(true)
  });
});
