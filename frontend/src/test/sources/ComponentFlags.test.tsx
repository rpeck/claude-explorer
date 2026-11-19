/**
 * N2 (PLANS/2026.11.18-codex-source.md §4.1, "Rules that N2 changes"):
 * the viewer and the list read the registry flags.
 *
 *   - ConversationHeader: the project-path gate (the list filter button
 *     and the project path copy button) reads `has_projects`.
 *   - ConversationToolbar: Re-download reads `refetchable`.
 *   - ConversationList: the project label of a row reads `has_projects`;
 *     the group badge keeps today's look with the real registry.
 *   - ConversationList group badge (orchestrator decision, option (a)):
 *     each SOURCE_PRESETS entry has `groupBadge: boolean`, true for
 *     CLAUDE_AI only. A folder shows the source badge when all its rows
 *     come from one source whose preset has `groupBadge`; otherwise the
 *     folder icon. The tests change the real SOURCE_PRESETS object after
 *     the import, so they do not depend on how the list reads it.
 */
import { createElement, type ComponentType } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { FAKE, serveApp, setRealRegistry, setRegistry, withFake, withFlags } from './fakeRegistry'

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
  it('a new source with projects shows the list filter button and the project path', async () => {
    setRegistry(withFake())
    await renderHeader(detail({ source: FAKE.id, project_path: '/home/u/work/x/y', project_name: 'y' }))
    expect(screen.getByTitle(PROJECT_PATH_BUTTON)).toBeInTheDocument()
    expect(screen.getByTitle(LIST_FILTER_TITLE)).toBeInTheDocument()
  })

  it('Claude Code without has_projects shows neither', async () => {
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
  it('Claude Code with refetchable shows Re-download', async () => {
    setRegistry(withFlags('CLAUDE_CODE', { refetchable: true }))
    await renderToolbar('CLAUDE_CODE')
    expect(screen.getByRole('button', REDOWNLOAD)).toBeInTheDocument()
  })

  it('Claude Desktop without refetchable hides Re-download', async () => {
    setRegistry(withFlags('CLAUDE_AI', { refetchable: false }))
    await renderToolbar('CLAUDE_AI')
    expect(screen.queryByRole('button', REDOWNLOAD)).toBeNull()
  })

  it('a new refetchable source shows Re-download', async () => {
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
  it('a row of a new source with projects shows its project name', async () => {
    setRegistry(withFake())
    await renderList([listRow({ source: FAKE.id, name: 'Fake row', project_name: 'fake-proj' })])
    await screen.findByText('Fake row')
    expect(screen.getByText('fake-proj')).toBeInTheDocument()
  })

  it('a Claude Code row without has_projects shows no project name', async () => {
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

type Presets = Record<string, Record<string, unknown>>

/** The real SOURCE_PRESETS of this test's module graph (N2 exports it). */
async function presets(): Promise<Presets> {
  const mod = (await import('@/components/conversation/SourceBadge')) as unknown as { SOURCE_PRESETS?: Presets }
  expect(mod.SOURCE_PRESETS).toBeDefined()
  return mod.SOURCE_PRESETS ?? {}
}

/** The class of the badge icon in the folder header named `name`. */
function groupIcon(name: string): string {
  return (
    screen
      .getAllByTestId('list-group-header')
      .find((h) => h.getAttribute('data-group-name') === name)
      ?.querySelectorAll('svg')[1]
      ?.getAttribute('class') ?? ''
  )
}

/** The real registry plus FAKE without projects: its rows go in their workspace folder. */
const withFakeNoProjects = () => [...withFake().slice(0, -1), { ...FAKE, has_projects: false }]

describe('ConversationList group badge reads the groupBadge preset field', () => {
  it('every preset has a boolean groupBadge, true for CLAUDE_AI only', async () => {
    setRealRegistry()
    const all = await presets()
    for (const [id, preset] of Object.entries(all)) {
      expect(typeof preset.groupBadge, id).toBe('boolean')
      expect(preset.groupBadge, id).toBe(id === 'CLAUDE_AI')
    }
  })

  it('a one-source folder of a source whose preset has groupBadge gets that source badge', async () => {
    setRegistry(withFakeNoProjects())
    const all = await presets()
    all[FAKE.id] = { ...all.CLAUDE_CODE, groupBadge: true }
    await renderList(
      [listRow({ uuid: 'f', name: 'Fake row', source: FAKE.id, organization_name: 'Org F' })],
      { groupByProject: true, sourceFilter: 'all' },
    )
    await waitFor(() => expect(screen.getAllByTestId('list-group-header')).toHaveLength(1))
    // The look of the copied CLAUDE_CODE preset: the terminal icon in green.
    expect(groupIcon('Org F')).toContain('lucide-terminal')
    expect(groupIcon('Org F')).toContain('text-green-500')
  })

  it('a Desktop-only folder gets the folder icon when the Desktop preset has no groupBadge', async () => {
    setRealRegistry()
    const all = await presets()
    all.CLAUDE_AI = { ...all.CLAUDE_AI, groupBadge: false }
    await renderList(
      [listRow({ uuid: 'd', name: 'Desktop row', source: 'CLAUDE_AI', organization_name: 'Org A' })],
      { groupByProject: true, sourceFilter: 'all' },
    )
    await waitFor(() => expect(screen.getAllByTestId('list-group-header')).toHaveLength(1))
    expect(groupIcon('Org A')).toContain('lucide-folder-code')
  })

  it('a folder with rows of two groupBadge sources gets the folder icon', async () => {
    setRegistry(withFakeNoProjects())
    const all = await presets()
    all[FAKE.id] = { ...all.CLAUDE_CODE, groupBadge: true }
    await renderList(
      [
        listRow({ uuid: 'd', name: 'Desktop row', source: 'CLAUDE_AI', organization_name: 'Org M' }),
        listRow({ uuid: 'f', name: 'Fake row', source: FAKE.id, organization_name: 'Org M' }),
      ],
      { groupByProject: true, sourceFilter: 'all' },
    )
    await waitFor(() => expect(screen.getAllByTestId('list-group-header')).toHaveLength(1))
    expect(groupIcon('Org M')).toContain('lucide-folder-code')
  })
})
