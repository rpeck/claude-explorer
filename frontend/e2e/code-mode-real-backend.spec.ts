import { test, expect, withNetRetry, installLocalPrefsMock } from './fixtures'

/**
 * Issue #90 on the real backend: no mock of /api/conversations or
 * /api/search. The backend that playwright.config.ts starts reads the
 * committed fixture tree, which holds scrubbed recordings of real Code Mode
 * sessions (scripts/generate_e2e_fixtures.py):
 *
 * - Claude Code 2.1.331: an MCP-to-CLI pipe (b007d327…) and a session with a
 *   failed, a refused, and a subagent-starting run (2339aab4…).
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
const CODEX_CASES = '01a2118a-13d2-77e2-b458-a98bffa8713c'

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

  test('real failed and refused runs', async ({ page }) => {
    await open(page, CC_CASES)
    await page.getByTestId('header-show-code-mode-checkbox').check()
    const runs = page.getByTestId('code-run')
    await expect(runs).toHaveCount(4)
    await expect(runs.nth(0).getByTestId('code-run-title')).toContainText('failed')
    await expect(runs.nth(0).getByTestId('code-run-error')).toContainText('synthetic failure')
    await expect(runs.nth(1).getByTestId('code-run-call')).toContainText('refused')
    await expect(runs.nth(2).getByTestId('code-run-stdout')).toContainText('y = 2')
  })

  test('real Codex cells: failed, terminated, and the call made while two were alive', async ({ page }) => {
    await open(page, CODEX_CASES)
    await page.getByTestId('header-show-code-mode-checkbox').check()
    const titles = page.getByTestId('code-run-title')
    await expect(titles).toHaveCount(6)
    await expect(titles.nth(0)).toContainText('failed')
    await expect(page.getByTestId('code-run-error').first()).toContainText('synthetic failure')
    await expect(titles.nth(5)).toContainText('terminated')

    await page.getByTestId('header-show-tools-checkbox').check()
    await expect(page.getByTestId('tool-from-code-mode').filter({ hasText: 'one of 2 running' })).toHaveCount(1)
  })
})
