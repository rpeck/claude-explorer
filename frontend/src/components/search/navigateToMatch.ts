import { useCallback, useMemo } from 'react'
import { useNavigate, useLocation } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/queryClient'
import { api } from '@/lib/api'
import { scrollBubbleIntoView } from '@/lib/scrollBubbleIntoView'
import { beginSearchNavigation } from '@/lib/searchNavigation'
import { useKeyboardNavigation } from '@/contexts/KeyboardNavigationContext'
import type { SearchMatch } from '@/contexts/SearchPanelContext'

/**
 * Shared navigation logic for search matches. Two-tier strategy:
 *
 *   1. **Fast path** (same conversation, bubble already mounted): scroll
 *      to the bubble in place via `scrollIntoView`, flash a yellow ring
 *      for 2 s, and skip URL navigation. Lowest-latency outcome for the
 *      common case and avoids transient `?highlight=` in the URL bar.
 *
 *   2. **URL fallback** (cross-conversation, OR same-conversation but
 *      bubble not yet mounted): `navigate('/conversations/<uuid>?highlight=<uuid>')`.
 *      `ConversationPage`'s highlight effect (routes/ConversationPage.tsx
 *      ~363-406) owns scroll + ring-flash + focus + URL cleanup.
 *
 * The fast path's "fall through to URL when the bubble isn't mounted"
 * behavior is the bug fix shipped on 2026-05-20. The previous version
 * `return`ed unconditionally after the keyboard-nav state update, even
 * when the synchronous `document.querySelector` returned `null` (React
 * 19 hadn't committed the focus state change yet on large
 * conversations). Result: clicking a search hit on a 15K-message
 * conversation was a no-op. Now the synchronous query is treated as
 * opportunistic — when it fails we hand off to the URL path so the
 * highlight effect can find the bubble after its 100 ms settle.
 *
 * The ring-flash inside the fast path uses a raw `setTimeout` (NOT
 * `useUnmountSafeTimer`) so rapid back-to-back navigations (Cmd+G
 * cycling matches) create independent ring-clear timers — without
 * that, the second click would cancel the first's clear timer and the
 * first match's ring would stick until unmount. Pinned by
 * `e2e/search-auto-focus.spec.ts` "does NOT yank the user back to
 * first match after Cmd+G navigation".
 *
 * Council decision history (2026-05-20): an initial pass collapsed to
 * a single URL path (Option A2). That broke two existing e2e
 * contracts: `search-focus-model` (highlight effect always calls
 * `element.focus()` — steals focus from search input on Cmd+G) and
 * `search-auto-focus` (the highlight effect's shared
 * `scheduleHighlightClear` cancels prior ring-clear timers). Reverted
 * to A1 (this two-tier strategy) after a 3-persona unanimous re-vote.
 * The fast path's accessibility gap (no `.focus()` call on
 * same-conv hits) is a pre-existing behavior preserved by the
 * `search-focus-model` test; a follow-up should address it without
 * the focus-stealing side effect.
 */
/**
 * Options accepted by the navigateToMatch callback.
 *
 *   - `focus` (default `true`): whether to move DOM focus onto the
 *     target message bubble. The auto-promote effect in
 *     SearchPanelContext passes `false` so typing in the search input
 *     isn't interrupted mid-keystroke (live-preview UX). Cmd+G / Enter
 *     / card-click pass `true` so the bubble owns focus and Cmd+C
 *     copies the message body.
 *
 * Implementation:
 *   - Same-conv fast path: if `focus` is true, calls `element.focus()`
 *     after the scroll + ring class.
 *   - URL fallback: appends `&focus=0` to the URL when `focus` is
 *     false. ConversationPage's highlight effect reads that param and
 *     skips its own `element.focus()` call. (Default — no `focus`
 *     param — preserves the pre-existing "always focus" behavior, so
 *     deep-link URLs from outside the app still focus the bubble.)
 */
export interface NavigateToMatchOptions {
  focus?: boolean
}

export function useNavigateToMatch() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { setFocusArea, setSelectedMessageIndex, messages, setNavSource } =
    useKeyboardNavigation()

  const currentUuid = useMemo(() => {
    const match = location.pathname.match(/\/conversations\/(.+)/)
    return match?.[1]
  }, [location.pathname])

  const navigateToMatch = useCallback(
    (match: SearchMatch, opts?: NavigateToMatchOptions) => {
      const focus = opts?.focus ?? true
      const isSameConversation = match.conversationUuid === currentUuid
      const hasRealMessage = match.messageUuid && match.messageUuid !== 'title'

      // Record that this navigation originated from the search panel, so
      // Escape from the detail pane returns focus to the panel rather than
      // the left sidebar.
      setNavSource('search')

      if (isSameConversation && hasRealMessage) {
        // Fast path attempt: only succeeds when the bubble is already
        // committed to the DOM. Otherwise we fall through to the URL
        // navigation branch below.
        const msgIdx = messages.findIndex((m) => m.uuid === match.messageUuid)
        if (msgIdx !== -1) {
          setSelectedMessageIndex(msgIdx)
          setFocusArea('detail')
          const element = document.querySelector<HTMLElement>(
            `[data-message-uuid="${match.messageUuid}"]`
          )
          if (element) {
            // Distance-gated scroll + post-settle correction. See
            // `scrollBubbleIntoView` docstring for the 15K-msg
            // layout-shift bug this fixes.
            // The newest jump wins: an older jump's pending ?highlight=
            // is now stale (lib/searchNavigation.ts).
            beginSearchNavigation(null)
            scrollBubbleIntoView(element)
            element.classList.add('ring-2', 'ring-yellow-400', 'ring-offset-2')
            // 2026-05-23: when the caller asks for focus (user-initiated
            // navigation — Cmd+G, Enter, card-click), move DOM focus
            // onto the bubble so Cmd+C copies the message body. The
            // bubble has tabIndex={-1} so .focus() works without
            // joining the tab order. Auto-promote callers pass
            // `focus: false` so typing in the search input is not
            // interrupted mid-keystroke.
            if (focus) {
              element.focus()
            }
            setTimeout(() => {
              element.classList.remove(
                'ring-2',
                'ring-yellow-400',
                'ring-offset-2'
              )
            }, 2000)
            // 2026-05-22 (compact-marker auto-open fix): if the
            // target is a /compact bubble, click the pill to expand
            // its summary panel so the user can see the matched
            // content. ConversationPage's URL-based `forceOpen`
            // wiring doesn't fire here because the fast path never
            // updates the URL. Skip the click when the pill is
            // already open so we don't toggle a user-opened marker
            // closed.
            if (element.hasAttribute('data-compact-marker')) {
              const pill = element.querySelector<HTMLButtonElement>(
                '[data-compact-marker-pill]',
              )
              if (pill && pill.getAttribute('aria-expanded') !== 'true') {
                pill.click()
              }
            }
            return
          }
          // Element not in DOM (e.g. React 19 concurrent render hasn't
          // committed yet on a large conversation, or the bubble's
          // MessageBubble returned null due to filter state churn).
          // Fall through to the URL fallback so ConversationPage's
          // highlight effect (100 ms settle + querySelector) can find
          // the bubble. THIS is the load-bearing change for the
          // 2026-05-20 fix: pre-fix, the code unconditionally
          // `return`ed here and the click became a silent no-op.
        }
      }

      // URL fallback: cross-conversation hops, OR same-conversation
      // hits that couldn't take the fast path. Warm the cache for the
      // cross-conversation case so the destination renders without a
      // loading skeleton.
      const isCached = !!queryClient.getQueryData(
        queryKeys.conversations.detail(match.conversationUuid)
      )
      if (!isCached) {
        queryClient.prefetchQuery({
          queryKey: queryKeys.conversations.detail(match.conversationUuid),
          queryFn: () => api.getConversation(match.conversationUuid),
          staleTime: Infinity,
        })
      }

      // 2026-05-23: append `&focus=0` only when the caller opted out
      // of focus. Omitting the param entirely (default) preserves the
      // pre-existing behavior for deep-link URLs (someone pastes
      // `/conversations/<uuid>?highlight=<msg>` into the address bar)
      // — they still get focus on the bubble.
      const focusSuffix = focus ? '' : '&focus=0'
      beginSearchNavigation(hasRealMessage ? match.messageUuid : null)
      navigate(
        `/conversations/${match.conversationUuid}${
          hasRealMessage
            ? `?highlight=${match.messageUuid}${focusSuffix}`
            : ''
        }`
      )
    },
    [
      currentUuid,
      messages,
      setSelectedMessageIndex,
      setFocusArea,
      setNavSource,
      queryClient,
      navigate,
    ]
  )

  return navigateToMatch
}
