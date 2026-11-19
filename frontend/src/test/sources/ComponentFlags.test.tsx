/**
 * N2 (PLANS/2026.11.18-codex-source.md §4.1, "Rules that N2 changes"):
 * the viewer and the list read the registry flags.
 *
 *   - ConversationHeader: the project-path gate (the list filter button
 *     and the project path copy button) reads `has_projects`.
 *   - ConversationToolbar: Re-download reads `refetchable`.
 *   - ConversationList: the project label of a row reads `has_projects`;
 *     the group badge keeps today's look with the real registry.
 */
import { createElement, type ComponentType } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { FAKE, serveApp, setRealRegistry, setRegistry, withFake, withFlags, xfail } from './fakeRegistry'

function detail(overrides: Record<string, unknown>) {
  return {
    uuid: 'conv-uuid-1',
    name: 'A session',
    source: 'CLAUDE_AI',
    model: 'm',
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-01T10:00:00Z',
    message_count: 2,
    has_branches: false,
    messages: [],
    compact_markers: [],
    prelude_hidden_count: 0,
    file_path: null,
    project_path: null,
    project_name: null,
    git_branch: null,
    sandbox_path: null,
    error: null,
    ...overrides,
  }
}

function listRow(overrides: Record<string, unknown>) {
  return {
    uuid: 'r-1',
    name: 'A row',
    model: 'm',
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-01T10:00:00Z',
    is_starred: false,
    message_count: 2,
    has_branches: false,
    source: 'CLAUDE_AI',
    ...overrides,
  }
}

const PROJECT_PATH_BUTTON = 'Click to copy project path'
const LIST_FILTER_TITLE = 'Filter the conversation list to this project'

async function renderHeader(conversation: Record<string, unknown>) {
  const { AppProviders } = await import('./harness')
  const { ConversationHeader } = await import('@/components/conversation/ConversationHeader')
  serveApp({})
  const Header = ConversationHeader as unknown as ComponentType<Record<string, unknown>>
  render(<AppProviders>{createElement(Header, { conversation, onOpenTree: vi.fn() })}</AppProviders>)
  // The UUID copy button is always there: the header has rendered.
  await screen.findByTitle('Click to copy UUID')
}

describe('ConversationHeader project-path gate reads has_projects', () => {
  xfail('a new source with projects shows the list filter button and the project path', async () => {
    setRegistry(withFake())
    await renderHeader(detail({ source: FAKE.id, project_path: '/home/u/work/x/y', project_name: 'y' }))
    expect(screen.getByTitle(PROJECT_PATH_BUTTON)).toBeInTheDocument()
    expect(screen.getByTitle(LIST_FILTER_TITLE)).toBeInTheDocument()
  })

  xfail('Claude Code without has_projects shows neither', async () => {
    setRegistry(withFlags('CLAUDE_CODE', { has_projects: false }))
    await renderHeader(detail({ source: 'CLAUDE_CODE', project_path: '/home/u/src/a/b', project_name: 'b' }))
    expect(screen.queryByTitle(PROJECT_PATH_BUTTON)).toBeNull()
    expect(screen.queryByTitle(LIST_FILTER_TITLE)).toBeNull()
  })
})

async function renderToolbar(conversationSource: string) {
  const { ConversationToolbar } = await import('@/components/conversation/ConversationToolbar')
  const Toolbar = ConversationToolbar as unknown as ComponentType<Record<string, unknown>>
  render(
    createElement(Toolbar, {
      showToolCalls: false,
      setShowToolCalls: vi.fn(),
      markPendingRecenter: vi.fn(),
      expandAllTools: false,
      handleToggleExpandAll: vi.fn(),
      isExpandPending: false,
      conversationSource,
      handleForceRefetch: vi.fn(),
      isRefetching: false,
      hasCompactMarkers: false,
      hideCompactMarkers: false,
      setHideCompactMarkers: vi.fn(),
      copiedAll: false,
      handleCopyAll: vi.fn(),
      setMarkdownDialogOpen: vi.fn(),
      handleExportPdf: vi.fn(),
      isExportingPdf: false,
    }),
  )
  await screen.findByRole('button', { name: /^PDF$/ })
}

const REDOWNLOAD = { name: 'Re-download this conversation' }

describe('ConversationToolbar Re-download reads refetchable', () => {
  xfail('Claude Code with refetchable shows Re-download', async () => {
    setRegistry(withFlags('CLAUDE_CODE', { refetchable: true }))
    await renderToolbar('CLAUDE_CODE')
    expect(screen.getByRole('button', REDOWNLOAD)).toBeInTheDocument()
  })

  xfail('Claude Desktop without refetchable hides Re-download', async () => {
    setRegistry(withFlags('CLAUDE_AI', { refetchable: false }))
    await renderToolbar('CLAUDE_AI')
    expect(screen.queryByRole('button', REDOWNLOAD)).toBeNull()
  })

  xfail('a new refetchable source shows Re-download', async () => {
    setRegistry(withFake())
    await renderToolbar(FAKE.id)
    expect(screen.getByRole('button', REDOWNLOAD)).toBeInTheDocument()
  })
})

async function renderList(rows: Record<string, unknown>[], props: Record<string, unknown> = {}) {
  const { AppProviders } = await import('./harness')
  const { ConversationList } = await import('@/components/conversation/ConversationList')
  serveApp({})
  server.use(http.get('/api/conversations', () => HttpResponse.json(rows)))
  const List = ConversationList as unknown as ComponentType<Record<string, unknown>>
  render(<AppProviders>{createElement(List, props)}</AppProviders>)
}

describe('ConversationList project label reads has_projects', () => {
  xfail('a row of a new source with projects shows its project name', async () => {
    setRegistry(withFake())
    await renderList([listRow({ source: FAKE.id, name: 'Fake row', project_name: 'fake-proj' })])
    await screen.findByText('Fake row')
    expect(screen.getByText('fake-proj')).toBeInTheDocument()
  })

  xfail('a Claude Code row without has_projects shows no project name', async () => {
    setRegistry(withFlags('CLAUDE_CODE', { has_projects: false }))
    await renderList([listRow({ source: 'CLAUDE_CODE', name: 'Code row', project_name: 'code-proj' })])
    await screen.findByText('Code row')
    expect(screen.queryByText('code-proj')).toBeNull()
  })
})

describe('ConversationList group badge (real registry, no visual change)', () => {
  it('a Desktop-only folder gets the Desktop badge; Code and Cowork-only folders get the folder icon', async () => {
    setRealRegistry()
    await renderList(
      [
        listRow({ uuid: 'd', name: 'Desktop row', source: 'CLAUDE_AI', organization_name: 'Org A' }),
        listRow({
          uuid: 'c',
          name: 'Code row',
          source: 'CLAUDE_CODE',
          project_path: '/home/u/src/a/b',
          project_name: 'b',
        }),
        listRow({
          uuid: 'w',
          name: 'Cowork row',
          source: 'CLAUDE_COWORK',
          project_path: '/sessions/vm1',
          organization_name: 'Org B',
        }),
      ],
      { groupByProject: true, sourceFilter: 'all' },
    )
    await waitFor(() => expect(screen.getAllByTestId('list-group-header')).toHaveLength(3))
    const icon = (name: string) =>
      screen
        .getAllByTestId('list-group-header')
        .find((h) => h.getAttribute('data-group-name') === name)
        ?.querySelectorAll('svg')[1]
        ?.getAttribute('class') ?? ''
    expect(icon('Org A')).toContain('lucide-message-square')
    expect(icon('Org A')).toContain('text-blue-500')
    expect(icon('a/b')).toContain('lucide-folder-code')
    expect(icon('Org B')).toContain('lucide-folder-code')
  })
})
