import { test, expect, recordPreferencePatches, withNetRetry, type Page } from './fixtures'
import {
  NOW,
  TIME_LABELS,
  TIME_OPTIONS,
  ccSession,
  closeProjectPicker,
  daysAgo,
  expectProjectEntries,
  expectRows,
  expectUrlParams,
  hoursAgo,
  listHonoringSource,
  openOptions,
  openProjectPicker,
  pickProject,
  pickTime,
  projectEntries,
  projectFilter,
  sentPrefValues,
  sidebar,
  startClock,
  timeLabel,
  timeSelect,
} from './sidebar-list-filters.helpers'
import type { ConversationSummary } from '../src/lib/types'

/**
 * Sidebar list filters, round 2: the time dropdown and the project picker.
 * Test cases 18-27 of PLANS/2026.10.26-list-filter-dropdowns.md.
 * Black-box, from the spec. Case 27 (no console errors or warnings) holds
 * for every test through the auto `consoleAssertions` fixture; the last
 * test also checks it explicitly after using both controls.
 */

const at = (msAgo: number) => new Date(NOW.getTime() - msAgo).toISOString()
const MINUTE = 60_000
const HOUR = 3_600_000

// ---------------------------------------------------------------------------
// Time dropdown
// ---------------------------------------------------------------------------

test.describe('Sidebar list filters: time dropdown', () => {
  test('18 time options: the dropdown lists the 10 options in table order, and the default is "Last: 1 day"', async ({
    page,
    mockBackend,
  }) => {
    const rows = [ccSession('Options one hour', '/w/opts', hoursAgo(1))]
    await mockBackend({
      conversations: rows,
      // No saved value: the app's own default applies.
      preferences: { listRecentOnly: undefined },
      extraRoutes: listHonoringSource(rows),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, rows, [])

    await expect(timeSelect(page)).toContainText(timeLabel('1 day'))
    expect(await openOptions(page, 'sidebar-recent-select')).toEqual(TIME_LABELS)
  })

  // Case 19: one test per window. Each has a session 1 minute inside the
  // window and one 1 minute outside it. The browser clock is fixed at NOW.
  for (const [label, hours] of TIME_OPTIONS) {
    if (hours === null) continue
    test(`19 each option filters: "${label}" (${hours} h) shows a session just inside and hides one just outside`, async ({
      page,
      mockBackend,
    }) => {
      const inside = ccSession(`Inside ${label}`, '/w/window', at(hours * HOUR - MINUTE))
      const outside = ccSession(`Outside ${label}`, '/w/window', at(hours * HOUR + MINUTE))
      const rows = [outside, inside]
      // The e2e default seeds listRecentOnly: false ("Forever").
      await mockBackend({ conversations: rows, extraRoutes: listHonoringSource(rows) })
      await page.clock.setFixedTime(NOW)
      await withNetRetry(page, () => page.goto('/'))
      await expectRows(page, rows, [])
      await expect(timeSelect(page)).toContainText(timeLabel('Forever'))

      await pickTime(page, label)
      await expectRows(page, [inside], [outside])
    })
  }

  test('19 each option filters: "Forever" shows a session from two years ago', async ({ page, mockBackend }) => {
    const recent = ccSession('Forever one hour', '/w/forever', hoursAgo(1))
    const twoYears = ccSession('Forever two years', '/w/forever', daysAgo(730))
    const rows = [twoYears, recent]
    await mockBackend({
      conversations: rows,
      preferences: { listRecentOnly: true, listRecentHours: 8760 },
      extraRoutes: listHonoringSource(rows),
    })
    await page.clock.setFixedTime(NOW)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, [recent], [twoYears])
    await expect(timeSelect(page)).toContainText(timeLabel('1 year'))

    await pickTime(page, 'Forever')
    await expectRows(page, [recent, twoYears], [])
  })

  test('20 preferences: "2 weeks" sends listRecentOnly: true and listRecentHours: 336, "Forever" sends listRecentOnly: false, and a reload keeps each choice', async ({
    page,
    mockBackend,
  }) => {
    const tenDays = ccSession('Prefs ten days', '/w/prefs', daysAgo(10))
    const twentyDays = ccSession('Prefs twenty days', '/w/prefs', daysAgo(20))
    const rows = [twentyDays, tenDays]
    await mockBackend({ conversations: rows, extraRoutes: listHonoringSource(rows) })
    await startClock(page)
    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, rows, [])

    await pickTime(page, '2 weeks')
    await expectRows(page, [tenDays], [twentyDays])
    await expect.poll(() => sentPrefValues(page, 'listRecentOnly')).toContain(true)
    await expect.poll(() => sentPrefValues(page, 'listRecentHours')).toContain(336)

    await withNetRetry(page, () => page.reload())
    await expect(timeSelect(page)).toContainText(timeLabel('2 weeks'))
    await expectRows(page, [tenDays], [twentyDays])

    await pickTime(page, 'Forever')
    await expectRows(page, rows, [])
    await expect.poll(() => sentPrefValues(page, 'listRecentOnly')).toContain(false)

    await withNetRetry(page, () => page.reload())
    await expect(timeSelect(page)).toContainText(timeLabel('Forever'))
    await expectRows(page, rows, [])
  })

  test('21 saved hours not in the table: listRecentHours 48 shows "Last: 48 hours" and applies; the "48 hours" option sits by size and goes away after picking "1 week"', async ({
    page,
    mockBackend,
  }) => {
    const h30 = ccSession('Saved thirty hours', '/w/saved', hoursAgo(30))
    const h50 = ccSession('Saved fifty hours', '/w/saved', hoursAgo(50))
    const rows = [h50, h30]
    await mockBackend({
      conversations: rows,
      preferences: { listRecentOnly: true, listRecentHours: 48 },
      extraRoutes: listHonoringSource(rows),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))

    await expect(timeSelect(page)).toContainText(timeLabel('48 hours'))
    await expectRows(page, [h30], [h50])
    expect(await openOptions(page, 'sidebar-recent-select')).toEqual([
      '8 hours',
      '1 day',
      '48 hours',
      '3 days',
      '1 week',
      '2 weeks',
      '1 month',
      '3 months',
      '6 months',
      '1 year',
      'Forever',
    ])

    await pickTime(page, '1 week')
    await expectRows(page, [h30, h50], [])
    expect(await openOptions(page, 'sidebar-recent-select')).toEqual(TIME_LABELS)
  })

  test('22 empty state: with "1 week" and only older sessions, the message names "last 1 week", and "Show older sessions" sets "Forever"', async ({
    page,
    mockBackend,
  }) => {
    const tenDays = ccSession('Empty ten days', '/w/empty', daysAgo(10))
    const twentyDays = ccSession('Empty twenty days', '/w/empty', daysAgo(20))
    const rows = [tenDays, twentyDays]
    await mockBackend({
      conversations: rows,
      preferences: { listRecentOnly: true, listRecentHours: 168 },
      extraRoutes: listHonoringSource(rows),
    })
    await startClock(page)
    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))

    await expect(timeSelect(page)).toContainText(timeLabel('1 week'))
    await expect(sidebar(page)).toContainText(/hidden by/i)
    await expect(sidebar(page)).toContainText('last 1 week')
    await expectRows(page, [], rows)

    await page.getByTestId('list-empty-show-older').click()
    await expect(timeSelect(page)).toContainText(timeLabel('Forever'))
    await expectRows(page, rows, [])
    await expect.poll(() => sentPrefValues(page, 'listRecentOnly')).toContain(false)
  })
})

// ---------------------------------------------------------------------------
// Project picker
// ---------------------------------------------------------------------------

// Case 23: newest first by each project's latest session. The names sort
// the other way, each project also has older sessions (so neither the
// oldest nor the first-listed session decides), Bravo and charlie tie (by
// name, ignoring case), and the mock returns the rows in another order.
const ORDER_ROWS: ConversationSummary[] = [
  ccSession('Order alpha new', '/w/alpha', daysAgo(14)),
  ccSession('Order zulu old', '/w/zulu', daysAgo(365)),
  ccSession('Order charlie tie', '/w/charlie', daysAgo(5)),
  ccSession('Order mid new', '/w/mid', daysAgo(3)),
  ccSession('Order alpha old', '/w/alpha', daysAgo(30)),
  ccSession('Order Bravo tie', '/x/Bravo', daysAgo(5)),
  ccSession('Order zulu new', '/w/zulu', hoursAgo(1)),
  ccSession('Order mid old', '/x/mid', daysAgo(60)),
]

// Cases 24, 25, 27: names for typing. "ap" matches webapp, Api-server, and
// mobile-app, not docs.
const webapp = ccSession('Type webapp one hour', '/w/webapp', hoursAgo(1))
const docs = ccSession('Type docs one day', '/w/docs', daysAgo(1))
const apiServer = ccSession('Type api two days', '/w/Api-server', daysAgo(2))
const mobileApp = ccSession('Type mobile one week', '/w/mobile-app', daysAgo(7))
const TYPE_ROWS = [mobileApp, docs, webapp, apiServer]

/** Name of the highlighted entry, if the picker exposes one. */
async function highlightedEntry(page: Page): Promise<string | null> {
  return page.evaluate(() => {
    const text = (el: Element | null) => (el?.textContent ?? '').trim() || null
    const field = document.querySelector('[data-testid="sidebar-project-filter"]')
    const active = field?.getAttribute('aria-activedescendant')
    if (active) return text(document.getElementById(active))
    const options = Array.from(document.querySelectorAll('[role="option"]'))
    for (const attr of ['data-highlighted', 'data-active']) {
      const hit = options.filter((o) => o.hasAttribute(attr) && o.getAttribute(attr) !== 'false')
      if (hit.length === 1) return text(hit[0])
    }
    const selected = options.filter((o) => o.getAttribute('aria-selected') === 'true' || o.getAttribute('data-selected') === 'true')
    return selected.length === 1 ? text(selected[0]) : null
  })
}

test.describe('Sidebar list filters: project picker', () => {
  test('23 project order: projects list newest first by their latest session, even when their names sort the other way; a tie sorts by name', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: ORDER_ROWS, extraRoutes: listHonoringSource(ORDER_ROWS) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expect(sidebar(page).locator('[role="button"]', { hasText: 'Order zulu new' })).toBeVisible()

    await openProjectPicker(page)
    await expectProjectEntries(page, ['All projects', 'zulu', 'mid', 'Bravo', 'charlie', 'alpha'])
  })

  test('24 type to filter: part of a name in any case keeps the matching projects newest first, "All projects" stays first, no match says "No matching project", and a pick clears the text', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: TYPE_ROWS, extraRoutes: listHonoringSource(TYPE_ROWS) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, TYPE_ROWS, [])

    await openProjectPicker(page)
    const field = projectFilter(page)
    const name = await field.evaluate(
      (el) => `${el.getAttribute('placeholder') ?? ''}|${el.getAttribute('aria-label') ?? ''}`,
    )
    expect(name, 'the field reads "Type to filter projects" (placeholder or label)').toMatch(/Type to filter projects/)
    await expectProjectEntries(page, ['All projects', 'webapp', 'docs', 'Api-server', 'mobile-app'])

    await field.pressSequentially('AP')
    await expectProjectEntries(page, ['All projects', 'webapp', 'Api-server', 'mobile-app'])
    await field.fill('ApP')
    await expectProjectEntries(page, ['All projects', 'webapp', 'mobile-app'])

    await field.fill('zzz')
    await expect(page.getByText('No matching project')).toBeVisible()
    await expect
      .poll(async () => (await projectEntries(page)).filter((t) => t !== 'No matching project'))
      .toEqual(['All projects'])

    // A click picks; picking closes the panel and clears the typed text.
    await field.fill('mobile')
    await expectProjectEntries(page, ['All projects', 'mobile-app'])
    await page.getByRole('option', { name: 'mobile-app', exact: true }).click()
    await expect(field).toBeHidden()
    await expectUrlParams(page, { project: 'mobile-app', path: null })
    await expectRows(page, [mobileApp], [webapp, docs, apiServer])

    await openProjectPicker(page)
    await expect(projectFilter(page)).toHaveValue('')
    await expectProjectEntries(page, ['All projects', 'webapp', 'docs', 'Api-server', 'mobile-app'])
    await closeProjectPicker(page)
  })

  test('25 keyboard: type, ArrowDown, Enter picks the highlighted project; Escape closes the picker and changes nothing', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: TYPE_ROWS, extraRoutes: listHonoringSource(TYPE_ROWS) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, TYPE_ROWS, [])

    await openProjectPicker(page)
    await projectFilter(page).pressSequentially('ap')
    await expectProjectEntries(page, ['All projects', 'webapp', 'Api-server', 'mobile-app'])
    await page.keyboard.press('ArrowDown')
    const highlighted = await highlightedEntry(page)
    await page.keyboard.press('Enter')
    await expect(projectFilter(page)).toBeHidden()

    await expect.poll(() => new URL(page.url()).searchParams.get('project')).not.toBeNull()
    const picked = new URL(page.url()).searchParams.get('project')
    expect(['webapp', 'Api-server', 'mobile-app'], `picked ${picked}`).toContain(picked)
    if (highlighted !== null) expect(picked, 'the picked project is the highlighted entry').toBe(highlighted)
    expect(new URL(page.url()).searchParams.get('path')).toBeNull()
    await expect(page.getByTestId('sidebar-project-select')).toContainText(picked!)

    // Escape: nothing changes.
    const urlBefore = page.url()
    await openProjectPicker(page)
    await expect(projectFilter(page)).toHaveValue('')
    await projectFilter(page).pressSequentially('doc')
    await expectProjectEntries(page, ['All projects', 'docs'])
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Escape')
    await expect(projectFilter(page)).toBeHidden()
    expect(page.url()).toBe(urlBefore)
    await expect(page.getByTestId('sidebar-project-select')).toContainText(picked!)
  })

  test('26 path entry: while ?path= is active, "Path: <path>" is the second entry and the button shows it', async ({
    page,
    mockBackend,
  }) => {
    const fooA = ccSession('Path foo a', '/a/foo', hoursAgo(1))
    const fooB = ccSession('Path foo b', '/b/foo', hoursAgo(2))
    const bar = ccSession('Path bar c', '/c/bar', hoursAgo(3))
    const rows = [bar, fooB, fooA]
    await mockBackend({ conversations: rows, extraRoutes: listHonoringSource(rows) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/conversations?path=/a/foo'))
    await expectRows(page, [fooA], [fooB, bar])

    const button = page.getByTestId('sidebar-project-select')
    await expect(button).toContainText('Path: /a/foo')
    await openProjectPicker(page)
    await expectProjectEntries(page, ['All projects', 'Path: /a/foo', 'foo', 'bar'])

    await page.getByRole('option', { name: 'All projects', exact: true }).click()
    await expect(projectFilter(page)).toBeHidden()
    await expectUrlParams(page, { project: null, path: null })
    await expect(button).toContainText('All projects')
    await expectRows(page, rows, [])
    // Without a path filter there is no "Path:" entry.
    await openProjectPicker(page)
    await expectProjectEntries(page, ['All projects', 'foo', 'bar'])
    await closeProjectPicker(page)
  })

  test('27 no console errors or warnings while using the time dropdown and the project picker', async ({
    page,
    mockBackend,
    consoleAssertions,
  }) => {
    await mockBackend({ conversations: TYPE_ROWS, extraRoutes: listHonoringSource(TYPE_ROWS) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, TYPE_ROWS, [])

    await pickTime(page, '3 days')
    await expectRows(page, [webapp, docs, apiServer], [mobileApp])
    await pickTime(page, 'Forever')
    await expectRows(page, TYPE_ROWS, [])

    await openProjectPicker(page)
    await projectFilter(page).pressSequentially('doc')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Escape')
    await expect(projectFilter(page)).toBeHidden()
    await pickProject(page, 'docs')
    await expectRows(page, [docs], [webapp, apiServer, mobileApp])
    await pickProject(page, 'All projects')
    await expectRows(page, TYPE_ROWS, [])

    expect(consoleAssertions.errors, 'console errors').toEqual([])
    expect(consoleAssertions.warnings, 'console warnings').toEqual([])
  })
})
