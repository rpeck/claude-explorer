/**
 * N11: the history links in the header of a Codex session (plan §4.4 N10,
 * §6.1 N11; the orchestrator's N11 contract, 2026-11-20).
 *
 *   - "Parent:" row (data-testid="codex-parent-row") when `parent_uuid` is
 *     set. A parent in the archive is a link to /conversations/<uuid>, named
 *     by its title. A parent that is not in the archive shows its id as
 *     text, with no link.
 *   - "Forks and subagents:" row (data-testid="codex-children-row") when
 *     `child_uuids` is not empty: one link per child, in `child_uuids` order.
 *   - The titles and "in the archive" come from one list call:
 *     GET /api/conversations?source=CODEX&show_archived=true. The header never
 *     fetches the detail of a linked conversation.
 *   - Other sources show neither row.
 *
 * A check that something is absent first waits for a link that needs the
 * list, so it cannot pass while the list call is still open.
 */
import { createElement, type ComponentType } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '@/test/mocks/server'
import { serveApp } from '@/test/sources/fakeRegistry'
import {
  THREAD_ID,
  clearLocalStorage,
  codexDetail,
  detail,
  importModule,
  setCodexRegistry,
} from '@/test/codex/codexHelpers'

const PARENT = '019a1b2c-3d4e-7f60-8a9b-0c1d2e3f4a01'
const CHILD_A = '019a1b2c-3d4e-7f60-8a9b-0c1d2e3f4a0a'
const CHILD_B = '019a1b2c-3d4e-7f60-8a9b-0c1d2e3f4a0b'
const MISSING = '019a1b2c-3d4e-7f60-8a9b-0c1d2e3f4aff'
const UNNAMED = '019a1b2c-3d4e-7f60-8a9b-0c1d2e3f4a0c'

function row(uuid: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    uuid,
    name,
    model: 'gpt-5.6-codex',
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-01T10:00:00Z',
    is_starred: false,
    message_count: 2,
    has_branches: false,
    source: 'CODEX',
    project_path: '/home/fixture/work/parser',
    project_name: 'parser',
    ...extra,
  }
}

const LIST = [
  row(THREAD_ID, 'Fix the parser'),
  row(PARENT, 'Plan the parser work'),
  row(CHILD_A, 'Scout: callers of parse'),
  row(CHILD_B, 'Fork: try a new lexer', { is_archived: true }),
  row(UNNAMED, ''),
]

interface Seen {
  lists: URLSearchParams[]
  details: string[]
}

/** Serve the list for any query; record each list query and each detail request. */
function serveList(rows: Record<string, unknown>[] = LIST): Seen {
  const seen: Seen = { lists: [], details: [] }
  server.use(
    http.get('/api/conversations', ({ request }) => {
      seen.lists.push(new URL(request.url).searchParams)
      return HttpResponse.json(rows)
    }),
    http.get('/api/conversations/:uuid', ({ params }) => {
      seen.details.push(String(params.uuid))
      return HttpResponse.json({ detail: 'not found' }, { status: 404 })
    }),
  )
  return seen
}

async function renderHeader(...conversations: Record<string, unknown>[]) {
  const { AppProviders } = await importModule<{ AppProviders: ComponentType<{ children: React.ReactNode }> }>(
    '@/test/sources/harness',
  )
  const { ConversationHeader } = await importModule<{ ConversationHeader: ComponentType<Record<string, unknown>> }>(
    '@/components/conversation/ConversationHeader',
  )
  serveApp({})
  render(
    createElement(
      AppProviders,
      null,
      ...conversations.map((conversation) =>
        createElement(
          'section',
          { key: String(conversation.uuid), 'data-testid': `header-${String(conversation.source)}` },
          createElement(ConversationHeader, { conversation, onOpenTree: vi.fn() }),
        ),
      ),
    ),
  )
  await screen.findAllByRole('heading', { level: 1 })
}

function linkNames(el: HTMLElement): string[] {
  return within(el)
    .queryAllByRole('link')
    .map((a) => a.textContent?.trim() ?? '')
}

function hrefs(el: HTMLElement): string[] {
  return within(el)
    .queryAllByRole('link')
    .map((a) => a.getAttribute('href') ?? '')
}

beforeEach(() => {
  clearLocalStorage()
  setCodexRegistry()
})

describe('Codex parent row', () => {
  it('shows a "Parent:" row when parent_uuid is set', async () => {
    serveList()
    await renderHeader(codexDetail({ parent_uuid: PARENT, child_uuids: [] }))
    const parentRow = await screen.findByTestId('codex-parent-row')
    expect(parentRow).toHaveTextContent('Parent:')
  })

  it('a parent in the archive is one link named by its title', async () => {
    serveList()
    await renderHeader(codexDetail({ parent_uuid: PARENT, child_uuids: [] }))
    const parentRow = await screen.findByTestId('codex-parent-row')
    await waitFor(() => expect(linkNames(parentRow)).toEqual(['Plan the parser work']))
  })

  it('the parent link opens /conversations/<parent_uuid>', async () => {
    serveList()
    await renderHeader(codexDetail({ parent_uuid: PARENT, child_uuids: [] }))
    const parentRow = await screen.findByTestId('codex-parent-row')
    await waitFor(() => expect(hrefs(parentRow)).toEqual([`/conversations/${PARENT}`]))
  })

  it('a parent with an empty title is named by its uuid', async () => {
    serveList()
    await renderHeader(codexDetail({ parent_uuid: UNNAMED, child_uuids: [] }))
    const parentRow = await screen.findByTestId('codex-parent-row')
    await waitFor(() => expect(linkNames(parentRow)).toEqual([UNNAMED]))
  })

  it('a parent that is not in the archive shows its id as text, with no link', async () => {
    serveList()
    await renderHeader(codexDetail({ parent_uuid: MISSING, child_uuids: [CHILD_A] }))
    // The child link needs the list, so the list is loaded before the parent is checked.
    const childRow = await screen.findByTestId('codex-children-row')
    await waitFor(() => expect(linkNames(childRow)).toEqual(['Scout: callers of parse']))
    const parentRow = screen.getByTestId('codex-parent-row')
    expect(parentRow).toHaveTextContent(MISSING)
    expect(within(parentRow).queryAllByRole('link')).toEqual([])
  })

  it('has no Parent row when parent_uuid is null', async () => {
    serveList()
    await renderHeader(codexDetail({ parent_uuid: null, child_uuids: [CHILD_A] }))
    const childRow = await screen.findByTestId('codex-children-row')
    await waitFor(() => expect(linkNames(childRow)).toEqual(['Scout: callers of parse']))
    expect(screen.queryByTestId('codex-parent-row')).toBeNull()
  })
})

describe('Codex forks and subagents row', () => {
  it('shows a "Forks and subagents:" row when child_uuids is not empty', async () => {
    serveList()
    await renderHeader(codexDetail({ child_uuids: [CHILD_A] }))
    const childRow = await screen.findByTestId('codex-children-row')
    expect(childRow).toHaveTextContent('Forks and subagents:')
  })

  it('has one link per child, in child_uuids order, named by its title', async () => {
    serveList()
    await renderHeader(codexDetail({ child_uuids: [CHILD_B, CHILD_A] }))
    const childRow = await screen.findByTestId('codex-children-row')
    await waitFor(() => expect(linkNames(childRow)).toEqual(['Fork: try a new lexer', 'Scout: callers of parse']))
  })

  it('each child link opens /conversations/<child_uuid>', async () => {
    serveList()
    await renderHeader(codexDetail({ child_uuids: [CHILD_A, CHILD_B] }))
    const childRow = await screen.findByTestId('codex-children-row')
    await waitFor(() =>
      expect(hrefs(childRow)).toEqual([`/conversations/${CHILD_A}`, `/conversations/${CHILD_B}`]),
    )
  })

  it('has no Forks and subagents row when child_uuids is empty', async () => {
    serveList()
    await renderHeader(codexDetail({ parent_uuid: PARENT, child_uuids: [] }))
    const parentRow = await screen.findByTestId('codex-parent-row')
    await waitFor(() => expect(linkNames(parentRow)).toEqual(['Plan the parser work']))
    expect(screen.queryByTestId('codex-children-row')).toBeNull()
  })
})

describe('Where the link titles come from', () => {
  it('the titles come from a list call with source=CODEX and show_archived=true', async () => {
    const seen = serveList()
    await renderHeader(codexDetail({ parent_uuid: PARENT, child_uuids: [CHILD_A] }))
    await screen.findByTestId('codex-children-row')
    await waitFor(() =>
      expect(
        seen.lists.some((q) => q.get('source') === 'CODEX' && q.get('show_archived') === 'true'),
      ).toBe(true),
    )
  })

  it('the header fetches no detail of a linked conversation', async () => {
    const seen = serveList()
    await renderHeader(codexDetail({ parent_uuid: MISSING, child_uuids: [CHILD_A, CHILD_B] }))
    const childRow = await screen.findByTestId('codex-children-row')
    await waitFor(() => expect(linkNames(childRow)).toHaveLength(2))
    expect(seen.details.filter((u) => [MISSING, CHILD_A, CHILD_B].includes(u))).toEqual([])
  })
})

describe('Other sources', () => {
  it('a Claude Code session shows no Parent row and no Forks and subagents row', async () => {
    serveList()
    const links = { parent_uuid: PARENT, child_uuids: [CHILD_A] }
    // The Codex header beside it proves that the rows render in this harness.
    await renderHeader(
      { ...codexDetail(links), uuid: THREAD_ID },
      detail({ ...links, uuid: 'cc-uuid-1', source: 'CLAUDE_CODE', project_path: '/p' }),
    )
    const codexParent = await within(screen.getByTestId('header-CODEX')).findByTestId('codex-parent-row')
    await waitFor(() => expect(linkNames(codexParent)).toEqual(['Plan the parser work']))
    const cc = screen.getByTestId('header-CLAUDE_CODE')
    expect(within(cc).queryByTestId('codex-parent-row')).toBeNull()
    expect(within(cc).queryByTestId('codex-children-row')).toBeNull()
  })
})
