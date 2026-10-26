import {
  test,
  expect,
  E2E_HOME_DIR,
  makeSummary,
  recordPreferencePatches,
  withNetRetry,
  type Page,
} from './fixtures'
import {
  NAME_MODE,
  ccSession,
  closeProjectPicker,
  detailsFor,
  expectProjectEntries,
  expectRows,
  expectUrlParams,
  folderHeader,
  folderOrder,
  hoursAgo,
  listHonoringSource,
  messageUuid,
  openProjectPicker,
  pickProject,
  projectEntryParts,
  projectEntries,
  projectFilter,
  projectOption,
  sentPrefValues,
  startClock,
  urlParam,
} from './sidebar-list-filters.helpers'
import type { ConversationSummary } from '../src/lib/types'

/**
 * Sidebar list filters, round 3: what a "project" is (path or name), and
 * labels with context. Test cases 28-38 of
 * PLANS/2026.10.26-list-project-key.md. Black-box, from the spec.
 *
 * The mocked `/api/config` reports `home_dir` = E2E_HOME_DIR
 * ("/Users/tester"). Case 38 (no console errors or warnings) holds for
 * every test through the auto `consoleAssertions` fixture; the last test
 * also checks it explicitly.
 */

const HOME = E2E_HOME_DIR

// The spec's example: three folders that all end in "ai", plus one more.
// Newest first: Source/clientco/ai (1 h), Work/clientco/ai (2 h),
// other/ai (3 h), Source/web (4 h).
const srcAi = ccSession('Key source ai session', `${HOME}/Source/clientco/ai`, hoursAgo(1))
const workAi = ccSession('Key work ai session', `${HOME}/Work/clientco/ai`, hoursAgo(2))
const otherAi = ccSession('Key other ai session', `${HOME}/Source/other/ai`, hoursAgo(3))
const web = ccSession('Key web session', `${HOME}/Source/web`, hoursAgo(4))
// The mock returns the rows in an order that is not newest first.
const EXAMPLE: ConversationSummary[] = [otherAi, web, srcAi, workAi]

const EXAMPLE_ENTRIES = [
  { label: 'Source/clientco/ai', detail: '~/Source/clientco/ai' },
  { label: 'Work/clientco/ai', detail: '~/Work/clientco/ai' },
  { label: 'other/ai', detail: '~/Source/other/ai' },
  { label: 'Source/web', detail: '~/Source/web' },
]

const header = (page: Page) => page.getByTestId('list-filter-button')

/**
 * Whether a setting option is the chosen one: a checked radio or checkbox,
 * `aria-checked` / `aria-pressed` / `aria-selected`, or `data-state`
 * checked/on/active, on the hook or on a radio inside it.
 */
async function isChosen(page: Page, testId: string): Promise<boolean | null> {
  return page.getByTestId(testId).evaluate((el) => {
    const read = (e: Element): boolean | null => {
      if (e instanceof HTMLInputElement && (e.type === 'radio' || e.type === 'checkbox')) return e.checked
      for (const attr of ['aria-checked', 'aria-pressed', 'aria-selected']) {
        const v = e.getAttribute(attr)
        if (v !== null) return v === 'true'
      }
      const state = e.getAttribute('data-state')
      if (state !== null) return ['checked', 'on', 'active'].includes(state)
      return null
    }
    const own = read(el)
    if (own !== null) return own
    const inner = el.querySelector('input[type="radio"], [role="radio"], input[type="checkbox"]')
    return inner ? read(inner) : null
  })
}

async function expectChosen(page: Page, chosen: 'path' | 'name'): Promise<void> {
  const other = chosen === 'path' ? 'name' : 'path'
  await expect(page.getByTestId(`settings-project-key-${chosen}`)).toBeVisible()
  await expect.poll(() => isChosen(page, `settings-project-key-${chosen}`), { message: `${chosen} chosen` }).toBe(true)
  await expect.poll(() => isChosen(page, `settings-project-key-${other}`), { message: `${other} not chosen` }).toBe(false)
}

async function openSettings(page: Page): Promise<void> {
  await withNetRetry(page, () => page.goto('/settings'))
  await expect(page.getByTestId('settings-project-key-path')).toBeVisible()
}

/** Open the header "List" menu (and wait for its first item). */
async function openHeaderMenu(page: Page): Promise<void> {
  await header(page).click()
  await expect(page.getByTestId('list-filter-project')).toBeVisible()
}

async function closeHeaderMenu(page: Page): Promise<void> {
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('list-filter-project')).toBeHidden()
}

test.describe('Sidebar list filters: projects are paths (default)', () => {
  test('28 default: with no saved listProjectKey, Settings shows "Folder path" chosen, and the picker lists one entry per path', async ({
    page,
    mockBackend,
  }) => {
    // The e2e default preferences have no listProjectKey.
    await mockBackend({ conversations: EXAMPLE, extraRoutes: listHonoringSource(EXAMPLE) })
    await startClock(page)
    await openSettings(page)
    await expectChosen(page, 'path')

    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, EXAMPLE, [])
    await openProjectPicker(page)
    // Three folders end in "ai": three entries, not one "ai".
    await expectProjectEntries(page, ['All projects', ...EXAMPLE_ENTRIES.map((e) => e.label)])
    await closeProjectPicker(page)
  })

  test('29 labels grow until unique: the example paths give "Source/clientco/ai", "Work/clientco/ai", and "other/ai", each with its display path as the second line', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: EXAMPLE, extraRoutes: listHonoringSource(EXAMPLE) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, EXAMPLE, [])

    await openProjectPicker(page)
    await expectProjectEntries(page, ['All projects', ...EXAMPLE_ENTRIES.map((e) => e.label)])
    const parts = await projectEntryParts(page)
    expect(parts.slice(1)).toEqual(EXAMPLE_ENTRIES)
    // The second line is always visible.
    for (const e of EXAMPLE_ENTRIES) {
      const opt = await projectOption(page, e.label)
      await expect(opt.getByTestId('project-entry-detail'), `${e.label}: second line`).toBeVisible()
    }
    await closeProjectPicker(page)
  })

  test('29 labels: "~/foo" is its own label, and the home folder shows as "~"', async ({ page, mockBackend }) => {
    const foo = ccSession('Key home foo session', `${HOME}/foo`, hoursAgo(1))
    const home = ccSession('Key home itself session', HOME, hoursAgo(2))
    const rows = [home, foo]
    await mockBackend({ conversations: rows, extraRoutes: listHonoringSource(rows) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, rows, [])

    await openProjectPicker(page)
    await expectProjectEntries(page, ['All projects', '~/foo', '~'])
    expect((await projectEntryParts(page)).slice(1)).toEqual([
      { label: '~/foo', detail: '~/foo' },
      { label: '~', detail: '~' },
    ])
    await closeProjectPicker(page)
  })

  test('30 paths outside home show as they are, and their labels use their last parts (also a look-alike of home, and a Windows path)', async ({
    page,
    mockBackend,
  }) => {
    const rows = [
      ccSession('Outside builds svc', '/opt/builds/svc', hoursAgo(1)),
      ccSession('Outside srv proj svc', '/srv/a/proj/svc', hoursAgo(2)),
      ccSession('Outside data proj svc', '/data/b/proj/svc', hoursAgo(3)),
      // Not inside /Users/tester: only a longer name that starts the same.
      ccSession('Outside look-alike repo', '/Users/testerx/repo', hoursAgo(4)),
      // The page splits on "\" too.
      ccSession('Outside windows api', 'D:\\work\\svc\\api', hoursAgo(5)),
    ]
    await mockBackend({ conversations: [...rows].reverse(), extraRoutes: listHonoringSource([...rows].reverse()) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, rows, [])

    await openProjectPicker(page)
    await expect
      .poll(async () => (await projectEntryParts(page)).slice(1))
      .toEqual([
        { label: 'builds/svc', detail: '/opt/builds/svc' },
        { label: 'a/proj/svc', detail: '/srv/a/proj/svc' },
        { label: 'b/proj/svc', detail: '/data/b/proj/svc' },
        { label: 'testerx/repo', detail: '/Users/testerx/repo' },
        { label: 'svc/api', detail: 'D:\\work\\svc\\api' },
      ])
    await closeProjectPicker(page)
  })

  test('31 typing matches the path: "work" keeps only "Work/clientco/ai"; "AI" keeps all three ai folders', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: EXAMPLE, extraRoutes: listHonoringSource(EXAMPLE) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, EXAMPLE, [])

    await openProjectPicker(page)
    await projectFilter(page).fill('work')
    await expectProjectEntries(page, ['All projects', 'Work/clientco/ai'])
    await projectFilter(page).fill('AI')
    await expectProjectEntries(page, ['All projects', 'Source/clientco/ai', 'Work/clientco/ai', 'other/ai'])
    // The display path matches too: "clientco" is not in "other/ai".
    await projectFilter(page).fill('clientco')
    await expectProjectEntries(page, ['All projects', 'Source/clientco/ai', 'Work/clientco/ai'])
    await closeProjectPicker(page)
  })

  test('32 picking in path mode sets ?path= to the absolute path and shows only that folder; another folder with the same name stays hidden', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: EXAMPLE, extraRoutes: listHonoringSource(EXAMPLE) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, EXAMPLE, [])

    await pickProject(page, 'Work/clientco/ai')
    await expectUrlParams(page, { path: `${HOME}/Work/clientco/ai`, project: null })
    await expectRows(page, [workAi], [srcAi, otherAi, web])
    await expect(page.getByTestId('sidebar-project-select')).toContainText('Work/clientco/ai')
  })

  test('33 folders in path mode: one folder per path, labels as data-group-name, display paths as tooltips, newest first; a session without a path goes in "Unknown Project"', async ({
    page,
    mockBackend,
  }) => {
    const noPath = makeSummary({
      uuid: '00000000-0000-4000-8000-00000000c0de',
      name: 'Key session without a path',
      source: 'CLAUDE_CODE',
      project_path: null,
      project_name: null,
      created_at: hoursAgo(5),
      updated_at: hoursAgo(5),
      message_count: 2,
      human_message_count: 1,
    })
    const rows = [noPath, ...EXAMPLE]
    await mockBackend({
      conversations: rows,
      preferences: { groupByProject: true, sortField: 'updated_at', sortOrder: 'desc' },
      extraRoutes: listHonoringSource(rows),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expect(page.getByRole('checkbox', { name: /Group sessions by project/i })).toBeChecked()

    await expect
      .poll(() => folderOrder(page))
      .toEqual(['Source/clientco/ai', 'Work/clientco/ai', 'other/ai', 'Source/web', 'Unknown Project'])
    for (const e of EXAMPLE_ENTRIES) {
      await expect(folderHeader(page, e.label), `${e.label}: tooltip`).toHaveAttribute('title', e.detail)
      await expect(folderHeader(page, e.label), `${e.label}: count`).toContainText('(1)')
    }
    await expect(folderHeader(page, 'Unknown Project')).toContainText('(1)')
  })

  test('34 header in path mode: "Filter list"; the menu has "Show only this project" and "Show all projects", no folder item; after a pick "List: this project"; from another folder "List: other project"', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: EXAMPLE,
      details: detailsFor(EXAMPLE),
      extraRoutes: listHonoringSource(EXAMPLE),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto(`/conversations/${srcAi.uuid}`))
    await expect(page.locator(`[data-message-uuid="${messageUuid(srcAi)}"]`)).toBeVisible()

    await expect(header(page)).toHaveAttribute('data-list-filter', 'all')
    await expect(header(page)).toContainText('Filter list')

    await openHeaderMenu(page)
    await expect(page.getByTestId('list-filter-project')).toContainText('Show only this project')
    await expect(page.getByTestId('list-filter-clear')).toContainText('Show all projects')
    await expect(page.getByTestId('list-filter-clear')).toBeDisabled()
    await expect(page.getByTestId('list-filter-path')).toHaveCount(0)
    await closeHeaderMenu(page)

    // A pick in the picker.
    await pickProject(page, 'Source/clientco/ai')
    await expectUrlParams(page, { path: srcAi.project_path!, project: null })
    await expect(header(page)).toHaveAttribute('data-list-filter', 'path')
    await expect(header(page)).toContainText('List: this project')

    // "Show all projects", then "Show only this project" from the menu (it sets ?path=).
    await openHeaderMenu(page)
    await page.getByTestId('list-filter-clear').click()
    await expectUrlParams(page, { path: null, project: null })
    await expect(header(page)).toContainText('Filter list')
    await openHeaderMenu(page)
    await page.getByTestId('list-filter-project').click()
    await expectUrlParams(page, { path: srcAi.project_path!, project: null })
    await expectRows(page, [srcAi], [workAi, otherAi, web])
    await expect(header(page)).toContainText('List: this project')

    // A conversation in another folder, while the list shows this one.
    await withNetRetry(page, () =>
      page.goto(`/conversations/${workAi.uuid}?path=${encodeURIComponent(srcAi.project_path!)}`),
    )
    await expect(page.locator(`[data-message-uuid="${messageUuid(workAi)}"]`)).toBeVisible()
    await expect(header(page)).toHaveAttribute('data-list-filter', 'other')
    await expect(header(page)).toContainText('List: other project')
  })

  test('36 old link in path mode: ?project=ai filters by name, the picker button shows "ai (all folders)" and lists it second, and "All projects" clears it', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: EXAMPLE, extraRoutes: listHonoringSource(EXAMPLE) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/conversations?project=ai'))
    await expectRows(page, [srcAi, workAi, otherAi], [web])

    const button = page.getByTestId('sidebar-project-select')
    await expect(button).toContainText('ai (all folders)')
    await openProjectPicker(page)
    await expect.poll(async () => (await projectEntries(page)).slice(0, 2)).toEqual(['All projects', 'ai (all folders)'])

    await (await projectOption(page, 'All projects')).click()
    await expect(projectFilter(page)).toBeHidden()
    await expectUrlParams(page, { project: null, path: null })
    await expectRows(page, EXAMPLE, [])
    await expect(button).toContainText('All projects')
  })
})

test.describe('Sidebar list filters: projects are names (setting)', () => {
  test('35 name mode: after "Folder name" in Settings, the three ai folders give one entry "ai" with "3 folders"; picking sets ?project=ai, folders group by name, and the header menu has all three items', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: EXAMPLE,
      details: detailsFor(EXAMPLE),
      extraRoutes: listHonoringSource(EXAMPLE),
    })
    await startClock(page)
    await recordPreferencePatches(page)
    await openSettings(page)
    await page.getByTestId('settings-project-key-name').click()
    await expectChosen(page, 'name')
    await expect.poll(() => sentPrefValues(page, 'listProjectKey')).toContain('name')

    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, EXAMPLE, [])
    await openProjectPicker(page)
    await expectProjectEntries(page, ['All projects', 'ai', 'web'])
    expect((await projectEntryParts(page)).slice(1)).toEqual([
      { label: 'ai', detail: '3 folders' },
      { label: 'web', detail: '~/Source/web' },
    ])
    // The tooltip lists the display paths.
    const titles = await (await projectOption(page, 'ai')).evaluate((el) =>
      [el, ...Array.from(el.querySelectorAll('[title]'))].map((e) => e.getAttribute('title') ?? '').join('\n'),
    )
    for (const p of ['~/Source/clientco/ai', '~/Work/clientco/ai', '~/Source/other/ai']) {
      expect(titles, `tooltip of "ai" lists ${p}`).toContain(p)
    }
    await (await projectOption(page, 'ai')).click()
    await expect(projectFilter(page)).toBeHidden()
    await expectUrlParams(page, { project: 'ai', path: null })
    await expectRows(page, [srcAi, workAi, otherAi], [web])

    // Folders group by name.
    await page.getByRole('checkbox', { name: /Group sessions by project/i }).click()
    await expect(folderHeader(page, 'ai')).toContainText('(3)')
    await expect(folderHeader(page, 'Source/clientco/ai')).toHaveCount(0)

    // The header menu has all three items.
    await withNetRetry(page, () => page.goto(`/conversations/${srcAi.uuid}?project=ai`))
    await expect(page.locator(`[data-message-uuid="${messageUuid(srcAi)}"]`)).toBeVisible()
    await expect(header(page)).toHaveAttribute('data-list-filter', 'project')
    await expect(header(page)).toContainText('List: this project')
    await openHeaderMenu(page)
    await expect(page.getByTestId('list-filter-project')).toContainText('Show only this project')
    await expect(page.getByTestId('list-filter-path')).toContainText('Show only this folder')
    await expect(page.getByTestId('list-filter-clear')).toContainText('Show all projects')
    await page.getByTestId('list-filter-path').click()
    await expectUrlParams(page, { path: srcAi.project_path!, project: null })
    await expect(header(page)).toHaveAttribute('data-list-filter', 'path')
    await expect(header(page)).toContainText('List: this folder')
  })

  test('37 the setting persists: "Folder name" sends listProjectKey: "name", and a reload keeps it', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: EXAMPLE, extraRoutes: listHonoringSource(EXAMPLE) })
    await startClock(page)
    await recordPreferencePatches(page)
    await openSettings(page)
    await expectChosen(page, 'path')
    await page.getByTestId('settings-project-key-name').click()
    await expectChosen(page, 'name')
    await expect.poll(() => sentPrefValues(page, 'listProjectKey')).toContain('name')

    await withNetRetry(page, () => page.reload())
    await expect(page.getByTestId('settings-project-key-name')).toBeVisible()
    await expectChosen(page, 'name')

    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, EXAMPLE, [])
    await openProjectPicker(page)
    await expectProjectEntries(page, ['All projects', 'ai', 'web'])
    await closeProjectPicker(page)
  })

  test('38 no console errors or warnings while switching the setting and using the picker, the folders, and the header', async ({
    page,
    mockBackend,
    consoleAssertions,
  }) => {
    await mockBackend({
      conversations: EXAMPLE,
      details: detailsFor(EXAMPLE),
      preferences: NAME_MODE,
      extraRoutes: listHonoringSource(EXAMPLE),
    })
    await startClock(page)
    await openSettings(page)
    await expectChosen(page, 'name')
    await page.getByTestId('settings-project-key-path').click()
    await expectChosen(page, 'path')

    await withNetRetry(page, () => page.goto(`/conversations/${srcAi.uuid}`))
    await expect(page.locator(`[data-message-uuid="${messageUuid(srcAi)}"]`)).toBeVisible()
    await openProjectPicker(page)
    await projectFilter(page).fill('clientco')
    await expectProjectEntries(page, ['All projects', 'Source/clientco/ai', 'Work/clientco/ai'])
    await (await projectOption(page, 'Work/clientco/ai')).click()
    await expectUrlParams(page, { path: workAi.project_path!, project: null })
    await expect(header(page)).toContainText('List: other project')
    await page.getByRole('checkbox', { name: /Group sessions by project/i }).click()
    await expect(folderHeader(page, 'Work/clientco/ai')).toContainText('(1)')
    await openHeaderMenu(page)
    await page.getByTestId('list-filter-clear').click()
    await expect.poll(() => urlParam(page, 'path')).toBeNull()

    expect(consoleAssertions.errors, 'console errors').toEqual([])
    expect(consoleAssertions.warnings, 'console warnings').toEqual([])
  })
})
