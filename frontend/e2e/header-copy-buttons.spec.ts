import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'
import type { Locator, Page } from '@playwright/test'

/**
 * Copy buttons in the Conversation Pane header
 * (PLANS/2026.10.28-header-copy-buttons.md, "Test Cases", e2e 1, 2, 4).
 *
 * Black-box contract:
 *   - Every header value gets a copy button: session name (icon-only,
 *     accessible name "Copy session name"), UUID, file path, project path,
 *     git branch, sandbox path. Tooltips: "Click to copy <label>".
 *   - A click writes the exact value from the API (a long or Windows path
 *     included). Success: a check mark with class `text-green-500` for 2 s.
 *   - The feedback belongs to the copied value: open another conversation
 *     within 2 s, and its UUID button shows the copy icon, not the check.
 *
 * Settle signal before each clipboard read: the green check inside the
 * clicked button, which shows only after the clipboard write resolved
 * (same pattern as cc-thinking-not-rendered.spec.ts). The console gate
 * (e2e 4) is the auto fixture `consoleAssertions` from ./fixtures.
 */

const CC = '00000000-0000-4000-8000-0000000cb001'
const COWORK = '00000000-0000-4000-8000-0000000cb002'
const DESKTOP = '00000000-0000-4000-8000-0000000cb003'
const DESKTOP_B = '00000000-0000-4000-8000-0000000cb004'

const CC_NAME = 'Copy buttons: Claude Code fixture "quoted" & more'
const CC_PROJECT = '/Users/tester/Source/header-copy-buttons-project-with-a-long-folder-name/packages/web'
const CC_BRANCH = 'feat/copy-every-header-value'
const CC_FILE =
  '/Users/tester/.claude/projects/-Users-tester-Source-header-copy-buttons-project-with-a-long-folder-name-packages-web/' +
  `${CC}.jsonl`

const COWORK_NAME = 'Copy buttons: Cowork fixture'
const COWORK_SANDBOX = '/sessions/vm-copy-buttons/work'

const DESKTOP_NAME = 'Copy buttons: Desktop fixture'
const DESKTOP_FILE = `C:\\Users\\tester\\.claude-explorer\\conversations\\${DESKTOP}.json`
const DESKTOP_B_NAME = 'Copy buttons: second Desktop fixture'

function oneMessage(prefix: string) {
  return [
    makeMessage({
      uuid: `${prefix}-m1`,
      sender: 'human',
      text: 'Anything',
      content: [{ type: 'text', text: 'Anything' }],
    }),
  ]
}

const ccSummary = makeSummary({
  uuid: CC,
  name: CC_NAME,
  source: 'CLAUDE_CODE',
  message_count: 1,
  human_message_count: 1,
  project_path: CC_PROJECT,
  project_name: 'web',
  git_branch: CC_BRANCH,
})
const ccDetail = makeDetail(ccSummary, oneMessage('cc'), { file_path: CC_FILE })

const coworkSummary = makeSummary({
  uuid: COWORK,
  name: COWORK_NAME,
  source: 'CLAUDE_COWORK',
  message_count: 1,
  human_message_count: 1,
})
const coworkDetail = makeDetail(coworkSummary, oneMessage('cw'), { sandbox_path: COWORK_SANDBOX })

const desktopSummary = makeSummary({
  uuid: DESKTOP,
  name: DESKTOP_NAME,
  source: 'CLAUDE_AI',
  message_count: 1,
  human_message_count: 1,
})
const desktopDetail = makeDetail(desktopSummary, oneMessage('dt'), { file_path: DESKTOP_FILE })

const desktopBSummary = makeSummary({
  uuid: DESKTOP_B,
  name: DESKTOP_B_NAME,
  source: 'CLAUDE_AI',
  message_count: 1,
  human_message_count: 1,
  updated_at: '2026-04-01T09:00:00Z',
})
const desktopBDetail = makeDetail(desktopBSummary, oneMessage('dtb'), {
  file_path: `/Users/tester/.claude-explorer/conversations/${DESKTOP_B}.json`,
})

async function openConversation(page: Page, uuid: string, name: string): Promise<Locator> {
  await withNetRetry(page, () => page.goto(`/conversations/${uuid}`))
  const header = page.locator('header').first()
  await expect(header.getByRole('heading', { level: 1, name })).toBeVisible()
  return header
}

/** Click a copy button, wait for its green check, and return the clipboard text. */
async function copyAndRead(page: Page, button: Locator): Promise<string> {
  await expect(button).toBeVisible()
  await button.click()
  await expect(button.locator('.text-green-500')).toBeVisible()
  return page.evaluate(() => navigator.clipboard.readText())
}

test.describe('Header copy buttons — each copies its exact value (e2e 1, 4)', () => {
  test('Claude Code: session name, UUID, file path, project path, git branch', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [ccSummary], details: { [CC]: ccDetail } })
    const header = await openConversation(page, CC, CC_NAME)

    const cases: Array<[string, Locator, string]> = [
      ['session name', header.getByRole('button', { name: 'Copy session name' }), CC_NAME],
      ['UUID', header.getByTitle('Click to copy UUID'), CC],
      ['file path', header.getByTitle('Click to copy file path'), CC_FILE],
      ['project path', header.getByTitle('Click to copy project path'), CC_PROJECT],
      ['git branch', header.getByTitle('Click to copy git branch'), CC_BRANCH],
    ]
    for (const [label, button, value] of cases) {
      expect(await copyAndRead(page, button), `${label} button copies the exact value`).toBe(value)
    }
    await expect(header.getByRole('button', { name: 'Copy session name' })).toHaveAttribute(
      'title',
      'Click to copy session name',
    )
    await expect(header.getByTitle('Click to copy sandbox path')).toHaveCount(0)
  })

  test('Cowork: session name, UUID, sandbox path', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [coworkSummary], details: { [COWORK]: coworkDetail } })
    const header = await openConversation(page, COWORK, COWORK_NAME)

    const cases: Array<[string, Locator, string]> = [
      ['session name', header.getByRole('button', { name: 'Copy session name' }), COWORK_NAME],
      ['UUID', header.getByTitle('Click to copy UUID'), COWORK],
      ['sandbox path', header.getByTitle('Click to copy sandbox path'), COWORK_SANDBOX],
    ]
    for (const [label, button, value] of cases) {
      expect(await copyAndRead(page, button), `${label} button copies the exact value`).toBe(value)
    }
    await expect(header.getByText('Sandbox path:')).toBeVisible()
    await expect(header.getByTitle('Click to copy project path')).toHaveCount(0)
    await expect(header.getByTitle('Click to copy git branch')).toHaveCount(0)
  })

  test('Desktop: session name, UUID, file path (a Windows path, unchanged)', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [desktopSummary], details: { [DESKTOP]: desktopDetail } })
    const header = await openConversation(page, DESKTOP, DESKTOP_NAME)

    const cases: Array<[string, Locator, string]> = [
      ['session name', header.getByRole('button', { name: 'Copy session name' }), DESKTOP_NAME],
      ['UUID', header.getByTitle('Click to copy UUID'), DESKTOP],
      ['file path', header.getByTitle('Click to copy file path'), DESKTOP_FILE],
    ]
    for (const [label, button, value] of cases) {
      expect(await copyAndRead(page, button), `${label} button copies the exact value`).toBe(value)
    }
    await expect(header.getByTitle('Click to copy project path')).toHaveCount(0)
    await expect(header.getByTitle('Click to copy git branch')).toHaveCount(0)
    await expect(header.getByTitle('Click to copy sandbox path')).toHaveCount(0)
  })
})

test.describe('Header copy buttons — the check mark belongs to the copied value (e2e 2, 4)', () => {
  test('copy the UUID of A, open B from the Conversation List within 2 s: B shows the copy icon', async ({
    page,
    mockBackend,
  }) => {
    // Issue #99: page time, not wall time. The test used to fail when a
    // loaded machine took more than 2 s from the copy to the read, because
    // A's 2 s reset could then fire first. The page clock is paused after
    // the copy and moves only when the test moves it, so the reset cannot
    // fire before the read, however slow the machine is.
    await page.clock.install()
    await mockBackend({
      conversations: [desktopSummary, desktopBSummary],
      details: { [DESKTOP]: desktopDetail, [DESKTOP_B]: desktopBDetail },
    })
    // The header's copy button remounts when the conversation changes, so
    // this end-to-end path cannot show a check mark that outlives its value;
    // the unit tests of CopyValueButton cover that logic (value changes).
    const header = await openConversation(page, DESKTOP, DESKTOP_NAME)
    const uuidButton = header.getByTitle('Click to copy UUID')
    await expect(uuidButton).toContainText(DESKTOP)

    // Pause 1 s ahead of the page's clock: it keeps running until the pause,
    // and a smaller margin raced it ("Cannot fast-forward to the past").
    // The jump comes before the copy, so it does not touch A's window.
    await page.clock.pauseAt((await page.evaluate(() => Date.now())) + 1000)
    expect(await copyAndRead(page, uuidButton)).toBe(DESKTOP)

    const row = page.locator('aside').getByText(DESKTOP_B_NAME, { exact: true })
    await expect(row).toBeVisible()
    await row.click()

    // The router and the data layer schedule their updates with timers, so
    // move page time in 50 ms steps until B's header and UUID show. The
    // bound counts page time (at most 1,500 ms), so it stays inside A's
    // 2 s window on any machine.
    const bHeading = header.getByRole('heading', { level: 1, name: DESKTOP_B_NAME })
    let pageMs = 0
    while (!((await bHeading.isVisible()) && (await uuidButton.textContent())?.includes(DESKTOP_B))) {
      expect(pageMs, 'B did not open within 1,500 ms of page time').toBeLessThan(1500)
      await page.clock.runFor(50)
      pageMs += 50
    }
    await page.clock.runFor(32) // two animation frames

    const read = () =>
      uuidButton.evaluate((el) => ({
        text: el.textContent ?? '',
        check: el.querySelector('.text-green-500') !== null,
        copyIcon: el.querySelector('.lucide-copy') !== null,
      }))
    // One read, no retry: a retrying assertion would pass once A's 2 s
    // reset fires, which is the bug this test exists to catch.
    const state = await read()
    expect(state.text).toContain(DESKTOP_B)
    expect(state.check, "B's UUID button must not show A's check mark").toBe(false)
    expect(state.copyIcon, "B's UUID button shows the copy icon").toBe(true)

    // A's reset fires now; B keeps its copy icon.
    await page.clock.runFor(2500)
    const after = await read()
    expect(after.check).toBe(false)
    expect(after.copyIcon).toBe(true)
  })
})
