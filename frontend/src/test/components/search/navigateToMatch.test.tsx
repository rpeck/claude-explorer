/**
 * useNavigateToMatch — A1 two-tier strategy with the silent-bail bug fixed (2026-05-20).
 *
 * Background: prior to this fix, the hook had a "fast path" that
 * tried a same-tick `document.querySelector('[data-message-uuid=...]')`
 * immediately after queueing React state updates. On a 15K-message
 * conversation, that synchronous query returned `null` (the bubble was
 * not yet mounted at this paint), and the fast path silently `return`ed
 * without falling through to the URL-based navigate path. Result:
 * clicking a search-result card did nothing.
 *
 * Council decision (3 personas, unanimous): two-tier strategy.
 *
 *   1. Fast path — same conv, bubble mounted → scrollIntoView in place,
 *      flash a yellow ring for 2s, NO URL change.
 *   2. URL fallback — same conv with bubble NOT mounted, OR
 *      cross-conv → `navigate('/conversations/<uuid>?highlight=<uuid>')`
 *      and let ConversationPage's highlight effect handle scroll + ring +
 *      focus + URL cleanup.
 *
 * The bug fix: replace the unconditional `return` after
 * `setSelectedMessageIndex/setFocusArea` with a fall-through to the URL
 * branch when `querySelector` returns null.
 *
 * Bidirectional contract pinned by these tests:
 *
 *   POSITIVE (Test 1): same-conv, bubble MOUNTED → calls
 *     `scrollIntoView()` directly, URL does NOT change. Fast path lives.
 *
 *   POSITIVE (Test 2 — THE BUG PIN): same-conv, bubble NOT mounted →
 *     calls `navigate(...)` with `?highlight=<uuid>` (NOT a silent
 *     return). This is the test that fails RED on pre-fix code.
 *
 *   POSITIVE (Test 3): cross-conv → calls `navigate(...)` with
 *     `?highlight=<uuid>`. Regression guard for the working path.
 *
 *   POSITIVE (Test 4): same-conv, `messageUuid === 'title'` → calls
 *     `navigate(...)` WITHOUT `?highlight=`. Regression guard.
 *
 * The Test-2 vs Test-1 split is load-bearing: it pins that the bubble's
 * DOM presence determines the path, NOT that we always navigate (which
 * would break the ring-flash independent-timer contract pinned by
 * e2e/search-auto-focus.spec.ts).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useEffect, type ReactNode } from 'react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { KeyboardNavigationProvider, useKeyboardNavigation } from '@/contexts/KeyboardNavigationContext'
import { useNavigateToMatch } from '@/components/search/navigateToMatch'
import { isStaleUrlHighlight, __resetSearchNavigationForTests } from '@/lib/searchNavigation'
import type { SearchMatch } from '@/contexts/SearchPanelContext'

const CONV_A = '00000000-0000-0000-0000-000000000aaa'
const CONV_B = '00000000-0000-0000-0000-000000000bbb'
const MSG_X = 'msg-x'

function makeMatch(overrides: Partial<SearchMatch> & { conversationUuid: string; messageUuid: string }): SearchMatch {
  return {
    conversationName: 'Test conv',
    snippet: 'snippet text',
    matchStart: 0,
    matchEnd: 4,
    sender: 'human',
    createdAt: '2026-05-20T00:00:00Z',
    conversationUpdatedAt: '2026-05-20T00:00:00Z',
    conversationCreatedAt: '2026-05-20T00:00:00Z',
    ...overrides,
  }
}

/**
 * Test wrapper. Mounts the hook inside a MemoryRouter at `/conversations/<currentUuid>`
 * so the hook's `useLocation` reflects the right "current conversation".
 * Also registers the test's keyboard-nav `messages` so `findIndex(...)`
 * can succeed when the test wants the same-conv fast-path branch.
 */
function makeWrapper(currentUuid: string, registerMessages: { uuid: string; sender: 'human' | 'assistant' }[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  })

  // Captures the most recent location (so tests can assert
  // pathname/search after navigate() is called).
  const locationSeen = { current: { pathname: '', search: '' } }

  function LocationCapture() {
    const loc = useLocation()
    // useEffect (not render-body write) so the react-hooks/immutability
    // lint allows the side-effect into the test-owned closure. Runs
    // after every render the router commits — exactly when we want to
    // record the new location.
    useEffect(() => {
      locationSeen.current = { pathname: loc.pathname, search: loc.search }
    }, [loc.pathname, loc.search])
    return null
  }

  function MessagesSeed() {
    // Register the synthetic keyboard-nav messages on first mount so
    // `messages.findIndex(...)` inside the hook returns a real index for
    // same-conv tests. Without this seeding, `findIndex` returns -1 and
    // the hook skips directly to the URL fallback branch — which doesn't
    // exercise the fast-path-vs-fallback split.
    //
    // Must run in useEffect (not render body) so we don't trigger
    // React's "setState in render of another component" warning.
    const { setMessages } = useKeyboardNavigation()
    useEffect(() => {
      if (registerMessages.length > 0) {
        setMessages(registerMessages)
      }
      // registerMessages is captured from the closure; intentionally
      // run once per Wrapper instance.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])
    return null
  }

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/conversations/${currentUuid}`]}>
          <KeyboardNavigationProvider>
            <MessagesSeed />
            <Routes>
              <Route path="/conversations/:uuid" element={<>{children}<LocationCapture /></>} />
              <Route path="*" element={<>{children}<LocationCapture /></>} />
            </Routes>
          </KeyboardNavigationProvider>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }

  return { Wrapper, locationSeen }
}

describe('useNavigateToMatch — A1 two-tier with silent-bail bug fixed', () => {
  let scrollIntoViewSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    // Per-test spy on Element.prototype.scrollIntoView so we can pin
    // WHICH path the hook took (fast-path scrolls directly; URL fallback
    // does not scroll from inside the hook).
    Element.prototype.scrollIntoView = vi.fn()
    scrollIntoViewSpy = vi.spyOn(Element.prototype, 'scrollIntoView')
  })

  afterEach(() => {
    scrollIntoViewSpy.mockRestore()
  })

  // ─────────────────────────────────────────────────────────────────────
  // Test 1 — Fast path lives. Same conv + real message + bubble
  // mounted → scrollIntoView called directly, URL unchanged.
  // ─────────────────────────────────────────────────────────────────────
  it('same-conv + bubble MOUNTED → fast path scrolls directly, URL unchanged', () => {
    // Seed a mounted bubble. The hook's querySelector finds it,
    // takes the fast path, scrolls in place.
    const bubble = document.createElement('div')
    bubble.setAttribute('data-message-uuid', MSG_X)
    document.body.appendChild(bubble)

    const { Wrapper, locationSeen } = makeWrapper(CONV_A, [
      { uuid: MSG_X, sender: 'assistant' },
    ])

    const { result } = renderHook(() => useNavigateToMatch(), { wrapper: Wrapper })

    const match = makeMatch({ conversationUuid: CONV_A, messageUuid: MSG_X })
    act(() => {
      result.current(match)
    })

    // Asserts (bidirectional):
    //   POSITIVE: scrollIntoView WAS called on the mounted element.
    //   NEGATIVE: URL stayed at the conversation root, no `?highlight=`.
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(1)
    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
    expect(locationSeen.current.pathname).toBe(`/conversations/${CONV_A}`)
    expect(locationSeen.current.search).toBe('')

    document.body.removeChild(bubble)
  })

  // ─────────────────────────────────────────────────────────────────────
  // Test 2 — THE BUG PIN. Same conv + real message + bubble NOT
  // mounted. Pre-fix: silent no-op (no scroll, no navigate). Post-fix:
  // falls through to URL navigation so ConversationPage's highlight
  // effect re-finds the bubble after its 100ms settle.
  // ─────────────────────────────────────────────────────────────────────
  it('same-conv + bubble NOT mounted → falls through to URL (the 15K-msg bug case)', () => {
    // Deliberately do NOT seed a bubble. The OLD fast path's
    // querySelector returned null here and hit the silent `return`.
    // The fix: when querySelector returns null, we fall through to
    // the navigate path.
    const { Wrapper, locationSeen } = makeWrapper(CONV_A, [
      { uuid: MSG_X, sender: 'assistant' },
    ])

    const { result } = renderHook(() => useNavigateToMatch(), { wrapper: Wrapper })

    const match = makeMatch({ conversationUuid: CONV_A, messageUuid: MSG_X })
    act(() => {
      result.current(match)
    })

    // Asserts (bidirectional):
    //   POSITIVE: URL acquired ?highlight=<uuid>. This is RED on
    //     pre-fix code where the silent `return` skipped navigate.
    //   NEGATIVE: hook did NOT call scrollIntoView (it couldn't —
    //     the element wasn't there; the highlight effect on the
    //     destination page handles the scroll, and is not under
    //     test here).
    expect(locationSeen.current.pathname).toBe(`/conversations/${CONV_A}`)
    expect(locationSeen.current.search).toBe(`?highlight=${MSG_X}`)
    expect(scrollIntoViewSpy).not.toHaveBeenCalled()
  })

  // ─────────────────────────────────────────────────────────────────────
  // Test 3 — cross-conv. Regression guard for the working path.
  // ─────────────────────────────────────────────────────────────────────
  it('cross-conv → navigates to the other conversation with ?highlight=', () => {
    // Currently viewing CONV_A; click takes us to CONV_B/MSG_X.
    // Cross-conv bypasses the fast path entirely (different UUID).
    const { Wrapper, locationSeen } = makeWrapper(CONV_A, [])

    const { result } = renderHook(() => useNavigateToMatch(), { wrapper: Wrapper })

    const match = makeMatch({ conversationUuid: CONV_B, messageUuid: MSG_X })
    act(() => {
      result.current(match)
    })

    expect(locationSeen.current.pathname).toBe(`/conversations/${CONV_B}`)
    expect(locationSeen.current.search).toBe(`?highlight=${MSG_X}`)
    expect(scrollIntoViewSpy).not.toHaveBeenCalled()
  })

  // ─────────────────────────────────────────────────────────────────────
  // Test 4 — `messageUuid === 'title'` is the sentinel for title-only
  // matches. Navigate to the conversation but NOT with ?highlight=
  // (there's no message bubble to flash). Regression guard.
  // ─────────────────────────────────────────────────────────────────────
  it("messageUuid === 'title' → navigates without ?highlight= (title-only match)", () => {
    const { Wrapper, locationSeen } = makeWrapper(CONV_A, [])

    const { result } = renderHook(() => useNavigateToMatch(), { wrapper: Wrapper })

    const match = makeMatch({ conversationUuid: CONV_B, messageUuid: 'title' })
    act(() => {
      result.current(match)
    })

    expect(locationSeen.current.pathname).toBe(`/conversations/${CONV_B}`)
    expect(locationSeen.current.search).toBe('')
    expect(scrollIntoViewSpy).not.toHaveBeenCalled()
  })

  // ─────────────────────────────────────────────────────────────────────
  // Test 5 — Compact-marker target via fast path.
  //
  // User report (2026-05-22): search hit lands on a /compact bubble.
  // My first fix wired `forceOpen` based on `?highlight=` URL param, but
  // the FAST PATH (same conv, bubble mounted) doesn't change the URL.
  // Result: the marker stayed collapsed despite the visible ring +
  // scroll. The fast path must also trigger expansion when the
  // target is a compact-marker.
  //
  // User-observable contract (per CLAUDE-TESTING §5.13):
  //   - Click a search hit whose UUID is a compact marker AND the
  //     marker is already in the DOM → the marker's panel must open
  //     (aria-expanded transitions to "true").
  //   - Regular MessageBubble targets (no data-compact-marker) must
  //     NOT receive a click — that would re-trigger their onClick
  //     selection logic spuriously.
  // ─────────────────────────────────────────────────────────────────────
  it('same-conv + compact-marker target → fast path opens the marker (pill clicked)', () => {
    // Build a compact-marker shape: outer div with both
    // data-message-uuid AND data-compact-marker, containing a pill
    // button. The pill's onClick toggles aria-expanded — the same
    // contract the real CompactMarker exposes.
    const marker = document.createElement('div')
    marker.setAttribute('data-message-uuid', MSG_X)
    marker.setAttribute('data-compact-marker', MSG_X)
    const pill = document.createElement('button')
    pill.setAttribute('data-compact-marker-pill', '')
    pill.setAttribute('aria-expanded', 'false')
    pill.addEventListener('click', () => {
      pill.setAttribute('aria-expanded', 'true')
    })
    marker.appendChild(pill)
    document.body.appendChild(marker)

    const { Wrapper } = makeWrapper(CONV_A, [
      { uuid: MSG_X, sender: 'assistant' },
    ])
    const { result } = renderHook(() => useNavigateToMatch(), { wrapper: Wrapper })

    const match = makeMatch({ conversationUuid: CONV_A, messageUuid: MSG_X })
    act(() => {
      result.current(match)
    })

    // POSITIVE: the marker pill must have been clicked → aria-expanded=true.
    expect(pill.getAttribute('aria-expanded')).toBe('true')
    // SANITY: scroll still happened (regression guard — we didn't
    // break the existing fast-path behavior for this branch).
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(1)

    document.body.removeChild(marker)
  })

  it('same-conv + regular bubble (non-marker) → fast path does NOT dispatch a click', () => {
    // Negative pair for the rule above: regular MessageBubble
    // targets do not carry data-compact-marker. The fast path
    // must NOT click them — it scrolls + flashes ring + returns.
    const bubble = document.createElement('div')
    bubble.setAttribute('data-message-uuid', MSG_X)
    // No data-compact-marker attribute.
    const clickSpy = vi.fn()
    bubble.addEventListener('click', clickSpy)
    document.body.appendChild(bubble)

    const { Wrapper } = makeWrapper(CONV_A, [
      { uuid: MSG_X, sender: 'assistant' },
    ])
    const { result } = renderHook(() => useNavigateToMatch(), { wrapper: Wrapper })

    const match = makeMatch({ conversationUuid: CONV_A, messageUuid: MSG_X })
    act(() => {
      result.current(match)
    })

    expect(clickSpy).not.toHaveBeenCalled()
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(1)

    document.body.removeChild(bubble)
  })

  // 2026-10-23: the newest search jump wins (lib/searchNavigation.ts).
  it('a fast-path jump makes the URL highlight of an older jump stale', () => {
    __resetSearchNavigationForTests()
    const bubble = document.createElement('div')
    bubble.setAttribute('data-message-uuid', MSG_X)
    document.body.appendChild(bubble)
    const { Wrapper } = makeWrapper(CONV_A, [
      { uuid: MSG_X, sender: 'assistant' },
      { uuid: 'msg-late', sender: 'human' },
    ])
    const { result } = renderHook(() => useNavigateToMatch(), { wrapper: Wrapper })

    // Older jump: its bubble is not mounted, so it takes the URL path.
    act(() => result.current(makeMatch({ conversationUuid: CONV_A, messageUuid: 'msg-late' })))
    expect(isStaleUrlHighlight('msg-late')).toBe(false)

    // Newer jump: mounted bubble, fast path.
    act(() => result.current(makeMatch({ conversationUuid: CONV_A, messageUuid: MSG_X })))
    expect(isStaleUrlHighlight('msg-late')).toBe(true)

    document.body.removeChild(bubble)
  })
})
