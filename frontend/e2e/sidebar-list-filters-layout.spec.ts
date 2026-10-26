import { test, expect, withNetRetry, type Page, type Route } from './fixtures'
import { ccSession, expectRows, hoursAgo, listHonoringSource, sidebar } from './sidebar-list-filters.helpers'
import type { ConversationSummary } from '../src/lib/types'

/**
 * Sidebar list filters, layout: test case 17 of
 * PLANS/2026.10.25-sidebar-list-filters.md ("The list keeps room on a
 * short screen"), and its "Layout" section. Black-box, from the spec.
 *
 * In CI the watcher-missing banner and the filter migration banner both
 * show, and the sidebar controls took all the height: the list was 0 px
 * high and showed no row. `toBeVisible()` does not see that (playwright.md
 * §3, ancestor clipping), so this spec measures rectangles: the list's
 * scroll container, cut by every clipping ancestor and by the window.
 */

const ROWS: ConversationSummary[] = Array.from({ length: 10 }, (_, i) =>
  ccSession(`Layout session ${String.fromCharCode(65 + i)}`, `/w/layout${i % 3}`, hoursAgo(i + 1)),
)

interface VisibleRect {
  top: number
  bottom: number
  height: number
  width: number
}

type VisibleRectFn = (el: Element) => VisibleRect

/**
 * Install `window.__visibleRect(el)`: the rectangle of `el` that the user
 * can see, cut by each clipping ancestor (overflow hidden/auto/scroll/clip)
 * and by the window. Call before `page.goto`.
 */
async function installVisibleRect(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const clippy = (v: string) => v === 'hidden' || v === 'auto' || v === 'scroll' || v === 'clip'
    ;(window as unknown as { __visibleRect: VisibleRectFn }).__visibleRect = (el: Element) => {
      const r = el.getBoundingClientRect()
      let top = r.top
      let bottom = r.bottom
      let left = r.left
      let right = r.right
      for (let n = el.parentElement; n; n = n.parentElement) {
        const cs = getComputedStyle(n)
        if (clippy(cs.overflowX) || clippy(cs.overflowY)) {
          const a = n.getBoundingClientRect()
          top = Math.max(top, a.top)
          bottom = Math.min(bottom, a.bottom)
          left = Math.max(left, a.left)
          right = Math.min(right, a.right)
        }
      }
      top = Math.max(top, 0)
      bottom = Math.min(bottom, window.innerHeight)
      left = Math.max(left, 0)
      right = Math.min(right, window.innerWidth)
      return { top, bottom, height: Math.max(0, bottom - top), width: Math.max(0, right - left) }
    }
  })
}

interface ListMeasure {
  /** Where the list's scroll container was found from, for the failure message. */
  found: string
  /** Height of the list area the user can see, in CSS px. */
  visibleHeight: number
  /** Rows (by title) whose box overlaps the list's visible rectangle. */
  rowsInside: string[]
  /** Rows of this fixture in the DOM at all. */
  rowsInDom: number
}

/**
 * Measure the list area: the nearest scroll container (overflow
 * auto/scroll/hidden/clip) above a list row, or above the list body when
 * no row is in the DOM.
 */
async function measureList(page: Page): Promise<ListMeasure> {
  return sidebar(page).evaluate((aside, names) => {
    const visibleRect = (window as unknown as { __visibleRect: VisibleRectFn }).__visibleRect
    const rows = Array.from(aside.querySelectorAll('[role="button"]')).filter((el) =>
      names.some((n) => (el.textContent ?? '').includes(n)),
    )
    const start: Element | null = rows[0] ?? aside.querySelector('[data-testid="conversation-list-flat"]')
    const found = rows[0] ? 'a row' : start ? 'the list body' : 'nothing'
    let container: Element | null = null
    for (let n = start?.parentElement ?? null; n && n !== aside.parentElement; n = n.parentElement) {
      if (['auto', 'scroll', 'hidden', 'clip'].includes(getComputedStyle(n).overflowY)) {
        container = n
        break
      }
    }
    if (!container) {
      return { found: `${found}, with no scroll container`, visibleHeight: 0, rowsInside: [], rowsInDom: rows.length }
    }
    const vis = visibleRect(container)
    const rowsInside = rows
      .filter((el) => {
        const r = el.getBoundingClientRect()
        return vis.height > 0 && Math.min(r.bottom, vis.bottom) - Math.max(r.top, vis.top) > 0
      })
      .map((el) => names.find((n) => (el.textContent ?? '').includes(n)) ?? '?')
    return { found, visibleHeight: Math.round(vis.height), rowsInside, rowsInDom: rows.length }
  }, ROWS.map((r) => r.name))
}

test.describe('Sidebar list filters: layout', () => {
  test.use({ viewport: { width: 1280, height: 720 } })

  test('17 the list keeps room on a short screen: with both banners, the list is at least 160 px high, at least 2 rows are inside it, and every control can be reached', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: ROWS,
      // The filter migration banner shows when the filters were migrated
      // and the banner was not dismissed.
      preferences: { filters: { nodes: {}, activeId: null, _migratedV1: true, _migratedV2: true } },
      extraRoutes: async (p) => {
        await listHonoringSource(ROWS)(p)
        await p.route('**/api/health/watcher', (route: Route) =>
          route.fulfill({
            contentType: 'application/json',
            body: JSON.stringify({ installed: false, platform: 'linux', install_command: 'x', docs_url: 'x' }),
          }),
        )
      },
    })
    await installVisibleRect(page)
    // The list loaded: its request was answered. (A row in the DOM is not
    // a settle signal here: a list with no height renders no row.)
    const listAnswered = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/conversations' && r.request().method() === 'GET',
    )
    await withNetRetry(page, () => page.goto('/'))
    await listAnswered

    // Both banners show (the CI state).
    await expect(page.getByTestId('watcher-missing-banner')).toBeVisible()
    await expect(page.getByTestId('filters-migration-banner')).toBeVisible()

    // The list area the user can see is at least 160 px high.
    let last: ListMeasure | null = null
    await expect
      .poll(
        async () => {
          last = await measureList(page)
          return last.visibleHeight
        },
        { message: 'visible height of the list area (px)' },
      )
      .toBeGreaterThanOrEqual(160)
      .catch((e: Error) => {
        throw new Error(`${e.message}\nlast measure: ${JSON.stringify(last)}`)
      })
    // At least 2 rows are inside its visible rectangle, not just in the DOM.
    const measure = await measureList(page)
    expect(measure.rowsInside.length, `rows inside the list's visible area: ${JSON.stringify(measure)}`).toBeGreaterThanOrEqual(2)

    // Every sidebar control can be reached: visible, or visible after the
    // block of controls scrolls to it.
    const side = sidebar(page)
    const controls: Array<[string, ReturnType<Page['locator']>]> = [
      ['title search', side.getByTestId('sidebar-title-search')],
      ['active filter select', side.getByTestId('active-filter-select')],
      ['source select', side.getByTestId('source-filter-select')],
      ['project picker', side.getByTestId('sidebar-project-select')],
      ['sort select', side.locator('button', { hasText: 'Last Activity' })],
      ['sort direction', side.getByTitle(/^(Ascending|Descending)$/)],
      ['time dropdown', side.getByTestId('sidebar-recent-select')],
      ['group checkbox', side.getByRole('checkbox', { name: /Group sessions by project/i })],
      ['empty toggle', side.getByTestId('show-phantom-sessions-toggle')],
      ['archived toggle', side.getByTestId('show-archived-sessions-toggle')],
    ]
    for (const [label, control] of controls) {
      await expect(control, `${label} is in the sidebar`).toHaveCount(1)
      await control.scrollIntoViewIfNeeded()
      const box = await control.boundingBox()
      expect(box, `${label} has a box`).not.toBeNull()
      const vis = await control.evaluate((el) =>
        (window as unknown as { __visibleRect: VisibleRectFn }).__visibleRect(el),
      )
      // The whole control is inside the visible area (1 px tolerance).
      expect(vis.height, `${label}: visible height vs its height ${box!.height}`).toBeGreaterThanOrEqual(box!.height - 1)
      expect(vis.width, `${label}: visible width vs its width ${box!.width}`).toBeGreaterThanOrEqual(box!.width - 1)
      // Actionability: nothing covers it at its center.
      await control.hover()
    }

    // The list still has its room after the controls scrolled.
    const after = await measureList(page)
    expect(after.visibleHeight, `list after scrolling the controls: ${JSON.stringify(after)}`).toBeGreaterThanOrEqual(160)
    await expectRows(page, [ROWS[0]], [])
  })
})
