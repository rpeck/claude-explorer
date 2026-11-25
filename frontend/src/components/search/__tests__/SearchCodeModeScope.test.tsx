/**
 * Issue #90: the Search Pane looks inside Code Mode runs only while Show
 * Code Mode is on.
 *
 *   - The search sends `include_code_mode=true` only while it is on, and a
 *     flip runs the search again.
 *   - An empty result names `hidden Code Mode runs` (the #80 line) only when
 *     the runs hold a match: with Show Code Mode off, one probe search with
 *     `include_code_mode=true` decides. Without a hidden match the line does
 *     not name it, so a user who never ran Code Mode never sees it.
 *   - Its button "Search Code Mode runs too" turns Show Code Mode on, and the
 *     search shows the hit.
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
import { SourceFilterProvider } from '@/contexts/SourceFilterContext'
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

/** Only in a Code Mode run: the mock finds it only with include_code_mode. */
const RUN_WORD = 'ocelotscript'
/** Nowhere. */
const NO_WORD = 'zzzz'

interface SearchCall {
  q: string
  includeCodeMode: boolean
}

function installHandlers() {
  const calls: SearchCall[] = []
  const reply = (c: SearchCall) => {
    calls.push(c)
    const results = c.q === RUN_WORD && c.includeCodeMode ? [{
      conversation_uuid: 'conv-hit',
      conversation_name: 'Script session',
      conversation_source: 'CLAUDE_CODE',
      conversation_created_at: '2026-01-01T00:00:00Z',
      conversation_updated_at: '2026-01-01T00:00:00Z',
      project_path: null,
      matching_messages: [{
        message_uuid: 'msg-hit', sender: 'assistant', snippet: `// ${RUN_WORD}`,
        match_start: 3, match_end: 3 + RUN_WORD.length, created_at: '2026-01-01T00:00:00Z', fragments: null,
      }],
    }] : []
    return HttpResponse.json({
      results, total_messages_matched: results.length, returned_messages: results.length, truncated: false,
    })
  }
  server.use(
    http.get('/api/preferences', () => HttpResponse.json({ version: 1, data: { 'searchPanel.isOpen': true } })),
    http.patch('/api/preferences', () => HttpResponse.json({ version: 1, data: {} })),
    http.get('/api/conversations', () => HttpResponse.json([])),
    http.get('/api/bookmarks', () => HttpResponse.json({ bookmarks: [] })),
    http.get('/api/orgs', () => HttpResponse.json({ authenticated: true, orgs: [] })),
    http.get('/api/search', ({ request }) => {
      const p = new URL(request.url).searchParams
      return reply({ q: p.get('q') ?? '', includeCodeMode: p.get('include_code_mode') === 'true' })
    }),
    http.post('/api/search', async ({ request }) => {
      const b = (await request.json()) as Record<string, unknown>
      return reply({ q: String(b.q ?? ''), includeCodeMode: b.include_code_mode === true })
    }),
  )
  return calls
}

/** Shows the Show Code Mode state; turns Show Tools on so its #84 part stays out. */
function Probe() {
  const { showCodeMode, setShowToolCalls } = useSettings()
  return (
    <div>
      <span data-testid="probe-code">{showCodeMode ? 'on' : 'off'}</span>
      <button type="button" data-testid="probe-tools-on" onClick={() => setShowToolCalls(true)}>tools</button>
    </div>
  )
}

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } } })
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

async function renderAndSearch(q: string) {
  const calls = installHandlers()
  localStorageMock.setItem('searchPanel.isOpen', JSON.stringify(true))
  render(<><SearchPanel /><Probe /></>, { wrapper: makeWrapper() })
  fireEvent.click(screen.getByTestId('probe-tools-on'))
  fireEvent.change(screen.getByPlaceholderText('Search messages...'), { target: { value: q } })
  return calls
}

const line = () => screen.queryByTestId('search-empty-filters')
const codeButton = () => screen.queryByTestId('search-empty-show-code-mode')

describe('Search Pane and Show Code Mode', () => {
  beforeEach(() => localStorageMock.clear())
  afterEach(() => server.resetHandlers())

  it('names hidden Code Mode runs when they hold a match, and the button finds it', async () => {
    const calls = await renderAndSearch(RUN_WORD)
    await waitFor(() => expect(line()?.textContent).toBe('Search limited by hidden Code Mode runs.'), { timeout: 3000 })
    expect(codeButton()).toHaveTextContent('Search Code Mode runs too')
    expect(calls.some((c) => c.q === RUN_WORD && !c.includeCodeMode)).toBe(true)

    fireEvent.click(screen.getByTestId('search-empty-show-code-mode'))
    await waitFor(() => expect(screen.getByTestId('probe-code').textContent).toBe('on'))
    await waitFor(() => expect(screen.getAllByTestId('search-result-card')).toHaveLength(1), { timeout: 3000 })
    expect(line()).toBeNull()
  })

  it('does not name Code Mode runs when they hold no match', async () => {
    const calls = await renderAndSearch(NO_WORD)
    await waitFor(() => expect(screen.queryByText(/No matches for/)).toBeInTheDocument(), { timeout: 3000 })
    // The probe ran, and found nothing.
    await waitFor(() => expect(calls.some((c) => c.q === NO_WORD && c.includeCodeMode)).toBe(true))
    expect(line()).toBeNull()
    expect(codeButton()).toBeNull()
  })
})
