import { test, expect, makeSummary, makeMessage, makeDetail, type Page, type Route, withNetRetry, expectNetworkError } from './fixtures'
import type { Message } from '../src/lib/types'

/**
 * Manual finding 2026-05-04: when a Claude Code image referenced in a
 * `[Image: source: <abs-path>]` marker no longer exists on disk
 * (eviction, session rotation, manual cleanup), the backend correctly
 * 404s but the bubble shows the browser's default broken-image glyph
 * (small gray square + filename). That looks like a bug in the app.
 *
 * Desired: render a friendly fallback (ImageOff icon + filename in a
 * dashed-border tile) the same way MessageAttachments does for missing
 * Desktop attachments. Same fix needed for InlineImageBlock (CC base64
 * inline images that fail to decode).
 *
 * RED before fix.
 */

const C = '00000000-0000-0000-0000-0000000000e0'

const TINY_PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='

async function mockCcImage404(page: Page) {
  await page.route('**/api/cc-image**', (route: Route) => {
    route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'image not found' }),
    })
  })
}

const summary = makeSummary({
  uuid: C,
  source: 'CLAUDE_CODE',
  message_count: 1,
  project_path: '/fixture/project',
  project_name: 'project',
})

test.describe('CC image broken-image fallback (manual finding 2026-05-04)', () => {
  test('CcImageMarkerText shows friendly fallback when /api/cc-image 404s', async ({ page, mockBackend, consoleAssertions }) => {
    // §5.15: the deliberate /api/cc-image 404 logs a Chromium network-layer
    // line that the app cannot suppress. Allowlist that one shape; every
    // other console error still fails the test.
    expectNetworkError(consoleAssertions, 404)
    const m = makeMessage({
      uuid: 'msg-marker',
      sender: 'human',
      text: 'has a missing image marker',
      content: [
        {
          type: 'text',
          text: 'before [Image: source: /Users/rpeck/.claude/image-cache/abc/14.png] after',
        },
      ],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])
    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockCcImage404(page)

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))

    // F3 audit: scope to THIS message's tile rather than .first(). The
    // current fixture happens to render only one tile, but a sibling
    // marker added later would silently shift coverage onto the wrong
    // tile. Anchor on the message uuid that owns the marker.
    const messageScope = page.locator('[data-message-uuid="msg-marker"]')
    const tile = messageScope.locator('[data-cc-image-marker]')
    await expect(tile).toBeVisible({ timeout: 5000 })

    // Wait for the <img> to error (network 404 has been mocked).
    // Friendly fallback: the same button gains data-cc-image-broken
    // and shows an ImageOff icon + filename instead of the <img>.
    const fallback = messageScope.locator('[data-cc-image-marker][data-cc-image-broken]')
    await expect(fallback).toBeVisible({ timeout: 5000 })
    await expect(fallback).toContainText('14.png')

    // The broken-glyph <img> must not be visible.
    await expect(fallback.locator('img')).toHaveCount(0)
  })

  test('InlineImageBlock shows friendly fallback when image url 404s', async ({ page, mockBackend, consoleAssertions }) => {
    expectNetworkError(consoleAssertions, 404)
    const m = makeMessage({
      uuid: 'msg-inline',
      sender: 'human',
      text: 'inline image with broken url',
      content: [
        { type: 'text', text: 'before' },
        // url-source pointing at a path that 404s.
        { type: 'image', source: { type: 'url', url: '/api/cc-image?path=/missing-inline.png' } },
        { type: 'text', text: 'after' },
      ],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])
    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockCcImage404(page)

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))

    // F3 audit: scope to msg-inline so a future sibling fixture can't
    // shift coverage to the wrong tile.
    const messageScope = page.locator('[data-message-uuid="msg-inline"]')
    const tile = messageScope.locator('[data-content-image]')
    await expect(tile).toBeVisible({ timeout: 5000 })
    const fallback = messageScope.locator('[data-content-image][data-content-image-broken]')
    await expect(fallback).toBeVisible({ timeout: 5000 })
    await expect(fallback.locator('img')).toHaveCount(0)
  })

  test('fallback tile shows clearer cache-miss copy and tooltip', async ({ page, mockBackend, consoleAssertions }) => {
    expectNetworkError(consoleAssertions, 404)
    const m = makeMessage({
      uuid: 'msg-marker-copy',
      sender: 'human',
      text: 'has a missing image marker',
      content: [
        {
          type: 'text',
          text: 'before [Image: source: /Users/rpeck/.claude/image-cache/abc/14.png] after',
        },
      ],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])
    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockCcImage404(page)

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))

    // F3 audit: scope to msg-marker-copy.
    const messageScope = page.locator('[data-message-uuid="msg-marker-copy"]')
    const fallback = messageScope.locator('[data-cc-image-marker][data-cc-image-broken]')
    await expect(fallback).toBeVisible({ timeout: 5000 })

    // Clearer copy: explicitly says the image is not in cache (not just
    // the bare filename or a generic "unavailable" label).
    await expect(fallback).toContainText(/Image not in cache/i)
    await expect(fallback).toContainText('14.png')

    // Tooltip explains *why*: CC rotates originals, so anything not
    // present at fetch time can't be cached.
    const titleAttr = await fallback.getAttribute('title')
    expect(titleAttr).toMatch(/rotated by Claude Code/i)
  })

  test('<img> auto-retries once via cache-busting URL before showing fallback tile', async ({
    page,
    mockBackend,
    consoleAssertions,
  }) => {
    // First request 404s before the retry succeeds; allowlist the 404 line.
    expectNetworkError(consoleAssertions, 404)
    const m = makeMessage({
      uuid: 'cc-marker-retry',
      sender: 'human',
      text: 'image marker that 404s once then succeeds',
      content: [
        {
          type: 'text',
          text: 'before [Image: source: /Users/rpeck/.claude/image-cache/sess/1.png] after',
        },
      ],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])
    await mockBackend({ conversations: [summary], details: { [C]: detail } })

    // First request 404s; second request (with cache-buster) returns 200
    // with valid PNG bytes. P4d spec: the tile retries silently before
    // giving up, so the user never sees the broken-image fallback.
    let calls = 0
    const tinyPng = Buffer.from(TINY_PNG_B64, 'base64')
    await page.route('**/api/cc-image**', (route: Route) => {
      calls += 1
      if (calls === 1) {
        route.fulfill({
          status: 404,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'missing' }),
        })
      } else {
        route.fulfill({
          status: 200,
          contentType: 'image/png',
          body: tinyPng,
        })
      }
    })

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))

    // F3 audit: scope to cc-marker-retry message.
    const messageScope = page.locator('[data-message-uuid="cc-marker-retry"]')
    const tile = messageScope.locator('[data-cc-image-marker]')
    await expect(tile).toBeVisible({ timeout: 5000 })
    // Wait until the image has really LOADED. A failed <img> is visible
    // too (the browser draws its broken-image icon), so toBeVisible()
    // passed in the window after the first 404 and before the component
    // sent its retry; then `calls` was still 1 (2026-10-22, a full-suite
    // flake). Only the retry can load it: the first request always 404s.
    await expect
      .poll(
        () => tile.locator('img').evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0),
        { timeout: 10000, message: 'the cache-busted retry must load the image' },
      )
      .toBe(true)
    await expect(
      messageScope.locator('[data-cc-image-marker][data-cc-image-broken]'),
    ).toHaveCount(0)
    expect(calls).toBeGreaterThanOrEqual(2)
  })
})
