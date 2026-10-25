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
  startClock,
  typeHours,
} from './sidebar-list-filters.helpers'

/**
 * Sidebar list filters, recent sessions: test cases 1-4 and 9 of
 * PLANS/2026.10.25-sidebar-list-filters.md. Black-box, from the spec.
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
  test('1 recent, default on: with no saved preference only the last 24 h show; turning it off shows all and saves listRecentOnly: false', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: FOUR,
      // No saved value: the app's own default applies (on, 24 h).
      preferences: { listRecentOnly: undefined },
      extraRoutes: listHonoringSource(FOUR),
    })
    await startClock(page)
    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))

    await expectRows(page, [oneHour, twentyThree], [twentyFive, tenDays])
    const toggle = page.getByTestId('sidebar-recent-toggle')
    await expect(toggle).toBeChecked()
    await expect(page.getByTestId('sidebar-recent-hours')).toHaveValue('24')

    await toggle.click()
    await expect(toggle).not.toBeChecked()
    await expectRows(page, FOUR, [])
    await expect.poll(() => sentPrefValues(page, 'listRecentOnly')).toContain(false)
  })

  test('2 recent, N: 48 shows the 25-hour session; 0, empty, text, and a negative number are not saved and the field shows 48 again on blur', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: FOUR,
      preferences: { listRecentOnly: true },
      extraRoutes: listHonoringSource(FOUR),
    })
    await startClock(page)
    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, [oneHour, twentyThree], [twentyFive, tenDays])

    const field = page.getByTestId('sidebar-recent-hours')
    // The field is a text field with a numeric keyboard (spec amendment).
    await expect(field).toHaveAttribute('inputmode', 'numeric')
    expect(await field.evaluate((el) => (el as HTMLInputElement).type)).toBe('text')

    await typeHours(page, '48')
    await expect(field).toHaveValue('48')
    await expectRows(page, [oneHour, twentyThree, twentyFive], [tenDays])
    await expect.poll(() => sentPrefValues(page, 'listRecentHours')).toContain(48)

    for (const bad of ['0', '', 'abc', '-5']) {
      await typeHours(page, bad)
      await expect(field, `after "${bad}" and blur`).toHaveValue('48')
    }
    // Let any debounced save run before checking that none was sent.
    await page.clock.runFor(5_000)
    const sent = await sentPrefValues(page, 'listRecentHours')
    expect(sent, `listRecentHours PATCH values: ${JSON.stringify(sent)}`).toEqual(sent.map(() => 48))
    // N is still 48: the 25-hour session still shows.
    await expectRows(page, [oneHour, twentyThree, twentyFive], [tenDays])
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

  test('4 recent, field disabled while the checkbox is off', async ({ page, mockBackend }) => {
    // The e2e default seeds listRecentOnly: false.
    await mockBackend({ conversations: FOUR, extraRoutes: listHonoringSource(FOUR) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, FOUR, [])

    const toggle = page.getByTestId('sidebar-recent-toggle')
    const field = page.getByTestId('sidebar-recent-hours')
    await expect(toggle).not.toBeChecked()
    await expect(field).toBeDisabled()

    await toggle.click()
    await expect(toggle).toBeChecked()
    await expect(field).toBeEnabled()
    await expectRows(page, [oneHour, twentyThree], [twentyFive, tenDays])

    await toggle.click()
    await expect(toggle).not.toBeChecked()
    await expect(field).toBeDisabled()
    await expectRows(page, FOUR, [])
  })

  test('4 recent control layout: its own sidebar row, below the sort controls', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: FOUR, extraRoutes: listHonoringSource(FOUR) })
    await startClock(page)
    await withNetRetry(page, () => page.goto('/'))
    await expectRows(page, FOUR, [])

    const toggle = page.getByTestId('sidebar-recent-toggle')
    await expect(toggle).toBeVisible()
    const box = async (testId: string) => {
      const b = await page.getByTestId(testId).boundingBox()
      expect(b, `${testId} has no box`).not.toBeNull()
      return b!
    }
    const recent = await box('sidebar-recent-toggle')
    const hours = await box('sidebar-recent-hours')
    const empty = await box('show-phantom-sessions-toggle')
    // The sort-direction button sits on the sort row ("↑" or "↓").
    const sortDir = await page.getByTitle(/^(Ascending|Descending)$/).boundingBox()
    expect(sortDir).not.toBeNull()

    const mid = (b: { y: number; height: number }) => b.y + b.height / 2
    // Checkbox and field share one row.
    expect(Math.abs(mid(recent) - mid(hours))).toBeLessThan(Math.max(recent.height, hours.height))
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
