import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'
import type { Locator, Page } from '@playwright/test'
import type { ConversationDetail, ConversationSummary } from '../src/lib/types'

/**
 * N11: the Codex history links in the Conversation Pane header
 * (PLANS/2026.11.18-codex-source.md §6.1 N11; the orchestrator's N11
 * contract, 2026-11-20).
 *
 *   - "Parent:" row (data-testid="codex-parent-row"): a link to the parent,
 *     named by its title, or the parent id as text when the parent is not in
 *     the archive.
 *   - "Forks and subagents:" row (data-testid="codex-children-row"): one link
 *     per child, in `child_uuids` order.
 *   - A click on a link opens that conversation in the Conversation Pane.
 *   - Other sources show neither row.
 *
 * mockBackend serves the list for any query, so the one list call
 * (source=CODEX, show_archived=true) gets every row below. The console gate
 * of TESTING.md §5.15 runs for every test: a detail fetch of a missing parent
 * (a 404) fails it.
 *
 * N11 is not in the code yet: every test is a strict expected failure until
 * the tester's final commit. RUNXFAIL=1 runs them as plain tests.
 */

const PARENT = '019ac000-0000-7000-8000-0000000012a0'
const SUBAGENT = '019ac000-0000-7000-8000-0000000012c1'
const FORK = '019ac000-0000-7000-8000-0000000012c2'
const ORPHAN = '019ac000-0000-7000-8000-0000000012d0'
const MISSING = '019ac000-0000-7000-8000-0000000012ff'
const CC = '00000000-0000-4000-8000-0000000cd001'

const TITLES: Record<string, string> = {
  [PARENT]: 'History: the parent thread',
  [SUBAGENT]: 'History: Scout subagent',
  [FORK]: 'History: a fork that tries a new lexer',
  [ORPHAN]: 'History: a fork of a lost thread',
  [CC]: 'History: a Claude Code session',
}

function summary(uuid: string, overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return makeSummary({
    uuid,
    name: TITLES[uuid],
    source: 'CODEX',
    model: 'gpt-5.6-codex',
    message_count: 1,
    human_message_count: 1,
    project_path: '/home/fixture/proj-history',
    project_name: 'proj-history',
    source_name: TITLES[uuid],
    source_home: '~/.codex',
    source_home_abs: '/home/fixture/.codex',
    notices: [],
    ...overrides,
  })
}

function detailOf(s: ConversationSummary, parent: string | null, children: string[]): ConversationDetail {
  const d = makeDetail(s, [
    makeMessage({
      uuid: `${s.uuid}-m1`,
      sender: 'human',
      text: `Question in ${s.name}`,
      content: [{ type: 'text', text: `Question in ${s.name}` }],
    }),
  ])
  // child_uuids is new in N11; Object.assign keeps this file type-safe before types.ts has it.
  return Object.assign(d, { parent_uuid: parent, child_uuids: children })
}

const rows = {
  parent: summary(PARENT),
  subagent: summary(SUBAGENT),
  fork: summary(FORK, { is_archived: true }),
  orphan: summary(ORPHAN),
  cc: summary(CC, {
    source: 'CLAUDE_CODE',
    model: 'claude-sonnet-4-6',
    source_name: null,
    source_home: null,
    source_home_abs: null,
  }),
}

const details: Record<string, ConversationDetail> = {
  [PARENT]: detailOf(rows.parent, null, [SUBAGENT, FORK]),
  [SUBAGENT]: detailOf(rows.subagent, PARENT, []),
  [FORK]: detailOf(rows.fork, PARENT, []),
  [ORPHAN]: detailOf(rows.orphan, MISSING, []),
  // A Claude Code session never shows the rows, even with these values.
  [CC]: detailOf(rows.cc, PARENT, [SUBAGENT]),
}

const conversations = [rows.parent, rows.subagent, rows.fork, rows.orphan, rows.cc]

async function openConversation(page: Page, uuid: string): Promise<Locator> {
  await withNetRetry(page, () => page.goto(`/conversations/${uuid}`))
  const header = page.locator('header').first()
  await expect(header.getByRole('heading', { level: 1, name: TITLES[uuid] })).toBeVisible()
  return header
}

test.describe('Codex history links in the header (N11)', () => {
  test.fail(process.env.RUNXFAIL !== '1', 'N11 not implemented yet')

  test('a subagent shows a Parent link that opens the parent', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, details })
    const header = await openConversation(page, SUBAGENT)

    const parentRow = header.getByTestId('codex-parent-row')
    await expect(parentRow).toContainText('Parent:')
    const link = parentRow.getByRole('link', { name: TITLES[PARENT] })
    await expect(link).toHaveAttribute('href', `/conversations/${PARENT}`)
    await expect(header.getByTestId('codex-children-row')).toHaveCount(0)

    await link.click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${PARENT}$`))
    await expect(header.getByRole('heading', { level: 1, name: TITLES[PARENT] })).toBeVisible()
  })

  test('a parent shows one link per fork and subagent, in order, and a click opens the child', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations, details })
    const header = await openConversation(page, PARENT)

    const childRow = header.getByTestId('codex-children-row')
    await expect(childRow).toContainText('Forks and subagents:')
    await expect(childRow.getByRole('link')).toHaveText([TITLES[SUBAGENT], TITLES[FORK]])
    await expect(header.getByTestId('codex-parent-row')).toHaveCount(0)

    // FORK is archived: the link still shows and opens it.
    await childRow.getByRole('link', { name: TITLES[FORK] }).click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${FORK}$`))
    await expect(header.getByRole('heading', { level: 1, name: TITLES[FORK] })).toBeVisible()
    await expect(header.getByTestId('codex-parent-row').getByRole('link', { name: TITLES[PARENT] })).toBeVisible()
  })

  test('a parent that is not in the archive shows its id as text, with no link', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, details })
    const header = await openConversation(page, ORPHAN)

    const parentRow = header.getByTestId('codex-parent-row')
    await expect(parentRow).toContainText('Parent:')
    await expect(parentRow).toContainText(MISSING)
    await expect(parentRow.getByRole('link')).toHaveCount(0)
  })

  test('a Claude Code session shows neither row', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, details })
    // The rows render for a Codex session first, so their absence below is the rule.
    const codexHeader = await openConversation(page, SUBAGENT)
    await expect(codexHeader.getByTestId('codex-parent-row')).toBeVisible()

    const header = await openConversation(page, CC)
    await expect(header.getByTestId('codex-parent-row')).toHaveCount(0)
    await expect(header.getByTestId('codex-children-row')).toHaveCount(0)
  })
})
