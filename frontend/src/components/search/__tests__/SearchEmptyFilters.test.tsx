/**
 * S1 (issue #80): the empty search result names the filters that limit it.
 *
 * The user filtered the list to "Codex" and searched for a word that only a
 * Claude Code session holds. The pane said only `No matches for "neon"`, so
 * the search looked broken. The control still wins (AGENTS.md, "UI Controls:
 * No Exceptions"); the empty result now says why it is empty.
 *
 * Contract (orchestrator, 2026-11-20, Option A for the named filter):
 *   - Only in the empty state. With results, or with no filter on, the DOM
 *     does not change.
 *   - Line `search-empty-filters`: `Search limited by ` + parts joined with
 *     " and " + `.`, in this order:
 *       source `source “<registry label>”`, workspace `workspace “<org name>”`
 *       (fallback `Workspace (<first 8 chars>)`), named filter
 *       `filter “<node name>”` (only when it limits the search). Curly
 *       quotes, as in the `No matches for “…”` line above it.
 *   - The pin keeps its text and its "Unpin and search all →" button, and the
 *     line does not repeat it.
 *   - One button per part, after the pin button:
 *       "Search all agents" (`search-empty-clear-source`) → source 'all';
 *       "Search all workspaces" (`search-empty-clear-workspace`) → no workspace;
 *       "Clear active filter" (`search-empty-clear-filter`) → no active filter.
 *     A click changes the shared state (the Sidebar shows it) and the search
 *     runs again without that limit.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { http, HttpResponse } from 'msw'
import type { ReactNode } from 'react'

import { server } from '@/test/mocks/server'
import { SearchPanel } from '@/components/search/SearchPanel'
import { SearchPanelProvider } from '@/contexts/SearchPanelContext'
import { SettingsProvider } from '@/contexts/SettingsContext'
import { SourceFilterProvider, useSourceFilter } from '@/contexts/SourceFilterContext'
import { FilterProvider, useFilters } from '@/contexts/FilterContext'
import { BookmarkProvider } from '@/contexts/BookmarkContext'
import { SearchPinProvider } from '@/contexts/SearchPinContext'
import { KeyboardNavigationProvider } from '@/contexts/KeyboardNavigationContext'

const localStorageMock = (() => {
  let store: Record<string, string> = {}
  return {
    getItem: vi.fn((k: string) => (k in store ? store[k] : null)),
    setItem: vi.fn((k: string, v: string) => { store[k] = v }),
    removeItem: vi.fn((k: string) => { delete store[k] }),
    clear: vi.fn(() => { store = {} }),
  }
})()
Object.defineProperty(window, 'localStorage', { value: localStorageMock })

const ORG_KEY = 'claude-explorer.organizationFilter'
const ACME = 'acme0001-0000-4000-8000-000000000001'
const BETA = 'beta0002-0000-4000-8000-000000000002'
const UNLISTED_ORG = 'abcdef12-3456-4789-8abc-def012345678'

const SOURCE_LABELS = {
  CLAUDE_AI: 'Claude Desktop',
  CLAUDE_CODE: 'Claude Code',
  CLAUDE_COWORK: 'Claude Cowork',
  CODEX: 'Codex',
} as const

/** A filter that keeps only "Work notes": it limits the search. */
const WORK_FILTER = {
  nodes: {
    'f-work': {
      id: 'f-work', name: 'Work', enabled: true, type: 'atom',
      patterns: ['Work*'], behavior: 'show-only', mode: 'glob', target: 'title',
    },
  },
  activeId: 'f-work',
  _migratedV1: true,
}
/** A filter that keeps every conversation: it does not limit the search. */
const EVERYTHING_FILTER = {
  nodes: {
    'f-all': {
      id: 'f-all', name: 'Everything', enabled: true, type: 'atom',
      patterns: ['*'], behavior: 'show-only', mode: 'glob', target: 'title',
    },
  },
  activeId: 'f-all',
  _migratedV1: true,
}

const CONVERSATIONS = [
  { uuid: 'conv-work', name: 'Work notes', source: 'CODEX', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  { uuid: 'conv-hit', name: 'Neon session', source: 'CLAUDE_CODE', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
]

const HIT = {
  conversation_uuid: 'conv-hit',
  conversation_name: 'Neon session',
  conversation_source: 'CLAUDE_CODE',
  conversation_created_at: '2026-01-01T00:00:00Z',
  conversation_updated_at: '2026-01-01T00:00:00Z',
  project_path: null,
  matching_messages: [
    {
      message_uuid: 'msg-neon',
      sender: 'human',
      snippet: 'the neon sign',
      match_start: 4,
      match_end: 8,
      created_at: '2026-01-01T00:00:00Z',
      fragments: null,
    },
  ],
}

interface SearchCall {
  q: string
  source: string
  organizationId: string | null
  conversationUuids: string[] | null
}

const unlimited = (c: SearchCall) =>
  c.source === 'all' && c.organizationId === null && c.conversationUuids === null

interface Setup {
  prefs?: Record<string, unknown>
  /** Which requests get the hit (only for the query "neon"). Default: an unlimited search. */
  hitWhen?: (c: SearchCall) => boolean
}

function installHandlers({ prefs = {}, hitWhen = unlimited }: Setup) {
  const data: Record<string, unknown> = { 'searchPanel.isOpen': true, ...prefs }
  const calls: SearchCall[] = []
  const listCalls: string[] = []
  const reply = (c: SearchCall) => {
    calls.push(c)
    const results = c.q === 'neon' && hitWhen(c) ? [HIT] : []
    return HttpResponse.json({
      results,
      total_messages_matched: results.length,
      returned_messages: results.length,
      truncated: false,
    })
  }
  server.use(
    http.get('/api/preferences', () => HttpResponse.json({ version: 1, data })),
    http.patch('/api/preferences', async ({ request }) => {
      const json = (await request.json()) as { data?: Record<string, unknown> }
      Object.assign(data, json.data ?? {})
      return HttpResponse.json({ version: 1, data })
    }),
    http.get('/api/conversations', ({ request }) => {
      listCalls.push(request.url)
      return HttpResponse.json(CONVERSATIONS)
    }),
    http.get('/api/bookmarks', () => HttpResponse.json({ bookmarks: [] })),
    http.get('/api/orgs', () =>
      HttpResponse.json({
        authenticated: true,
        orgs: [
          { org_id: ACME, name: 'Acme', is_primary: true },
          { org_id: BETA, name: 'Beta', is_primary: false },
        ],
      }),
    ),
    http.get('/api/search', ({ request }) => {
      const p = new URL(request.url).searchParams
      return reply({
        q: p.get('q') ?? '',
        source: p.get('source') ?? 'all',
        organizationId: p.get('organization_id'),
        conversationUuids: null,
      })
    }),
    http.post('/api/search', async ({ request }) => {
      const b = (await request.json()) as Record<string, unknown>
      return reply({
        q: String(b.q ?? ''),
        source: String(b.source ?? 'all'),
        organizationId: (b.organization_id as string | undefined) ?? null,
        conversationUuids: (b.conversation_uuids as string[] | undefined) ?? null,
      })
    }),
  )
  return { calls, listCalls }
}

/** Shows the shared state that the Sidebar controls hold. */
function Probe() {
  const { sourceFilter, organizationId } = useSourceFilter()
  const { filtersState } = useFilters()
  return (
    <div>
      <span data-testid="probe-source">{sourceFilter}</span>
      <span data-testid="probe-org">{organizationId ?? ''}</span>
      <span data-testid="probe-filter">{filtersState.activeId ?? ''}</span>
    </div>
  )
}

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <SettingsProvider>
            <SourceFilterProvider>
              <FilterProvider>
                <BookmarkProvider>
                  <KeyboardNavigationProvider>
                    <SearchPinProvider>
                      <SearchPanelProvider>{children}</SearchPanelProvider>
                    </SearchPinProvider>
                  </KeyboardNavigationProvider>
                </BookmarkProvider>
              </FilterProvider>
            </SourceFilterProvider>
          </SettingsProvider>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
  return Wrapper
}

async function renderAndSearch(setup: Setup, q: string) {
  const handlers = installHandlers(setup)
  localStorageMock.setItem('searchPanel.isOpen', JSON.stringify(true))
  render(
    <>
      <SearchPanel />
      <Probe />
    </>,
    { wrapper: makeWrapper() },
  )
  // Wait for the stored preferences, so the search runs with them.
  const want = setup.prefs ?? {}
  await waitFor(() => {
    if ('sourceFilter' in want) expect(screen.getByTestId('probe-source').textContent).toBe(want.sourceFilter)
    if (ORG_KEY in want) expect(screen.getByTestId('probe-org').textContent).toBe(want[ORG_KEY])
    if ('filters' in want) {
      expect(screen.getByTestId('probe-filter').textContent).toBe((want.filters as { activeId: string }).activeId)
    }
  })
  fireEvent.change(screen.getByPlaceholderText('Search messages...'), { target: { value: q } })
  return handlers
}

const noMatches = () => screen.queryByText(/No matches for/)
const line = () => screen.queryByTestId('search-empty-filters')
const CLEAR_IDS = ['search-empty-clear-source', 'search-empty-clear-workspace', 'search-empty-clear-filter'] as const

/** Wait for the empty result whose filter line reads `text`. */
async function expectEmptyLine(text: string): Promise<void> {
  await waitFor(() => {
    expect(noMatches()).toBeInTheDocument()
    expect(line()?.textContent ?? null).toBe(text)
  }, { timeout: 3000 })
}

/** The testids of the empty-state buttons, in document order. */
function buttonOrder(): string[] {
  const ids = ['search-unpin-and-search-all', ...CLEAR_IDS]
  return Array.from(document.querySelectorAll<HTMLElement>('[data-testid]'))
    .map((el) => el.dataset.testid ?? '')
    .filter((id) => ids.includes(id))
}

const hitCard = () => screen.queryAllByTestId('search-result-card')

beforeEach(() => {
  localStorageMock.clear()
  window.history.replaceState(null, '', '/')
})

afterEach(() => {
  localStorageMock.clear()
  window.history.replaceState(null, '', '/')
})

describe('the empty search result with no filter on (unchanged)', () => {
  it('shows only "No matches for", with no filter line and no clear button', async () => {
    await renderAndSearch({}, 'zzz')
    await waitFor(() => expect(noMatches()).toBeInTheDocument(), { timeout: 3000 })
    expect(noMatches()?.textContent).toContain('zzz')
    expect(line()).toBeNull()
    for (const id of CLEAR_IDS) expect(screen.queryByTestId(id), id).toBeNull()
  })

  it('with only a pin, keeps the pin text and button and adds no filter line', async () => {
    window.history.replaceState(null, '', '/?pin=project:%2Fwork%2Falpha')
    await renderAndSearch({}, 'zzz')
    await waitFor(() => expect(screen.getByTestId('search-unpin-and-search-all')).toBeInTheDocument(), { timeout: 3000 })
    expect(noMatches()?.textContent).toMatch(/No matches for.*zzz.*in\s*alpha/)
    expect(screen.getByTestId('search-unpin-and-search-all').textContent).toBe('Unpin and search all →')
    expect(line()).toBeNull()
    for (const id of CLEAR_IDS) expect(screen.queryByTestId(id), id).toBeNull()
  })
})

describe('the control wins when results exist (unchanged)', () => {
  it('with the source filter on and a result, shows the result and no filter line', async () => {
    await renderAndSearch({ prefs: { sourceFilter: 'CODEX' }, hitWhen: () => true }, 'neon')
    await waitFor(() => expect(hitCard()).toHaveLength(1), { timeout: 3000 })
    expect(noMatches()).toBeNull()
    expect(line()).toBeNull()
    for (const id of CLEAR_IDS) expect(screen.queryByTestId(id), id).toBeNull()
  })
})

describe('the source filter', () => {
  for (const [source, label] of Object.entries(SOURCE_LABELS)) {
    it(`names the source filter ${source} by its registry label "${label}"`, async () => {
      await renderAndSearch({ prefs: { sourceFilter: source } }, 'zzz')
      await expectEmptyLine(`Search limited by source “${label}”.`)
    })
  }

  it('#80: gives "Search all agents", which sets the source filter to all', async () => {
    await renderAndSearch({ prefs: { sourceFilter: 'CODEX' } }, 'neon')
    await expectEmptyLine('Search limited by source “Codex”.')
    const button = screen.getByTestId('search-empty-clear-source')
    expect(button.textContent).toBe('Search all agents')
    expect(buttonOrder()).toEqual(['search-empty-clear-source'])
    fireEvent.click(button)
    await waitFor(() => expect(screen.getByTestId('probe-source').textContent).toBe('all'))
    expect(document.activeElement).toBe(screen.getByPlaceholderText('Search messages...'))
  })

  it('#80: after "Search all agents", the search runs again with no source limit and shows the hit', async () => {
    const { calls } = await renderAndSearch({ prefs: { sourceFilter: 'CODEX' } }, 'neon')
    await expectEmptyLine('Search limited by source “Codex”.')
    expect(calls.some((c) => c.q === 'neon' && c.source === 'CODEX')).toBe(true)
    const before = calls.length
    fireEvent.click(screen.getByTestId('search-empty-clear-source'))
    await waitFor(() => expect(hitCard()).toHaveLength(1), { timeout: 3000 })
    const after = calls.slice(before).filter((c) => c.q === 'neon')
    expect(after.length).toBeGreaterThan(0)
    expect(after.every((c) => c.source === 'all')).toBe(true)
    expect(line()).toBeNull()
    expect(noMatches()).toBeNull()
  })
})

describe('the workspace', () => {
  it('names the workspace by its org name, and gives "Search all workspaces"', async () => {
    await renderAndSearch({ prefs: { [ORG_KEY]: ACME } }, 'zzz')
    await expectEmptyLine('Search limited by workspace “Acme”.')
    expect(screen.getByTestId('search-empty-clear-workspace').textContent).toBe('Search all workspaces')
    expect(buttonOrder()).toEqual(['search-empty-clear-workspace'])
  })

  it('names an org that /api/orgs does not list as "Workspace (<first 8 chars>)"', async () => {
    await renderAndSearch({ prefs: { [ORG_KEY]: UNLISTED_ORG } }, 'zzz')
    await expectEmptyLine('Search limited by workspace “Workspace (abcdef12)”.')
  })

  it('"Search all workspaces" clears the workspace, and the search runs again without it', async () => {
    const { calls } = await renderAndSearch({ prefs: { [ORG_KEY]: ACME } }, 'neon')
    await expectEmptyLine('Search limited by workspace “Acme”.')
    const before = calls.length
    fireEvent.click(screen.getByTestId('search-empty-clear-workspace'))
    await waitFor(() => expect(screen.getByTestId('probe-org').textContent).toBe(''))
    await waitFor(() => expect(hitCard()).toHaveLength(1), { timeout: 3000 })
    const after = calls.slice(before).filter((c) => c.q === 'neon')
    expect(after.length).toBeGreaterThan(0)
    expect(after.every((c) => c.organizationId === null)).toBe(true)
    expect(line()).toBeNull()
  })
})

describe('the active named filter (Option A: named only when it limits the search)', () => {
  it('names a filter that excludes conversations, and gives "Clear active filter"', async () => {
    await renderAndSearch({ prefs: { filters: WORK_FILTER } }, 'zzz')
    await expectEmptyLine('Search limited by filter “Work”.')
    expect(screen.getByTestId('search-empty-clear-filter').textContent).toBe('Clear active filter')
    expect(buttonOrder()).toEqual(['search-empty-clear-filter'])
  })

  it('"Clear active filter" clears the filter, and the search runs again with no conversation limit', async () => {
    const { calls } = await renderAndSearch({ prefs: { filters: WORK_FILTER } }, 'neon')
    await expectEmptyLine('Search limited by filter “Work”.')
    expect(calls.some((c) => c.q === 'neon' && c.conversationUuids?.join() === 'conv-work')).toBe(true)
    const before = calls.length
    fireEvent.click(screen.getByTestId('search-empty-clear-filter'))
    await waitFor(() => expect(screen.getByTestId('probe-filter').textContent).toBe(''))
    await waitFor(() => expect(hitCard()).toHaveLength(1), { timeout: 3000 })
    const after = calls.slice(before).filter((c) => c.q === 'neon')
    expect(after.length).toBeGreaterThan(0)
    expect(after.every((c) => c.conversationUuids === null)).toBe(true)
    expect(line()).toBeNull()
  })

  it('does not name a filter that passes every conversation', async () => {
    const { listCalls } = await renderAndSearch({ prefs: { sourceFilter: 'CODEX', filters: EVERYTHING_FILTER } }, 'zzz')
    // The list loaded, so the context knows that the filter passes everything.
    await waitFor(() => expect(listCalls.length).toBeGreaterThan(0))
    await expectEmptyLine('Search limited by source “Codex”.')
    expect(screen.queryByTestId('search-empty-clear-filter')).toBeNull()
  })
})

describe('more than one filter', () => {
  it('names source, workspace, and filter in that order, joined with " and "', async () => {
    await renderAndSearch({ prefs: { sourceFilter: 'CODEX', [ORG_KEY]: ACME, filters: WORK_FILTER } }, 'zzz')
    await expectEmptyLine('Search limited by source “Codex” and workspace “Acme” and filter “Work”.')
    expect(buttonOrder()).toEqual([
      'search-empty-clear-source',
      'search-empty-clear-workspace',
      'search-empty-clear-filter',
    ])
  })

  it('clearing one filter keeps the others named', async () => {
    await renderAndSearch({ prefs: { sourceFilter: 'CODEX', [ORG_KEY]: ACME } }, 'zzz')
    await expectEmptyLine('Search limited by source “Codex” and workspace “Acme”.')
    fireEvent.click(screen.getByTestId('search-empty-clear-source'))
    await expectEmptyLine('Search limited by workspace “Acme”.')
    expect(screen.queryByTestId('search-empty-clear-source')).toBeNull()
    expect(screen.getByTestId('search-empty-clear-workspace')).toBeInTheDocument()
  })

  it('with a pin, keeps the pin text and puts the pin button first', async () => {
    window.history.replaceState(null, '', '/?pin=project:%2Fwork%2Falpha')
    await renderAndSearch({ prefs: { sourceFilter: 'CODEX' } }, 'zzz')
    await expectEmptyLine('Search limited by source “Codex”.')
    expect(noMatches()?.textContent).toMatch(/No matches for.*zzz.*in\s*alpha/)
    expect(screen.getByTestId('search-unpin-and-search-all').textContent).toBe('Unpin and search all →')
    expect(buttonOrder()).toEqual(['search-unpin-and-search-all', 'search-empty-clear-source'])
  })
})
