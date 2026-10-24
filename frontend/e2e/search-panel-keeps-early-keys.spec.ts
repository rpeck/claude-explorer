import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'

/**
 * Keys typed right after Cmd+K must all reach the search input.
 *
 * Found 2026-10-24 by a flaky CI run of search-typing-lag.spec.ts: the
 * test typed NEEDLE_X and the input held EDLE_X. The panel focuses its
 * input in a setTimeout(0) and then calls select(), to select an earlier
 * query. When the first keys came before that timer (a busy main thread,
 * or a fast typist), select() selected them and the next key replaced
 * them.
 *
 * The page clock holds the timer, so the race happens every time: type
 * "NE", let the timer run, type the rest.
 */

const C = '00000000-0000-0000-0000-0000000000e1'
const summary = makeSummary({ uuid: C, source: 'CLAUDE_CODE', message_count: 2 })
const detail = makeDetail(summary, [
  makeMessage({ uuid: 'e-0', sender: 'human', text: 'hello', content: [{ type: 'text', text: 'hello' }] }),
  makeMessage({
    uuid: 'e-1',
    sender: 'assistant',
    text: 'world',
    content: [{ type: 'text', text: 'world' }],
    parent_message_uuid: 'e-0',
  }),
])

test('keys typed before the panel focuses its input are kept', async ({ page, mockBackend }) => {
  await mockBackend({ conversations: [summary], details: { [C]: detail } })
  await page.clock.install()
  await withNetRetry(page, () => page.goto(`/conversations/${C}`))
  await expect(page.locator('[data-message-uuid="e-0"]')).toBeVisible()

  // From here on, no page timer runs until the test says so.
  await page.clock.pauseAt(Date.now() + 60_000)
  await page.locator('main').click()
  await page.keyboard.press('Meta+k')
  const input = page.locator('input[placeholder="Search messages..."]')
  await expect(input).toBeVisible()

  await input.pressSequentially('NE')
  await page.clock.runFor(50) // the panel's focus timer runs now
  await input.pressSequentially('EDLE_X')

  await expect(input).toHaveValue('NEEDLE_X')
})

test('Cmd+F from elsewhere still selects the earlier query', async ({ page, mockBackend }) => {
  await mockBackend({ conversations: [summary], details: { [C]: detail } })
  await withNetRetry(page, () => page.goto(`/conversations/${C}`))
  await expect(page.locator('[data-message-uuid="e-0"]')).toBeVisible()

  await page.locator('main').click()
  await page.keyboard.press('Meta+k')
  const input = page.locator('input[placeholder="Search messages..."]')
  await input.fill('first')

  // Leave the input, then ask for it again: the panel moves the focus
  // itself, so it selects the earlier query and typing replaces it.
  await page.locator('[data-message-uuid="e-0"]').click()
  await expect(input).not.toBeFocused()
  await page.keyboard.press('Meta+f')
  await expect(input).toBeFocused()
  await page.keyboard.type('second')
  await expect(input).toHaveValue('second')
})
