import {
  test,
  expect,
  E2E_HOME_DIR,
  PRIMARY_ORG,
  recordPreferencePatches,
  withNetRetry,
  type Page,
} from './fixtures'
import {
  ccSession,
  daysAgo,
  expectRows,
  hoursAgo,
  listHonoringSource,
  pickOption,
  pickProject,
  pickTime,
  sentPrefValues,
  sidebar,
  startClock,
  timeLabel,
  timeSelect,
} from './sidebar-list-filters.helpers'
import type { ConversationSummary } from '../src/lib/types'

/**
 * Sidebar list filters, round 4: one collapsible "Filters and Sorting"
 * section, and "Forever" by default. Test cases 39-46 of
 * PLANS/2026.10.26-sidebar-filters-accordion.md. Black-box, from the spec.
 *
 * The e2e harness seeds `sidebarFiltersOpen: true`; the tests of the
 * section's default pass `sidebarFiltersOpen: undefined`, which leaves the
 * key out of the saved preferences. Case 46 (no console errors or
 * warnings) holds for every test through the auto `consoleAssertions`
 * fixture; the last test also checks it explicitly.
 */

const toggle = (page: Page) => page.getByTestId('sidebar-filters-toggle')
const section = (page: Page) => page.getByTestId('sidebar-filters')
const activeCount = (page: Page) => page.getByTestId('sidebar-filters-active-count')

const SECTION_CLOSED = { sidebarFiltersOpen: undefined }

const alpha = ccSession('Accordion alpha one hour', `${E2E_HOME_DIR}/Source/alpha`, hoursAgo(1))
const beta = ccSession('Accordion beta two hours', `${E2E_HOME_DIR}/Source/beta`, hoursAgo(2))
const ROWS = [beta, alpha]

async function expectClosed(page: Page): Promise<void> {
  await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false')
  await expect(section(page)).toHaveCount(0)
  await expect(page.getByTestId('source-filter-select')).toHaveCount(0)
  await expect(page.getByTestId('sidebar-project-select')).toHaveCount(0)
  await expect(sidebar(page).locator('button', { hasText: 'Last Activity' })).toHaveCount(0)
  await expect(page.getByTestId('sidebar-recent-select')).toHaveCount(0)
}

async function expectOpen(page: Page): Promise<void> {
  await expect(toggle(page)).toHaveAttribute('aria-expanded', 'true')
  await expect(section(page)).toBeVisible()
}

/**
 * Visible height of the list area: the nearest scroll container above a
 * list row, cut by every clipping ancestor and by the window.
 */
async function listVisibleHeight(page: Page, rowName: string): Promise<number> {
  return sidebar(page).evaluate((aside, name) => {
    const clippy = (v: string) => ['hidden', 'auto', 'scroll', 'clip'].includes(v)
    const row = Array.from(aside.querySelectorAll('[role="button"]')).find((el) =>
      (el.textContent ?? '').includes(name),
    )
    if (!row) return -1
    let container: Element | null = null
    for (let n = row.parentElement; n && n !== aside.parentElement; n = n.parentElement) {
      if (clippy(getComputedStyle(n).overflowY)) {
        container = n
        break
      }
    }
    if (!container) return -2
    const r = container.getBoundingClientRect()
    let top = r.top
    let bottom = r.bottom
    for (let n = container.parentElement; n; n = n.parentElement) {
      const cs = getComputedStyle(n)
      if (clippy(cs.overflowX) || clippy(cs.overflowY)) {
        const a = n.getBoundingClientRect()
        top = Math.max(top, a.top)
        bottom = Math.min(bottom, a.bottom)
      }
    }
    top = Math.max(top, 0)
    bottom = Math.min(bottom, window.innerHeight)
    return Math.max(0, Math.round(bottom - top))
  }, rowName)
}

test.describe('Sidebar list filters: the "Filters and Sorting" section', () => {
  test('39 default closed: with no saved sidebarFiltersOpen, the section is closed, its controls are not in the page, and the search field is visible', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: ROWS, preferences: SECTION_CLOSED, extraRoutes: listHonoringSource(ROWS) })
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, ROWS, [])

    await expect(toggle(page)).toBeVisible()
    await expect(toggle(page)).toContainText('Filters and Sorting')
    await expectClosed(page)
    // Above the section, always visible: the header and the search field.
    await expect(page.getByTestId('sidebar-title-search')).toBeVisible()
    await expect(sidebar(page).getByRole('heading', { name: 'Claude Explorer' })).toBeVisible()
    await expect(sidebar(page).getByTitle('Refresh conversation list')).toBeVisible()
  })

  test('40 open and close: a click opens the section with every control in the spec order (with the workspace select), a second click closes it, and aria-expanded follows', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: ROWS,
      preferences: SECTION_CLOSED,
      // Two workspaces, so the workspace select is shown.
      orgs: {
        authenticated: true,
        orgs: [
          { org_id: PRIMARY_ORG, name: 'Personal', is_primary: true },
          { org_id: '11111111-2222-4333-8444-555555555555', name: 'Team Space', is_primary: false },
        ],
      },
      extraRoutes: listHonoringSource(ROWS),
    })
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, ROWS, [])
    await expectClosed(page)

    await toggle(page).click()
    await expectOpen(page)
    const inSection = section(page)
    const controls: Array<[string, ReturnType<Page['locator']>]> = [
      ['active filter', inSection.getByTestId('active-filter-select')],
      ['source', inSection.getByTestId('source-filter-select')],
      ['workspace', inSection.getByTestId('workspace-select')],
      ['project', inSection.getByTestId('sidebar-project-select')],
      ['sort', inSection.locator('button', { hasText: 'Last Activity' })],
      ['time', inSection.getByTestId('sidebar-recent-select')],
      ['group', inSection.getByRole('checkbox', { name: /Group sessions by project/i })],
    ]
    for (const [label, control] of controls) {
      await control.scrollIntoViewIfNeeded()
      await expect(control, `${label} in the section`).toBeVisible()
    }
    await expect(inSection.getByTitle(/^(Ascending|Descending)$/)).toBeVisible()
    // Top to bottom in the spec order (scrolling the section keeps the
    // relative order, so compare positions in the section's own space).
    const sectionTops = await inSection.evaluate((el, ids) => {
      const base = el.getBoundingClientRect().top
      return ids.map((sel) => {
        const t = el.querySelector(sel)
        return t ? t.getBoundingClientRect().top - base : null
      })
    }, [
      '[data-testid="active-filter-select"]',
      '[data-testid="source-filter-select"]',
      '[data-testid="workspace-select"]',
      '[data-testid="sidebar-project-select"]',
      '[data-testid="sidebar-recent-select"]',
    ])
    expect(sectionTops.every((t) => t !== null), `section positions: ${JSON.stringify(sectionTops)}`).toBe(true)
    const nums = sectionTops as number[]
    expect([...nums].sort((a, b) => a - b), 'spec order: filter, source, workspace, project, time').toEqual(nums)
    // The row with the toggles and the ⌘K hint is in the section too.
    await expect(inSection.getByTestId('show-phantom-sessions-toggle')).toBeVisible()
    await expect(inSection.getByTestId('show-archived-sessions-toggle')).toBeVisible()
    await expect(inSection).toContainText('to search messages')

    await toggle(page).click()
    await expectClosed(page)
    await expect(page.getByTestId('sidebar-title-search')).toBeVisible()
  })

  test('41 kept across a reload: opening sends sidebarFiltersOpen: true and survives a reload; closing sends false and survives a reload', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: ROWS, preferences: SECTION_CLOSED, extraRoutes: listHonoringSource(ROWS) })
    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, ROWS, [])
    await expectClosed(page)

    await toggle(page).click()
    await expectOpen(page)
    await expect.poll(() => sentPrefValues(page, 'sidebarFiltersOpen')).toContain(true)
    await withNetRetry(page, () => page.reload())
    await expectRows(page, ROWS, [])
    await expectOpen(page)
    await expect(page.getByTestId('source-filter-select')).toBeVisible()

    await toggle(page).click()
    await expectClosed(page)
    await expect.poll(() => sentPrefValues(page, 'sidebarFiltersOpen')).toContain(false)
    await withNetRetry(page, () => page.reload())
    await expectRows(page, ROWS, [])
    await expectClosed(page)
  })

  test('42 count: no count with the defaults; each of the 7 filters adds one; sort, direction, and grouping add none; the count shows while closed', async ({
    page,
    mockBackend,
  }) => {
    const cowork = { ...ccSession('Accordion cowork task', '/sessions/vm', hoursAgo(3)), source: 'CLAUDE_COWORK' as const }
    const rows: ConversationSummary[] = [...ROWS, cowork]
    await mockBackend({
      conversations: rows,
      preferences: {
        // A saved named filter that hides nothing, so it can be made active.
        filters: {
          nodes: {
            'f-none': {
              type: 'atom', id: 'f-none', name: 'Hide nothing', enabled: true,
              patterns: ['*zzzzzzzzzz*'], behavior: 'hide', mode: 'glob', target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
          migrationBannerDismissed: true,
        },
      },
      orgs: {
        authenticated: true,
        orgs: [
          { org_id: PRIMARY_ORG, name: 'Personal', is_primary: true },
          { org_id: '11111111-2222-4333-8444-555555555555', name: 'Team Space', is_primary: false },
        ],
      },
      extraRoutes: listHonoringSource(rows),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, rows, [])
    await expectOpen(page)
    await expect(timeSelect(page)).toContainText(timeLabel('Forever'))
    await expect(activeCount(page)).toHaveCount(0)

    let n = 0
    const step = async (label: string) => {
      n += 1
      await expect(activeCount(page), `after ${label}`).toBeVisible()
      await expect(activeCount(page), `after ${label}`).toHaveText(new RegExp(`^\\s*${n} active\\s*$`))
    }

    // 1. The active-filter picker is not "All conversations".
    await pickOption(page, 'active-filter-select', 'Hide nothing')
    await step('the active filter')
    // 2. A project filter is on.
    await pickProject(page, 'Source/alpha')
    await step('a project')
    // 3. The time dropdown is not "Forever".
    await pickTime(page, '1 week')
    await step('a time window')
    // 4. "Empty" is checked.
    await page.getByTestId('show-phantom-sessions-toggle').click()
    await expect(page.getByTestId('show-phantom-sessions-toggle')).toBeChecked()
    await step('Empty')
    // 5. "Archived" is checked.
    await page.getByTestId('show-archived-sessions-toggle').click()
    await expect(page.getByTestId('show-archived-sessions-toggle')).toBeChecked()
    await step('Archived')
    // 6. A workspace is selected.
    await pickOption(page, 'workspace-select', /Team Space/)
    await step('a workspace')
    // 7. The source is not "All Conversations" (Cowork keeps "Archived" shown).
    await pickOption(page, 'source-filter-select', 'Claude Cowork')
    await step('a source')

    // The sort, its direction, and the grouping change no count.
    await sidebar(page).locator('button', { hasText: 'Last Activity' }).click()
    const startTime = page.getByRole('option', { name: 'Start Time', exact: true })
    await expect(startTime).toBeVisible()
    await startTime.click()
    await expect(sidebar(page).locator('button', { hasText: 'Start Time' })).toBeVisible()
    await sidebar(page).getByTitle(/^(Ascending|Descending)$/).click()
    await expect(activeCount(page)).toHaveText(/^\s*7 active\s*$/)

    // Back to a source that shows the grouping checkbox, then group.
    await pickOption(page, 'source-filter-select', 'All Conversations')
    await expect(activeCount(page)).toHaveText(/^\s*6 active\s*$/)
    await sidebar(page).getByRole('checkbox', { name: /Group sessions by project/i }).click()
    await expect(sidebar(page).getByRole('checkbox', { name: /Group sessions by project/i })).toBeChecked()
    await expect(activeCount(page)).toHaveText(/^\s*6 active\s*$/)

    // The count shows also while the section is closed.
    await toggle(page).click()
    await expectClosed(page)
    await expect(activeCount(page)).toBeVisible()
    await expect(activeCount(page)).toHaveText(/^\s*6 active\s*$/)
  })

  test('43 Forever by default: with no saved listRecentOnly, the dropdown shows "Last: Forever", and a session from two years ago is in the list', async ({
    page,
    mockBackend,
  }) => {
    const twoYears = ccSession('Accordion two years old', `${E2E_HOME_DIR}/Source/old`, daysAgo(730))
    const rows = [twoYears, alpha]
    await mockBackend({
      conversations: rows,
      preferences: { listRecentOnly: undefined },
      extraRoutes: listHonoringSource(rows),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))

    await expectRows(page, [alpha, twoYears], [])
    await expect(timeSelect(page)).toContainText(timeLabel('Forever'))
    await expect(activeCount(page)).toHaveCount(0)
  })

  test('44 a saved window stays: listRecentOnly true with listRecentHours 8 shows "Last: 8 hours", and a 9-hour-old session is not in the list', async ({
    page,
    mockBackend,
  }) => {
    const seven = ccSession('Accordion seven hours', `${E2E_HOME_DIR}/Source/seven`, hoursAgo(7))
    const nine = ccSession('Accordion nine hours', `${E2E_HOME_DIR}/Source/nine`, hoursAgo(9))
    const rows = [nine, seven]
    await mockBackend({
      conversations: rows,
      preferences: { listRecentOnly: true, listRecentHours: 8 },
      extraRoutes: listHonoringSource(rows),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))

    await expectRows(page, [seven], [nine])
    await expect(timeSelect(page)).toContainText(timeLabel('8 hours'))
    await expect(activeCount(page)).toHaveText(/^\s*1 active\s*$/)
  })

  test.describe('in a 1280x720 window', () => {
    test.use({ viewport: { width: 1280, height: 720 } })

    test('45 the list gains height: with the section closed, the list area is taller than with it open', async ({
      page,
      mockBackend,
    }) => {
      const many = Array.from({ length: 12 }, (_, i) =>
        ccSession(`Accordion height session ${String.fromCharCode(65 + i)}`, `${E2E_HOME_DIR}/Source/h${i}`, hoursAgo(i + 1)),
      )
      await mockBackend({ conversations: many, extraRoutes: listHonoringSource(many) })
      await withNetRetry(page, () => page.goto('/'))
      await expectOpen(page)
      const first = many[0].name
      await expect(sidebar(page).locator('[role="button"]', { hasText: first })).toBeVisible()

      let open = 0
      await expect.poll(async () => (open = await listVisibleHeight(page, first))).toBeGreaterThan(0)

      await toggle(page).click()
      await expectClosed(page)
      await expect
        .poll(() => listVisibleHeight(page, first), { message: `list height closed vs open (${open} px)` })
        .toBeGreaterThan(open)
    })
  })

  test('46 no console errors or warnings while opening, closing, reloading, and counting', async ({
    page,
    mockBackend,
    consoleAssertions,
  }) => {
    await mockBackend({ conversations: ROWS, preferences: SECTION_CLOSED, extraRoutes: listHonoringSource(ROWS) })
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, ROWS, [])
    await expectClosed(page)

    await toggle(page).click()
    await expectOpen(page)
    await pickTime(page, '3 days')
    await expect(activeCount(page)).toHaveText(/^\s*1 active\s*$/)
    await toggle(page).click()
    await expectClosed(page)
    await withNetRetry(page, () => page.reload())
    await expectRows(page, ROWS, [])
    await expectClosed(page)
    await expect(activeCount(page)).toHaveText(/^\s*1 active\s*$/)

    expect(consoleAssertions.errors, 'console errors').toEqual([])
    expect(consoleAssertions.warnings, 'console warnings').toEqual([])
  })
})
