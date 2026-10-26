import { test, expect, recordPreferencePatches, withNetRetry } from './fixtures'
import {
  bodyText,
  ccSession,
  daysAgo,
  detailsFor,
  expectRows,
  hoursAgo,
  listHonoringSource,
  messageUuid,
  row,
  sentPrefValues,
  sidebar,
  pickTime,
  startClock,
  timeLabel,
  timeSelect,
} from './sidebar-list-filters.helpers'

/**
 * Sidebar list filters, recent sessions: test cases 1-4 and 9 of
 * PLANS/2026.10.25-sidebar-list-filters.md, changed to the round-2 time
 * dropdown of PLANS/2026.10.26-list-filter-dropdowns.md (the checkbox and
 * the hours field are gone). Black-box, from the spec.
 *
 * Every test sets the browser clock to NOW with page.clock, because the
 * recent filter compares `updated_at` with the browser clock. Case 16 (no
 * console errors or warnings) holds for every test through the auto
 * `consoleAssertions` fixture.
 */

const oneHour = ccSession('Recent one hour', '/w/alpha', hoursAgo(1))
const twentyThree = ccSession('Recent twenty-three hours', '/w/alpha', hoursAgo(23))
const twentyFive = ccSession('Older twenty-five hours', '/w/beta', hoursAgo(25))
const tenDays = ccSession('Old ten days', '/w/beta', daysAgo(10))
const FOUR = [oneHour, twentyThree, twentyFive, tenDays]

test.describe('Sidebar list filters: recent sessions', () => {
  test('1 recent, default on: with no saved preference only the last 1 day shows and the trigger reads "Last: 1 day"; "Forever" shows all and saves listRecentOnly: false', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: FOUR,
      // No saved value: the app's own default applies (1 day).
      preferences: { listRecentOnly: undefined },
      extraRoutes: listHonoringSource(FOUR),
    })
    await startClock(page)
    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))

    await expectRows(page, [oneHour, twentyThree], [twentyFive, tenDays])
    await expect(timeSelect(page)).toContainText(timeLabel('1 day'))

    await pickTime(page, 'Forever')
    await expectRows(page, FOUR, [])
    await expect.poll(() => sentPrefValues(page, 'listRecentOnly')).toContain(false)
  })

  test('2 recent, N: picking "3 days" shows the 25-hour session, keeps the 10-day session hidden, and saves listRecentHours: 72', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: FOUR,
      preferences: { listRecentOnly: true, listRecentHours: 24 },
      extraRoutes: listHonoringSource(FOUR),
    })
    await startClock(page)
    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, [oneHour, twentyThree], [twentyFive, tenDays])
    await expect(timeSelect(page)).toContainText(timeLabel('1 day'))

    await pickTime(page, '3 days')
    await expectRows(page, [oneHour, twentyThree, twentyFive], [tenDays])
    await expect.poll(() => sentPrefValues(page, 'listRecentHours')).toContain(72)
    expect(await sentPrefValues(page, 'listRecentOnly')).not.toContain(false)
  })

  test('3 recent, the clock moves: a session at now-23 h leaves the list 2 hours later, without a reload', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: FOUR,
      preferences: { listRecentOnly: true, listRecentHours: 24 },
      extraRoutes: listHonoringSource(FOUR),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, [oneHour, twentyThree], [twentyFive, tenDays])

    const navigations: string[] = []
    page.on('framenavigated', (f) => {
      if (f === page.mainFrame()) navigations.push(f.url())
    })
    await page.clock.fastForward('02:00:00')
    // The list applies the cutoff at least once a minute.
    await page.clock.runFor(61_000)

    await expectRows(page, [oneHour], [twentyThree, twentyFive, tenDays])
    expect(navigations, 'the page reloaded').toEqual([])
  })

  test('4 recent control layout: the time dropdown is on its own sidebar row, below the sort controls', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: FOUR, extraRoutes: listHonoringSource(FOUR) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, FOUR, [])

    await expect(timeSelect(page)).toBeVisible()
    const box = async (testId: string) => {
      const b = await page.getByTestId(testId).boundingBox()
      expect(b, `${testId} has no box`).not.toBeNull()
      return b!
    }
    const recent = await box('sidebar-recent-select')
    const empty = await box('show-phantom-sessions-toggle')
    // The sort-direction button sits on the sort row ("↑" or "↓").
    const sortDir = await sidebar(page).getByTitle(/^(Ascending|Descending)$/).boundingBox()
    expect(sortDir).not.toBeNull()

    const mid = (b: { y: number; height: number }) => b.y + b.height / 2
    // Below the sort controls.
    expect(mid(recent)).toBeGreaterThan(sortDir!.y + sortDir!.height)
    // Not on the row with "Empty" and "Archived".
    expect(Math.abs(mid(recent) - mid(empty))).toBeGreaterThan(Math.max(recent.height, empty.height) / 2)
  })

  test('9 no exceptions: with the recent filter on, a 10-day-old session opened by URL shows in the pane, and its row is not in the list', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: FOUR,
      details: detailsFor(FOUR),
      preferences: { listRecentOnly: true, listRecentHours: 24 },
      extraRoutes: listHonoringSource(FOUR),
    })
    await startClock(page)
    await withNetRetry(page, () => page.goto(`/conversations/${tenDays.uuid}`))

    const bubble = page.locator(`[data-message-uuid="${messageUuid(tenDays)}"]`)
    await expect(bubble).toBeVisible()
    await expect(bubble).toContainText(bodyText(tenDays))
    await expectRows(page, [oneHour, twentyThree], [tenDays, twentyFive])
    await expect(row(page, tenDays)).toHaveCount(0)
  })
})
