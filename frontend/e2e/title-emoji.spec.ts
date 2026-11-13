import type { Locator, Page, Route } from '@playwright/test'
import {
  test,
  expect,
  makeSummary,
  makeMessage,
  makeDetail,
  withNetRetry,
  PRIMARY_ORG,
} from './fixtures'

/**
 * Issue #44: a leading emoji in a title looked like an app icon.
 *
 * A title such as "⚡ Weekly report" showed the emoji in color, next to the
 * colored source icons, so users did not see it as part of the title. Each
 * emoji of a title now renders in grayscale (`TitleText`).
 *
 * What this spec pins, in a real browser:
 *   1. The text of the title is exactly the API title, emoji included.
 *   2. The emoji span has a grayscale filter (computed style).
 *   3. The emoji pixels have no color. This is what the user sees. CI
 *      installs `fonts-noto-color-emoji` (`playwright install --with-deps`),
 *      so before the fix this check fails on Linux too.
 *   4. A hover on the list row and on the header shows the exact title.
 *   5. NEGATIVE: an emoji in a message keeps its color.
 *
 * Sites: the list row, the header, the search result card (name line and
 * title-match snippet), and the pin chip of the Search Pane.
 */

const EMOJI_UUID = '00000000-0000-0000-0000-00000000e044'
const PLAIN_UUID = '00000000-0000-0000-0000-00000000e045'
const EMOJI_TITLE = '⚡ Weekly report'
const MESSAGE_TEXT = '🎉 The needle report is done.'

const emojiSummary = makeSummary({
  uuid: EMOJI_UUID,
  name: EMOJI_TITLE,
  source: 'CLAUDE_COWORK',
  message_count: 1,
  human_message_count: 1,
  updated_at: '2026-04-02T10:00:00Z',
})
const plainSummary = makeSummary({ uuid: PLAIN_UUID, name: 'Plain title' })
const emojiDetail = makeDetail(emojiSummary, [
  makeMessage({
    uuid: 'e044-m1',
    text: MESSAGE_TEXT,
    content: [{ type: 'text', text: MESSAGE_TEXT }],
  }),
])

/** Count the dark pixels ("ink") and the colored pixels in an element. */
async function pixelCounts(page: Page, el: Locator): Promise<{ ink: number; color: number }> {
  const png = await el.screenshot()
  return page.evaluate(async (b64) => {
    const img = new Image()
    img.src = `data:image/png;base64,${b64}`
    await img.decode()
    const canvas = document.createElement('canvas')
    canvas.width = img.width
    canvas.height = img.height
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(img, 0, 0)
    const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data
    let ink = 0
    let color = 0
    for (let i = 0; i < d.length; i += 4) {
      const max = Math.max(d[i], d[i + 1], d[i + 2])
      const min = Math.min(d[i], d[i + 1], d[i + 2])
      if (d[i] + d[i + 1] + d[i + 2] >= 720) continue // background
      ink++
      if (max > 0 && (max - min) / max > 0.25) color++
    }
    return { ink, color }
  }, png.toString('base64'))
}

/** Assert that `title` shows `text` exactly, with `emoji` drawn without color. */
async function expectGrayEmojiTitle(page: Page, title: Locator, text: string, emoji: string) {
  await expect(title).toHaveText(text)
  const span = title.locator('span.title-emoji')
  await expect(span).toHaveCount(1)
  await expect(span).toHaveText(emoji)
  const filter = await span.evaluate((el) => getComputedStyle(el).filter)
  expect(filter).toContain('grayscale(1)')
  const { ink, color } = await pixelCounts(page, span)
  expect(ink, 'the emoji drew no pixels').toBeGreaterThan(20)
  expect(color, 'the emoji still has colored pixels').toBe(0)
}

async function setup(mockBackend: (o?: object) => Promise<void>) {
  await mockBackend({
    conversations: [emojiSummary, plainSummary],
    details: { [EMOJI_UUID]: emojiDetail },
    preferences: { 'claude-explorer.organizationFilter': PRIMARY_ORG },
  })
}

test.describe('Emoji in titles render without color (issue #44)', () => {
  test('list row: exact title, gray emoji, exact title on hover', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await withNetRetry(page, () => page.goto('/'))

    const row = page.locator('[role="button"]').filter({ hasText: EMOJI_TITLE })
    const title = row.locator(`span[title="${EMOJI_TITLE}"]`)
    await expect(title).toBeVisible()
    await expectGrayEmojiTitle(page, title, EMOJI_TITLE, '⚡')

    // NEGATIVE: a title without emoji gets no emoji span.
    const plain = page.locator('span[title="Plain title"]')
    await expect(plain).toHaveText('Plain title')
    await expect(plain.locator('span.title-emoji')).toHaveCount(0)
  })

  test('header: exact title, gray emoji, exact title on hover', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await withNetRetry(page, () => page.goto(`/conversations/${EMOJI_UUID}`))

    const h1 = page.getByRole('heading', { level: 1, name: EMOJI_TITLE })
    await expect(h1).toHaveAttribute('title', EMOJI_TITLE)
    await expectGrayEmojiTitle(page, h1, EMOJI_TITLE, '⚡')

    // NEGATIVE: an emoji in a message keeps its color.
    const message = page.locator('[data-message-uuid="e044-m1"]').getByText(MESSAGE_TEXT)
    await expect(message).toBeVisible()
    const { color } = await pixelCounts(page, message)
    expect(color, 'the message emoji lost its color').toBeGreaterThan(0)
  })

  test('Search Pane: card name, title-match snippet, and pin chip', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    let searches = 0
    await page.route('**/api/search**', async (route: Route) => {
      searches++
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          results: [
            {
              conversation_uuid: EMOJI_UUID,
              conversation_name: EMOJI_TITLE,
              conversation_updated_at: emojiSummary.updated_at,
              conversation_created_at: emojiSummary.created_at,
              project_name: null,
              matching_messages: [
                {
                  message_uuid: 'title',
                  sender: '',
                  snippet: EMOJI_TITLE,
                  match_start: 2,
                  match_end: 8,
                  created_at: null,
                },
              ],
            },
          ],
          total_messages_matched: 1,
          returned_messages: 1,
          truncated: false,
        }),
      })
    })
    await withNetRetry(page, () => page.goto(`/conversations/${EMOJI_UUID}`))
    await expect(page.getByRole('heading', { level: 1, name: EMOJI_TITLE })).toHaveText(EMOJI_TITLE)

    // Pin search to this conversation: the Search Pane then shows a chip
    // with the title.
    await page.getByRole('button', { name: /search scope/i }).click()
    await page.getByTestId('pin-this-conversation').click()

    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible()
    await input.fill('Weekly')

    const card = page.getByTestId('search-result-card')
    await expect(card).toHaveCount(1)
    expect(searches).toBeGreaterThan(0)
    await expectGrayEmojiTitle(page, card.locator(`span[title="${EMOJI_TITLE}"]`), EMOJI_TITLE, '⚡')

    // The title-match snippet shows the title, with "Weekly" marked.
    const snippet = card.locator('mark').locator('..')
    await expect(snippet.locator('mark')).toHaveText('Weekly')
    await expectGrayEmojiTitle(page, snippet, EMOJI_TITLE, '⚡')

    const chip = page.getByTestId('search-scope-chip')
    await expect(chip).toContainText(EMOJI_TITLE)
    await expect(chip.locator('span.title-emoji')).toHaveText('⚡')
    const { color } = await pixelCounts(page, chip.locator('span.title-emoji'))
    expect(color, 'the chip emoji still has colored pixels').toBe(0)
  })
})
