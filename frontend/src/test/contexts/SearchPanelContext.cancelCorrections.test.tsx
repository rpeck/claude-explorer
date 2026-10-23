/**
 * Demonstrated focus cancels pending scroll corrections (2026-10-23).
 *
 * A search jump's scrollBubbleIntoView schedules corrections that
 * re-center the hit. When the user clicked another bubble within that
 * window, a correction scrolled back to the hit and undid the click
 * (auto-promote-respects-demonstrated-focus.spec.ts, a full-suite flake).
 * markDemonstratedFocus is the one place that records a bubble click or
 * a manual wheel/touch scroll, so it cancels the pending corrections.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

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

const cancelSpy = vi.fn()
vi.mock('@/lib/scrollBubbleIntoView', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/scrollBubbleIntoView')>()
  return { ...actual, cancelScrollCorrections: () => cancelSpy() }
})

import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { http, HttpResponse } from 'msw'
import type { ReactNode } from 'react'

import { server } from '../mocks/server'
import { SearchPanelProvider, useSearchPanel } from '@/contexts/SearchPanelContext'
import { SettingsProvider } from '@/contexts/SettingsContext'
import { SourceFilterProvider } from '@/contexts/SourceFilterContext'
import { FilterProvider } from '@/contexts/FilterContext'
import { BookmarkProvider } from '@/contexts/BookmarkContext'
import { SearchPinProvider } from '@/contexts/SearchPinContext'

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <SettingsProvider>
            <SourceFilterProvider>
              <FilterProvider>
                <BookmarkProvider>
                  <SearchPinProvider>
                    <SearchPanelProvider>{children}</SearchPanelProvider>
                  </SearchPinProvider>
                </BookmarkProvider>
              </FilterProvider>
            </SourceFilterProvider>
          </SettingsProvider>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
}

beforeEach(() => {
  cancelSpy.mockClear()
  server.use(
    http.get('/api/preferences', () => HttpResponse.json({ version: 1, data: {} })),
    http.patch('/api/preferences', () => HttpResponse.json({ version: 1, data: {} })),
    http.get('/api/conversations', () => HttpResponse.json([])),
    http.get('/api/orgs', () => HttpResponse.json([])),
    http.get('/api/bookmarks', () => HttpResponse.json([])),
  )
})

describe('markDemonstratedFocus cancels pending scroll corrections', () => {
  it('cancels them when the user demonstrates focus (click or manual scroll)', () => {
    const { result } = renderHook(() => useSearchPanel(), { wrapper: wrapper() })
    act(() => result.current.markDemonstratedFocus('m-0085'))
    expect(cancelSpy).toHaveBeenCalledTimes(1)
  })

  it('does not cancel when the latch is only cleared (null)', () => {
    const { result } = renderHook(() => useSearchPanel(), { wrapper: wrapper() })
    act(() => result.current.markDemonstratedFocus(null))
    expect(cancelSpy).not.toHaveBeenCalled()
  })
})
