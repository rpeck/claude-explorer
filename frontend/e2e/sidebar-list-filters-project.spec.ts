import { test, expect, recordPreferencePatches, withNetRetry } from './fixtures'
import {
  bodyText,
  ccSession,
  coworkSession,
  daysAgo,
  desktopChat,
  detailsFor,
  expectRows,
  expectUrlParams,
  hoursAgo,
  listHonoringSource,
  listMenu,
  messageUuid,
  openOptions,
  pickOption,
  sentPrefValues,
  sidebar,
  startClock,
  typeHours,
  urlParam,
} from './sidebar-list-filters.helpers'

/**
 * Sidebar list filters, project and path: test cases 5-8, 10, and 15 of
 * PLANS/2026.10.25-sidebar-list-filters.md. Black-box, from the spec.
 * Case 16 (no console errors or warnings) holds for every test through
 * the auto `consoleAssertions` fixture.
 */

// ---- Case 5: three projects (names differ in case), and a Desktop chat.
const zeta1 = ccSession('Zeta first session', '/w/zeta', hoursAgo(2))
const alpha1 = ccSession('Alpha first session', '/w/Alpha', hoursAgo(3))
const beta1 = ccSession('Beta first session', '/w/beta', hoursAgo(4))
const beta2 = ccSession('Beta second session', '/x/beta', hoursAgo(5))
const desk5 = desktopChat('Desktop chat five', hoursAgo(1))
const SET5 = [zeta1, alpha1, beta1, beta2, desk5]

// ---- Cases 6-8: /a/foo, /b/foo, /c/bar, and a Desktop chat.
const fooA1 = ccSession('Foo A one', '/a/foo', hoursAgo(1))
const fooA2 = ccSession('Foo A two', '/a/foo', hoursAgo(2))
const fooB1 = ccSession('Foo B one', '/b/foo', hoursAgo(3))
const barC1 = ccSession('Bar C one', '/c/bar', hoursAgo(4))
const desk6 = desktopChat('Desktop chat six', hoursAgo(5))
const SET6 = [fooA1, fooA2, fooB1, barC1, desk6]

const label = (page: import('./fixtures').Page) => page.getByTestId('list-filter-button')

test.describe('Sidebar list filters: project select', () => {
  test('5 project select: options are "All projects" then the names sorted ignoring case; a pick sets ?project= and shows only that project; "All projects" shows all and clears the URL', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: SET5, extraRoutes: listHonoringSource(SET5) })
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, SET5, [])

    const select = page.getByTestId('sidebar-project-select')
    await expect(select).toBeVisible()
    expect(await openOptions(page, 'sidebar-project-select')).toEqual(['All projects', 'Alpha', 'beta', 'zeta'])

    await pickOption(page, 'sidebar-project-select', 'beta')
    await expectUrlParams(page, { project: 'beta', path: null })
    // One project can hold several paths (/w/beta and /x/beta).
    await expectRows(page, [beta1, beta2], [zeta1, alpha1, desk5])

    // The options come from the list before the project filter.
    expect(await openOptions(page, 'sidebar-project-select')).toEqual(['All projects', 'Alpha', 'beta', 'zeta'])

    await pickOption(page, 'sidebar-project-select', 'All projects')
    await expectUrlParams(page, { project: null, path: null })
    await expectRows(page, SET5, [])
  })

  test('5 project select: options do not change when the recent filter hides a whole project', async ({
    page,
    mockBackend,
  }) => {
    const oldZeta = ccSession('Zeta old session', '/w/zeta', daysAgo(30))
    const rows = [oldZeta, alpha1, beta1]
    await mockBackend({
      conversations: rows,
      preferences: { listRecentOnly: true, listRecentHours: 24 },
      extraRoutes: listHonoringSource(rows),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, [alpha1, beta1], [oldZeta])

    expect(await openOptions(page, 'sidebar-project-select')).toEqual(['All projects', 'Alpha', 'beta', 'zeta'])
  })
})

test.describe('Sidebar list filters: conversation-header "List" menu', () => {
  test('6 header, path vs project: from a /b/foo session, path shows only /b/foo; project shows /a/foo and /b/foo; "Show all projects" shows all', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: SET6,
      details: detailsFor(SET6),
      extraRoutes: listHonoringSource(SET6),
    })
    await withNetRetry(page, () => page.goto(`/conversations/${fooB1.uuid}`))
    await expect(page.locator(`[data-message-uuid="${messageUuid(fooB1)}"]`)).toBeVisible()
    await expectRows(page, SET6, [])

    await expect(label(page)).toHaveAttribute('data-list-filter', 'all')
    await expect(label(page)).toContainText('List: all')

    // "Show all projects" is disabled while no project or path filter is active.
    await page.getByTestId('list-filter-button').click()
    await expect(page.getByTestId('list-filter-clear')).toBeVisible()
    await expect(page.getByTestId('list-filter-clear')).toBeDisabled()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('list-filter-clear')).toBeHidden()

    await listMenu(page, 'list-filter-path')
    await expectUrlParams(page, { path: '/b/foo', project: null })
    await expectRows(page, [fooB1], [fooA1, fooA2, barC1, desk6])
    await expect(label(page)).toHaveAttribute('data-list-filter', 'path')
    await expect(label(page)).toContainText('List: path')

    await listMenu(page, 'list-filter-project')
    await expectUrlParams(page, { project: 'foo', path: null })
    await expectRows(page, [fooA1, fooA2, fooB1], [barC1, desk6])
    await expect(label(page)).toHaveAttribute('data-list-filter', 'project')
    await expect(label(page)).toContainText('List: project')

    await listMenu(page, 'list-filter-clear')
    await expectUrlParams(page, { project: null, path: null })
    await expectRows(page, SET6, [])
    await expect(label(page)).toHaveAttribute('data-list-filter', 'all')
    await expect(label(page)).toContainText('List: all')
    // The open conversation stays open throughout.
    await expect(page).toHaveURL(new RegExp(`/conversations/${fooB1.uuid}`))
  })

  test('6 header, other: a filter for another project or path shows "List: other", and "Show all projects" is enabled', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: SET6,
      details: detailsFor(SET6),
      extraRoutes: listHonoringSource(SET6),
    })
    // A /c/bar session, while the list shows project foo.
    await withNetRetry(page, () => page.goto(`/conversations/${barC1.uuid}?project=foo`))
    await expect(page.locator(`[data-message-uuid="${messageUuid(barC1)}"]`)).toBeVisible()
    await expectRows(page, [fooA1, fooA2, fooB1], [barC1, desk6])
    await expect(label(page)).toHaveAttribute('data-list-filter', 'other')
    await expect(label(page)).toContainText('List: other')

    await page.getByTestId('list-filter-button').click()
    await expect(page.getByTestId('list-filter-clear')).toBeEnabled()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('list-filter-clear')).toBeHidden()

    // Another path of the same project name is "other" for a path filter too.
    await withNetRetry(page, () => page.goto(`/conversations/${fooB1.uuid}?path=/a/foo`))
    await expect(page.locator(`[data-message-uuid="${messageUuid(fooB1)}"]`)).toBeVisible()
    await expectRows(page, [fooA1, fooA2], [fooB1, barC1, desk6])
    await expect(label(page)).toHaveAttribute('data-list-filter', 'other')
    await expect(label(page)).toContainText('List: other')

    await listMenu(page, 'list-filter-clear')
    await expectUrlParams(page, { project: null, path: null })
    await expectRows(page, SET6, [])
    await expect(label(page)).toHaveAttribute('data-list-filter', 'all')
  })

  test('7 header, hidden for a Claude Desktop conversation and for a Cowork session', async ({ page, mockBackend }) => {
    const cowork = coworkSession('Cowork session seven', hoursAgo(6))
    const rows = [...SET6, cowork]
    await mockBackend({ conversations: rows, details: detailsFor(rows), extraRoutes: listHonoringSource(rows) })

    // Positive control: a Claude Code session with a project_path has it.
    await withNetRetry(page, () => page.goto(`/conversations/${fooA1.uuid}`))
    await expect(page.locator(`[data-message-uuid="${messageUuid(fooA1)}"]`)).toBeVisible()
    await expect(label(page)).toBeVisible()

    for (const conv of [desk6, cowork]) {
      await withNetRetry(page, () => page.goto(`/conversations/${conv.uuid}`))
      // Settle: the conversation and its header ("Search scope") rendered.
      await expect(page.locator(`[data-message-uuid="${messageUuid(conv)}"]`)).toContainText(bodyText(conv))
      await expect(page.getByTestId('pin-scope-button')).toBeVisible()
      await expect(page.getByTestId('list-filter-button'), `${conv.source} header`).toHaveCount(0)
    }
  })

  test('8 header, keeps other URL parameters (pin, q)', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: SET6,
      details: detailsFor(SET6),
      extraRoutes: listHonoringSource(SET6),
    })
    await withNetRetry(page, () => page.goto(`/conversations/${fooB1.uuid}?q=o`))
    await expect(page.locator(`[data-message-uuid="${messageUuid(fooB1)}"]`)).toBeVisible()
    await expect(label(page)).toBeVisible()
    // Pin search to this project through the app, so `pin` has the app's own form.
    await page.getByTestId('pin-scope-button').click()
    await page.getByTestId('pin-this-project').click()
    await expect.poll(() => urlParam(page, 'pin')).toMatch(/^project:/)
    const pin = urlParam(page, 'pin')

    await listMenu(page, 'list-filter-path')
    await expectUrlParams(page, { path: '/b/foo', project: null, pin, q: 'o' })
    await expectRows(page, [fooB1], [fooA1, barC1])

    await listMenu(page, 'list-filter-project')
    await expectUrlParams(page, { project: 'foo', path: null, pin, q: 'o' })
    await expectRows(page, [fooA1, fooA2, fooB1], [barC1])

    await listMenu(page, 'list-filter-clear')
    await expectUrlParams(page, { project: null, path: null, pin, q: 'o' })
    await expectRows(page, [fooA1, fooA2, fooB1, barC1], [])
  })
})

test.describe('Sidebar list filters: empty state', () => {
  const fooOld1 = ccSession('Foo old one', '/a/foo', daysAgo(2))
  const fooOld2 = ccSession('Foo old two', '/b/foo', daysAgo(3))
  const barNew = ccSession('Bar new one', '/c/bar', hoursAgo(1))
  const SET10 = [fooOld1, fooOld2, barNew]

  async function openHiddenByBoth(page: import('./fixtures').Page) {
    await recordPreferencePatches(page)
    await startClock(page)
    await withNetRetry(page, () => page.goto('/conversations?project=foo'))
    const side = sidebar(page)
    // Settle: the empty-state message rendered (the list loaded rows).
    await expect(side).toContainText(/hidden by/i)
    await expect(side).toContainText('project=foo')
    await expect(side).toContainText(/last 24 h/)
    await expectRows(page, [], SET10)
    await expect(page.getByTestId('list-empty-clear-project')).toBeVisible()
    await expect(page.getByTestId('list-empty-show-older')).toBeVisible()
  }

  test('10 empty state: names the project and recent filters; "Show older sessions" turns the checkbox off', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: SET10,
      preferences: { listRecentOnly: true, listRecentHours: 24 },
      extraRoutes: listHonoringSource(SET10),
    })
    await openHiddenByBoth(page)

    await page.getByTestId('list-empty-show-older').click()
    await expect(page.getByTestId('sidebar-recent-toggle')).not.toBeChecked()
    await expectRows(page, [fooOld1, fooOld2], [barNew])
    await expectUrlParams(page, { project: 'foo' })
    await expect.poll(() => sentPrefValues(page, 'listRecentOnly')).toContain(false)
  })

  test('10 empty state: "Show all projects" removes the URL filter', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: SET10,
      preferences: { listRecentOnly: true, listRecentHours: 24 },
      extraRoutes: listHonoringSource(SET10),
    })
    await openHiddenByBoth(page)

    await page.getByTestId('list-empty-clear-project').click()
    await expectUrlParams(page, { project: null, path: null })
    await expectRows(page, [barNew], [fooOld1, fooOld2])
    await expect(page.getByTestId('sidebar-recent-toggle')).toBeChecked()
  })
})

test.describe('Sidebar list filters: reload', () => {
  const fooNew = ccSession('Foo reload new', '/a/foo', hoursAgo(1))
  const fooDay = ccSession('Foo reload thirty hours', '/b/foo', hoursAgo(30))
  const fooAncient = ccSession('Foo reload ancient', '/a/foo', daysAgo(100))
  const barNew = ccSession('Bar reload new', '/c/bar', hoursAgo(2))
  const SET15 = [fooNew, fooDay, fooAncient, barNew]

  test('15 reload keeps state: the project filter (URL) and the recent setting (N = 48, on) survive a reload', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: SET15,
      preferences: { listRecentOnly: true },
      extraRoutes: listHonoringSource(SET15),
    })
    await startClock(page)
    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, [fooNew, barNew], [fooDay, fooAncient])

    await typeHours(page, '48')
    await expect.poll(() => sentPrefValues(page, 'listRecentHours')).toContain(48)
    await pickOption(page, 'sidebar-project-select', 'foo')
    await expectUrlParams(page, { project: 'foo', path: null })
    await expectRows(page, [fooNew, fooDay], [barNew, fooAncient])

    await withNetRetry(page, () => page.reload())
    await expectUrlParams(page, { project: 'foo', path: null })
    await expectRows(page, [fooNew, fooDay], [barNew, fooAncient])
    await expect(page.getByTestId('sidebar-recent-toggle')).toBeChecked()
    await expect(page.getByTestId('sidebar-recent-hours')).toHaveValue('48')
    await expect(page.getByTestId('sidebar-project-select')).toContainText('foo')
  })

  test('15 reload keeps state: the path filter (URL) and the recent checkbox turned off survive a reload', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: SET15,
      details: detailsFor(SET15),
      preferences: { listRecentOnly: true, listRecentHours: 24 },
      extraRoutes: listHonoringSource(SET15),
    })
    await startClock(page)
    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto(`/conversations/${fooNew.uuid}`))
    await expect(page.locator(`[data-message-uuid="${messageUuid(fooNew)}"]`)).toBeVisible()

    await listMenu(page, 'list-filter-path')
    await expectUrlParams(page, { path: '/a/foo', project: null })
    await page.getByTestId('sidebar-recent-toggle').click()
    await expect(page.getByTestId('sidebar-recent-toggle')).not.toBeChecked()
    await expect.poll(() => sentPrefValues(page, 'listRecentOnly')).toContain(false)
    await expectRows(page, [fooNew, fooAncient], [fooDay, barNew])

    await withNetRetry(page, () => page.reload())
    await expectUrlParams(page, { path: '/a/foo', project: null })
    await expectRows(page, [fooNew, fooAncient], [fooDay, barNew])
    await expect(page.getByTestId('sidebar-recent-toggle')).not.toBeChecked()
    // While ?path= is active, the project select shows "Path: <path>".
    await expect(page.getByTestId('sidebar-project-select')).toContainText('Path: /a/foo')
    await expect(label(page)).toHaveAttribute('data-list-filter', 'path')
  })
})
