/**
 * S2 (issue #84): the Search Pane looks inside tool content only while
 * Show Tools is on.
 *
 * Contract (orchestrator, 2026-11-20):
 *   - The search sends include_tool_calls = Show Tools. GET sends
 *     `include_tool_calls=false` only when it is off; POST always sends it.
 *   - Turning Show Tools on or off runs the search again.
 *   - The empty result names it: the 4th part of the #80 line is
 *     `hidden tool calls`, after source, workspace, and filter, while Show
 *     Tools is off. Its button, the last one, is "Search tool calls too"
 *     (`search-empty-show-tools`). A click turns Show Tools on (the shared
 *     state that the header checkbox shows), the search runs again, and the
 *     focus goes to the input.
 *   - Same rules as #80: only in the empty result, and the control wins.
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
import { SettingsProvider, useSettings } from '@/contexts/SettingsContext'
import { SourceFilterProvider, useSourceFilter } from '@/contexts/SourceFilterContext'
import { FilterProvider } from '@/contexts/FilterContext'
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

/** "kumquat" is only in a tool output: the mock finds it only with tool content. */
const TOOL_WORD = 'kumquat'
/** "neon" is in message text: the mock finds it either way. */
const TEXT_WORD = 'neon'

const hit = (word: string) => ({
  conversation_uuid: 'conv-hit',
  conversation_name: 'Fruit session',
  conversation_source: 'CLAUDE_CODE',
  conversation_created_at: '2026-01-01T00:00:00Z',
  conversation_updated_at: '2026-01-01T00:00:00Z',
  project_path: null,
  matching_messages: [
    {
      message_uuid: 'msg-hit',
      sender: 'human',
      snippet: `the ${word} result`,
      match_start: 4,
      match_end: 4 + word.length,
      created_at: '2026-01-01T00:00:00Z',
      fragments: null,
    },
  ],
})

interface SearchCall {
  q: string
  includeToolCalls: boolean
}

function installHandlers(prefs: Record<string, unknown> = {}) {
  const data: Record<string, unknown> = { 'searchPanel.isOpen': true, ...prefs }
  const calls: SearchCall[] = []
  const reply = (c: SearchCall) => {
    calls.push(c)
    const found = c.q === TEXT_WORD || (c.q === TOOL_WORD && c.includeToolCalls)
    const results = found ? [hit(c.q)] : []
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
    http.get('/api/conversations', () => HttpResponse.json([])),
    http.get('/api/bookmarks', () => HttpResponse.json({ bookmarks: [] })),
    http.get('/api/orgs', () =>
      HttpResponse.json({ authenticated: true, orgs: [{ org_id: ACME, name: 'Acme', is_primary: true }] }),
    ),
    http.get('/api/search', ({ request }) => {
      const p = new URL(request.url).searchParams
      // No parameter means the backend default, true.
      return reply({ q: p.get('q') ?? '', includeToolCalls: p.get('include_tool_calls') !== 'false' })
    }),
    http.post('/api/search', async ({ request }) => {
      const b = (await request.json()) as Record<string, unknown>
      return reply({ q: String(b.q ?? ''), includeToolCalls: b.include_tool_calls !== false })
    }),
  )
  return calls
}

/** Holds the Show Tools state, like the header checkbox, and the source state. */
function Probe() {
  const { showToolCalls, setShowToolCalls } = useSettings()
  const { sourceFilter, organizationId } = useSourceFilter()
  return (
    <div>
      <span data-testid="probe-tools">{showToolCalls ? 'on' : 'off'}</span>
      <span data-testid="probe-source">{sourceFilter}</span>
      <span data-testid="probe-org">{organizationId ?? ''}</span>
      <button type="button" data-testid="probe-tools-on" onClick={() => setShowToolCalls(true)}>on</button>
      <button type="button" data-testid="probe-tools-off" onClick={() => setShowToolCalls(false)}>off</button>
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

interface Setup {
  prefs?: Record<string, unknown>
  toolsOn?: boolean
}

async function renderAndSearch(q: string, { prefs = {}, toolsOn = false }: Setup = {}) {
  const calls = installHandlers(prefs)
  localStorageMock.setItem('searchPanel.isOpen', JSON.stringify(true))
  render(
    <>
      <SearchPanel />
      <Probe />
    </>,
    { wrapper: makeWrapper() },
  )
  await waitFor(() => {
    if ('sourceFilter' in prefs) expect(screen.getByTestId('probe-source').textContent).toBe(prefs.sourceFilter)
    if (ORG_KEY in prefs) expect(screen.getByTestId('probe-org').textContent).toBe(prefs[ORG_KEY])
  })
  if (toolsOn) {
    fireEvent.click(screen.getByTestId('probe-tools-on'))
    await waitFor(() => expect(screen.getByTestId('probe-tools').textContent).toBe('on'))
  }
  fireEvent.change(screen.getByPlaceholderText('Search messages...'), { target: { value: q } })
  return calls
}

const noMatches = () => screen.queryByText(/No matches for/)
const line = () => screen.queryByTestId('search-empty-filters')
const toolsButton = () => screen.queryByTestId('search-empty-show-tools')
const hitCard = () => screen.queryAllByTestId('search-result-card')
const callsFor = (calls: SearchCall[], q: string) => calls.filter((c) => c.q === q)

async function expectEmptyLine(text: string): Promise<void> {
  await waitFor(() => {
    expect(noMatches()).toBeInTheDocument()
    expect(line()?.textContent ?? null).toBe(text)
  }, { timeout: 3000 })
}

/** The testids of the empty-state buttons, in document order. */
function buttonOrder(): string[] {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-testid^="search-empty-"], [data-testid="search-unpin-and-search-all"]'))
    .map((el) => el.dataset.testid ?? '')
    .filter((id) => id !== 'search-empty-filters')
}

beforeEach(() => {
  localStorageMock.clear()
  window.history.replaceState(null, '', '/')
})

afterEach(() => {
  localStorageMock.clear()
  window.history.replaceState(null, '', '/')
})

describe('the request follows Show Tools', () => {
  it('sends include_tool_calls=false while Show Tools is off', async () => {
    const calls = await renderAndSearch('zzz')
    await waitFor(() => expect(callsFor(calls, 'zzz').length).toBeGreaterThan(0), { timeout: 3000 })
    expect(screen.getByTestId('probe-tools').textContent).toBe('off')
    expect(callsFor(calls, 'zzz').map((c) => c.includeToolCalls)).not.toContain(true)
  })

  it('does not send include_tool_calls=false while Show Tools is on', async () => {
    const calls = await renderAndSearch('zzz', { toolsOn: true })
    await waitFor(() => expect(callsFor(calls, 'zzz').length).toBeGreaterThan(0), { timeout: 3000 })
    expect(callsFor(calls, 'zzz').map((c) => c.includeToolCalls)).not.toContain(false)
  })

  it('turning Show Tools on runs the search again with tool content', async () => {
    const calls = await renderAndSearch(TOOL_WORD)
    await waitFor(() => expect(noMatches()).toBeInTheDocument(), { timeout: 3000 })
    const before = calls.length
    fireEvent.click(screen.getByTestId('probe-tools-on'))
    await waitFor(() => expect(hitCard()).toHaveLength(1), { timeout: 3000 })
    const after = callsFor(calls.slice(before), TOOL_WORD)
    expect(after.length).toBeGreaterThan(0)
    expect(after.every((c) => c.includeToolCalls)).toBe(true)
  })

  it('turning Show Tools off runs the search again without tool content', async () => {
    const calls = await renderAndSearch(TOOL_WORD, { toolsOn: true })
    await waitFor(() => expect(hitCard()).toHaveLength(1), { timeout: 3000 })
    const before = calls.length
    fireEvent.click(screen.getByTestId('probe-tools-off'))
    await waitFor(() => expect(noMatches()).toBeInTheDocument(), { timeout: 3000 })
    expect(hitCard()).toHaveLength(0)
    const after = callsFor(calls.slice(before), TOOL_WORD)
    expect(after.length).toBeGreaterThan(0)
    expect(after.every((c) => !c.includeToolCalls)).toBe(true)
  })
})

describe('the empty result names hidden tool calls', () => {
  it('an empty result with Show Tools off names hidden tool calls', async () => {
    await renderAndSearch('zzz')
    await expectEmptyLine('Search limited by hidden tool calls.')
    expect(toolsButton()?.textContent).toBe('Search tool calls too')
    expect(buttonOrder()).toEqual(['search-empty-show-tools'])
  })

  it('the hidden tool calls part joins the other parts with "and"', async () => {
    await renderAndSearch('zzz', { prefs: { sourceFilter: 'CODEX', [ORG_KEY]: ACME } })
    await expectEmptyLine('Search limited by source “Codex” and workspace “Acme” and hidden tool calls.')
    expect(buttonOrder()).toEqual([
      'search-empty-clear-source',
      'search-empty-clear-workspace',
      'search-empty-show-tools',
    ])
  })

  it('Search tool calls too turns Show Tools on and runs the search again', async () => {
    const calls = await renderAndSearch(TOOL_WORD)
    await expectEmptyLine('Search limited by hidden tool calls.')
    const before = calls.length
    fireEvent.click(screen.getByTestId('search-empty-show-tools'))
    await waitFor(() => expect(screen.getByTestId('probe-tools').textContent).toBe('on'))
    await waitFor(() => expect(hitCard()).toHaveLength(1), { timeout: 3000 })
    expect(document.activeElement).toBe(screen.getByPlaceholderText('Search messages...'))
    const after = callsFor(calls.slice(before), TOOL_WORD)
    expect(after.length).toBeGreaterThan(0)
    expect(after.every((c) => c.includeToolCalls)).toBe(true)
    expect(line()).toBeNull()
    expect(toolsButton()).toBeNull()
  })

  it('no hidden tool calls part or button while Show Tools is on', async () => {
    await renderAndSearch('zzz', { toolsOn: true })
    await waitFor(() => expect(noMatches()).toBeInTheDocument(), { timeout: 3000 })
    expect(line()).toBeNull()
    expect(toolsButton()).toBeNull()
  })

  it('no hidden tool calls part when the search has results', async () => {
    await renderAndSearch(TEXT_WORD)
    await waitFor(() => expect(hitCard()).toHaveLength(1), { timeout: 3000 })
    expect(screen.getByTestId('probe-tools').textContent).toBe('off')
    expect(noMatches()).toBeNull()
    expect(line()).toBeNull()
    expect(toolsButton()).toBeNull()
  })
})
