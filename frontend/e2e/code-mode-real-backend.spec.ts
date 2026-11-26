import { test, expect, withNetRetry, installLocalPrefsMock, waitForKeyboardShortcuts } from './fixtures'

/**
 * Issue #90 on the real backend: no mock of /api/conversations or
 * /api/search. The backend that playwright.config.ts starts reads the
 * committed fixture tree, which holds scrubbed recordings of real Code Mode
 * sessions (scripts/generate_e2e_fixtures.py):
 *
 * - Claude Code 2.1.331: an MCP-to-CLI pipe (b007d327…) and a session with a
 *   failed, a refused, and a subagent-starting run (2339aab4…), and a
 *   session whose runs read a PNG and a PDF and cut a long output; the
 *   second run ended through the Poll tool (6c2110ef…).
 * - Codex 0.164.0: a session with a failed script, `wait`, `notify()`, two
 *   cells alive at once, and a terminated cell (01a2118a…).
 *
 * The e2e backend runs with the search index off, so search uses the linear
 * path. Only the preferences are mocked (installLocalPrefsMock), so UI state
 * such as an open Search Pane does not carry from one test to the next. The consoleAssertions auto-fixture fails on any unexpected console
 * error.
 */

const CC_PIPE = 'b007d327-80c4-46ea-b737-53d84b30c624'
const CC_CASES = '2339aab4-744c-4556-95b8-5ee5552c1b85'
const CC_POLL = '6c2110ef-d09c-4f7b-9e2e-657d6e24353d'
const CODEX_CASES = '01a2118a-13d2-77e2-b458-a98bffa8713c'

/** Scroll the virtualized message stream until it settles at the bottom. */
async function scrollToBottom(page: import('@playwright/test').Page) {
  await page.evaluate(async () => {
    const stream = document.querySelector('[data-testid="message-stream"]') as HTMLElement | null
    if (!stream) return
    let lastHeight = -1
    for (let i = 0; i < 60; i++) {
      stream.scrollTop = stream.scrollHeight
      await new Promise((r) => setTimeout(r, 100))
      const atBottom = stream.scrollTop + stream.clientHeight >= stream.scrollHeight - 2
      if (stream.scrollHeight === lastHeight && atBottom) return
      lastHeight = stream.scrollHeight
    }
  })
}

async function open(page: import('@playwright/test').Page, uuid: string) {
  await installLocalPrefsMock(page)
  await withNetRetry(page, () => page.goto(`/conversations/${uuid}`))
  await expect(page.getByTestId('header-show-code-mode-checkbox')).toBeVisible()
}

test.describe('Code Mode runs on the real backend (#90)', () => {
  test('a real MCP-to-CLI pipe: one block, the pipe, and what reached the model', async ({ page }) => {
    await open(page, CC_PIPE)
    await expect(page.getByTestId('code-run')).toHaveCount(0)
    await page.getByTestId('header-show-code-mode-checkbox').check()

    const run = page.getByTestId('code-run')
    await expect(run).toHaveCount(1)
    await expect(run.getByTestId('code-run-title')).toContainText('completed')
    await expect(run.getByTestId('code-run-title')).toContainText('background')
    const calls = run.getByTestId('code-run-call')
    await expect(calls).toHaveCount(2)
    await expect(calls.nth(0)).toContainText('mcp__fetch__fetch')
    await expect(calls.nth(1)).toContainText('Bash')
    await expect(calls.nth(1)).toContainText('input from call 1')
    await expect(run.getByTestId('code-run-returned')).toHaveText(/31/)

    await run.getByRole('button', { name: 'Expand' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('tab', { name: 'Data flow' }).click()
    await expect(dialog.getByTestId('sequence-lane')).toHaveText(['Model', 'Script', 'mcp__fetch__fetch', 'Bash'])
    await expect(dialog.getByTestId('sequence-step-label').filter({ hasText: 'output of call 1 → call 2' })).toHaveCount(1)
    await expect(dialog.getByTestId('code-run-context')).toHaveText(
      'Tool output: 216 characters. Returned to the model: 2 characters.',
    )
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
  })

  test('a word only inside a run: the empty search names the runs, and the button finds it', async ({ page }) => {
    await open(page, CC_PIPE)
    await page.keyboard.press(process.platform === 'darwin' ? 'Meta+f' : 'Control+f')
    await page.getByPlaceholder('Search messages...').fill('EOF_PAGE')
    await expect(page.getByText(/No matches for/)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('search-empty-filters')).toContainText('hidden Code Mode runs')

    await page.getByTestId('search-empty-show-code-mode').click()
    await expect(page.getByTestId('header-show-code-mode-checkbox')).toBeChecked()
    const cards = page.locator('[data-result-card]')
    await expect(cards).toHaveCount(1, { timeout: 10_000 })
    await expect(cards.first()).toContainText('Use your REPL')
  })

  test('a real run that read an image and a PDF, and a cut output that ended through Poll', async ({ page }) => {
    await open(page, CC_POLL)
    await page.getByTestId('header-show-code-mode-checkbox').check()
    const media = page.getByTestId('code-run').filter({ hasText: 'tiny.pdf' })
    const box = media.getByTestId('code-run-media')
    await expect(box.getByRole('img')).toHaveAttribute('src', /^data:image\/png;base64,iVBORw0KGgo/)
    await expect(box.getByRole('link', { name: 'Download document 1 (application/pdf)' })).toHaveAttribute('download', 'code-mode-document-1.pdf')

    await scrollToBottom(page)
    const cut = page.getByTestId('code-run').filter({ hasText: '400000' })
    await expect(cut.getByTestId('code-run-title')).toContainText('completed')
    await expect(cut.getByTestId('code-run-returned')).toHaveText(/done/)
    await expect(cut.getByTestId('code-run-cut-note')).toContainText('Part of this output was cut.')
  })

  test('Cmd+G steps into a run and out again, and the toggle sets the match count', async ({ page }) => {
    // "searchTools" is in the script (match 1, the newest message) and in the user prompt (match 2).
    await open(page, CC_PIPE)
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.getByTestId('header-show-code-mode-checkbox').check()
    await page.keyboard.press(process.platform === 'darwin' ? 'Meta+f' : 'Control+f')
    await page.getByPlaceholder('Search messages...').fill('searchTools')
    await expect(page.getByText(/1\s+of\s+2\s+matches/)).toBeVisible({ timeout: 10_000 })
    const runMark = page.getByTestId('code-run').locator('mark', { hasText: 'searchTools' })
    await expect(runMark.first()).toBeInViewport()

    await page.keyboard.press('Meta+g')
    await expect(page.getByText(/2\s+of\s+2\s+matches/)).toBeVisible()
    await expect(runMark).toHaveCount(0)

    await page.keyboard.press('Meta+g')
    await expect(page.getByText(/1\s+of\s+2\s+matches/)).toBeVisible()
    await expect(runMark.first()).toBeInViewport()

    await page.getByTestId('header-show-code-mode-checkbox').uncheck()
    await expect(page.getByText(/of\s+1\s+match\b/)).toBeVisible({ timeout: 10_000 })
  })

  test('a word that is nowhere: the empty search still names the hidden runs (#80 option B)', async ({ page }) => {
    // The archive holds runs, so the user learns that hidden content might
    // match. The e2e backend runs with the index off, so the archive check
    // is the scan of the conversations.
    await open(page, CC_PIPE)
    await page.keyboard.press(process.platform === 'darwin' ? 'Meta+f' : 'Control+f')
    await page.getByPlaceholder('Search messages...').fill('qqzzuunowhere')
    await expect(page.getByText(/No matches for/)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('search-empty-filters')).toContainText('hidden Code Mode runs')
    await expect(page.getByTestId('search-empty-show-code-mode')).toHaveText('Search Code Mode runs too')
  })

  // The Conversation Pane is virtualized: only rows near the screen are in
  // the DOM, and row heights depend on the fonts (CI showed 5 of 6 runs).
  // So find each run by its content, and scroll to reach the later ones;
  // never count the rendered rows.
  test('real failed and refused runs', async ({ page }) => {
    await open(page, CC_CASES)
    await page.getByTestId('header-show-code-mode-checkbox').check()
    const failed = page.getByTestId('code-run').filter({ hasText: 'synthetic failure' })
    await expect(failed.getByTestId('code-run-title')).toContainText('failed')
    await expect(failed.getByTestId('code-run-error')).toContainText('synthetic failure')
    await expect(page.getByTestId('code-run-call').filter({ hasText: 'Write' })).toContainText('refused')
    await expect(page.getByTestId('code-run-stdout').filter({ hasText: 'y = 2' })).toHaveCount(1)
  })

  test('real Codex cells: failed, terminated, and the call made while two were alive', async ({ page }) => {
    await open(page, CODEX_CASES)
    await page.getByTestId('header-show-code-mode-checkbox').check()
    await page.getByTestId('header-show-tools-checkbox').check()
    const failed = page.getByTestId('code-run').filter({ hasText: 'synthetic failure' })
    await expect(failed.getByTestId('code-run-title')).toContainText('failed')

    await scrollToBottom(page)
    const terminated = page.getByTestId('code-run').filter({ hasText: 'sleep 30' })
    await expect(terminated.getByTestId('code-run-title')).toContainText('terminated')
    await expect(page.getByTestId('tool-from-code-mode').filter({ hasText: 'one of 2 running' })).toHaveCount(1)
  })
})
