/**
 * Issue #96: the session page lists its agent transcripts in the header
 * Details (contract items 3 and 4a).
 *
 * * A row labeled "Subagents:" (`data-testid="details-subagents"`), fed by
 *   the session detail field `subagents_found`.
 * * One link per agent to `/conversations/<session>/agents/<agent>`; the
 *   link text is the description, else the agent id.
 * * No row when the list is empty or missing.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { ConversationHeader } from '../../components/conversation/ConversationHeader'
import { SearchPinProvider } from '../../contexts/SearchPinContext'
import { SettingsProvider } from '../../contexts/SettingsContext'
import type { ConversationDetail } from '../../lib/types'

vi.mock('@/lib/errorToast', () => ({ errorToast: vi.fn() }))

const SESSION = '5a6e0000-0000-4000-8000-000000000096'

beforeEach(() => {
  server.use(
    http.get('/api/preferences', () => HttpResponse.json({ version: 1, data: {} })),
    http.patch('/api/preferences', () => HttpResponse.json({ version: 1, data: {} })),
  )
})

function makeSession(subagentsFound: unknown): ConversationDetail {
  return {
    uuid: SESSION,
    name: 'Start the agents',
    source: 'CLAUDE_CODE',
    model: 'claude-sonnet-5-5',
    created_at: '2026-11-24T10:00:00Z',
    updated_at: '2026-11-24T10:00:00Z',
    message_count: 2,
    has_branches: false,
    messages: [],
    compact_markers: [],
    prelude_hidden_count: 0,
    file_path: null,
    project_path: '/home/fixture/proj-sa',
    git_branch: null,
    sandbox_path: null,
    error: null,
    ...(subagentsFound === undefined ? {} : { subagents_found: subagentsFound }),
  } as unknown as ConversationDetail
}

function renderHeader(conversation: ConversationDetail) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        <MemoryRouter>
          <SearchPinProvider>
            <ConversationHeader conversation={conversation} onOpenTree={vi.fn()} />
          </SearchPinProvider>
        </MemoryRouter>
      </SettingsProvider>
    </QueryClientProvider>,
  )
}

const FOUND = [
  { agent_id: '5a1e9ac', agent_description: null },
  { agent_id: 'a5a0000000000096c', agent_description: 'Synthetic search agent' },
]

describe('ConversationHeader — Subagents row (#96)', () => {
  it('shows a "Subagents:" row with one link per agent', () => {
    renderHeader(makeSession(FOUND))
    const row = screen.getByTestId('details-subagents')
    expect(row.textContent).toContain('Subagents:')
    const links = within(row).getAllByRole('link')
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      `/conversations/${SESSION}/agents/5a1e9ac`,
      `/conversations/${SESSION}/agents/a5a0000000000096c`,
    ])
  })

  it('names each link by its description, else by the agent id', () => {
    renderHeader(makeSession(FOUND))
    const row = screen.getByTestId('details-subagents')
    expect(within(row).getByRole('link', { name: 'Synthetic search agent' })).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: '5a1e9ac' })).toBeInTheDocument()
  })

  it('shows the row only when the list is not empty', () => {
    const { unmount } = renderHeader(makeSession(FOUND))
    expect(screen.getByTestId('details-subagents')).toBeInTheDocument()
    unmount()
    renderHeader(makeSession([]))
    expect(screen.queryByTestId('details-subagents')).toBeNull()
  })

  it('a session without the field shows no row (an old payload)', () => {
    renderHeader(makeSession(undefined))
    expect(screen.queryByTestId('details-subagents')).toBeNull()
  })
})
