import { test, expect, makeSummary, withNetRetry, E2E_HOME_DIR } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * A2 UI round 3 (issue #92): _orchestration/A2-UI.md W1 and W2.
 *
 *   - W1: the Machines dialog never scrolls sideways, at 1440 and 800 px
 *     wide, with long preview and shared-folder paths. A preview row shows
 *     its path on one line, with the full path as its title.
 *   - W2: at the 320 px sidebar, a list row with a long machine label keeps
 *     the model badge, the time, and the message count each on one line;
 *     only the machine chip shrinks, cut with an ellipsis.
 *
 * The /api/machines routes are synthetic; the console gate of TESTING.md
 * §5.15 runs for every test.
 */

// The list shows times in the browser's time zone; UTC keeps "12:50 PM" the same everywhere.
test.use({ timezoneId: 'UTC' })

const ME = '2f6a1b3c-0000-4000-8000-00000000000a'
const OTHER = '9d8e7f6a-0000-4000-8000-00000000000b'
const LONG_LABEL = 'Work laptop with a very long synthetic machine label'
const LONG_FOLDER = '-Users-tester-Source-a-very-long-folder-name-for-a-synthetic-project-that-keeps-going-and-going'
const PATHS = [1, 2, 3].map(
  (n) => `claude-code/projects/${LONG_FOLDER}/5a1e0001-0000-4000-8000-00000000a00${n}.jsonl`,
)
const SHARED = `${E2E_HOME_DIR}/Library/CloudStorage/a-very-long-synthetic-shared-folder-name/claude-explorer/shared-archive`

async function serveMachines(page: Page) {
  await page.route('**/api/machines', (route) =>
    route.fulfill({
      json: {
        this_machine_id: ME,
        shared_archive_dir: SHARED,
        shared_archive_block: null,
        machines: [
          { machine_id: ME, label: 'Home Mac', is_this_machine: true, session_count: 3 },
          { machine_id: OTHER, label: LONG_LABEL, is_this_machine: false, session_count: 3 },
        ],
      },
    }),
  )
  await page.route('**/api/machines/export-rules', (route) =>
    route.fulfill({
      json: {
        version: 1,
        mode: 'export',
        allow: [{ source: 'claude-code', project: `~/Source/${LONG_FOLDER}/**` }],
        deny: [`~/Source/work/${LONG_FOLDER}/**`],
        since: null,
      },
    }),
  )
  await page.route('**/api/machines/export-preview*', (route) =>
    route.fulfill({
      json: {
        files: PATHS.map((path) => ({ path, bytes: 1234, source: 'claude-code', project: `${E2E_HOME_DIR}/Source/x` })),
        file_count: PATHS.length,
        total_bytes: 1234 * PATHS.length,
      },
    }),
  )
}

const ROWS = [
  makeSummary({
    uuid: 'a2ui0000-0000-4000-8000-0000000000a1',
    name: 'Row from the machine with a long label',
    source: 'CLAUDE_CODE',
    model: 'claude-sonnet-4-6',
    message_count: 2,
    project_path: `${E2E_HOME_DIR}/Source/billing`,
    project_name: 'billing',
    updated_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
    ...({ machine_id: OTHER, machine_label: LONG_LABEL, machine_labels: [LONG_LABEL] } as object),
  }),
  makeSummary({
    uuid: 'a2ui0000-0000-4000-8000-0000000000a2',
    name: 'Row from this machine',
    source: 'CLAUDE_CODE',
    model: 'claude-sonnet-4-6',
    message_count: 2,
    project_path: `${E2E_HOME_DIR}/Source/garden`,
    project_name: 'garden',
    updated_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    ...({ machine_id: ME, machine_label: 'Home Mac', machine_labels: ['Home Mac'] } as object),
  }),
]

for (const width of [1440, 800]) {
  test(`W1: the Machines dialog never scrolls sideways at ${width} px`, async ({ page, mockBackend }) => {
    await page.setViewportSize({ width, height: 900 })
    await mockBackend({ conversations: ROWS })
    await serveMachines(page)
    await withNetRetry(page, () => page.goto('/'))
    await page.getByTestId('machines-button').click()
    const dialog = page.getByRole('dialog')
    const preview = dialog.getByTestId('export-preview')
    await expect(preview.locator(`[title="${PATHS[0]}"]`)).toBeVisible()
    await expect(dialog.getByTestId('shared-folder-input')).toHaveValue(SHARED)

    const sizes = await dialog.evaluate((el) => {
      const out: Record<string, [number, number]> = { dialog: [el.scrollWidth, el.clientWidth] }
      const box = el.querySelector('[data-testid="export-preview"]') as HTMLElement | null
      if (box) out.preview = [box.scrollWidth, box.clientWidth]
      return out
    })
    expect(sizes.dialog[0], `dialog scrollWidth ${sizes.dialog[0]} > clientWidth ${sizes.dialog[1]}`).toBeLessThanOrEqual(sizes.dialog[1])
    expect(sizes.preview[0], 'the preview box scrolls down, not sideways').toBeLessThanOrEqual(sizes.preview[1])

    const dialogBox = (await dialog.boundingBox())!
    for (const id of ['export-rules-allow', 'export-rules-deny', 'shared-folder-input']) {
      const b = (await dialog.getByTestId(id).boundingBox())!
      expect(b.x + b.width, `${id} fits the dialog`).toBeLessThanOrEqual(dialogBox.x + dialogBox.width)
    }
  })

  test(`W1: each preview row is one line with the full path as its title at ${width} px`, async ({ page, mockBackend }) => {
    await page.setViewportSize({ width, height: 900 })
    await mockBackend({ conversations: ROWS })
    await serveMachines(page)
    await withNetRetry(page, () => page.goto('/'))
    await page.getByTestId('machines-button').click()
    const dialog = page.getByRole('dialog')
    const dialogBox = (await dialog.boundingBox())!
    for (const path of PATHS) {
      const row = dialog.getByTestId('export-preview').locator(`[title="${path}"]`)
      await expect(row).toHaveCount(1)
      const lines = await row.evaluate((el) => {
        const r = document.createRange()
        r.selectNodeContents(el)
        return new Set(Array.from(r.getClientRects()).map((c) => Math.round(c.top))).size
      })
      expect(lines, `${path} is on one line`).toBe(1)
      const b = (await row.boundingBox())!
      expect(b.x + b.width, 'the row fits the dialog').toBeLessThanOrEqual(dialogBox.x + dialogBox.width)
    }
  })
}

for (const width of [1440, 800]) {
  test(`W1: a cut preview row still shows the whole file name at ${width} px`, async ({ page, mockBackend }) => {
    await page.setViewportSize({ width, height: 900 })
    await mockBackend({ conversations: ROWS })
    await serveMachines(page)
    await withNetRetry(page, () => page.goto('/'))
    await page.getByTestId('machines-button').click()
    const preview = page.getByRole('dialog').getByTestId('export-preview')
    for (const path of PATHS) {
      const row = preview.locator(`[title="${path}"]`)
      await expect(row).toHaveCount(1)
      // The row text without its size, for example " (1.2 KB)".
      const text = (await row.innerText()).replace(/\s*\([^)]*\)\s*$/, '')
      const fileName = path.split('/').pop()!
      expect(text.includes('…'), `${path} is cut`).toBe(true)
      expect(text.endsWith(fileName), `"${text}" ends with the whole file name "${fileName}"`).toBe(true)
    }
  })
}

// The wrap depends on the widths: a fixed clock and time zone give the same row text every run.
const NOW = new Date('2026-11-27T13:00:00Z')

function w2Rows(label: string) {
  return [
    makeSummary({
      uuid: 'a2ui0000-0000-4000-8000-0000000000b1',
      name: 'Row from the other machine',
      source: 'CLAUDE_CODE',
      model: 'claude-sonnet-4-6',
      message_count: 2,
      project_path: `${E2E_HOME_DIR}/Source/billing`,
      project_name: 'billing',
      created_at: '2026-11-27T12:50:00Z',
      updated_at: '2026-11-27T12:50:00Z',
      ...({ machine_id: OTHER, machine_label: label, machine_labels: [label] } as object),
    }),
    ROWS[1],
  ]
}

for (const label of ['Work laptop', LONG_LABEL]) {
  test(`W2: a list row with the machine chip "${label}" keeps its meta line on one line`, async ({ page, mockBackend }) => {
    await page.clock.setFixedTime(NOW)
    await page.setViewportSize({ width: 1440, height: 900 })
    await mockBackend({ conversations: w2Rows(label) })
    await serveMachines(page)
    await withNetRetry(page, () => page.goto('/'))
    const row = page.getByText('Row from the other machine').locator('xpath=ancestor::*[@role="button"][1]')
    const chip = row.getByTestId('machine-chip')
    await expect(chip).toBeVisible()
    await expect(row.getByText('12:50 PM')).toBeVisible()
    const sidebar = (await page.locator('aside').first().boundingBox())!
    expect(Math.round(sidebar.width)).toBe(320)

    const lineCount = (loc: ReturnType<Page['locator']>) =>
      loc.evaluate((el) => {
        const r = document.createRange()
        r.selectNodeContents(el)
        return new Set(Array.from(r.getClientRects()).map((c) => Math.round(c.top))).size
      })
    expect(await lineCount(row.getByTestId('list-row-model')), 'the model badge is one line').toBe(1)
    expect(await lineCount(row.getByText('12:50 PM')), 'the time is one line').toBe(1)
    expect(await lineCount(row.getByText(/^\d+ msgs?$/)), 'the message count is one line').toBe(1)

    // Only the chip shrinks: its label is cut with an ellipsis when it does not fit,
    // and its title always holds the full label.
    await expect(chip).toHaveAttribute('title', `Machine: ${label}`)
    if (label === LONG_LABEL) {
      const cut = await chip.evaluate((el) =>
        [el, ...Array.from(el.querySelectorAll('*'))].some((e) => {
          const s = getComputedStyle(e)
          return s.textOverflow === 'ellipsis' && (e as HTMLElement).scrollWidth > (e as HTMLElement).clientWidth
        }),
      )
      expect(cut, 'the chip label is cut with an ellipsis').toBe(true)
    }
  })
}
