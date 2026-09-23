import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useParams, useSearchParams } from 'react-router'
import { useVirtualizer } from '@tanstack/react-virtual'
import { toast } from 'sonner'
import { errorToast } from '@/lib/errorToast'
import { useQueryClient } from '@tanstack/react-query'
import { invalidateAfterFetch, queryKeys } from '@/lib/queryClient'
import { useConversation } from '@/hooks/useConversations'
import { useSettings } from '@/contexts/SettingsContext'
import { useKeyboardNavigation } from '@/contexts/KeyboardNavigationContext'
import {
  MANUAL_SCROLL_SENTINEL_UUID,
  useSearchPanel,
} from '@/contexts/SearchPanelContext'
import { useBookmarks } from '@/contexts/BookmarkContext'
import { TreeViewModal } from '@/components/branch/TreeViewModal'
import { MarkdownExportDialog } from '@/components/conversation/MarkdownExportDialog'
import { ConversationToolbar } from '@/components/conversation/ConversationToolbar'
import { ConversationViewerScrollControls } from '@/components/conversation/ConversationViewerScrollControls'
import { ConversationHeader } from '@/components/conversation/ConversationHeader'
import { ConversationMessageStream } from '@/components/conversation/ConversationMessageStream'
import { cn, computeVisibleMessages } from '@/lib/utils'
import { api } from '@/lib/api'
import { ApiError } from '@/lib/types'
import { scrollBubbleIntoView } from '@/lib/scrollBubbleIntoView'
import { useUnmountSafeTimer } from '@/hooks/useUnmountSafeTimer'
import { useBracketCompactNav } from '@/hooks/useBracketCompactNav'
import { useScrollToHighlight } from '@/hooks/useScrollToHighlight'
import { useSearchMatchHighlighting } from '@/hooks/useSearchMatchHighlighting'
import { useConversationCopyAndExports } from '@/hooks/useConversationCopyAndExports'
import { useExpandAllToolsAnchor } from '@/hooks/useExpandAllToolsAnchor'
import { useMessageNavigationRegistry } from '@/hooks/useMessageNavigationRegistry'
import { useBookmarkHotkey } from '@/hooks/useBookmarkHotkey'

export function ConversationPage() {
  const { uuid } = useParams<{ uuid: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const highlightMessageId = searchParams.get('highlight') || searchParams.get('m')
  // 2026-05-23: when `?focus=0` is present, navigate + scroll + ring
  // but DO NOT move DOM focus to the bubble. This is how the search
  // auto-promote effect (live-preview UX while the user is typing)
  // opts out of focus-steal. Default behavior (no param OR `focus=1`)
  // preserves the pre-existing always-focus behavior for deep-link
  // URLs and user-initiated Cmd+G / Enter / click-on-card navigation.
  const shouldFocusOnHighlight = searchParams.get('focus') !== '0'
  const branchLeaf = searchParams.get('leaf') || undefined
  const { data: conversation, isLoading, error } = useConversation(uuid || '', branchLeaf)
  const {
    showToolCalls,
    setShowToolCalls,
    expandAllTools,
    setExpandAllTools,
    hideCompactMarkers,
    setHideCompactMarkers,
  } = useSettings()
  // V1 polish 2026-05-24 (Bug 2) — unified toggle: the conversation
  // header's "Show Compactions" checkbox (whose underlying pref is
  // `hideCompactMarkers`) drives BOTH viewer visibility AND export
  // inclusion. The previous separate `export.includeCompactContent`
  // pref is removed. Mapping: includeCompact = !hideCompactMarkers.
  const includeCompactInExports = !hideCompactMarkers
  const { toggleBookmark } = useBookmarks()
  const queryClient = useQueryClient()
  const [isRefetching, setIsRefetching] = useState(false)
  const {
    isOpen: isSearchPanelOpen,
    query: searchPanelQuery,
    activeMatchIndex,
    flatMatches,
    demonstratedFocusUuidRef,
    markDemonstratedFocus,
    clearDemonstratedFocus,
  } = useSearchPanel()
  // 2026-05-24 + 2026-05-31 (Commit 3 of decomposition plan): derive
  // the active-match UUID + deferred query in useSearchMatchHighlighting.
  // The hook reads its inputs from the page's already-extant
  // useSearchPanel() destructure rather than calling useSearchPanel()
  // itself, so the page still has exactly ONE subscription to the
  // SearchPanel provider. See the hook's docstring for the perf rationale
  // (active-match-only gating prevents the 15K-msg re-render storm).
  const { activeMatchUuid, deferredSearchQuery } = useSearchMatchHighlighting({
    query: searchPanelQuery,
    activeMatchIndex,
    flatMatches,
  })
  const {
    setMessages,
    setMessagesAndPinSelection,
    messages,
    selectedMessageIndex,
    setSelectedMessageIndex,
    getSelectedMessageId,
    getSelectedId,
    focusArea,
    setFocusArea,
  } = useKeyboardNavigation()
  const [isTreeOpen, setIsTreeOpen] = useState(false)
  const [markdownDialogOpen, setMarkdownDialogOpen] = useState(false)
  // 2026-05-31 (Commit 4 of decomposition plan): copy/export side-effects
  // moved into useConversationCopyAndExports (clipboard flags + 2 s timer
  // resets, PDF export pipeline with abort + spinner toast). The hook
  // returns the same prop names the existing ConversationToolbar already
  // consumes (`copiedAll`, `handleCopyAll`, `handleExportPdf`,
  // `isExportingPdf`) plus the details-collapsible callbacks
  // (`copiedUuid`, `copiedPath`, `onCopyUuid`, `onCopyPath`) that
  // Commit 5's ConversationHeader will consume.
  const {
    copiedAll,
    copiedUuid,
    copiedPath,
    handleCopyAll,
    onCopyUuid,
    onCopyPath,
    handleExportPdf,
    isExportingPdf,
  } = useConversationCopyAndExports({
    conversation,
    showToolCalls,
    includeCompactInExports,
  })
  // The highlight-clear timer (sets the URL parameter after the
  // ring-flash animation completes) is scheduled from inside the
  // highlight useEffect — see below.
  const scheduleHighlightClear = useUnmountSafeTimer()
  const [showScrollButton, setShowScrollButton] = useState(false)
  const [showTopButton, setShowTopButton] = useState(false)
  const [activeCompactIdx, setActiveCompactIdx] = useState<number | null>(null)
  // V1 polish (2026-05-12, council round 2): CC sessions that opened with
  // one or more /exit runs have a "prelude" of synthetic markers BEFORE
  // the first real user turn (each marker absorbs its canned-response
  // assistant via `assistant_canned_response_consumed`). We hide them by
  // default so scroll-to-top lands on the real conversation start, and
  // surface a click-to-reveal affordance above the stream.
  const [showPrelude, setShowPrelude] = useState(false)
  // Reset the toggle when navigating between conversations so the next
  // CC session also opens with the prelude hidden.
  // oxlint-disable-next-line react-doctor/no-derived-state-effect -- The "use a key prop instead" recommendation would unmount/remount the ENTIRE ConversationPage subtree on every uuid navigation. That would discard the virtualizer's measurement cache, the scroll position, every memoized bubble, and every open dialog. Resetting one boolean is cheaper than nuking the page. See PLANS/POSTMORTEM-search-typing-lag-2026-05-22.md for the perf cost of avoidable re-renders on this surface.
  useEffect(() => {
    setShowPrelude(false)
  }, [uuid])
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map())

  // P11.A11.1 cached-per-id ref-setter factory (2026-05-23). The previous
  // shape used inline arrow callbacks at the bubble-list .map() sites:
  //   ref={(el) => { if (el) messageRefs.current.set(uuid, el); else
  //                  messageRefs.current.delete(uuid) }}
  // React invokes the OLD ref callback with `null` and the NEW one with
  // the element whenever the ref callback's function IDENTITY changes,
  // and inline arrows change identity every parent render. At 4051
  // visible bubbles, one parent re-render fires 2N = 8102 ref-callback
  // invocations — and ConversationPage re-renders on every Settings /
  // SearchPanel / Keyboard context churn. The cached factory below
  // returns the SAME function per uuid across renders, so React's ref
  // reconciler treats unchanged-identity as a no-op.
  //
  // Cleanup contract: when the element detaches (`el === null`), we
  // delete BOTH the DOM ref entry AND the cached factory entry so the
  // cache doesn't grow unboundedly. This matters under virtualization
  // where rows mount/unmount during scroll and under hide-toggles
  // (Tools, prelude) that drop entries from `visibleMessages`.
  const refSettersRef = useRef<
    Map<string, (el: HTMLDivElement | null) => void>
  >(new Map())
  const getSetRef = useCallback((uuid: string) => {
    const existing = refSettersRef.current.get(uuid)
    if (existing) return existing
    const fn = (el: HTMLDivElement | null) => {
      if (el) {
        messageRefs.current.set(uuid, el)
      } else {
        messageRefs.current.delete(uuid)
        // Prevent unbounded cache growth for filterable / removable
        // lists. The next time this uuid appears (e.g. user toggles
        // Tools back on), getSetRef will lazily re-create the callback.
        refSettersRef.current.delete(uuid)
      }
    }
    refSettersRef.current.set(uuid, fn)
    return fn
  }, [])

  // Expand/Collapse-all-tools transition + scroll-anchor restoration
  // extracted into useExpandAllToolsAnchor (2026-05-31, Commit 6 of
  // decomposition plan). The hook owns the useTransition, the
  // expandAnchorBeforeRef anchor capture in `handleToggleExpandAll`,
  // AND the useLayoutEffect that restores scroll position via delta
  // math. See the hook docstring for the Issue 2 (long re-render) +
  // Issue 3 (anchor drift) UX rationale; the two-step protocol MUST
  // stay co-located inside the hook to preserve the layout-effect
  // timing relative to the anchor capture.
  const { handleToggleExpandAll, isExpandPending } = useExpandAllToolsAnchor({
    expandAllTools,
    setExpandAllTools,
    scrollAreaRef,
    messageRefs,
    getSelectedMessageId,
  })

  // Header-toggle scroll-pin (Show Tools / Show Compactions,
  // 2026-05-25). Defense-in-depth: under Chromium's CSS scroll-
  // anchoring + TanStack Virtual's spacer tracking, the browser usually
  // keeps the focused bubble centered across a toggle on its own.
  // Safari has weaker scroll-anchoring, and absolutely-positioned +
  // transform:translateY virtualizer rows defeat the heuristic in
  // some lazy-image cases. The user-observable bug: focused bubble
  // visibly jumps off-screen after toggling Show Compactions on a deep
  // search hit. See toggle-preserves-focus-scroll.spec.ts for the pin.
  //
  // Contract:
  //   - If a focused message exists (activeMatchUuid > highlightMessageId
  //     > keyboard-selected uuid) at toggle time, capture its uuid into
  //     pendingRecenterRef BEFORE the state flip.
  //   - One rAF after the next commit (the useEffect on the toggle
  //     props), look up the element by uuid and call
  //     scrollBubbleIntoView(el, forceInstant=true) if it's still
  //     mounted AND drift is > ±100 px.
  //   - If the bubble is GONE (compact-summary that just got hidden, or
  //     unmounted by overscan eviction): silent no-op. The user accepts
  //     the page settles where it lands; the focus ring disappears with
  //     the bubble, which is itself the user feedback.
  //
  // Why rAF instead of useLayoutEffect: TanStack Virtual's
  // useFlushSync:false means measurement updates happen via async
  // ResizeObserver microtasks AFTER the layout effect would fire. rAF
  // yields to that pipeline; the multi-shot correction in
  // scrollBubbleIntoView absorbs subsequent measurement settles.
  //
  // Why guard on the 100px threshold: when Chromium scroll-anchoring
  // already kept the bubble in place, an unconditional scrollIntoView
  // would cause a visible flicker.
  const pendingRecenterRef = useRef<string | null>(null)
  // Bug 2 (2026-05-26) — the last bubble the user explicitly clicked on
  // (or the MANUAL_SCROLL_SENTINEL_UUID if the user wheel/touch-
  // scrolled). Drives the post-toggle recenter target via the priority
  // chain in `markPendingRecenter`. Without it, a stale `activeMatchUuid`
  // (pointing at a search hit the user has since clicked AWAY from)
  // would target an about-to-be-hidden compaction row, leaving the user
  // wherever the virtualizer's reflow landed.
  //
  // Bug 3 (2026-05-26) — moved from a local `useRef` into
  // `SearchPanelContext` (as `demonstratedFocusUuidRef`) so the
  // auto-promote effect in the context can also gate on the same
  // signal: when the user has demonstrated focus (clicked or scrolled),
  // refetch-driven auto-promote is suppressed. The ref is the SAME
  // synchronous channel as before; only its declaration moved.
  //
  // Cleared on conversation change so a click in conv A doesn't bleed
  // recenter behavior into conv B. Within a conversation, every click
  // overwrites; no manual cleanup needed.
  useEffect(() => {
    clearDemonstratedFocus()
  }, [uuid, clearDemonstratedFocus])
  const markPendingRecenter = useCallback(() => {
    // Priority chain (Bug 2 fix, 2026-05-26):
    //   1. demonstratedFocusUuidRef — last explicit bubble click OR
    //      MANUAL_SCROLL_SENTINEL_UUID for a manual wheel/touch
    //      scroll. Highest priority because it's the user's most-
    //      recent intent. When the sentinel is set, the recenter
    //      effect's DOM lookup returns null and the toggle is a
    //      silent no-op (browser scroll-anchoring preserves the
    //      user's position).
    //   2. activeMatchUuid — current search-panel match (Cmd+G /
    //      card-click / auto-promote).
    //   3. highlightMessageId — ephemeral ?highlight= URL param.
    //
    // CRITICAL: we do NOT fall back to ``getSelectedMessageId()`` here.
    // The keyboard-selected message defaults to index 0 on
    // conversation load, which means EVERY conversation has a
    // selected-id from the moment it mounts — even when the user
    // never interacted with the conversation pane. Falling back to
    // that index-0 default would yank the user to the top of the
    // conversation on every toggle, breaking the "toggle preserves
    // mid-scroll reading position" contract pinned by
    // ``toggle-preserves-focus-scroll.spec.ts::NEGATIVE PAIR``.
    //
    // The recenter effect downstream is a no-op when target is null
    // (pendingRecenterRef stays null), so the toggle becomes a pure
    // visual reflow and the browser's CSS scroll-anchoring keeps the
    // user roughly where they were.
    const target =
      demonstratedFocusUuidRef.current ?? activeMatchUuid ?? highlightMessageId
    if (target) pendingRecenterRef.current = target
  }, [demonstratedFocusUuidRef, activeMatchUuid, highlightMessageId])

  // The post-toggle recenter effect was relocated below `visibleMessages`
  // and `virtualizer` definitions so the refs that read those values can
  // see them at first render. See `recenterEffect` block further down.

  const compactMarkers = useMemo(
    () => (hideCompactMarkers ? [] : conversation?.compact_markers ?? []),
    [conversation?.compact_markers, hideCompactMarkers]
  )

  const compactMarkerByUuid = useMemo(() => {
    const map = new Map<string, { marker: typeof compactMarkers[number]; index: number }>()
    compactMarkers.forEach((marker, index) => {
      map.set(marker.message_uuid, { marker, index })
    })
    return map
  }, [compactMarkers])

  const hasCompactMarkers = (conversation?.compact_markers ?? []).length > 0

  // V1 polish (2026-05-12, council round 2): prelude markers (leading
  // `is_prelude: true` rows on CC sessions that opened with /exit) are
  // hidden by default. The `SessionPreludeAffordance` button above the
  // stream toggles `showPrelude`, which un-filters them.
  //
  // We filter at the LIST level (not inside MessageBubble) so the keyboard
  // navigation registration and the scrollTop landing position both ignore
  // the hidden messages — otherwise scroll-to-top would land on a hidden
  // bubble and the user would still see the prelude dominate.
  const preludeHiddenCount = conversation?.prelude_hidden_count ?? 0
  // NIT-1 + keyboard-nav alignment (council follow-up, 2026-05-22):
  // delegate to the pure `computeVisibleMessages` helper in lib/utils
  // so the predicate is unit-testable AND keyboard-nav registration
  // (at L298-302 below) can share the exact same shape. Previously
  // this filter dropped only `is_prelude` messages, leaving empty
  // `<div onClick>` wrappers for tool-only messages when
  // `showToolCalls=false` — a clickable dead zone that no-op'd
  // because `messages.findIndex` returned -1.
  // 2026-05-24: build the UUID set from the CONVERSATION's full
  // compact_markers list (not the user-controlled `compactMarkers` which
  // collapses to [] when hideCompactMarkers=true). When the user toggles
  // "Show Compactions" OFF, computeVisibleMessages needs the full set
  // to know which messages to DROP entirely (otherwise the LLM summary
  // body falls through to messageHasVisibleContent and renders as a
  // raw user-prompt-styled bubble — the bug the user reported on
  // 2026-05-24).
  const allCompactMarkerUuidSet = useMemo(
    () =>
      new Set(
        (conversation?.compact_markers ?? []).map((m) => m.message_uuid),
      ),
    [conversation?.compact_markers],
  )
  const visibleMessages = useMemo(() => {
    if (!conversation?.messages) return []
    return computeVisibleMessages(conversation.messages, {
      showPrelude,
      showToolCalls,
      compactMarkerUuids: allCompactMarkerUuidSet,
      hideCompactSummaries: hideCompactMarkers,
    })
  }, [conversation?.messages, showPrelude, showToolCalls, allCompactMarkerUuidSet, hideCompactMarkers])

  // Virtualization (2026-05-23, the load-bearing perf fix) — see
  // PLANS/PERFORMANCE_BASELINE_2026-05-23.md. Pre-fix shape
  // `visibleMessages.map(<MessageBubble />)` rendered all 4051 bubbles
  // for the real-corpus conversation, costing ~8.5s of synchronous React
  // commit work per warm-switch and ~141K DOM nodes. We now render only
  // the visible window (~20 rows) via `@tanstack/react-virtual`. Same
  // library precedent: `components/conversation/ConversationList.tsx`
  // (sidebar).
  //
  // Variable-height handling: bubble heights vary ~50-5000px (1-line
  // text vs huge code/tool blocks). `estimateSize` provides the initial
  // total scroll height; `virtualizer.measureElement` (ResizeObserver
  // under the hood) corrects each row's measured size post-mount. The
  // estimate is a coarse average of observed bubble heights on the real
  // corpus; misses are absorbed by post-mount measurement + the
  // distance-gated `scrollBubbleIntoView` correction for highlight
  // targets.
  //
  // jsdom fallback: vitest renders ConversationPage in jsdom which has
  // no layout (getBoundingClientRect returns zeros, ResizeObserver is
  // a polyfill stub). The virtualizer would render 0 items there,
  // breaking the ~50 existing vitest tests that mount this page. Same
  // UA-sniff fallback as `ConversationList.tsx` (`isJsdom` early-render
  // path below); production is unaffected.
  //
  // Estimate value choice: 240px is the observed median bubble height
  // across the real-corpus a70251a5 conversation (sampled via
  // `getBoundingClientRect()` over 100 mounted bubbles in DevTools).
  // The virtualizer corrects after first mount via measureElement, so
  // the cost of estimate error is bounded to "scrollbar thumb jiggles
  // until you've scrolled past everything once" — not a correctness
  // issue.
  //
  // React 19 / React-Compiler note: same `react-hooks/incompatible-library`
  // opt-out as ConversationList. TanStack Virtual's API is not React
  // Compiler compatible at the time of writing; library-level constraint.
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Virtual API is not React Compiler compatible; same opt-out as ConversationList.tsx.
  const virtualizer = useVirtualizer({
    count: visibleMessages.length,
    getScrollElement: () => scrollAreaRef.current,
    estimateSize: () => 240,
    // Overscan 5 keeps a small mount buffer above + below the viewport
    // so smooth scrolling doesn't reveal blank gaps mid-frame. The
    // sidebar uses 8 because rows are 83px each (~1 viewport of safety
    // at 8 rows). Bubble rows are ~240px each, so 5 ≈ 1.3 viewports of
    // buffer at default 900px viewport — same safety budget for less
    // mount work.
    overscan: 5,
    // 2026-05-23: bubble uuids are stable across renders; using them
    // as React keys avoids the "duplicate stale row" cascade documented
    // in ConversationList.tsx (vi.key alignment). The virtualizer also
    // uses the same key internally for stable measurement caching.
    getItemKey: (index) => visibleMessages[index]?.uuid ?? index,
    // 2026-05-24: React 19 escalated `flushSync was called from inside
    // a lifecycle method` from a warning to a throw. TanStack Virtual's
    // default `useFlushSync: true` fires flushSync inside its onChange
    // callback (which runs from a ResizeObserver during render), which
    // throws under React 19 and triggers a route-transition rollback
    // (symptom: navigating to /settings flashes the page on then
    // bounces back to /conversations). The async-rerender path is the
    // React 19 compatible fix per TanStack's migration guide. Tradeoff
    // is a one-frame delay between measurement and re-render during
    // rapid scrolls — acceptable for a measurement-driven re-layout
    // that the user can't perceive at 60fps.
    useFlushSync: false,
  })

  // jsdom (vitest) doesn't run layout, so the virtualizer can't measure
  // rows and would render zero items — breaking every existing vitest
  // test that mounts ConversationPage. Detect jsdom by user-agent and
  // fall back to non-virtualized rendering. Real browsers don't match.
  // ResizeObserver-presence isn't reliable as a detector because
  // src/test/setup.ts polyfills it for cmdk's sake. Same pattern as
  // ConversationList.tsx.
  const isJsdom =
    typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent)

  const focusCompactMarker = useCallback((index: number) => {
    if (compactMarkers.length === 0) return
    const clamped = Math.max(0, Math.min(index, compactMarkers.length - 1))
    setActiveCompactIdx(clamped)
    const target = compactMarkers[clamped]
    if (!target) return
    const el = document.querySelector(`[data-compact-marker="${target.message_uuid}"]`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [compactMarkers])

  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    // Hunt #2: use currentTarget, which React types as the element the
    // handler is attached to (HTMLDivElement). e.target is the actual
    // event target (could be a descendant during bubbling) and was
    // previously cast with `as HTMLDivElement` — a runtime lie for any
    // scroll bubbled from a descendant.
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 200
    const isNearTop = scrollTop < 200
    setShowScrollButton(!isNearBottom)
    setShowTopButton(!isNearTop)
  }, [])

  // 2026-05-23 — virtualization landing: smooth `scrollTo` and
  // `scrollIntoView` are intercepted by the virtualizer's measurement-
  // driven scroll-height updates. Each ResizeObserver fire that grows
  // the total height during the animation cancels the in-flight smooth
  // scroll (observed: jump-to-top on a 30-msg conv froze ~280 px in
  // because the virtualizer was still settling rows mid-animation),
  // and the virtualizer's own smooth `scrollToIndex` has the same
  // single-pass issue — it computes the destination ONCE on call,
  // then doesn't re-aim when measurement adjusts the estimate.
  //
  // Trade smooth-scroll motion for correctness: do instant scroll-to-
  // index, then a follow-up correction tick once rows have mounted +
  // measured. The instant snap is acceptable for a "Jump" affordance
  // (the user explicitly asked for a hop, not a tour) and matches the
  // distance-gated branch in `scrollBubbleIntoView`. Fallback to the
  // pre-virt path on the empty-list case (no visible messages).
  const scrollToBottom = useCallback(() => {
    if (visibleMessages.length > 0) {
      virtualizer.scrollToIndex(visibleMessages.length - 1, { align: 'end' })
      // Post-mount re-aim: measureElement may grow the row beyond the
      // initial estimate, leaving us a few hundred px short of the
      // true bottom. A microtask + rAF tick gives the virtualizer one
      // chance to settle then snaps again. Bounded; not a chain.
      requestAnimationFrame(() => {
        if (scrollAreaRef.current) {
          scrollAreaRef.current.scrollTop = scrollAreaRef.current.scrollHeight
        }
      })
    } else {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
    }
  }, [virtualizer, visibleMessages.length])

  const scrollToTop = useCallback(() => {
    if (visibleMessages.length > 0) {
      virtualizer.scrollToIndex(0, { align: 'start' })
      requestAnimationFrame(() => {
        if (scrollAreaRef.current) scrollAreaRef.current.scrollTop = 0
      })
    } else {
      scrollAreaRef.current?.scrollTo({ top: 0, behavior: 'auto' })
    }
  }, [virtualizer, visibleMessages.length])

  // ─── Post-toggle recenter effect (Bug 2 fix, 2026-05-26) ─────────────
  // Placed AFTER ``visibleMessages`` + ``virtualizer`` so the ref-mirror
  // pattern below sees them at first render. Co-located with
  // ``markPendingRecenter`` upstream (the source-of-truth for the target
  // UUID) via the ``pendingRecenterRef`` channel. See
  // ``demonstratedFocusUuidRef`` (in SearchPanelContext) for the full
  // rationale.
  //
  // Reads `visibleMessages` and `virtualizer` via refs so they aren't
  // useEffect deps — otherwise the effect would re-fire on EVERY render
  // (visibleMessages changes identity often), defeating the
  // "fire-once-per-toggle" intent.
  const visibleMessagesRef = useRef(visibleMessages)
  visibleMessagesRef.current = visibleMessages
  const virtualizerRef = useRef(virtualizer)
  virtualizerRef.current = virtualizer
  useEffect(() => {
    const recenterUuid = pendingRecenterRef.current
    if (!recenterUuid) return
    pendingRecenterRef.current = null
    const rafId = requestAnimationFrame(() => {
      let el =
        messageRefs.current.get(recenterUuid) ??
        document.querySelector<HTMLElement>(
          `[data-message-uuid="${CSS.escape(recenterUuid)}"]`,
        )
      // Bug 2 (2026-05-26) follow-up: the target may be outside the
      // virtualizer's overscan window (the user clicked a bubble at
      // viewport edge; the toggle's reflow + scroll-anchoring shifted
      // the viewport so the bubble is now beyond the ±5-row overscan).
      // Tell the virtualizer to mount it, then re-query the DOM.
      // Without this, the user-clicked bubble after the compactions
      // toggle has a non-mounted DOM node → el is null → silent early
      // return → user stranded wherever the virtualizer's reflow
      // landed (the Bug 2 symptom).
      //
      // Skipped in jsdom (vitest) where the virtualizer isn't used —
      // the non-virtualized branch renders every row so querySelector
      // always finds the node.
      if (!el && !isJsdom) {
        const visIdx = visibleMessagesRef.current.findIndex(
          (m) => m.uuid === recenterUuid,
        )
        if (visIdx !== -1) {
          virtualizerRef.current.scrollToIndex(visIdx, { align: 'center' })
          // Re-query after the mount commits. measureElement may
          // still adjust the row's height after this, but the
          // scrollBubbleIntoView post-settle correction chain absorbs
          // that drift (250 / 750 / 1250 ms ticks).
          el =
            messageRefs.current.get(recenterUuid) ??
            document.querySelector<HTMLElement>(
              `[data-message-uuid="${CSS.escape(recenterUuid)}"]`,
            )
        }
      }
      if (!el) return // vanished focus target (filtered out)
      const container = scrollAreaRef.current
      if (!container) return
      const targetRect = el.getBoundingClientRect()
      const containerRect = container.getBoundingClientRect()
      const drift = Math.abs(
        targetRect.top + targetRect.height / 2 -
          (containerRect.top + containerRect.height / 2),
      )
      if (drift <= 100) return // already pinned by browser scroll-anchoring
      scrollBubbleIntoView(el, true)
    })
    return () => cancelAnimationFrame(rafId)
  }, [hideCompactMarkers, showToolCalls, isJsdom])

  // Keyboard-navigation registry + selection-driven auto-scroll extracted
  // into useMessageNavigationRegistry (2026-05-31, Commit 7a of decomposition
  // plan). Owns the three coupled effects: (1) reset selectedMessageIndex
  // when uuid changes, (2) rebuild the kbd-nav context on
  // conversation/showPrelude/showToolCalls change, (3) auto-scroll to the
  // selected message via ref-lookup + virtualizer fallback for off-screen
  // rows. See the hook docstring for the virtualization-recovery rationale.
  useMessageNavigationRegistry({
    uuid,
    conversation,
    visibleMessages,
    showPrelude,
    showToolCalls,
    focusArea,
    messageRefs,
    virtualizer,
    getSelectedMessageId,
    selectedMessageIndex,
    setSelectedMessageIndex,
    setMessages,
    setMessagesAndPinSelection,
  })

  // Keyboard: 'b' toggles bookmark on the focused message. Extracted
  // into useBookmarkHotkey (2026-05-31, Commit 7b of decomposition plan).
  useBookmarkHotkey({ conversation, getSelectedMessageId, toggleBookmark })

  // Keyboard: '[' / ']' navigate compact markers within the open
  // conversation. Extracted into useBracketCompactNav (P1.4 Commit A,
  // 2026-05-30) so this route loses ~25 lines of listener wiring. See
  // the hook for the Phase 2 perf rationale (useEffectEvent).
  useBracketCompactNav({
    compactMarkers,
    activeCompactIdx,
    focusCompactMarker,
  })

  // Scroll-to-highlight orchestration extracted into useScrollToHighlight
  // (P1.4 Commit B, 2026-05-30). The hook owns the 5-stage pipeline
  // (focus area + message index + virtualizer scrollToIndex + DOM rAF-poll +
  // ring-flash + 2s URL cleanup with race guard) plus the eslint-disable
  // rationale that previously bracketed this site. See the hook for the
  // virtualization-landing context and the council Q4 race-guard story.
  useScrollToHighlight({
    highlightMessageId,
    conversation,
    isLoading,
    setSearchParams,
    setFocusArea,
    messages,
    setSelectedMessageIndex,
    visibleMessages,
    virtualizer,
    isJsdom,
    shouldFocusOnHighlight,
    scheduleHighlightClear,
  })

  // When sidebar has focus and keyboard selection differs from displayed conversation,
  // show a hint instead of the (stale) conversation content
  const sidebarSelectedId = getSelectedId()
  const sidebarSelectionDiffers = focusArea === 'list' && sidebarSelectedId && sidebarSelectedId !== uuid

  if (!uuid || sidebarSelectionDiffers) {
    return <HintState />
  }

  if (isLoading) {
    return <LoadingState />
  }

  if (error || !conversation) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">
            Conversation not found
          </h2>
          <p className="text-sm text-zinc-500">
            The conversation you're looking for doesn't exist.
          </p>
        </div>
      </div>
    )
  }

  const handleForceRefetch = async () => {
    if (!conversation) return
    setIsRefetching(true)
    try {
      await api.forceRefetchConversation(conversation.uuid)
      await queryClient.invalidateQueries({ queryKey: queryKeys.conversations.detail(conversation.uuid) })
      await invalidateAfterFetch(queryClient)
      toast.success('Conversation re-downloaded.')
    } catch (e) {
      // Build-9 Bug 3: the backend returns FRIENDLY user copy in `detail`
      // for 404 / 401 / 503 (see backend/routers/fetch.py). The api layer
      // surfaces that as ApiError.message, so we can show it verbatim
      // instead of "Re-fetch failed: {\"detail\":\"...\"}".
      //
      // Hunt #2: narrow with `instanceof ApiError` instead of the prior
      // `as Error & { status?: number }` cast — the cast was a runtime
      // lie because catch sees `unknown`, and a non-Error throw (e.g.,
      // a thrown string from a future caller) would have crashed at
      // `.message` read. ApiError is the only typed throw site in
      // api.ts, so this also tightens the contract.
      const isApiErr = e instanceof ApiError
      const message = isApiErr
        ? e.message
        : (e instanceof Error ? e.message : 'Re-download failed.')
      // 404/401/503 messages are already actionable; don't offer Retry on
      // 404 (the conversation isn't coming back) or 401 (user must run
      // capture). Retry only on 5xx-ish unknown failures.
      const status = isApiErr ? e.status : undefined
      const allowRetry = status === undefined || (status >= 500 && status !== 503)
      errorToast(message, {
        retry: allowRetry ? handleForceRefetch : undefined,
      })
    } finally {
      setIsRefetching(false)
    }
  }

  // Phase 1 a11y: same passive-focus-area pattern as Sidebar. Clicking
  // anywhere in the detail pane marks 'detail' as the active focus
  // area for keyboard shortcuts. Tab-ing into the inner controls
  // (header buttons, bubbles, search panel) achieves the same routing
  // via their own focus handlers. No tabIndex on the wrapper.
  return (
    /* react-doctor-disable-next-line react-doctor/click-events-have-key-events,react-doctor/no-static-element-interactions */
    <div
      onClick={() => setFocusArea('detail')}
      className={cn(
        'flex h-full flex-col',
        focusArea === 'detail' && 'ring-2 ring-inset ring-blue-500/50'
      )}
    >
      {/* Header
          Layout note: at narrow widths (≤1366px) the right-side action
          cluster (Tools, Expand, Re-download, Hide compact markers,
          Copy as Markdown, Markdown, PDF) was tall enough that it
          collided with the conversation metadata row underneath the
          title. Stack the rows vertically (`flex-col gap-3`) so the
          title + metadata block can never share horizontal space with
          the action buttons; the buttons get their own row that
          `flex-wrap`s to a second line if it still overflows. */}
      <header className="flex flex-col gap-3 border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
        <ConversationHeader
          conversation={conversation}
          copiedUuid={copiedUuid}
          copiedPath={copiedPath}
          onCopyUuid={onCopyUuid}
          onCopyPath={onCopyPath}
          onOpenTree={() => setIsTreeOpen(true)}
        />
        <ConversationToolbar
          showToolCalls={showToolCalls}
          setShowToolCalls={setShowToolCalls}
          markPendingRecenter={markPendingRecenter}
          expandAllTools={expandAllTools}
          handleToggleExpandAll={handleToggleExpandAll}
          isExpandPending={isExpandPending}
          conversationSource={conversation.source}
          handleForceRefetch={handleForceRefetch}
          isRefetching={isRefetching}
          hasCompactMarkers={hasCompactMarkers}
          hideCompactMarkers={hideCompactMarkers}
          setHideCompactMarkers={setHideCompactMarkers}
          copiedAll={copiedAll}
          handleCopyAll={handleCopyAll}
          setMarkdownDialogOpen={setMarkdownDialogOpen}
          handleExportPdf={handleExportPdf}
          isExportingPdf={isExportingPdf}
        />
      </header>

      <ConversationMessageStream
        conversation={conversation}
        visibleMessages={visibleMessages}
        messages={messages}
        scrollAreaRef={scrollAreaRef}
        messagesEndRef={messagesEndRef}
        virtualizer={virtualizer}
        isJsdom={isJsdom}
        getSetRef={getSetRef}
        handleScroll={handleScroll}
        markDemonstratedFocus={markDemonstratedFocus}
        manualScrollSentinelUuid={MANUAL_SCROLL_SENTINEL_UUID}
        preludeHiddenCount={preludeHiddenCount}
        showPrelude={showPrelude}
        onTogglePrelude={() => setShowPrelude((v) => !v)}
        getSelectedMessageId={getSelectedMessageId}
        focusArea={focusArea}
        compactMarkerByUuid={compactMarkerByUuid}
        compactMarkers={compactMarkers}
        activeCompactIdx={activeCompactIdx}
        focusCompactMarker={focusCompactMarker}
        highlightMessageId={highlightMessageId}
        activeMatchUuid={activeMatchUuid}
        deferredSearchQuery={deferredSearchQuery}
        showToolCalls={showToolCalls}
        expandAllTools={expandAllTools}
        setSelectedMessageIndex={setSelectedMessageIndex}
        scrollControls={
          <ConversationViewerScrollControls
            showScrollButton={showScrollButton}
            showTopButton={showTopButton}
            scrollToTop={scrollToTop}
            scrollToBottom={scrollToBottom}
            isSearchPanelOpen={isSearchPanelOpen}
          />
        }
      />

      <MarkdownExportDialog
        open={markdownDialogOpen}
        onOpenChange={setMarkdownDialogOpen}
        conversationUuid={conversation.uuid}
        conversationName={conversation.name || 'conversation'}
      />

      {/* Tree View Modal */}
      {conversation.has_branches && (
        <TreeViewModal
          uuid={conversation.uuid}
          isOpen={isTreeOpen}
          onClose={() => setIsTreeOpen(false)}
          onSelectPath={(path) => {
            const leaf = path[path.length - 1]
            if (!leaf) return
            setSearchParams((prev) => {
              const next = new URLSearchParams(prev)
              next.set('leaf', leaf)
              return next
            })
          }}
        />
      )}
    </div>
  )
}

function HintState() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-center">
        <p className="text-sm text-zinc-500">
          Press <kbd className="mx-1 rounded border border-zinc-300 bg-zinc-100 px-1.5 py-0.5 font-mono text-xs dark:border-zinc-600 dark:bg-zinc-800">Enter</kbd> to open this conversation.
        </p>
      </div>
    </div>
  )
}

function LoadingState() {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
        <div className="flex-1">
          <div className="h-6 w-48 animate-pulse rounded bg-zinc-200 dark:bg-zinc-800" />
          <div className="mt-2 h-4 w-32 animate-pulse rounded bg-zinc-200 dark:bg-zinc-800" />
        </div>
      </header>
      <div className="flex-1 p-6">
        <div className="mx-auto max-w-3xl space-y-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className={`flex ${i % 2 === 0 ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`h-24 w-2/3 animate-pulse rounded-lg ${
                  i % 2 === 0 ? 'bg-blue-100 dark:bg-blue-900' : 'bg-zinc-100 dark:bg-zinc-800'
                }`}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}