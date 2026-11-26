import { test, expect, Route, withNetRetry, installLocalPrefsMock } from './fixtures'
import { CM_CONV_UUID, CM_DETAIL, CM_SUMMARY, CM_WORDS } from '../src/test/fixtures/codeMode'

/**
 * Issue #90: Code Mode runs in the viewer, with synthetic data.
 *
 * - The toolbar shows Show Code Mode only in a conversation with a run.
 * - Off (the default): the run is hidden. On: one dark terminal bubble
 *   with the highlighted script, the numbered calls, and what it returned.
 *   Show Tools does not change it.
 * - Expand opens the overlay: Script, Calls, Data flow, Returned. Esc
 *   closes it.
 * - An unlinked Code Mode call is a tool call, labeled.
 * - An empty search names hidden Code Mode runs when they hold a match;
 *   its button turns the toggle on and the hit shows.
 * - No unexpected console errors (the consoleAssertions auto-fixture).
 */

const PLAIN_UUID = 'c0de0000-0000-4000-8000-0000000000f3'
const plain = {
  ...CM_SUMMARY,
  uuid: PLAIN_UUID,
  name: 'Plain session',
}

async function mockBackend(page: import('@playwright/test').Page) {
  const searches: URLSearchParams[] = []
  await page.route('**/api/conversations**', (route: Route) => {
    const url = route.request().url()
    for (const uuid of [CM_CONV_UUID, PLAIN_UUID]) {
      if (url.includes(`/conversations/${uuid}/tree`)) {
        return route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({ uuid, root_messages: [], active_path: [] }),
        })
      }
      if (url.includes(`/conversations/${uuid}`)) {
        const detail = uuid === CM_CONV_UUID
          ? CM_DETAIL
          : { ...CM_DETAIL, ...plain, messages: [CM_DETAIL.messages[0], CM_DETAIL.messages[2]], current_leaf_message_uuid: 'cm-a2' }
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(detail) })
      }
    }
    if (url.match(/\/api\/conversations(\?|$)/)) {
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify([CM_SUMMARY, plain]) })
    }
    return route.continue()
  })
  await page.route('**/api/config', (route: Route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data_dir: '/tmp', config_corrupt_reason: null }) }),
  )
  await installLocalPrefsMock(page)
  await page.route('**/api/search**', async (route: Route) => {
    const params = new URL(route.request().url()).searchParams
    searches.push(params)
    // The script word is only inside the run: found only with include_code_mode.
    const hit = params.get('q') === CM_WORDS.code && params.get('include_code_mode') === 'true'
    const results = hit ? [{
      conversation_uuid: CM_CONV_UUID,
      conversation_name: CM_SUMMARY.name,
      conversation_updated_at: CM_SUMMARY.updated_at,
      conversation_created_at: CM_SUMMARY.created_at,
      project_name: 'cm',
      matching_messages: [{
        message_uuid: 'cm-a1', sender: 'assistant', snippet: `// ${CM_WORDS.code}`,
        match_start: 3, match_end: 3 + CM_WORDS.code.length, created_at: '2026-11-24T10:01:00Z',
      }],
    }] : []
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ results, total_messages_matched: results.length, returned_messages: results.length, truncated: false }),
    })
  })
  return searches
}

async function open(page: import('@playwright/test').Page, uuid = CM_CONV_UUID) {
  await withNetRetry(page, () => page.goto(`/conversations/${uuid}`))
  await expect(page.locator('[data-message-uuid="cm-a2"]')).toBeVisible()
}

test.describe('Code Mode runs (#90)', () => {
  test('the toggle shows only in a conversation with a run', async ({ page }) => {
    await mockBackend(page)
    await open(page, PLAIN_UUID)
    await expect(page.getByTestId('header-show-tools-checkbox')).toBeVisible()
    await expect(page.getByTestId('header-show-code-mode-checkbox')).toHaveCount(0)
    await open(page)
    await expect(page.getByTestId('header-show-code-mode-checkbox')).toBeVisible()
  })

  test('off by default; on shows the terminal bubble; Show Tools does not change it', async ({ page }) => {
    await mockBackend(page)
    await open(page)
    await expect(page.getByTestId('code-run')).toHaveCount(0)
    await page.getByTestId('header-show-tools-checkbox').check()
    await expect(page.getByText('Tool: shell')).toBeVisible()
    await expect(page.getByTestId('code-run')).toHaveCount(0)

    await page.getByTestId('header-show-code-mode-checkbox').check()
    const run = page.getByTestId('code-run')
    await expect(run).toHaveCount(1)
    await expect(run.getByTestId('code-run-title')).toContainText('Code Mode')
    await expect(run.getByTestId('code-run-title')).toContainText('21 lines')
    await expect(run.locator('.hljs-keyword').first()).toBeVisible()
    await expect(run.getByTestId('code-run-line-number')).toHaveCount(15)
    await expect(run.getByTestId('code-run-call')).toHaveCount(2)
    await expect(run.getByTestId('code-run-returned')).toContainText(CM_WORDS.returned)

    await page.getByTestId('header-show-tools-checkbox').uncheck()
    await expect(page.getByTestId('code-run')).toHaveCount(1)
  })

  test('an unlinked Code Mode call is a labeled tool call', async ({ page }) => {
    await mockBackend(page)
    await open(page)
    await page.getByTestId('header-show-tools-checkbox').check()
    await expect(page.getByTestId('tool-from-code-mode')).toHaveText('made by a Code Mode script (one of 2 running)')
  })

  test('Expand opens the overlay with four tabs; Esc closes it', async ({ page }) => {
    await mockBackend(page)
    await open(page)
    await page.getByTestId('header-show-code-mode-checkbox').check()
    await page.getByTestId('code-run').getByRole('button', { name: 'Expand' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('tab')).toHaveText(['Script', 'Calls', 'Data flow', 'Returned'])
    await expect(dialog.getByTestId('code-run-line-number')).toHaveCount(21)
    await dialog.getByRole('tab', { name: 'Calls' }).click()
    await expect(dialog.getByTestId('code-run-call')).toHaveCount(2)
    await dialog.getByRole('tab', { name: 'Data flow' }).click()
    await expect(dialog.getByTestId('sequence-lane')).toHaveText(['Model', 'Script', 'Bash', 'Write'])
    await dialog.getByRole('tab', { name: 'Returned' }).click()
    await expect(dialog.getByTestId('code-run-returned')).toContainText(CM_WORDS.returned)
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
  })

  test('an empty search names hidden Code Mode runs; the button turns the toggle on and finds the hit', async ({ page }) => {
    const searches = await mockBackend(page)
    await open(page)
    await page.keyboard.press(process.platform === 'darwin' ? 'Meta+f' : 'Control+f')
    await page.getByPlaceholder('Search messages...').fill(CM_WORDS.code)

    await expect(page.getByText(/No matches for/)).toBeVisible({ timeout: 5000 })
    await expect(page.getByTestId('search-empty-filters')).toContainText('hidden Code Mode runs')
    // The search itself left the runs out; only the probe looked inside.
    expect(searches.some((p) => p.get('q') === CM_WORDS.code && p.get('include_code_mode') === null)).toBe(true)

    await page.getByTestId('search-empty-show-code-mode').click()
    await expect(page.getByTestId('header-show-code-mode-checkbox')).toBeChecked()
    await expect(page.locator('[data-result-card]')).toHaveCount(1)
    await expect(page.getByTestId('code-run')).toHaveCount(1)
  })
})
