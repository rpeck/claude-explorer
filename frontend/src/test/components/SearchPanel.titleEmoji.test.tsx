/**
 * SearchPanel — issue #44: an emoji in a title looked like an app icon.
 *
 * Contract for the result cards:
 *   - The conversation name on each card keeps the exact title, and each
 *     emoji in it is in a `span.title-emoji` (grayscale in index.css).
 *   - A title match shows the title as its snippet. That snippet gets the
 *     same style, also when a <mark> highlights a part of it.
 *   - A message snippet is message content, not a title. Its emoji keep
 *     their color (negative check).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { http, HttpResponse } from 'msw'
import type { ReactNode } from 'react'

import { server } from '../mocks/server'
import { SearchPanel } from '@/components/search/SearchPanel'
import { SearchPanelProvider } from '@/contexts/SearchPanelContext'
import { SettingsProvider } from '@/contexts/SettingsContext'
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

beforeEach(() => {
  localStorageMock.clear()
  document.body.innerHTML = ''
})

afterEach(() => {
  document.body.innerHTML = ''
  localStorageMock.clear()
})


const RESULTS = [
  {
    conversation_uuid: 'conv-msg',
    conversation_name: '⚡ Weekly report',
    conversation_source: 'CLAUDE_COWORK',
    conversation_created_at: '2026-01-01T00:00:00Z',
    conversation_updated_at: '2026-01-01T00:00:00Z',
    project_path: null,
    matching_messages: [
      {
        message_uuid: 'msg-1',
        sender: 'human',
        snippet: '🎉 NEEDLE in a message',
        match_start: 3,
        match_end: 9,
        created_at: '2026-01-01T00:00:00Z',
        fragments: null,
      },
    ],
  },
  {
    conversation_uuid: 'conv-title',
    conversation_name: '📊 NEEDLE stats',
    conversation_source: 'CLAUDE_AI',
    conversation_created_at: '2026-01-01T00:00:00Z',
    conversation_updated_at: '2026-01-01T00:00:00Z',
    project_path: null,
    matching_messages: [
      {
        message_uuid: 'title',
        sender: '',
        snippet: '📊 NEEDLE stats',
        match_start: 3,
        match_end: 9,
        created_at: null,
        fragments: null,
      },
    ],
  },
]

function installHandlers() {
  const prefs = { data: { 'searchPanel.isOpen': true } as Record<string, unknown> }
  server.use(
    http.get('/api/preferences', () => HttpResponse.json({ version: 1, data: prefs.data })),
    http.patch('/api/preferences', async ({ request }) => {
      const json = (await request.json()) as { data?: Record<string, unknown> }
      Object.assign(prefs.data, json.data ?? {})
      return HttpResponse.json({ version: 1, data: prefs.data })
    }),
    http.get('/api/conversations', () => HttpResponse.json([])),
    http.get('/api/orgs', () => HttpResponse.json([])),
    http.get('/api/search', () =>
      HttpResponse.json({
        results: RESULTS,
        total_messages_matched: 2,
        returned_messages: 2,
        truncated: false,
      }),
    ),
  )
}

async function searchAndGetCards(): Promise<HTMLElement[]> {
  installHandlers()
  localStorageMock.setItem('searchPanel.isOpen', JSON.stringify(true))
  render(<SearchPanel />, { wrapper: makeWrapper() })
  const input = screen.getByPlaceholderText('Search messages...')
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
  setter.call(input, 'NEEDLE')
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await vi.waitFor(() => {
    expect(document.querySelectorAll('[data-testid="search-result-card"]')).toHaveLength(2)
  }, { timeout: 3000 })
  return Array.from(document.querySelectorAll<HTMLElement>('[data-testid="search-result-card"]'))
}

// The card's name line carries the exact title in its `title` attribute.
// Compare in code: the jsdom selector engine does not match an attribute
// selector that holds an emoji outside the BMP, such as 📊.
function nameLine(card: HTMLElement): HTMLElement | undefined {
  return Array.from(card.querySelectorAll<HTMLElement>('[title]')).find(
    (el) => el.getAttribute('title') === el.textContent,
  )
}

function cardFor(cards: HTMLElement[], name: string): HTMLElement {
  const card = cards.find((c) => nameLine(c)?.getAttribute('title') === name)
  expect(card, `no card with the name ${name}`).toBeDefined()
  return card!
}

function emojiIn(el: Element): string[] {
  return Array.from(el.querySelectorAll('span.title-emoji')).map((s) => s.textContent ?? '')
}

describe('SearchPanel — emoji in titles (issue #44)', () => {
  it('styles the emoji in the conversation name of a card, and keeps the exact text', async () => {
    const cards = await searchAndGetCards()
    const name = nameLine(cardFor(cards, '⚡ Weekly report'))!
    expect(name.textContent).toBe('⚡ Weekly report')
    expect(emojiIn(name)).toEqual(['⚡'])
  })

  it('styles the emoji in the snippet of a title match', async () => {
    const cards = await searchAndGetCards()
    const card = cardFor(cards, '📊 NEEDLE stats')
    // The name line and the title snippet both show the title.
    expect(emojiIn(card)).toEqual(['📊', '📊'])
    expect(card.querySelector('mark')?.textContent).toBe('NEEDLE')
  })

  it('NEGATIVE: keeps the color of an emoji in a message snippet', async () => {
    const cards = await searchAndGetCards()
    const card = cardFor(cards, '⚡ Weekly report')
    // Only the name line has a styled emoji; the message emoji 🎉 does not.
    expect(emojiIn(card)).toEqual(['⚡'])
    expect(card.textContent).toContain('🎉 NEEDLE in a message')
  })
})
