/**
 * Shared fixtures and locators for the sidebar list-filter specs
 * (PLANS/2026.10.25-sidebar-list-filters.md):
 *
 *   - sidebar-list-filters-recent.spec.ts    (cases 1-4, 9)
 *   - sidebar-list-filters-project.spec.ts   (cases 5-8, 10, 15)
 *   - sidebar-list-filters-grouping.spec.ts  (cases 11-14, 16)
 *
 * Black-box: every locator here is a DOM hook from the spec's "DOM hooks"
 * table, or a control that already exists on main (the source select, the
 * "Group sessions by project" checkbox, the sidebar rows).
 */
import {
  expect,
  makeDetail,
  makeMessage,
  makeSummary,
  preferencePatchesSent,
  type Page,
  type Route,
} from './fixtures'
import type { ConversationDetail, ConversationSummary } from '../src/lib/types'

/** The browser clock of every test that depends on "now". */
export const NOW = new Date('2026-10-25T12:00:00.000Z')

export function hoursAgo(hours: number): string {
  return new Date(NOW.getTime() - hours * 3_600_000).toISOString()
}

export function daysAgo(days: number): string {
  return hoursAgo(days * 24)
}

/** Install the fake browser clock at NOW. Call before `page.goto`. */
export async function startClock(page: Page): Promise<void> {
  await page.clock.install({ time: NOW })
}

let uuidCounter = 0
/** A stable, unique conversation uuid per fixture name. */
function uuidFor(seed: string): string {
  uuidCounter += 1
  let hash = 0
  for (const ch of seed) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  const tail = (hash.toString(16) + uuidCounter.toString(16)).padStart(12, '0').slice(-12)
  return `00000000-0000-4000-8000-${tail}`
}

/** A Claude Code session in `path` (project_name = last folder). */
export function ccSession(
  name: string,
  path: string,
  updatedAt: string,
  extra: Partial<ConversationSummary> = {},
): ConversationSummary {
  return makeSummary({
    uuid: uuidFor(name),
    name,
    source: 'CLAUDE_CODE',
    project_path: path,
    project_name: path.split('/').filter(Boolean).pop() ?? null,
    created_at: updatedAt,
    updated_at: updatedAt,
    message_count: 2,
    human_message_count: 1,
    ...extra,
  })
}

export function desktopChat(name: string, updatedAt: string): ConversationSummary {
  return makeSummary({
    uuid: uuidFor(name),
    name,
    source: 'CLAUDE_AI',
    created_at: updatedAt,
    updated_at: updatedAt,
    message_count: 2,
    human_message_count: 1,
  })
}

export function coworkSession(name: string, updatedAt: string): ConversationSummary {
  return makeSummary({
    uuid: uuidFor(name),
    name,
    source: 'CLAUDE_COWORK',
    project_path: '/sessions/sandbox-vm',
    project_name: 'sandbox-vm',
    created_at: updatedAt,
    updated_at: updatedAt,
    message_count: 2,
    human_message_count: 1,
  })
}

/** The body text of each conversation detail: `Body of <name>`. */
export function bodyText(summary: ConversationSummary): string {
  return `Body of ${summary.name}`
}

export function messageUuid(summary: ConversationSummary): string {
  return `${summary.uuid}-m1`
}

/** One detail per summary, each with a single human message. */
export function detailsFor(summaries: ConversationSummary[]): Record<string, ConversationDetail> {
  const out: Record<string, ConversationDetail> = {}
  for (const s of summaries) {
    out[s.uuid] = makeDetail(s, [
      makeMessage({
        uuid: messageUuid(s),
        sender: 'human',
        text: bodyText(s),
        content: [{ type: 'text', text: bodyText(s) }],
      }),
    ])
  }
  return out
}

/**
 * `extraRoutes` for mockBackend: the list route answers like the real
 * backend for `?source=` (a server-side filter), and returns the rows in
 * the order given. It does NOT sort: the specs that check order give the
 * rows in an order that matches no expected result.
 */
export function listHonoringSource(all: ConversationSummary[]) {
  return async (p: Page) => {
    await p.route('**/api/conversations**', (route: Route) => {
      const url = new URL(route.request().url())
      if (/\/api\/conversations\/[^/?]+/.test(url.pathname)) return route.fallback()
      const src = url.searchParams.get('source')
      const rows = !src || src === 'all' ? all : all.filter((c) => c.source === src)
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(rows) })
    })
  }
}

/** The Conversation List sidebar (the <aside> that holds the source select). */
export function sidebar(page: Page) {
  return page.locator('aside').filter({ has: page.getByTestId('source-filter-select') })
}

/**
 * The list row of a conversation: the existing `role="button"` row, which
 * shows the title. Fixture titles are chosen so that no title contains
 * another.
 */
export function row(page: Page, summary: ConversationSummary) {
  return sidebar(page).locator('[role="button"]', { hasText: summary.name })
}

/**
 * Wait until every `shown` row is visible, then assert every `hidden` row
 * is absent. The positive half is the settle signal for the negative half.
 */
export async function expectRows(
  page: Page,
  shown: ConversationSummary[],
  hidden: ConversationSummary[],
): Promise<void> {
  for (const s of shown) {
    await expect(row(page, s), `row "${s.name}" should show`).toBeVisible()
  }
  for (const s of hidden) {
    await expect(row(page, s), `row "${s.name}" should be hidden`).toHaveCount(0)
  }
}

/** The `data-group-name` of each folder header, top to bottom. */
export async function folderOrder(page: Page): Promise<string[]> {
  return sidebar(page)
    .getByTestId('list-group-header')
    .evaluateAll((els) => els.map((e) => e.getAttribute('data-group-name') ?? ''))
}

export function folderHeader(page: Page, name: string) {
  return sidebar(page).locator(`[data-testid="list-group-header"][data-group-name="${name}"]`)
}

/** Search params of the current URL. */
export function urlParam(page: Page, key: string): string | null {
  return new URL(page.url()).searchParams.get(key)
}

/** Poll until each key of `want` has that value in the URL (null = absent). */
export async function expectUrlParams(page: Page, want: Record<string, string | null>): Promise<void> {
  await expect
    .poll(() => {
      const got: Record<string, string | null> = {}
      for (const k of Object.keys(want)) got[k] = urlParam(page, k)
      return got
    })
    .toEqual(want)
}

/**
 * Every value of `key` sent in a preferences PATCH/PUT so far (needs
 * `recordPreferencePatches(page)` before `page.goto`).
 */
export async function sentPrefValues(page: Page, key: string): Promise<unknown[]> {
  const patches = await preferencePatchesSent(page)
  return patches.filter((p) => p?.data && key in p.data).map((p) => p.data![key])
}

/** Open a shadcn/Radix select and pick an option (playwright.md §3). */
export async function pickOption(page: Page, triggerTestId: string, option: string | RegExp): Promise<void> {
  await page.getByTestId(triggerTestId).click()
  const opt = page.getByRole('option', { name: option, exact: typeof option === 'string' })
  await expect(opt).toBeVisible()
  await opt.click()
}

/** The option labels of an open select, top to bottom. */
export async function openOptions(page: Page, triggerTestId: string): Promise<string[]> {
  await page.getByTestId(triggerTestId).click()
  const options = page.getByRole('option')
  await expect(options.first()).toBeVisible()
  const labels = (await options.allTextContents()).map((t) => t.trim())
  await page.keyboard.press('Escape')
  await expect(page.getByRole('option')).toHaveCount(0)
  return labels
}

/** Open the header "List" menu and click one item. */
export async function listMenu(
  page: Page,
  item: 'list-filter-project' | 'list-filter-path' | 'list-filter-clear',
): Promise<void> {
  await page.getByTestId('list-filter-button').click()
  const entry = page.getByTestId(item)
  await expect(entry).toBeVisible()
  await entry.click()
}

/**
 * Time dropdown (round 2, PLANS/2026.10.26-list-filter-dropdowns.md): its
 * options in table order, with the hours each one means (null = Forever).
 */
export const TIME_OPTIONS: ReadonlyArray<readonly [string, number | null]> = [
  ['8 hours', 8],
  ['1 day', 24],
  ['3 days', 72],
  ['1 week', 168],
  ['2 weeks', 336],
  ['1 month', 720],
  ['3 months', 2160],
  ['6 months', 4320],
  ['1 year', 8760],
  ['Forever', null],
]

export const TIME_LABELS: string[] = TIME_OPTIONS.map(([label]) => label)

/** The time dropdown trigger. */
export function timeSelect(page: Page) {
  return page.getByTestId('sidebar-recent-select')
}

/**
 * The trigger label "Last: <option>". "Last:" and the option may be
 * separate elements, spaced by layout rather than by a space character
 * (the accessible value reads "Last: 1 day" while textContent is
 * "Last:1 day"), so the whitespace between them is optional.
 */
export function timeLabel(option: string): RegExp {
  return new RegExp(`Last:\\s*${option.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`)
}

/** Pick a window in the time dropdown; the trigger then shows "Last: <option>". */
export async function pickTime(page: Page, option: string): Promise<void> {
  await pickOption(page, 'sidebar-recent-select', option)
  await expect(timeSelect(page)).toContainText(timeLabel(option))
}

/** The project picker's filter field. */
export function projectFilter(page: Page) {
  return page.getByTestId('sidebar-project-filter')
}

/** Open the project picker; the filter field shows and has the focus. */
export async function openProjectPicker(page: Page): Promise<void> {
  await page.getByTestId('sidebar-project-select').click()
  await expect(projectFilter(page)).toBeVisible()
  await expect(projectFilter(page)).toBeFocused()
}

/** The entries (role="option") on the page, top to bottom. */
export async function projectEntries(page: Page): Promise<string[]> {
  return (await page.getByRole('option').allInnerTexts()).map((t) => t.trim())
}

/** Poll until the open picker lists exactly `want`, in order. */
export async function expectProjectEntries(page: Page, want: string[]): Promise<void> {
  await expect.poll(() => projectEntries(page)).toEqual(want)
}

/** Close the open project picker with Escape. */
export async function closeProjectPicker(page: Page): Promise<void> {
  await page.keyboard.press('Escape')
  await expect(projectFilter(page)).toBeHidden()
}

/** Open the project picker and click one entry by its exact text. */
export async function pickProject(page: Page, entry: string): Promise<void> {
  await openProjectPicker(page)
  const opt = page.getByRole('option', { name: entry, exact: true })
  await expect(opt).toBeVisible()
  await opt.click()
  await expect(projectFilter(page)).toBeHidden()
}
