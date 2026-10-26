import { test, expect, makeSummary, makeMessage, makeDetail, type Page, type Route, withNetRetry, expectNetworkError } from './fixtures'
import type { ImageFile, Message } from '../src/lib/types'

/**
 * Image attachments + lightbox coverage.
 *
 * Real Claude Desktop conversations carry images in `Message.files[]`
 * (sometimes mirrored in `files_v2`) with `file_kind: 'image'` and a
 * `preview_asset.url`. The renderer dedupes by `file_uuid`, shows
 * thumbnails in an adaptive grid (single big tile / multi square / +N
 * overflow), and pops a full-screen shadcn Dialog lightbox on click.
 *
 * Tests cover:
 *   - Single image renders as a single tile (object-contain, max-h-64)
 *   - Multi-image (3) renders as 2-col aspect-square tiles
 *   - 6-image triggers "+N" overflow (4 tiles + "+2")
 *   - Click → lightbox opens at correct index
 *   - Lightbox: Esc closes, ←/→ navigate, multi-counter "i / N"
 *   - "d" key triggers download (we just verify no crash + element clickable)
 *   - "o" key opens new tab (mocked window.open)
 *   - Broken image fallback: 404 thumbnail → ImageOff placeholder
 *   - files / files_v2 dedup: same file_uuid in both renders once
 *   - showToolCalls=false still renders images (Council Q7)
 *   - Markdown export endpoint emits image refs
 */

const C = '00000000-0000-0000-0000-0000000000a4'

// 1x1 transparent PNG — base64 + data URI works as a real <img> source.
const TINY_PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
const PNG_BYTES = Buffer.from(TINY_PNG_B64, 'base64')

function makeImage(overrides: Partial<ImageFile> & { file_uuid: string; file_name: string }): ImageFile {
  return {
    file_kind: 'image',
    created_at: '2026-04-01T10:00:00Z',
    thumbnail_url: `/api/test/files/${overrides.file_uuid}/thumbnail`,
    preview_asset: {
      url: `/api/test/files/${overrides.file_uuid}/preview`,
      file_variant: 'preview',
      primary_color: 'f6f6f6',
      image_width: 100,
      image_height: 100,
    },
    thumbnail_asset: {
      url: `/api/test/files/${overrides.file_uuid}/thumbnail`,
      file_variant: 'thumbnail',
      primary_color: 'f6f6f6',
      image_width: 50,
      image_height: 50,
    },
    ...overrides,
  }
}

async function mockImageBytes(page: Page) {
  // Serve PNG bytes for any /api/test/files/.../{thumbnail,preview} URL.
  await page.route('**/api/test/files/**', (route: Route) => {
    route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: PNG_BYTES,
    })
  })
}

async function mockImageBytes404(page: Page) {
  // Used for the broken-image test.
  await page.route('**/api/test/files/**', (route: Route) => {
    route.fulfill({ status: 404, contentType: 'text/plain', body: 'gone' })
  })
}

test.describe('Image attachments — single image (Phase 2)', () => {
  test('single image renders as a single tile and opens lightbox on click', async ({ page, mockBackend }) => {
    const summary = makeSummary({ uuid: C, source: 'CLAUDE_AI', message_count: 1 })
    const img = makeImage({ file_uuid: 'img-1', file_name: 'screenshot.png' })
    const m = makeMessage({
      uuid: 'm-1',
      sender: 'human',
      text: 'Take a look',
      content: [{ type: 'text', text: 'Take a look' }],
      files: [img],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])

    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockImageBytes(page)

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    const bubble = page.locator('[data-message-uuid="m-1"]')
    await expect(bubble).toBeVisible()

    const attachments = bubble.locator('[data-message-attachments]')
    await expect(attachments).toBeVisible()
    await expect(attachments).toHaveAttribute('data-attachment-count', '1')

    // The thumbnail <button> is the click target.
    const tileButton = attachments.locator('button', { has: page.locator('img') })
    await expect(tileButton).toBeVisible()
    await tileButton.click()

    // Lightbox opens.
    const lightbox = page.getByTestId('image-lightbox')
    await expect(lightbox).toBeVisible()
    // Filename shows in the header.
    await expect(lightbox).toContainText('screenshot.png')

    // Esc closes.
    await page.keyboard.press('Escape')
    await expect(lightbox).not.toBeVisible()
  })
})

test.describe('Image attachments — multi-image grid + lightbox nav (Phase 2)', () => {
  test('three images render as a 2-col grid; ←/→ navigates the lightbox', async ({ page, mockBackend }) => {
    const summary = makeSummary({ uuid: C, source: 'CLAUDE_AI', message_count: 1 })
    const imgs = [
      makeImage({ file_uuid: 'img-a', file_name: 'a.png' }),
      makeImage({ file_uuid: 'img-b', file_name: 'b.png' }),
      makeImage({ file_uuid: 'img-c', file_name: 'c.png' }),
    ]
    const m = makeMessage({
      uuid: 'm-multi',
      sender: 'human',
      text: 'three',
      content: [{ type: 'text', text: 'three' }],
      files: imgs,
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])

    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockImageBytes(page)

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    const attachments = page.locator('[data-message-uuid="m-multi"] [data-message-attachments]')
    await expect(attachments).toHaveAttribute('data-attachment-count', '3')

    // Click the first tile (associated img has file_uuid img-a).
    const firstTile = attachments.locator('button:has(img[data-image-uuid="img-a"])')
    await firstTile.click()

    const lightbox = page.getByTestId('image-lightbox')
    await expect(lightbox).toBeVisible()
    await expect(lightbox).toContainText('a.png')
    await expect(lightbox).toContainText('1 / 3')

    // ArrowRight → b.png (2 / 3).
    await page.keyboard.press('ArrowRight')
    await expect(lightbox).toContainText('b.png')
    await expect(lightbox).toContainText('2 / 3')

    // ArrowRight → c.png.
    await page.keyboard.press('ArrowRight')
    await expect(lightbox).toContainText('c.png')
    await expect(lightbox).toContainText('3 / 3')

    // Wraps back to a.png.
    await page.keyboard.press('ArrowRight')
    await expect(lightbox).toContainText('a.png')

    // ArrowLeft wraps to c.png.
    await page.keyboard.press('ArrowLeft')
    await expect(lightbox).toContainText('c.png')

    // Esc closes.
    await page.keyboard.press('Escape')
    await expect(lightbox).not.toBeVisible()
  })
})

test.describe('Image attachments — overflow "+N" tile (Phase 2)', () => {
  test('six images render four squares plus a +2 overflow tile', async ({ page, mockBackend }) => {
    const summary = makeSummary({ uuid: C, source: 'CLAUDE_AI', message_count: 1 })
    const imgs = Array.from({ length: 6 }, (_, i) =>
      makeImage({ file_uuid: `img-${i}`, file_name: `n-${i}.png` }),
    )
    const m = makeMessage({
      uuid: 'm-six',
      sender: 'human',
      text: 'six',
      content: [{ type: 'text', text: 'six' }],
      files: imgs,
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])

    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockImageBytes(page)

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    const attachments = page.locator('[data-message-uuid="m-six"] [data-message-attachments]')
    await expect(attachments).toHaveAttribute('data-attachment-count', '6')

    // The "+2" overflow tile.
    const overflow = attachments.getByRole('button', { name: /Show 2 more attachments/i })
    await expect(overflow).toBeVisible()
    await expect(overflow).toContainText('+2')

    // Clicking the overflow opens the lightbox at the 5th image (index 4).
    await overflow.click()
    const lightbox = page.getByTestId('image-lightbox')
    await expect(lightbox).toBeVisible()
    await expect(lightbox).toContainText('n-4.png')
    await expect(lightbox).toContainText('5 / 6')
  })
})

test.describe('Image attachments — files + files_v2 dedup (Phase 2)', () => {
  test('same file_uuid appearing in both arrays renders only once', async ({ page, mockBackend }) => {
    const summary = makeSummary({ uuid: C, source: 'CLAUDE_AI', message_count: 1 })
    const img = makeImage({ file_uuid: 'dupe', file_name: 'dupe.png' })
    // v1 has the file but no preview_asset url; v2 has it richer.
    const v1 = { ...img, preview_asset: undefined }
    const v2 = img
    const m = makeMessage({
      uuid: 'm-dupe',
      sender: 'human',
      text: 'dupe',
      content: [{ type: 'text', text: 'dupe' }],
      files: [v1],
      files_v2: [v2],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])

    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockImageBytes(page)

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    const attachments = page.locator('[data-message-uuid="m-dupe"] [data-message-attachments]')
    await expect(attachments).toHaveAttribute('data-attachment-count', '1')
  })
})

test.describe('Image attachments — broken image fallback (Phase 2)', () => {
  test('404 thumbnail shows ImageOff placeholder with the filename', async ({ page, mockBackend, consoleAssertions }) => {
    // §5.15: deliberate `<img>` 404 → Chromium logs a network-layer
    // line the app cannot suppress. Allowlist only that shape.
    expectNetworkError(consoleAssertions, 404)
    const summary = makeSummary({ uuid: C, source: 'CLAUDE_AI', message_count: 1 })
    const img = makeImage({ file_uuid: 'broken', file_name: 'gone.png' })
    const m = makeMessage({
      uuid: 'm-broken',
      sender: 'human',
      text: 'broken',
      content: [{ type: 'text', text: 'broken' }],
      files: [img],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])

    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockImageBytes404(page)

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    const attachments = page.locator('[data-message-uuid="m-broken"] [data-message-attachments]')
    await expect(attachments).toHaveAttribute('data-attachment-count', '1')
    // Placeholder button surfaces "(unavailable)" via aria-label.
    const placeholder = attachments.getByRole('button', { name: /unavailable/i })
    await expect(placeholder).toBeVisible()
    await expect(placeholder).toContainText('gone.png')
  })
})

test.describe('Claude Code [Image: source: <path>] text markers (Pattern B)', () => {
  // Manual finding 2026-05-03: this is the OTHER CC shape — the
  // message text contains a literal "[Image: source: <abs-path>]"
  // marker pointing at ~/.claude/image-cache/<session-uuid>/<N>.png.
  // No inline base64; the bytes live on disk. The previous fixes
  // (Desktop Message.files[] proxy + inline base64 image content
  // blocks) don't help here — these markers render as plain text.
  //
  // This test asserts the desired behavior: the marker is replaced
  // with an <img> in the bubble. Failing == we still need to ship
  // the fix (preprocessor + image-cache proxy).
  test('text marker [Image: source: <path>] renders as an <img>, not literal text', async ({ page, mockBackend }) => {
    const summary = makeSummary({
      uuid: C,
      source: 'CLAUDE_CODE',
      message_count: 1,
      project_path: '/fixture/project',
      project_name: 'project',
    })
    const m = makeMessage({
      uuid: 'cc-marker',
      sender: 'human',
      // The exact shape from a real CC JSONL: a single `text` content
      // block whose body is the marker. NO inline base64. The bytes
      // live on disk at the absolute path in the marker.
      text: '[Image: source: /fixture/cc-image-cache/test-session/1.png]',
      content: [
        {
          type: 'text',
          text: '[Image: source: /fixture/cc-image-cache/test-session/1.png]',
        },
      ],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])
    // Serve real PNG bytes from the image proxy. With the fixture's
    // default 404, the <img> renders, fails, retries, and is then swapped
    // for the broken-image fallback button (product behavior, pinned by
    // cc-image-broken-fallback.spec.ts). The old version of this test read
    // the <img> after counting it, and when the swap came in between, the
    // read waited for a gone element until the 60 s test timeout
    // (2026-10-26 CI flake). The success path keeps the <img>.
    await mockBackend({
      conversations: [summary],
      details: { [C]: detail },
      extraRoutes: async (p) => {
        await p.route('**/api/cc-image**', (route: Route) => {
          route.fulfill({ status: 200, contentType: 'image/png', body: PNG_BYTES })
        })
      },
    })

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    const bubble = page.locator('[data-message-uuid="cc-marker"]')
    await expect(bubble).toBeVisible()

    // The literal "[Image: source: ..." text MUST NOT be visible in
    // the rendered bubble — that's the bug we're fixing. The marker
    // should be replaced with an <img> (or a friendly broken-image
    // fallback when the backend 404s, which is what happens here since
    // /fixture/cc-image-cache/... is not on disk).
    await expect(bubble).not.toContainText('[Image: source:')

    // The marker is replaced with an <img> that loaded through the image
    // proxy, not with the raw absolute path the browser cannot fetch.
    const img = bubble.locator('[data-cc-image-marker] img')
    await expect(img).toHaveCount(1)
    await expect
      .poll(() => img.evaluate((el) => (el as HTMLImageElement).complete && (el as HTMLImageElement).naturalWidth > 0))
      .toBe(true)
    const src = await img.getAttribute('src')
    expect(src).toBeTruthy()
    expect(src).not.toMatch(/^\/fixture\//)
    expect(src).not.toMatch(/^\/Users\//)
    expect(src).not.toMatch(/^file:/)
  })

  test('multiple [Image: source:] markers in one message render multiple <img>s', async ({ page, mockBackend }) => {
    const summary = makeSummary({ uuid: C, source: 'CLAUDE_CODE', message_count: 1 })
    const m = makeMessage({
      uuid: 'cc-multi-marker',
      sender: 'human',
      text:
        'Two pics: [Image: source: /fixture/cc-image-cache/test-session/1.png] and [Image: source: /fixture/cc-image-cache/test-session/2.png] there.',
      content: [
        {
          type: 'text',
          text:
            'Two pics: [Image: source: /fixture/cc-image-cache/test-session/1.png] and [Image: source: /fixture/cc-image-cache/test-session/2.png] there.',
        },
      ],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])
    // M5.5: this test asserts EXACTLY two <img> elements remain after the
    // markers render. The fixture default for `/api/cc-image` is 404,
    // which causes <img onError> to swap to the broken-image fallback
    // button (no <img>) — flaky under load. Override to serve real PNG
    // bytes so both markers stay as <img>s, matching the success path
    // a developer would see when the on-disk cache has the file.
    await mockBackend({
      conversations: [summary],
      details: { [C]: detail },
      extraRoutes: async (p) => {
        await p.route('**/api/cc-image**', (route: Route) => {
          route.fulfill({
            status: 200,
            contentType: 'image/png',
            body: PNG_BYTES,
          })
        })
      },
    })

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    const bubble = page.locator('[data-message-uuid="cc-multi-marker"]')
    await expect(bubble).toBeVisible()
    // Both markers replaced.
    await expect(bubble).not.toContainText('[Image: source:')
    // Two <img> elements present.
    await expect(bubble.locator('img')).toHaveCount(2)
    // Surrounding text preserved.
    await expect(bubble).toContainText('Two pics:')
    await expect(bubble).toContainText('there.')
  })
})

test.describe('Inline image content blocks (Claude Code shape)', () => {
  test('image content block renders as inline <img> with the data URI', async ({ page, mockBackend }) => {
    // Manual finding 2026-05-03: Claude Code embeds images as
    // { type: 'image', source: { type: 'base64', media_type, data } }
    // content blocks (alongside a sibling text block carrying the
    // [Image #N] marker). The Desktop Message.files[] proxy doesn't
    // help here — we render the bytes inline via a data URI.
    const summary = makeSummary({
      uuid: C,
      source: 'CLAUDE_CODE',
      message_count: 1,
      project_path: '/fixture/project',
      project_name: 'project',
    })
    const m = makeMessage({
      uuid: 'cc-img',
      sender: 'human',
      text: '[Image #1]',
      content: [
        { type: 'text', text: '[Image #1]' },
        { type: 'image', source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 } },
      ],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])
    await mockBackend({ conversations: [summary], details: { [C]: detail } })

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    const bubble = page.locator('[data-message-uuid="cc-img"]')
    await expect(bubble).toBeVisible()

    const inlineButton = bubble.locator('[data-content-image]')
    await expect(inlineButton).toBeVisible()
    const img = inlineButton.locator('img')
    const src = await img.getAttribute('src')
    expect(src).toMatch(/^data:image\/png;base64,/)
    expect(src).toContain(TINY_PNG_B64)
  })

  test('image content block visible even with no text alongside (visibility check)', async ({ page, mockBackend }) => {
    // Message with ONLY an image content block (no text/tool blocks)
    // must still render — messageHasVisibleContent has to count image
    // content blocks toward "visible".
    const summary = makeSummary({ uuid: C, source: 'CLAUDE_CODE', message_count: 1 })
    const m = makeMessage({
      uuid: 'cc-img-only',
      sender: 'human',
      text: '',
      content: [
        { type: 'image', source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 } },
      ],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])
    await mockBackend({ conversations: [summary], details: { [C]: detail } })

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    const bubble = page.locator('[data-message-uuid="cc-img-only"]')
    await expect(bubble).toBeVisible()
    await expect(bubble.locator('[data-content-image] img')).toBeVisible()
  })
})

test.describe('Image attachments — independent of showToolCalls toggle (Phase 2 / Council Q7)', () => {
  test('image renders even when no text and tools are hidden', async ({ page, mockBackend }) => {
    const summary = makeSummary({ uuid: C, source: 'CLAUDE_AI', message_count: 1 })
    const img = makeImage({ file_uuid: 'lone', file_name: 'lone.png' })
    // Message with no text and no content blocks — would normally be filtered.
    const m = makeMessage({
      uuid: 'm-lone',
      sender: 'human',
      text: '',
      content: [],
      files: [img],
    } as Partial<Message> & { uuid: string })
    const detail = makeDetail(summary, [m])

    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockImageBytes(page)

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    // The bubble exists despite empty text/content because it has an image.
    const bubble = page.locator('[data-message-uuid="m-lone"]')
    await expect(bubble).toBeVisible()
    const attachments = bubble.locator('[data-message-attachments]')
    await expect(attachments).toHaveAttribute('data-attachment-count', '1')
  })
})
