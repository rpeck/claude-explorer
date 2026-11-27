/**
 * A2 (issue #92): the machine chip on a list row and in the header.
 *
 * `data-testid="machine-chip"` shows the row's `machine_label` (fallback
 * "Machine <first 8 of machine_id>"). It shows only when `GET /api/machines`
 * lists two or more machines; with one machine, no row has a chip.
 *
 * `pending` (`it.fails`): A2 is not built yet. VITE_RUNXFAIL=1 runs them as
 * plain tests.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { render as rtlRender } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'

import { render, screen, waitFor, within } from '../utils'
import { ConversationList } from '../../components/conversation/ConversationList'
import { ConversationHeader } from '../../components/conversation/ConversationHeader'
import { SearchPinProvider } from '../../contexts/SearchPinContext'
import { SettingsProvider } from '../../contexts/SettingsContext'
import { server } from '../mocks/server'
import { mockConversations } from '../mocks/data'
import { serveApp } from '../sources/fakeRegistry'
import type { ConversationDetail } from '../../lib/types'

const pending = import.meta.env.VITE_RUNXFAIL ? it : it.fails

const ME = '2f6a1b3c-0000-4000-8000-00000000000a'
const OTHER = '9d8e7f6a-0000-4000-8000-00000000000b'

const base = mockConversations[0]
const rows = [
  { ...base, uuid: 'a2-row-home', name: 'Row from this machine', machine_id: ME, machine_label: 'Home Mac', machine_labels: ['Home Mac'], is_starred: false },
  { ...base, uuid: 'a2-row-work', name: 'Row from the work laptop', machine_id: OTHER, machine_label: 'Work laptop', machine_labels: ['Work laptop'], is_starred: false },
  { ...base, uuid: 'a2-row-nolabel', name: 'Row with no label', machine_id: OTHER, machine_label: null, machine_labels: [], is_starred: false },
]

let machinesAsked = false

function machines(count: 1 | 2) {
  machinesAsked = false
  const all = [
    { machine_id: ME, label: 'Home Mac', is_this_machine: true, session_count: 1 },
    { machine_id: OTHER, label: 'Work laptop', is_this_machine: false, session_count: 2 },
  ]
  server.use(
    http.get('/api/machines', () => {
      machinesAsked = true
      return HttpResponse.json({ this_machine_id: ME, machines: all.slice(0, count) })
    }),
  )
}

vi.mock('@/lib/errorToast', () => ({ errorToast: vi.fn() }))

beforeEach(() => serveApp({}))

async function row(title: string): Promise<HTMLElement> {
  server.use(http.get('/api/conversations', () => HttpResponse.json(rows)))
  render(<ConversationList />)
  const name = await screen.findByText(title)
  const el = name.closest<HTMLElement>('[role="button"]')
  expect(el, `the list row of "${title}"`).not.toBeNull()
  return el!
}

describe('the machine chip of a list row', () => {
  pending('shows the machine label when two machines are present', async () => {
    machines(2)
    const el = await row('Row from the work laptop')

    expect(await within(el).findByTestId('machine-chip')).toHaveTextContent(/^Work laptop$/)
  })

  pending('falls back to "Machine" and the first 8 of the id', async () => {
    machines(2)
    const el = await row('Row with no label')

    expect(await within(el).findByTestId('machine-chip')).toHaveTextContent(/^Machine 9d8e7f6a$/)
  })

  pending('shows no chip on any row when only one machine is present', async () => {
    machines(1)
    await row('Row from this machine')
    // The list asks how many machines there are, and then shows no chip.
    await screen.findByText('Row from the work laptop')
    await waitFor(() => expect(machinesAsked).toBe(true))
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryAllByTestId('machine-chip')).toHaveLength(0)
  })
})

function renderHeader(conversation: ConversationDetail) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return rtlRender(
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

function detail(): ConversationDetail {
  const value = {
    uuid: 'a2-detail',
    name: 'Session from the work laptop',
    source: 'CLAUDE_CODE',
    model: 'claude-opus-5-5',
    created_at: '2026-11-20T10:00:00Z',
    updated_at: '2026-11-20T10:00:00Z',
    message_count: 2,
    has_branches: false,
    messages: [],
    compact_markers: [],
    prelude_hidden_count: 0,
    file_path: null,
    project_path: null,
    git_branch: null,
    sandbox_path: null,
    error: null,
    machine_id: OTHER,
    machine_label: 'Work laptop',
    machine_labels: ['Work laptop', 'Home Mac'],
  }
  return value as unknown as ConversationDetail
}

describe('the machine chip in the header', () => {
  pending('shows the machine label when two machines are present', async () => {
    machines(2)
    renderHeader(detail())

    expect(await screen.findByTestId('machine-chip')).toHaveTextContent(/^Work laptop$/)
  })

  pending('shows no chip when only one machine is present', async () => {
    machines(1)
    renderHeader(detail())

    await screen.findByRole('heading', { level: 1, name: 'Session from the work laptop' })
    await waitFor(() => expect(machinesAsked).toBe(true))
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByTestId('machine-chip')).toBeNull()
  })
})
