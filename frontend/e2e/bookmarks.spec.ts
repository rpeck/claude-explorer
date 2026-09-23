import { test, expect, Route, withNetRetry, installLocalPrefsMock } from './fixtures';

/**
 * Message bookmarks (Build-4).
 *
 * Mocks both the conversations endpoint and the bookmarks endpoint with an
 * in-memory state. Tests cover: hover-revealed star, b-key toggle, deep-link
 * round-trip, Markdown export, and persistence.
 */

const FAKE_UUID = '00000000-0000-0000-0000-0000000000bb';

const baseConv = {
  uuid: FAKE_UUID,
  name: 'Bookmarks fixture',
  summary: '',
  model: 'claude-sonnet-4-6',
  created_at: '2026-04-01T10:00:00Z',
  updated_at: '2026-04-01T13:00:00Z',
  is_starred: false,
  message_count: 3,
  human_message_count: 2,
  has_branches: false,
  source: 'CLAUDE_CODE' as const,
  project_path: '/tmp/proj',
  project_name: 'proj',
  git_branch: '',
  subagents: [],
};

const messages = [
  {
    uuid: 'msg-A',
    sender: 'human' as const,
    text: 'First user prompt',
    content: [{ type: 'text', text: 'First user prompt' }],
    created_at: '2026-04-01T10:00:00Z',
    updated_at: '2026-04-01T10:00:00Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
  },
  {
    uuid: 'msg-B',
    sender: 'assistant' as const,
    text: 'Assistant reply with bookmark target text',
    content: [{ type: 'text', text: 'Assistant reply with bookmark target text' }],
    created_at: '2026-04-01T10:01:00Z',
    updated_at: '2026-04-01T10:01:00Z',
    truncated: false,
    parent_message_uuid: 'msg-A',
    attachments: [],
    files: [],
  },
  {
    uuid: 'msg-C',
    sender: 'human' as const,
    text: 'Follow-up message',
    content: [{ type: 'text', text: 'Follow-up message' }],
    created_at: '2026-04-01T10:02:00Z',
    updated_at: '2026-04-01T10:02:00Z',
    truncated: false,
    parent_message_uuid: 'msg-B',
    attachments: [],
    files: [],
  },
];

interface MockBookmark {
  id: string;
  conversation_id: string;
  message_uuid: string;
  source: 'claude_code' | 'claude_desktop';
  created_at: string;
  note: string;
  snippet: string;
}

async function mockBackend(page: import('@playwright/test').Page) {
  const state: { bookmarks: MockBookmark[] } = { bookmarks: [] };
  // Per-test preferences store. The 2026-06-01 regression: bookmarks:172
  // / :245 / :268 saw a PERSISTED `rightPaneTab: 'bookmarks'` from a
  // prior test's tab click via the Vite-proxy leak, and Cmd+K's "open
  // Search tab" race surfaced as a visible-tablist timeout. See
  // fixtures.installLocalPrefsMock header for the shared rationale.
  await installLocalPrefsMock(page);

  await page.route('**/api/config', (route: Route) => {
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data_dir: '/tmp', conversation_count: 1 }),
    });
  });

  await page.route('**/api/bookmarks**', async (route: Route) => {
    const req = route.request();
    const url = req.url();
    const method = req.method();
    const idMatch = url.match(/\/api\/bookmarks\/([^/?]+)/);
    if (method === 'GET') {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify({ bookmarks: state.bookmarks }) });
      return;
    }
    if (method === 'POST') {
      const body = req.postDataJSON() as Partial<MockBookmark>;
      const newBm: MockBookmark = {
        id: `bm-${Math.random().toString(36).slice(2, 8)}`,
        conversation_id: body.conversation_id ?? '',
        message_uuid: body.message_uuid ?? '',
        source: (body.source as MockBookmark['source']) ?? 'claude_code',
        created_at: new Date().toISOString(),
        note: body.note ?? '',
        snippet: (body.snippet ?? '').slice(0, 140),
      };
      state.bookmarks.push(newBm);
      route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(newBm) });
      return;
    }
    if (method === 'PATCH' && idMatch) {
      const id = idMatch[1];
      const body = req.postDataJSON() as Partial<MockBookmark>;
      const bm = state.bookmarks.find((b) => b.id === id);
      if (!bm) {
        route.fulfill({ status: 404, body: 'Not found' });
        return;
      }
      if (body.note !== undefined) bm.note = body.note;
      if (body.snippet !== undefined) bm.snippet = body.snippet;
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bm) });
      return;
    }
    if (method === 'DELETE' && idMatch) {
      const id = idMatch[1];
      const before = state.bookmarks.length;
      state.bookmarks = state.bookmarks.filter((b) => b.id !== id);
      if (state.bookmarks.length === before) {
        route.fulfill({ status: 404, body: 'Not found' });
      } else {
        route.fulfill({ status: 204, body: '' });
      }
      return;
    }
    route.continue();
  });

  await page.route('**/api/conversations**', (route: Route) => {
    const url = route.request().url();
    if (url.includes(`/conversations/${FAKE_UUID}/tree`)) {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ uuid: FAKE_UUID, root_messages: [], active_path: [] }),
      });
      return;
    }
    if (url.includes(`/conversations/${FAKE_UUID}`)) {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          ...baseConv,
          messages,
          current_leaf_message_uuid: 'msg-C',
          file_path: '/tmp/proj/fake.jsonl',
          compact_markers: [],
        }),
      });
      return;
    }
    route.fulfill({ contentType: 'application/json', body: JSON.stringify([baseConv]) });
  });
}

test.describe('Message bookmarks (Build-4)', () => {
  test.beforeEach(async ({ page }) => {
    await mockBackend(page);
  });

  test('right pane has Search and Bookmarks tabs', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${FAKE_UUID}`));
    // Settle signal: the bubble being in the DOM is the deterministic
    // signal that the App tree (including useKeyboardShortcuts' window
    // keydown handler) has mounted. Without this, a race between the
    // navigation finishing and the useEffect that wires up the keydown
    // handler causes Cmd+K to no-op (panel never opens, tablist never
    // appears). 2026-06-01 hardening — same shape as the rest of the
    // suite (toggle-preserves-focus-after-click, search-* specs).
    await expect(page.locator('[data-message-uuid="msg-A"]')).toBeVisible();
    // 2026-06-01: switched from window.dispatchEvent(new KeyboardEvent)
    // back to page.keyboard.press. The original `evaluate` shape was
    // documented as "more reliable" than keyboard.press, but the rest
    // of the e2e suite uses page.keyboard.press successfully (e.g.
    // toggle-preserves-focus-after-click). dispatchEvent was the
    // intermittent path here — bare keyboard.press works once the App
    // tree has mounted (see the settle signal above).
    await page.keyboard.press('Meta+k');
    await expect(page.getByRole('tablist')).toBeVisible({ timeout: 10_000 });

    const tablist = page.getByRole('tablist');
    await expect(tablist).toBeVisible();
    await expect(tablist.getByRole('tab', { name: /search/i })).toBeVisible();
    await expect(tablist.getByRole('tab', { name: /bookmarks/i })).toBeVisible();
  });

  test('hover-revealed star creates a bookmark; another click removes it', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${FAKE_UUID}`));
    const bubble = page.locator('[data-message-uuid="msg-B"]');
    await bubble.hover();

    const star = bubble.getByRole('button', { name: /bookmark/i });
    await expect(star).toBeVisible();
    // The star sits in a `opacity-0 transition-opacity group-hover:opacity-100`
    // wrapper (~150ms Tailwind transition). Playwright's `.click()` happily
    // dispatches at any opacity > 0, which on cold render can land mid-transition
    // and miss. Wait for the opacity transition to settle to opacity:1 before
    // clicking. (Flake repro 2026-05-12, council fix.)
    await expect(star).toHaveCSS('opacity', '1');
    await star.click();

    // After bookmarking, star should reflect filled state via aria.
    await expect(bubble.locator('[data-bookmarked]')).toHaveCount(1);

    // Click again to remove.
    await bubble.hover();
    const star2 = bubble.getByRole('button', { name: /bookmark/i });
    await expect(star2).toHaveCSS('opacity', '1');
    await star2.click();
    await expect(bubble.locator('[data-bookmarked]')).toHaveCount(0);
  });

  test('pressing b on a focused message toggles its bookmark', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${FAKE_UUID}`));
    const bubble = page.locator('[data-message-uuid="msg-B"]');
    await bubble.click();
    // The 'b' handler reads `getSelectedMessageId()` from React context.
    // `bubble.click()` triggers two state updates (`setSelectedMessageIndex`
    // on the wrapper, then `setFocusArea('detail')` on the bubbling outer
    // container), and `page.keyboard.press('b')` can fire before React has
    // committed either, leading to a stale read that bookmarks msg-A (the
    // default index-0 selection). The bubble's inner content div gets
    // `ring-2 ring-blue-500 ring-offset-2` *only* when both states have
    // committed (`isKeyboardSelected = focusArea === 'detail' && uuid match`),
    // so waiting for that ring is a deterministic commit barrier.
    // TODO: replace with a `data-testid` or `aria-selected` attribute when
    // the bubble selection state gets a dedicated marker.
    // (Flake repro 2026-05-12, council fix.)
    await expect(bubble.locator('.ring-2')).toBeVisible();
    await page.keyboard.press('b');

    await expect(bubble.locator('[data-bookmarked]')).toHaveCount(1);
  });

  test('bookmark deep-link from Bookmarks tab scrolls and flashes the message', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${FAKE_UUID}`));
    const bubble = page.locator('[data-message-uuid="msg-B"]');
    await bubble.hover();
    await bubble.getByRole('button', { name: /bookmark/i }).click();
    await expect(bubble.locator('[data-bookmarked]')).toHaveCount(1);

    // 2026-06-01: switched from window.dispatchEvent to page.keyboard.press
    // — same reliability fix as test 172 above. See that test's comment.
    await page.keyboard.press('Meta+k');
    await expect(page.getByRole('tablist')).toBeVisible({ timeout: 10_000 });
    await page.getByRole('tablist').getByRole('tab', { name: /bookmarks/i }).click();

    // Click the bookmark item.
    const item = page.locator('[data-bookmark-item]').first();
    await expect(item).toBeVisible();
    await item.click();

    // URL updates with ?m=msg-B, target bubble visible, panel closes.
    await expect(page).toHaveURL(/m=msg-B/);
    await expect(bubble).toBeVisible();
  });

  test('Export to Markdown button on Bookmarks tab triggers a download', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${FAKE_UUID}`));
    const bubble = page.locator('[data-message-uuid="msg-B"]');
    await bubble.hover();
    await bubble.getByRole('button', { name: /bookmark/i }).click();
    // Settle signal: confirm the bookmark write has been reflected in
    // the DOM before continuing. Without this, the subsequent
    // main.click() + Cmd+K dispatch races with the bookmark PATCH and
    // its React re-render cascade — the global keydown handler reads
    // a half-mounted SearchPanelContext and the toggle silently no-ops,
    // leaving the tablist never visible (2026-06-01 baseline regression
    // for `Export to Markdown button` — see TESTING.md on
    // playwright settle signals).
    await expect(bubble.locator('[data-bookmarked]')).toHaveCount(1);

    // 2026-06-01: switched from window.dispatchEvent to page.keyboard.press
    // — same reliability fix as test 172 above. See that test's comment.
    await page.keyboard.press('Meta+k');
    await expect(page.getByRole('tablist')).toBeVisible({ timeout: 10_000 });
    await page.getByRole('tablist').getByRole('tab', { name: /bookmarks/i }).click();

    const exportButton = page.getByRole('button', { name: /export.*markdown/i });
    await expect(exportButton).toBeVisible();

    const downloadPromise = page.waitForEvent('download');
    await exportButton.click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/bookmarks.*\.md/);
  });
});
