import { test, expect, Route, withNetRetry, E2E_DEFAULT_PREFS } from './fixtures'

/**
 * M4 from PLANS/articles/part2_revision_plan.md.
 *
 * Article promise (line ~208 of part_2_web_app.md):
 *
 *   "Each content block shows a 'two overlaid pages' copy icon on
 *    hover, and the conversation header includes a 'Copy as Markdown'
 *    action that copies the entire thread as Markdown to your clipboard."
 *
 * Implementation: `MessageBubble.tsx:138` wraps the per-bubble action
 * buttons (bubble-tools chevron, bookmark star, copy icon) in:
 *
 *     <div className="absolute ... opacity-0 transition-opacity
 *                     group-hover:opacity-100">
 *
 * So the buttons are present in the DOM at all times (they need to
 * be focusable for keyboard users), but only become visible to a
 * sighted user when the parent's `:hover` matches. This test pins:
 *
 *   (a) before hover: container has `opacity: 0` (computed style).
 *   (b) after hover:  container has `opacity: 1`.
 *   (c) the copy button is clickable while hovered and writes
 *       message Markdown to the clipboard.
 *
 * Settle signals (per feedback_playwright_settle_signals):
 *   - We DO NOT rely on `.toBeVisible()` for the opacity transition.
 *     A toBeVisible call would pass even at opacity 0 because the
 *     element is in the DOM and has size. We assert on the COMPUTED
 *     `opacity` style instead — that's the only signal that proves
 *     the Tailwind transition has settled in either direction.
 *   - We wait for clipboard write via the readClipboard helper, not
 *     a fixed timeout. The handler is synchronous but clipboard
 *     permission grants are async on Chromium.
 */

const FAKE_UUID = '00000000-0000-0000-0000-0000000000c6'

const baseConv = {
  uuid: FAKE_UUID,
  name: 'Per-block copy fixture',
  summary: '',
  model: 'claude-sonnet-4-6',
  created_at: '2026-04-01T10:00:00Z',
  updated_at: '2026-04-01T10:00:00Z',
  is_starred: false,
  message_count: 1,
  human_message_count: 0,
  has_branches: false,
  source: 'CLAUDE_AI' as const,
  project_path: null,
  project_name: null,
  git_branch: '',
  subagents: [],
}

const assistantMessage = {
  uuid: 'msg-1',
  sender: 'assistant' as const,
  text: 'Hello, this is the body that should be copied.',
  content: [{ type: 'text', text: 'Hello, this is the body that should be copied.' }],
  created_at: '2026-04-01T10:00:00Z',
  updated_at: '2026-04-01T10:00:00Z',
  truncated: false,
  parent_message_uuid: null,
  attachments: [],
  files: [],
}

async function mockBackend(page: import('@playwright/test').Page) {
  await page.route('**/api/config', (route: Route) => {
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data_dir: '/tmp', conversation_count: 1 }),
    })
  })
  await page.route('**/api/conversations**', (route: Route) => {
    const url = route.request().url()
    if (url.includes(`/${FAKE_UUID}/tree`)) {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ uuid: FAKE_UUID, root_messages: [], active_path: [] }),
      })
      return
    }
    if (url.includes(`/${FAKE_UUID}`)) {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          ...baseConv,
          messages: [assistantMessage],
          current_leaf_message_uuid: 'msg-1',
          file_path: '/tmp/x.json',
          compact_markers: [],
        }),
      })
      return
    }
    route.fulfill({ contentType: 'application/json', body: JSON.stringify([baseConv]) })
  })
  await page.route('**/api/orgs', (route: Route) => {
    route.fulfill({ contentType: 'application/json', body: JSON.stringify([]) })
  })
  await page.route('**/api/bookmarks', (route: Route) => {
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ bookmarks: [] }),
    })
  })
  await page.route('**/api/preferences', (route: Route) => {
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ version: 1, data: { ...E2E_DEFAULT_PREFS } }),
    })
  })
}

test.describe('M4: Per-block copy icon is hover-revealed', () => {
  test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

  test('action cluster is opacity-0 by default and opacity-1 on bubble hover', async ({
    page,
  }) => {
    await mockBackend(page)
    await withNetRetry(page, () => page.goto(`/conversations/${FAKE_UUID}`))

    const bubble = page.locator('[data-message-uuid="msg-1"]')
    await expect(bubble).toBeVisible()

    // The action cluster is the inner absolute-positioned div that
    // carries the opacity-0 / group-hover:opacity-100 classes. We
    // locate it via the "Copy message as Markdown" button's parent
    // so the test stays robust to css-class refactoring.
    const copyButton = bubble.getByRole('button', { name: /Copy message as Markdown/i })
    const actionCluster = copyButton.locator('xpath=..')

    // Settle signal: computed opacity is the only reliable
    // assertion for a hover-revealed element (toBeVisible passes at
    // opacity 0 because the DOM node still has layout).
    await expect(actionCluster).toHaveCSS('opacity', '0')

    // Hover the bubble. The Tailwind group-hover:opacity-100
    // takes effect; we wait on the COMPUTED opacity to settle to 1.
    await bubble.hover()
    await expect(actionCluster).toHaveCSS('opacity', '1')

    // Moving the mouse off the bubble takes opacity back to 0.
    // Use a non-bubble locator's bounding box for a safe park spot.
    await page.locator('main h1, main h2').first().hover({ trial: false }).catch(() => {})
    // Fallback: hover the page top-left corner using the mouse API.
    await page.mouse.move(0, 0)
    await expect(actionCluster).toHaveCSS('opacity', '0')
  })

  test('hovering the bubble and clicking the copy icon writes message Markdown to the clipboard', async ({
    page,
  }) => {
    await mockBackend(page)
    await withNetRetry(page, () => page.goto(`/conversations/${FAKE_UUID}`))

    const bubble = page.locator('[data-message-uuid="msg-1"]')
    await expect(bubble).toBeVisible()
    await bubble.hover()

    const copyButton = bubble.getByRole('button', { name: /Copy message as Markdown/i })
    const actionCluster = copyButton.locator('xpath=..')

    // Settle signal: wait for the cluster's opacity to reach 1
    // before clicking. Without this, a click that lands mid-
    // transition could miss the button's expanded hit-test region.
    await expect(actionCluster).toHaveCSS('opacity', '1')

    await copyButton.click()

    // Settle signal: read the clipboard until it contains our
    // marker text. The writeText is sync but clipboard API
    // grants are async on Chromium.
    await expect
      .poll(async () => await page.evaluate(() => navigator.clipboard.readText()), {
        timeout: 3000,
      })
      .toContain('Hello, this is the body that should be copied.')
  })
})
