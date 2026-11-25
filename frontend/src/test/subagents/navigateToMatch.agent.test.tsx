/**
 * Issue #96: a search hit inside an agent opens the agent page at the
 * message (contract item 8):
 *
 *   `/conversations/<session>/agents/<agent>?highlight=<message>`.
 *
 * The same-page fast path runs only on that agent's page: the session page
 * and another agent's page navigate. A session hit on an agent page goes
 * back to the session.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useEffect, type ReactNode } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { KeyboardNavigationProvider, useKeyboardNavigation } from '@/contexts/KeyboardNavigationContext'
import { useNavigateToMatch } from '@/components/search/navigateToMatch'
import type { SearchMatch } from '@/contexts/SearchPanelContext'

const SESSION = '5a6e0000-0000-4000-8000-000000000096'
const AGENT = 'a5a0000000000096c'
const OTHER_AGENT = '5a1e9ac'
const MSG = 'sa-t1'

function makeMatch(agentId: string | null, messageUuid = MSG): SearchMatch {
  return {
    conversationUuid: SESSION,
    messageUuid,
    conversationName: 'Start the agents',
    snippet: 'snippet text',
    matchStart: 0,
    matchEnd: 4,
    sender: 'assistant',
    createdAt: '2026-11-24T10:00:00Z',
    conversationUpdatedAt: '2026-11-24T10:00:00Z',
    conversationCreatedAt: '2026-11-24T10:00:00Z',
    agentId,
    agentDescription: agentId ? 'Synthetic search agent' : null,
  }
}

function makeWrapper(startPath: string, messages: { uuid: string; sender: 'human' | 'assistant' }[]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const seen = { current: { pathname: '', search: '' } }

  function LocationCapture() {
    const loc = useLocation()
    useEffect(() => {
      seen.current = { pathname: loc.pathname, search: loc.search }
    }, [loc.pathname, loc.search])
    return null
  }

  function MessagesSeed() {
    const { setMessages } = useKeyboardNavigation()
    useEffect(() => {
      if (messages.length > 0) setMessages(messages)
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])
    return null
  }

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[startPath]}>
          <KeyboardNavigationProvider>
            <MessagesSeed />
            <Routes>
              <Route path="*" element={<>{children}<LocationCapture /></>} />
            </Routes>
          </KeyboardNavigationProvider>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }

  return { Wrapper, seen }
}

function mountBubble(uuid: string): HTMLElement {
  const bubble = document.createElement('div')
  bubble.setAttribute('data-message-uuid', uuid)
  document.body.appendChild(bubble)
  return bubble
}

describe('useNavigateToMatch — agent hits (#96)', () => {
  let scrollSpy: ReturnType<typeof vi.spyOn>
  const bubbles: HTMLElement[] = []

  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn()
    scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView')
  })
  afterEach(() => {
    scrollSpy.mockRestore()
    while (bubbles.length) bubbles.pop()?.remove()
  })

  function run(startPath: string, match: SearchMatch, mounted: boolean) {
    if (mounted) bubbles.push(mountBubble(match.messageUuid))
    const { Wrapper, seen } = makeWrapper(startPath, mounted ? [{ uuid: match.messageUuid, sender: 'assistant' }] : [])
    const { result } = renderHook(() => useNavigateToMatch(), { wrapper: Wrapper })
    act(() => result.current(match))
    return seen
  }

  it('from another page, an agent hit opens the agent page with ?highlight=', () => {
    const seen = run('/conversations/00000000-0000-4000-8000-000000000000', makeMatch(AGENT), false)
    expect(seen.current.pathname).toBe(`/conversations/${SESSION}/agents/${AGENT}`)
    expect(seen.current.search).toBe(`?highlight=${MSG}`)
  })

  it('on the session page, an agent hit navigates even when a bubble with that uuid is mounted', () => {
    const seen = run(`/conversations/${SESSION}`, makeMatch(AGENT), true)
    expect(seen.current.pathname).toBe(`/conversations/${SESSION}/agents/${AGENT}`)
    expect(seen.current.search).toBe(`?highlight=${MSG}`)
    expect(scrollSpy).not.toHaveBeenCalled()
  })

  it("on another agent's page, an agent hit navigates to its own agent", () => {
    const seen = run(`/conversations/${SESSION}/agents/${OTHER_AGENT}`, makeMatch(AGENT), true)
    expect(seen.current.pathname).toBe(`/conversations/${SESSION}/agents/${AGENT}`)
    expect(scrollSpy).not.toHaveBeenCalled()
  })

  it("on that agent's page with the bubble mounted, the fast path scrolls in place", () => {
    const seen = run(`/conversations/${SESSION}/agents/${AGENT}`, makeMatch(AGENT), true)
    expect(scrollSpy).toHaveBeenCalledTimes(1)
    expect(seen.current.pathname).toBe(`/conversations/${SESSION}/agents/${AGENT}`)
    expect(seen.current.search).toBe('')
  })

  it('on an agent page, a session hit goes back to the session page', () => {
    const seen = run(`/conversations/${SESSION}/agents/${AGENT}`, makeMatch(null), true)
    expect(seen.current.pathname).toBe(`/conversations/${SESSION}`)
    expect(seen.current.search).toBe(`?highlight=${MSG}`)
    expect(scrollSpy).not.toHaveBeenCalled()
  })

  it('a session hit with agentId null still opens the session (regression guard)', () => {
    const seen = run('/conversations/00000000-0000-4000-8000-000000000000', makeMatch(null), false)
    expect(seen.current.pathname).toBe(`/conversations/${SESSION}`)
    expect(seen.current.search).toBe(`?highlight=${MSG}`)
  })
})
