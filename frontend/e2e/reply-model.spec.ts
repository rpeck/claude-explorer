import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry, type Page } from './fixtures'
import type { Message } from '../src/lib/types'

/**
 * B52 (issue #52), end to end: each reply names its model, and the output
 * of a slash command shows under its command.
 *
 * The session switches with `/model` from Opus 5.5 to Fable 5.1. The
 * detail is the shape that the backend gives after B52 (`model` on each
 * reply, `command_output` on the marker); backend/tests/test_reply_model.py
 * and test_cc_command_output.py pin that the reader makes it, and that the
 * Markdown export of the backend carries the same names.
 *
 * The data is mocked like the other specs: a new file in
 * backend/tests/fixtures/claude changes the goldens and the registry tests,
 * which copy that whole folder. The console gate of TESTING.md §5.15 runs
 * for every test (`./fixtures`).
 *
 * B52 is not implemented yet, so the tests are expected failures.
 * `B52_RUN_XFAIL=1` runs them as plain tests. Remove the switch when B52
 * passes.
 */

const UUID = 'b52f0000-50ba-41c0-8495-0e8749b20552'
const TITLE = 'B52 fixture: compare two models'
const MODEL_OUTPUT = 'Set model to Fable 5.1 (claude-fable-5-1)'

const withFields = (m: Message, fields: Record<string, unknown>): Message => ({ ...m, ...fields }) as Message

const summary = makeSummary({
  uuid: UUID,
  name: TITLE,
  source: 'CLAUDE_CODE',
  model: 'claude-opus-5-5',
  message_count: 7,
  human_message_count: 5,
  project_path: '/fixture/project',
  project_name: 'project',
})

const messages: Message[] = [
  makeMessage({
    uuid: 'b52-u1',
    sender: 'human',
    text: TITLE,
    content: [{ type: 'text', text: TITLE }],
    created_at: '2026-04-02T10:00:00Z',
  }),
  withFields(
    makeMessage({
      uuid: 'b52-a1',
      sender: 'assistant',
      text: 'Opus here: the first answer.',
      content: [{ type: 'text', text: 'Opus here: the first answer.' }],
      created_at: '2026-04-02T10:00:05Z',
      parent_message_uuid: 'b52-u1',
    }),
    { model: 'claude-opus-5-5', model_from_conversation: false },
  ),
  withFields(
    makeMessage({
      uuid: 'b52-c1',
      sender: 'human',
      text: 'Session: /model',
      content: [{ type: 'text', text: 'Session: /model' }],
      created_at: '2026-04-02T10:01:00Z',
      parent_message_uuid: 'b52-a1',
      is_command_marker: true,
      slash_command: '/model',
    }),
    { command_output: MODEL_OUTPUT, command_output_is_error: false },
  ),
  makeMessage({
    uuid: 'b52-u2',
    sender: 'human',
    text: 'Now answer again.',
    content: [{ type: 'text', text: 'Now answer again.' }],
    created_at: '2026-04-02T10:02:00Z',
    parent_message_uuid: 'b52-c1',
  }),
  withFields(
    makeMessage({
      uuid: 'b52-a2',
      sender: 'assistant',
      text: 'Fable here: the second answer.',
      content: [{ type: 'text', text: 'Fable here: the second answer.' }],
      created_at: '2026-04-02T10:02:05Z',
      parent_message_uuid: 'b52-u2',
    }),
    { model: 'claude-fable-5-1', model_from_conversation: false },
  ),
]

async function openSession(page: Page) {
  await withNetRetry(page, () => page.goto(`/conversations/${UUID}`))
  const header = page.locator('header').first()
  await expect(header.getByRole('heading', { level: 1, name: TITLE })).toBeVisible()
  await expect(page.locator('[data-message-uuid="b52-a2"]')).toBeAttached()
  return header
}

test.describe('B52: the model of each reply, and command output', () => {
  test.fail(!process.env.B52_RUN_XFAIL, 'B52 not implemented yet')

  test.beforeEach(async ({ mockBackend }) => {
    await mockBackend({ conversations: [summary], details: { [UUID]: makeDetail(summary, messages) } })
  })

  test('a /model switch: "Claude Opus 5.5" before it, its output, and "Claude Fable 5.1" after it', async ({ page }) => {
    await openSession(page)
    await expect(page.locator('[data-message-uuid="b52-a1"]').getByTestId('message-sender')).toHaveText('Claude Opus 5.5')
    await expect(page.locator('[data-message-uuid="b52-a2"]').getByTestId('message-sender')).toHaveText('Claude Fable 5.1')

    const output = page.locator('[data-message-uuid="b52-c1"]').getByTestId('command-output')
    await expect(output).toContainText(MODEL_OUTPUT)
    await expect(output).toContainText('Claude Code')

    // DOM order: the Opus reply, then the /model output, then the Fable reply.
    const order = await page
      .locator('[data-testid="message-sender"], [data-testid="command-output"]')
      .evaluateAll((els) =>
        els
          .map((el) => (el.getAttribute('data-testid') === 'command-output' ? 'output' : (el.textContent ?? '').trim()))
          .filter((t) => t !== 'You'),
      )
    expect(order).toEqual(['Claude Opus 5.5', 'output', 'Claude Fable 5.1'])
  })

  test('the header badge shows the model name, with the raw id as its tooltip', async ({ page }) => {
    const header = await openSession(page)
    const badge = header.getByTestId('model-badge')
    await expect(badge).toHaveText('Opus 5.5')
    await expect(badge).toHaveAttribute('title', 'claude-opus-5-5')
  })

  test('"Copy as Markdown" carries the model name of each reply', async ({ page }) => {
    const header = await openSession(page)
    const button = header.getByRole('button', { name: /Copy as Markdown/i })
    await button.click()
    await expect(button.locator('.text-green-500')).toBeVisible()
    const text = await page.evaluate(() => navigator.clipboard.readText())
    expect(text).toContain('**Claude Opus 5.5:**')
    expect(text).toContain('**Claude Fable 5.1:**')
    expect(text.indexOf('**Claude Opus 5.5:**')).toBeLessThan(text.indexOf('**Claude Fable 5.1:**'))
    expect(text).not.toContain('**Claude:**')
  })

  test('"Copy message as Markdown" on a reply carries its model name', async ({ page }) => {
    await openSession(page)
    const bubble = page.locator('[data-message-uuid="b52-a2"]')
    await bubble.hover()
    const button = bubble.getByTitle('Copy message as Markdown')
    await button.click()
    await expect(button.locator('.text-green-500')).toBeVisible()
    const text = await page.evaluate(() => navigator.clipboard.readText())
    expect(text).toContain('**Claude Fable 5.1:**')
  })
})
