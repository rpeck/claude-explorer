import { test, expect, waitForKeyboardShortcuts, withNetRetry, type Page } from './fixtures'
import {
  ccSession,
  coworkSession,
  daysAgo,
  desktopChat,
  detailsFor,
  expectRows,
  folderHeader,
  folderOrder,
  hoursAgo,
  listHonoringSource,
  listMenu,
  messageUuid,
  pickOption,
  row,
  sidebar,
  startClock,
  typeHours,
} from './sidebar-list-filters.helpers'
import type { ConversationSummary } from '../src/lib/types'

/**
 * Sidebar list filters, grouping ("Group sessions by project"): test cases
 * 11-14 and 16 of PLANS/2026.10.25-sidebar-list-filters.md. Black-box,
 * from the spec. Case 16 (no console errors or warnings) holds for every
 * test through the auto `consoleAssertions` fixture; the last test here
 * also checks it explicitly after using every new control.
 */

// ---------------------------------------------------------------------------
// Case 11 fixture. Four folders; names differ in case so that a
// case-sensitive comparison gives another order ("Beta" < "Delta" <
// "alpha" < "charlie" in code-point order). Two sessions per folder.
//
// For every sort field and direction, the expected folder order differs
// from: folder-name order (both directions, both case rules), the order
// in which the mock returns the folders (Beta, Delta, charlie, alpha) and
// its reverse, and the order from the wrong end of each folder (largest
// value for an ascending sort, smallest for a descending one).
// ---------------------------------------------------------------------------
const BASE = new Date('2026-04-10T00:00:00.000Z').getTime()
const at = (hoursFromBase: number) => new Date(BASE + hoursFromBase * 3_600_000).toISOString()

function folderSession(folder: string, name: string, updatedH: number, createdH: number): ConversationSummary {
  return ccSession(name, `/w/${folder}`, at(updatedH), { created_at: at(createdH) })
}

// The mock returns the rows in this order, which matches no expected order.
const FOLDER_ROWS: ConversationSummary[] = [
  folderSession('Beta', 'Fig', -46, -76),
  folderSession('Delta', 'Hazel', -41, -42),
  folderSession('charlie', 'cherry', -29, -50),
  folderSession('alpha', 'Banana', -23, -46),
  folderSession('alpha', 'grape', -50, -75),
  folderSession('charlie', 'apple', -32, -34),
  folderSession('Beta', 'Date', -16, -30),
  folderSession('Delta', 'elder', -14, -21),
]

const FOLDER_CASES: Array<{ field: string; label: string; order: 'asc' | 'desc'; expected: string[] }> = [
  // Last Activity (updated_at): desc = largest in folder, asc = smallest.
  { field: 'updated_at', label: 'Last Activity', order: 'desc', expected: ['Delta', 'Beta', 'alpha', 'charlie'] },
  { field: 'updated_at', label: 'Last Activity', order: 'asc', expected: ['alpha', 'Beta', 'Delta', 'charlie'] },
  // Start Time (created_at).
  { field: 'created_at', label: 'Start Time', order: 'desc', expected: ['Delta', 'Beta', 'charlie', 'alpha'] },
  { field: 'created_at', label: 'Start Time', order: 'asc', expected: ['Beta', 'alpha', 'charlie', 'Delta'] },
  // Title (name, ignoring case).
  { field: 'name', label: 'Title', order: 'desc', expected: ['Delta', 'alpha', 'Beta', 'charlie'] },
  { field: 'name', label: 'Title', order: 'asc', expected: ['charlie', 'alpha', 'Beta', 'Delta'] },
  // Project (folder name, ignoring case).
  { field: 'project', label: 'Project', order: 'desc', expected: ['Delta', 'charlie', 'Beta', 'alpha'] },
  { field: 'project', label: 'Project', order: 'asc', expected: ['alpha', 'Beta', 'charlie', 'Delta'] },
]

test.describe('Sidebar list filters: folder order (case 11)', () => {
  for (const c of FOLDER_CASES) {
    test(`11 folder order: ${c.label} ${c.order === 'desc' ? 'descending' : 'ascending'} gives ${c.expected.join(', ')}`, async ({
      page,
      mockBackend,
    }) => {
      await mockBackend({
        conversations: FOLDER_ROWS,
        preferences: { groupByProject: true, sortField: c.field, sortOrder: c.order },
        extraRoutes: listHonoringSource(FOLDER_ROWS),
      })
      await withNetRetry(page, () => page.goto('/'))
      await expectRows(page, FOLDER_ROWS, [])
      await expect(page.getByRole('checkbox', { name: /Group sessions by project/i })).toBeChecked()

      await expect.poll(() => folderOrder(page)).toEqual(c.expected)
    })
  }

  test('11 folder order: a tie sorts by folder name, A to Z, in both directions', async ({ page, mockBackend }) => {
    // beta and Alpha have the same newest and the same oldest session.
    const rows = [
      ccSession('Zulu newest', '/w/zulu', at(-1)),
      ccSession('Beta tie new', '/w/beta', at(-5)),
      ccSession('Zulu older', '/w/zulu', at(-2)),
      ccSession('Beta tie old', '/w/beta', at(-10)),
      ccSession('Alpha tie new', '/w/Alpha', at(-5)),
      ccSession('Alpha tie old', '/w/Alpha', at(-10)),
    ]
    await mockBackend({
      conversations: rows,
      preferences: { groupByProject: true, sortField: 'updated_at', sortOrder: 'desc' },
      extraRoutes: listHonoringSource(rows),
    })
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, rows, [])
    await expect.poll(() => folderOrder(page)).toEqual(['zulu', 'Alpha', 'beta'])

    // Ascending: Alpha and beta tie on their oldest session; zulu's oldest is the newest.
    await sidebar(page).getByTitle('Descending').click()
    await expect(sidebar(page).getByTitle('Ascending')).toBeVisible()
    await expect.poll(() => folderOrder(page)).toEqual(['Alpha', 'beta', 'zulu'])
  })
})

test.describe('Sidebar list filters: grouping', () => {
  const ccA = ccSession('Grouped code session', '/w/proj', hoursAgo(1))
  const desk = desktopChat('Grouped desktop chat', hoursAgo(2))
  const cowork = coworkSession('Grouped cowork task', hoursAgo(3))
  const ROWS12 = [ccA, desk, cowork]
  const groupCheckbox = (page: Page) => page.getByRole('checkbox', { name: /Group sessions by project/i })

  test('12 grouping off under Desktop and Cowork: with the saved setting on, each gives a flat list with no folder headers', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: ROWS12,
      preferences: { groupByProject: true },
      extraRoutes: listHonoringSource(ROWS12),
    })
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, ROWS12, [])
    // Positive control: under "All Conversations" the list groups.
    await expect(groupCheckbox(page)).toBeChecked()
    await expect(folderHeader(page, 'proj')).toBeVisible()

    for (const source of ['Claude Desktop', 'Claude Cowork']) {
      await pickOption(page, 'source-filter-select', source)
      const only = source === 'Claude Desktop' ? desk : cowork
      await expectRows(page, [only], ROWS12.filter((r) => r !== only))
      await expect(groupCheckbox(page), `${source}: checkbox`).toHaveCount(0)
      await expect(sidebar(page).getByTestId('list-group-header'), `${source}: folder headers`).toHaveCount(0)
      // No folder header of any kind: a header shows "<name> (<count>)".
      await expect(sidebar(page).locator('button', { hasText: /\(\d+\)\s*$/ }), `${source}: header-like buttons`).toHaveCount(0)
      // The project select is always shown.
      await expect(page.getByTestId('sidebar-project-select')).toBeVisible()
    }

    // The saved setting is still on: under "Claude Code" the list groups again.
    await pickOption(page, 'source-filter-select', 'Claude Code')
    await expect(groupCheckbox(page)).toBeChecked()
    await expect(folderHeader(page, 'proj')).toBeVisible()
  })

  test('13 grouping with filters: a folder whose rows are all older than N is not shown; a folder count equals its visible rows', async ({
    page,
    mockBackend,
  }) => {
    const keep1 = ccSession('Keep one hour', '/w/keep', hoursAgo(1))
    const keep2 = ccSession('Keep two hours', '/w/keep', hoursAgo(2))
    const keepOld = ccSession('Keep thirty hours', '/w/keep', hoursAgo(30))
    const gone1 = ccSession('Gone thirty hours', '/w/gone', hoursAgo(30))
    const gone2 = ccSession('Gone forty hours', '/w/gone', hoursAgo(40))
    const other = ccSession('Other three hours', '/w/other', hoursAgo(3))
    const rows = [gone1, keepOld, keep1, other, gone2, keep2]
    await mockBackend({
      conversations: rows,
      preferences: { groupByProject: true, listRecentOnly: true, listRecentHours: 24 },
      extraRoutes: listHonoringSource(rows),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))

    await expectRows(page, [keep1, keep2, other], [keepOld, gone1, gone2])
    await expect(folderHeader(page, 'keep')).toBeVisible()
    await expect(folderHeader(page, 'keep')).toContainText('(2)')
    await expect(folderHeader(page, 'other')).toContainText('(1)')
    await expect(folderHeader(page, 'gone')).toHaveCount(0)

    // Turn the recent filter off: the old rows and the folder return.
    await page.getByTestId('sidebar-recent-toggle').click()
    await expect(folderHeader(page, 'gone')).toContainText('(2)')
    await expect(folderHeader(page, 'keep')).toContainText('(3)')
  })

  test('14 keyboard order, grouped: j and ArrowDown move in display order; k moves back; a collapsed folder is skipped', async ({
    page,
    mockBackend,
  }) => {
    // Last Activity descending. Grouped display order:
    //   pfold: P1, P2   qfold: Q2 (starred), Q1   rfold: R1
    // The flat order of the same rows is P1, Q1, P2, Q2, R1 (Q2 first when
    // starred rows come first in the whole list).
    const P1 = ccSession('Pee first', '/w/pfold', hoursAgo(1))
    const Q1 = ccSession('Queue first', '/w/qfold', hoursAgo(2))
    const P2 = ccSession('Pee second', '/w/pfold', hoursAgo(3))
    const Q2 = ccSession('Queue starred', '/w/qfold', hoursAgo(4), { is_starred: true })
    const R1 = ccSession('Arr only', '/w/rfold', hoursAgo(5))
    const rows = [P1, Q1, P2, Q2, R1]
    await mockBackend({
      conversations: rows,
      details: detailsFor(rows),
      preferences: { groupByProject: true, keyboardMode: 'vim' },
      extraRoutes: listHonoringSource(rows),
    })
    await withNetRetry(page, () => page.goto(`/conversations/${P1.uuid}`))
    await waitForKeyboardShortcuts(page, 'vim')
    await expect(page.locator(`[data-message-uuid="${messageUuid(P1)}"]`)).toBeVisible()
    await expect.poll(() => folderOrder(page)).toEqual(['pfold', 'qfold', 'rfold'])

    const listFocus = async () => {
      // A click in the sidebar (not on a row) makes the list the focus area.
      await sidebar(page).getByRole('heading', { name: 'Claude Explorer' }).click()
    }
    const openSelected = async (expected: ConversationSummary) => {
      await page.keyboard.press('Enter')
      await expect(page).toHaveURL(new RegExp(`/conversations/${expected.uuid}`))
      await expect(page.locator(`[data-message-uuid="${messageUuid(expected)}"]`)).toBeVisible()
    }

    await listFocus()
    await page.keyboard.press('j')
    await openSelected(P2)

    await listFocus()
    await page.keyboard.press('ArrowDown')
    await openSelected(Q2)

    await listFocus()
    await page.keyboard.press('j')
    await openSelected(Q1)

    await listFocus()
    await page.keyboard.press('k')
    await page.keyboard.press('k')
    await openSelected(P2)

    // Collapse qfold: its rows leave the list, and j skips them.
    await folderHeader(page, 'qfold').click()
    await expect(row(page, Q1)).toHaveCount(0)
    await expect(row(page, Q2)).toHaveCount(0)
    await row(page, P2).click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${P2.uuid}`))
    await listFocus()
    await page.keyboard.press('j')
    await openSelected(R1)

    await listFocus()
    await page.keyboard.press('k')
    await openSelected(P2)
  })
})

test.describe('Sidebar list filters: console (case 16)', () => {
  test('16 no console errors or warnings while using every new control', async ({
    page,
    mockBackend,
    consoleAssertions,
  }) => {
    const a = ccSession('Console foo one', '/a/foo', hoursAgo(1))
    const b = ccSession('Console foo two', '/b/foo', hoursAgo(30))
    const c = ccSession('Console bar one', '/c/bar', daysAgo(5))
    const d = desktopChat('Console desktop chat', hoursAgo(2))
    const rows = [a, b, c, d]
    await mockBackend({
      conversations: rows,
      details: detailsFor(rows),
      preferences: { listRecentOnly: true, listRecentHours: 24 },
      extraRoutes: listHonoringSource(rows),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto(`/conversations/${a.uuid}`))
    await expect(page.locator(`[data-message-uuid="${messageUuid(a)}"]`)).toBeVisible()
    await expectRows(page, [a, d], [b, c])

    await typeHours(page, '48')
    await expectRows(page, [a, b, d], [c])
    await page.getByTestId('sidebar-recent-toggle').click()
    await expectRows(page, rows, [])

    await pickOption(page, 'sidebar-project-select', 'foo')
    await expectRows(page, [a, b], [c, d])
    await listMenu(page, 'list-filter-path')
    await expectRows(page, [a], [b, c, d])
    await listMenu(page, 'list-filter-clear')
    await expectRows(page, rows, [])

    await page.getByRole('checkbox', { name: /Group sessions by project/i }).click()
    await expect(folderHeader(page, 'foo')).toContainText('(2)')
    await folderHeader(page, 'foo').click()
    await expect(row(page, a)).toHaveCount(0)

    expect(consoleAssertions.errors, 'console errors').toEqual([])
    expect(consoleAssertions.warnings, 'console warnings').toEqual([])
  })
})
