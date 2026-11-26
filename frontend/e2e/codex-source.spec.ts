import { readFile } from 'node:fs/promises'
import {
  test,
  expect,
  installLocalPrefsMock,
  withNetRetry,
  waitForScrollQuiet,
  type Page,
  type Route,
} from './fixtures'
import {
  closeProjectPicker,
  openProjectPicker,
  pickOption,
  pickProject,
  projectEntries,
  sidebar,
} from './sidebar-list-filters.helpers'

/**
 * Codex end to end (plan PLANS/2026.11.18-codex-source.md, N9 and AC2):
 * the source and project filters, search and the jump to a match, the
 * detail controls, copy, Markdown, and PDF, against the REAL fixture
 * backend (playwright.config.ts).
 *
 * The data is the committed archive in
 * `backend/tests/fixtures/desktop/codex/fixture000000/`, which
 * `scripts/generate_e2e_fixtures.py` makes with the real copier from the
 * NF corpus (`backend/tests/codex_fake.py`, `build_codex_home`). The thread
 * ids and titles below come from that corpus. `CODEX_HOME` points to a
 * missing folder, so the backend copies nothing into the fixtures.
 *
 * Only `/api/preferences` and `/api/bookmarks` are mocked: their real
 * routes write next to the fixtures or read the developer's own file.
 * The console gate of TESTING.md §5.15 runs for every test (`./fixtures`).
 */

const PAGINATED = '019a7c10-0000-7000-8000-00000000a001'
const LEGACY = '019a0f20-0000-7000-8000-00000000b002'
const IMPORTED = '019a8060-0000-7000-8000-00000000f006'

const TITLES = {
  paginated: 'Alpha parser refactor',
  legacy: 'Why does the build fail? Here is the error.',
  subagent: 'Scout: callers of parse',
  guardian: 'Review the proposed command: rm -rf build/',
  imported: 'Continue the migration from the Claude Code session.',
  archived: 'Gamma release notes',
}
const VISIBLE_TITLES = [TITLES.paginated, TITLES.legacy, TITLES.subagent, TITLES.guardian, TITLES.imported]
const DESKTOP_TITLE = 'Phase 5 fixture: TLS handshakes (long)'
// The newest non-Codex row: a Claude Code Code Mode recording (issue #90). The
// list is virtualized, so an older row such as DESKTOP_TITLE may not be rendered.
const NEWEST_NON_CODEX_TITLE = 'Use your REPL (Code Mode) tool'
const FIXTURE_HOME = '/home/fixture/.codex'
// L7: a Codex home outside ~ shows only its last folder name; the copy button keeps the full path.
const FIXTURE_HOME_DISPLAY = '…/.codex'

interface ApiBlock {
  type: string
  text?: string | null
  name?: string | null
}
interface ApiMessage {
  uuid: string
  sender: string
  content: ApiBlock[]
}
interface ApiDetail {
  uuid: string
  name: string
  messages: ApiMessage[]
  notices: string[]
  source_name: string | null
  source_home: string | null
}

async function mockWriters(page: Page): Promise<void> {
  await installLocalPrefsMock(page)
  await page.route('**/api/bookmarks', (route: Route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ bookmarks: [] }) }),
  )
}

/** The real backend's detail of a thread (not mocked: page.route does not see page.request). */
async function apiDetail(page: Page, uuid: string): Promise<ApiDetail> {
  const r = await page.request.get(`/api/conversations/${uuid}`)
  expect(r.status(), `GET /api/conversations/${uuid}`).toBe(200)
  return (await r.json()) as ApiDetail
}

function messageWith(detail: ApiDetail, pred: (b: ApiBlock) => boolean): ApiMessage {
  const m = detail.messages.find((msg) => msg.content.some(pred))
  expect(m, `a message of ${detail.uuid} that matches`).toBeTruthy()
  return m!
}

/** The list row of a title; no fixture title contains another. */
const rowOf = (page: Page, name: string) => sidebar(page).locator('[role="button"]', { hasText: name })

async function openList(page: Page): Promise<void> {
  await withNetRetry(page, () => page.goto('/'))
  await expect(sidebar(page)).toBeVisible()
}

async function pickCodex(page: Page): Promise<void> {
  await pickOption(page, 'source-filter-select', 'Codex')
  await expect(page.getByTestId('source-filter-select')).toContainText('Codex')
}

/** The picker label of the project whose folder is `name` (the label can add a parent folder). */
async function projectLabel(page: Page, name: string): Promise<string> {
  await openProjectPicker(page)
  await expect.poll(async () => (await projectEntries(page)).filter((e) => e.endsWith(name))).toHaveLength(1)
  const label = (await projectEntries(page)).find((e) => e.endsWith(name))!
  await closeProjectPicker(page)
  return label
}

async function openConversation(page: Page, uuid: string, title: string) {
  await withNetRetry(page, () => page.goto(`/conversations/${uuid}`))
  const header = page.locator('header').first()
  await expect(header.getByRole('heading', { level: 1, name: title })).toBeVisible()
  return header
}

/** Click a copy button, wait for its green check, and return the clipboard text. */
async function copyAndRead(page: Page, title: string): Promise<string> {
  const button = page.locator('header').first().getByTitle(title)
  await expect(button).toBeVisible()
  await button.click()
  await expect(button.locator('.text-green-500')).toBeVisible()
  return page.evaluate(() => navigator.clipboard.readText())
}

test.describe('Codex source end to end (N9, AC2)', () => {
  test.beforeEach(async ({ page }) => {
    await mockWriters(page)
  })

  test('the list shows every Codex session with a turn', async ({ page }) => {
    await openList(page)
    for (const title of VISIBLE_TITLES) {
      await expect(rowOf(page, title), `row "${title}"`).toBeVisible()
    }
    // Archived Codex sessions are hidden by default (plan §4.1).
    await expect(rowOf(page, TITLES.archived)).toHaveCount(0)
  })

  test('the Codex source filter shows only the Codex sessions', async ({ page }) => {
    await openList(page)
    await expect(rowOf(page, NEWEST_NON_CODEX_TITLE).first()).toBeVisible()
    await pickCodex(page)
    for (const title of VISIBLE_TITLES) {
      await expect(rowOf(page, title), `row "${title}"`).toBeVisible()
    }
    await expect(rowOf(page, NEWEST_NON_CODEX_TITLE)).toHaveCount(0)
    await expect(rowOf(page, DESKTOP_TITLE)).toHaveCount(0)
  })

  test('the Archived toggle shows the archived Codex session', async ({ page }) => {
    await openList(page)
    await pickCodex(page)
    await expect(rowOf(page, TITLES.paginated)).toBeVisible()
    await expect(rowOf(page, TITLES.archived)).toHaveCount(0)
    // click(), then assert: .check() races the controlled re-render (TESTING.md §3).
    const archived = page.getByTestId('show-archived-sessions-toggle')
    await archived.click()
    await expect(archived).toBeChecked()
    await expect(rowOf(page, TITLES.archived)).toBeVisible()
  })

  test('the project filter narrows the Codex list to one project', async ({ page }) => {
    await openList(page)
    await pickCodex(page)
    await expect(rowOf(page, TITLES.paginated)).toBeVisible()
    await pickProject(page, await projectLabel(page, 'proj-beta'))
    await expect(rowOf(page, TITLES.legacy)).toBeVisible()
    for (const title of [TITLES.paginated, TITLES.subagent, TITLES.guardian, TITLES.imported]) {
      await expect(rowOf(page, title), `row "${title}" is in another project`).toHaveCount(0)
    }
  })

  test('group by project puts the Codex sessions in one folder per project', async ({ page }) => {
    await openList(page)
    await pickCodex(page)
    const group = page.getByRole('checkbox', { name: 'Group sessions by project' })
    if (!(await group.isChecked())) await group.click()
    await expect(group).toBeChecked()
    for (const name of ['proj-alpha', 'proj-beta', 'proj-delta']) {
      // The folder label may add a parent folder to tell projects apart.
      const header = sidebar(page).locator(`[data-testid="list-group-header"][data-group-name$="${name}"]`)
      await expect(header, `folder "${name}"`).toHaveCount(1)
      await expect(header).toBeVisible()
    }
  })

  test('search finds a Codex message, and a click jumps to it', async ({ page }) => {
    const detail = await apiDetail(page, LEGACY)
    const target = messageWith(detail, (b) => b.type === 'text' && /semicolon/.test(b.text ?? ''))

    await openList(page)
    await expect(rowOf(page, TITLES.legacy)).toBeVisible()
    await page.keyboard.press('Meta+k')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible()
    await input.fill('semicolon')

    const card = page.locator('[data-result-card]').filter({ hasText: TITLES.legacy })
    await expect(card.first()).toBeVisible({ timeout: 10_000 })
    await card.first().click()

    await expect(page).toHaveURL(new RegExp(`/conversations/${LEGACY}`))
    const bubble = page.locator(`[data-message-uuid="${target.uuid}"]`)
    await expect(bubble).toHaveClass(/ring-yellow-400/, { timeout: 5_000 })
    await waitForScrollQuiet(page)
    await expect(bubble).toBeInViewport()
  })

  test('the viewer shows the Codex transcript with the Codex assistant label', async ({ page }) => {
    const detail = await apiDetail(page, PAGINATED)
    const reply = messageWith(detail, (b) => b.type === 'text' && b.text === 'The parser lives in src/parser.py.')
    await openConversation(page, PAGINATED, TITLES.paginated)

    await expect(page.getByText('Refactor the parser module and list the files first.').first()).toBeVisible()
    const bubble = page.locator(`[data-message-uuid="${reply.uuid}"]`)
    await expect(bubble).toContainText('The parser lives in src/parser.py.')
    await expect(bubble).toContainText('Codex')
  })

  test('the header shows the thread ID, the Codex name, and the Codex home', async ({ page }) => {
    const header = await openConversation(page, PAGINATED, TITLES.paginated)
    await expect(header.getByText('Thread ID:')).toBeVisible()
    await expect(header.getByTitle('Click to copy thread ID')).toHaveText(PAGINATED)
    await expect(header.getByText('Codex name:')).toBeVisible()
    await expect(header.getByTitle('Click to copy Codex name')).toContainText(TITLES.paginated)
    await expect(header.getByText('Codex home:')).toBeVisible()
    await expect(header.getByTitle('Click to copy Codex home')).toContainText(FIXTURE_HOME_DISPLAY)
    // A Codex session shows the thread ID, not the generic UUID row.
    await expect(header.getByTitle('Click to copy UUID')).toHaveCount(0)
  })

  test('a Codex session without a Codex name shows "none"', async ({ page }) => {
    const header = await openConversation(page, LEGACY, TITLES.legacy)
    const nameRow = header.locator('div', { has: page.getByText('Codex name:', { exact: true }) }).last()
    await expect(nameRow).toContainText('none')
    await expect(header.getByTitle('Click to copy Codex name')).toHaveCount(0)
  })

  test('each Codex header copy button copies its exact value', async ({ page }) => {
    await openConversation(page, PAGINATED, TITLES.paginated)
    expect(await copyAndRead(page, 'Click to copy thread ID')).toBe(PAGINATED)
    expect(await copyAndRead(page, 'Click to copy Codex name')).toBe(TITLES.paginated)
    expect(await copyAndRead(page, 'Click to copy Codex home')).toBe(FIXTURE_HOME)
  })

  test('the notices show above the messages', async ({ page }) => {
    for (const [uuid, title] of [
      [PAGINATED, TITLES.paginated],
      [IMPORTED, TITLES.imported],
    ] as const) {
      const detail = await apiDetail(page, uuid)
      expect(detail.notices.length, `${title} has notices`).toBeGreaterThan(0)
      await openConversation(page, uuid, title)
      const list = page.getByTestId('conversation-notices')
      await expect(list.getByRole('listitem')).toHaveText(detail.notices)
    }
    await expect(page.getByTestId('conversation-notices')).toContainText('Imported from Claude Code')
  })

  test('the Show Tools checkbox shows and hides the Codex command', async ({ page }) => {
    const detail = await apiDetail(page, PAGINATED)
    const tool = messageWith(detail, (b) => b.type === 'tool_use')
    await openConversation(page, PAGINATED, TITLES.paginated)

    const tools = page.getByTestId('header-show-tools-checkbox')
    const bubble = page.locator(`[data-message-uuid="${tool.uuid}"]`)
    await expect(tools).not.toBeChecked()
    await expect(page.getByText('The parser lives in src/parser.py.').first()).toBeVisible()
    await expect(bubble).toHaveCount(0)

    await tools.click()
    await expect(tools).toBeChecked()
    await expect(bubble).toBeVisible()

    await tools.click()
    await expect(tools).not.toBeChecked()
    await expect(bubble).toHaveCount(0)
  })

  test('Copy as Markdown copies the Codex transcript', async ({ page }) => {
    await openConversation(page, PAGINATED, TITLES.paginated)
    const button = page.getByRole('button', { name: /Copy as Markdown/i })
    await button.click()
    await expect(button.locator('.text-green-500')).toBeVisible()
    const text = await page.evaluate(() => navigator.clipboard.readText())
    expect(text).toContain('Refactor the parser module and list the files first.')
    expect(text).toContain('The parser lives in src/parser.py.')
  })

  test('the Markdown export downloads the transcript with the Codex header', async ({ page }) => {
    await openConversation(page, PAGINATED, TITLES.paginated)
    await page.getByRole('button', { name: 'Markdown', exact: true }).click()
    const dialog = page.getByTestId('markdown-export-dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('radio', { name: 'Inline' }).click()
    await expect(dialog.getByRole('radio', { name: 'Inline' })).toBeChecked()

    const download = page.waitForEvent('download')
    await dialog.getByRole('button', { name: 'Download' }).click()
    const file = await (await download).path()
    const md = await readFile(file, 'utf-8')

    expect(md).toContain(`**Thread ID:** ${PAGINATED}`)
    expect(md).toContain(`**Codex name:** ${TITLES.paginated}`)
    expect(md).toContain(`**Codex home:** ${FIXTURE_HOME_DISPLAY}`)
    expect(md).toContain('The parser lives in src/parser.py.')
  })

  test('the PDF button sends the Codex id and the Show Tools state, and starts a download', async ({ page }) => {
    // The CI e2e job has no WeasyPrint, so the route is mocked (exports.spec.ts,
    // B25 and the F4 note). The test checks only what the page sends; the bytes
    // come from the mock. N8's test_the_pdf_export_of_a_codex_session_works
    // covers the real PDF.
    const urls: string[] = []
    await page.route(`**/api/conversations/${LEGACY}/export/pdf**`, (route: Route) => {
      urls.push(route.request().url())
      route.fulfill({ status: 200, contentType: 'application/pdf', body: Buffer.from('%PDF-1.4\n%%EOF\n') })
    })
    await openConversation(page, LEGACY, TITLES.legacy)
    const pdf = page.getByRole('button', { name: /^PDF$/ })
    const tools = page.getByTestId('header-show-tools-checkbox')

    for (const showTools of [false, true]) {
      if (showTools) {
        await tools.click()
        await expect(tools).toBeChecked()
      } else {
        await expect(tools).not.toBeChecked()
      }
      const download = page.waitForEvent('download')
      await pdf.click()
      expect((await download).suggestedFilename()).toMatch(/\.pdf$/)
      await expect.poll(() => urls.length).toBe(showTools ? 2 : 1)
      const url = new URL(urls[urls.length - 1])
      expect(url.pathname).toBe(`/api/conversations/${LEGACY}/export/pdf`)
      expect(url.searchParams.get('include_tools')).toBe(String(showTools))
    }
  })
})
