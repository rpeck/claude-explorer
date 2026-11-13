// Spec-driven test: anchored glob patterns (issue #42) and the edit flow.
//
// UX.md clauses verified ("Composable filters", under **Atoms**):
//   - "Glob: ... A leading `^` or a trailing `$` anchors the pattern at
//     that end, as in a regex."
//   - "Patterns match the exact title text, case-insensitively."
//   - The atom editor shows a help line for the current mode.
//
// Every negative count comes after a positive assertion. The first render
// happens before the preferences arrive, so a lone `toHaveCount(0)` can
// pass before the filter applies.

import { test, expect, withNetRetry } from './fixtures'
import { makeSummary } from './fixtures'

const conversations = [
  makeSummary({ uuid: 'c-daily', name: 'Daily digest' }),
  makeSummary({ uuid: 'c-weekly', name: 'Weekly report' }),
  makeSummary({ uuid: 'c-daily-notes', name: 'Daily digest notes' }),
  makeSummary({ uuid: 'c-kickoff', name: 'Project kickoff' }),
]

function seed(behavior: 'hide' | 'show-only', patterns: string[], activeId: string | null = 'a-1') {
  return {
    filters: {
      nodes: {
        'a-1': {
          id: 'a-1',
          type: 'atom',
          name: 'Exact titles',
          enabled: true,
          behavior,
          patterns,
          mode: 'glob',
          target: 'title',
        },
      },
      activeId,
      _migratedV1: true,
      _migratedV2: true,
    },
  }
}

const title = (page: import('@playwright/test').Page, name: string) =>
  page.getByText(name, { exact: true })

async function openModal(page: import('@playwright/test').Page) {
  const picker = page.getByTestId('active-filter-select')
  await picker.click()
  const manageOpt = page.getByRole('option', { name: /manage filters/i }).or(
    page.getByRole('menuitem', { name: /manage filters/i }),
  ).first()
  await expect(manageOpt).toBeVisible()
  await manageOpt.click()
  const modal = page.getByRole('dialog')
  await expect(modal).toBeVisible()
  return modal
}

async function pickFilter(page: import('@playwright/test').Page, name: string | RegExp) {
  const picker = page.getByTestId('active-filter-select')
  await picker.click()
  const opt = page.getByRole('option', { name }).first()
  await expect(opt).toBeVisible()
  await opt.click()
}

test.describe('Glob anchors ^ and $ (issue #42)', () => {
  test('Show only matches with ^…$ globs shows exactly the matching titles', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, preferences: seed('show-only', ['^Daily digest$', '^Weekly report$']) })
    await withNetRetry(page, () => page.goto('/'))

    await expect(title(page, 'Daily digest')).toBeVisible()
    await expect(title(page, 'Weekly report')).toBeVisible()
    await expect(title(page, 'Daily digest notes')).toHaveCount(0)
    await expect(title(page, 'Project kickoff')).toHaveCount(0)
  })

  test('Hide matches with ^…$ globs hides exactly the matching titles', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, preferences: seed('hide', ['^Daily digest$', '^Weekly report$']) })
    await withNetRetry(page, () => page.goto('/'))

    await expect(title(page, 'Daily digest notes')).toBeVisible()
    await expect(title(page, 'Project kickoff')).toBeVisible()
    await expect(title(page, 'Daily digest')).toHaveCount(0)
    await expect(title(page, 'Weekly report')).toHaveCount(0)
  })

  test('An edit to the patterns applies after Save and Close, also after "All conversations"', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, preferences: seed('show-only', ['Project'], null) })
    await withNetRetry(page, () => page.goto('/'))
    await expect(title(page, 'Daily digest')).toBeVisible()

    const modal = await openModal(page)
    await modal.getByText(/Exact titles/).first().click()
    await modal.getByTestId('filter-editor-patterns').fill('Daily digest notes\nkickoff')
    await modal.getByTestId('filter-editor-save').click()
    await page.keyboard.press('Escape')
    await expect(modal).toHaveCount(0)

    await pickFilter(page, /Exact titles/)
    await expect(title(page, 'Daily digest notes')).toBeVisible()
    await expect(title(page, 'Project kickoff')).toBeVisible()
    await expect(title(page, 'Daily digest')).toHaveCount(0)
    await expect(title(page, 'Weekly report')).toHaveCount(0)

    await pickFilter(page, /^All conversations$/i)
    await expect(title(page, 'Weekly report')).toBeVisible()
    await pickFilter(page, /Exact titles/)
    await expect(title(page, 'Project kickoff')).toBeVisible()
    await expect(title(page, 'Weekly report')).toHaveCount(0)
  })

  test('The atom editor explains the rules of the current mode', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, preferences: seed('show-only', ['^Daily digest$'], null) })
    await withNetRetry(page, () => page.goto('/'))

    const modal = await openModal(page)
    await modal.getByText(/Exact titles/).first().click()
    const help = modal.getByTestId('filter-editor-pattern-help')
    await expect(help).toContainText('A leading ^ or a trailing $ anchors the pattern at that end.')
    await expect(help).toContainText('exact title text')

    await modal.getByRole('radio', { name: /^regex$/i }).click()
    await expect(help).toContainText('JavaScript regular expression')
    await expect(help).not.toContainText('A leading ^')
  })
})
