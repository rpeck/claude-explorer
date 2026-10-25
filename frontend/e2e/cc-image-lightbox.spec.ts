import { test, expect, makeSummary, makeMessage, makeDetail, type Page, type Route, withNetRetry, expectNetworkError } from './fixtures'
import type { Message } from '../src/lib/types'

/**
 * Issue #1 — Claude Code image rendering must open the in-page
 * ImageLightbox (overlay) instead of `window.open(...)` to a new tab.
 *
 * Two CC code paths render images today:
 *
 *   - InlineImageBlock (commit ed2f311): Claude Code inline base64
 *     `image` content blocks.
 *   - CcImageMarkerText (commit a5ff282): `[Image: source: <abs-path>]`
 *     plain-text markers that resolve via the `/api/cc-image` proxy.
 *
 * Both currently call `window.open(src, '_blank', ...)` on click.
 * The spec was that they should pop the same shadcn Dialog lightbox
 * the Desktop image grid uses (data-testid="image-lightbox"), which
 * provides keyboard nav, download, and "open original" affordances.
 *
 * Pass condition: clicking either rendering opens the lightbox in
 * the same tab; the lightbox is dismissible with Esc; no popup is
 * triggered.
 */

const C = '00000000-0000-0000-0000-0000000000d5'

const TINY_PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
const PNG_BYTES = Buffer.from(TINY_PNG_B64, 'base64')

async function mockCcImageBytes(page: Page) {
  await page.route('**/api/cc-image**', (route: Route) => {
    route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: PNG_BYTES,
    })
  })
}

async function trackPopups(page: Page): Promise<{ count: () => number }> {
  let popups = 0
  page.on('popup', () => {
    popups++
  })
  await page.addInitScript(() => {
    ;(window as unknown as { __openCalls: number }).__openCalls = 0
    window.open = () => {
      ;(window as unknown as { __openCalls: number }).__openCalls++
      return null
    }
  })
  return {
    count: () => popups,
  }
}

async function getOpenCount(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as { __openCalls?: number }).__openCalls ?? 0)
}

test.describe('CC inline image content block opens lightbox (Issue #1)', () => {
  test('clicking a CC inline image opens the lightbox, not a new tab', async ({ page, mockBackend }) => {
    const tracker = await trackPopups(page)

    const summary = makeSummary({
      uuid: C,
      name: 'CC inline image',
      source: 'CLAUDE_CODE',
      message_count: 1,
      project_path: '/tmp/proj',
      project_name: 'proj',
    })
    const m = makeMessage({
      uuid: 'cci-1',
      sender: 'human',
      text: '',
      content: [
        {
          type: 'image',
          source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 },
        },
      ],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])

    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await withNetRetry(page, () => page.goto(`/conversations/${C}`))

    const bubble = page.locator('[data-message-uuid="cci-1"]')
    await expect(bubble).toBeVisible()
    const inlineButton = bubble.locator('[data-content-image]')
    await expect(inlineButton).toBeVisible()

    await inlineButton.click()

    const lightbox = page.getByTestId('image-lightbox')
    await expect(lightbox).toBeVisible()

    // No popup, no window.open call.
    expect(tracker.count()).toBe(0)
    expect(await getOpenCount(page)).toBe(0)

    // Esc dismisses.
    await page.keyboard.press('Escape')
    await expect(lightbox).not.toBeVisible()
  })
})

test.describe('CC `[Image: source: <path>]` marker opens lightbox (Issue #1)', () => {
  test('clicking a CC image marker opens the lightbox, not a new tab', async ({ page, mockBackend, consoleAssertions }) => {
    // §5.15: the CC marker bubble preprocessor probes both
    // `/api/cc-image?path=...` (mocked 200 below) AND the org-files /
    // attachments fallbacks (default 404 from mockBackend). Allowlist the
    // 404 line that Chromium logs for those fallback probes — the lightbox
    // opens on the successful /api/cc-image fetch regardless.
    expectNetworkError(consoleAssertions, 404)
    const tracker = await trackPopups(page)

    const summary = makeSummary({
      uuid: C,
      name: 'CC image marker',
      source: 'CLAUDE_CODE',
      message_count: 1,
      project_path: '/tmp/proj',
      project_name: 'proj',
    })
    const m = makeMessage({
      uuid: 'ccm-1',
      sender: 'human',
      text: '',
      content: [
        {
          type: 'text',
          text: 'Look at this:\n[Image: source: /Users/rpeck/.claude/image-cache/abc/1.png]',
        },
      ],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])

    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    // AFTER mockBackend, whose own /api/cc-image route answers 404: the
    // route registered last runs first. Before 2026-10-24 this came
    // first, so the lightbox opened on a broken image.
    await mockCcImageBytes(page)
    await withNetRetry(page, () => page.goto(`/conversations/${C}`))

    const bubble = page.locator('[data-message-uuid="ccm-1"]')
    await expect(bubble).toBeVisible()
    const markerButton = bubble.locator('[data-cc-image-marker]')
    await expect(markerButton).toBeVisible()

    await markerButton.click()

    const lightbox = page.getByTestId('image-lightbox')
    await expect(lightbox).toBeVisible()
    // The image itself loaded (the mocked PNG), not a broken image.
    await expect.poll(() => lightbox.locator('img').first().evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)

    expect(tracker.count()).toBe(0)
    expect(await getOpenCount(page)).toBe(0)

    await page.keyboard.press('Escape')
    await expect(lightbox).not.toBeVisible()
  })
})
