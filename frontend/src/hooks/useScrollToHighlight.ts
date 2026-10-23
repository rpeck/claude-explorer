/**
 * useScrollToHighlight — multi-stage scroll-to-highlight orchestration.
 *
 * Extracted from ConversationPage.tsx (2026-05-30, P1.4 Commit B from
 * PLANS/2026.05.30-STRICT-CODE-QUALITY-REVIEW.md) verbatim — every line
 * of behavior is preserved. The hook owns the 5-stage pipeline that
 * fires whenever `highlightMessageId` flips:
 *
 *   1. setFocusArea('detail') + setSelectedMessageIndex(matchIdx)
 *   2. virtualizer.scrollToIndex(visIdx, { align: 'center' }) to mount
 *      the target row (post-virt: rows aren't mounted until requested).
 *   3. rAF-poll up to ~600 ms for the bubble's DOM node to appear.
 *   4. scrollBubbleIntoView (lazy-image-aware post-settle correction),
 *      ring-flash, focus (if shouldFocusOnHighlight), 2 s scheduled
 *      URL-cleanup that ALSO race-guards against newer navigations.
 *   5. cleanup on re-fire / unmount cancels the rAF chain.
 *
 * All deps are passed in — the hook reaches into no context internally
 * so unit tests can drive it directly. See useBracketCompactNav for the
 * lighter sibling extraction.
 *
 * 2026-05-23 virtualization landing: target bubbles deep in the
 * conversation (e.g. idx 550/600 of the search-hit-scroll fixture) are
 * NOT mounted on initial page load. Pre-virt, all bubbles were mounted
 * eagerly, so a 100 ms setTimeout was enough for React to commit and
 * `querySelector` to find them. Post-virt, the target exists only as
 * virtualizer state until we tell it to bring the row into view.
 *
 * Why polling instead of a component mount-effect (Option A "pure" from
 * the council): the cleanup callback (URL `setSearchParams`) belongs to
 * the parent's URL state, and the highlight target may be either
 * MessageBubble OR CompactMarker — threading symmetric props + cleanup
 * callbacks through both component types is broader-diff than the
 * polling approach with no functional benefit. The polling window is
 * bounded (600 ms ≪ the 2 s ring-flash duration) and only runs when
 * `highlightMessageId` is truthy (Cmd+G hits and deep-link navigation;
 * not the typing hot path).
 */
import { useEffect } from 'react'
import type { Virtualizer } from '@tanstack/react-virtual'
import type { SetURLSearchParams } from 'react-router'
import type { Message, ConversationDetail } from '@/lib/types'
import type { FocusArea, MessageInfo } from '@/contexts/KeyboardNavigationContext'
import { scrollBubbleIntoView } from '@/lib/scrollBubbleIntoView'
import { consumeStaleUrlHighlight, isStaleUrlHighlight } from '@/lib/searchNavigation'

interface UseScrollToHighlightArgs {
  highlightMessageId: string | null
  conversation: ConversationDetail | null | undefined
  isLoading: boolean
  setSearchParams: SetURLSearchParams
  setFocusArea: (area: FocusArea) => void
  /**
   * The keyboard-navigation registry's view of the conversation
   * (filtered for prelude / tool-only / compactions). Indexed by
   * `setSelectedMessageIndex`. Recovery 2026-05-30 REG-3: narrowed from
   * `readonly Message[]` to `readonly MessageInfo[]` (the narrower type
   * the call site actually passes). The hook reads only `m.uuid`, so
   * the broader type was a contract-lie; assigning `MessageInfo[]`
   * tripped the type-checker. Pick narrowing over widening every time.
   */
  messages: readonly MessageInfo[]
  setSelectedMessageIndex: (index: number) => void
  visibleMessages: readonly Message[]
  virtualizer: Virtualizer<HTMLDivElement, Element>
  isJsdom: boolean
  shouldFocusOnHighlight: boolean
  /**
   * Schedule a callback to fire after `delayMs` ms. Cleared on unmount.
   * Wrap with useUnmountSafeTimer() at the call site so the timer is
   * the right one for the consumer's lifecycle.
   */
  scheduleHighlightClear: (callback: () => void, delayMs: number) => void
}

export function useScrollToHighlight({
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
}: UseScrollToHighlightArgs): void {
  // react-doctor-disable-next-line react-doctor/no-cascading-set-state -- Phase 2: multi-stage scroll-to-highlight orchestration (focus area, message index, scroll position, ring flash, URL cleanup). The setState calls fire across multiple promise/rAF boundaries, NOT in a single render. useReducer would force consolidation that loses the timing semantics — each stage is meaningful on its own commit boundary (e.g. setSelectedMessageIndex commits before the rAF poll starts so the bubble's `isKeyboardSelected` is true when querySelector finds it).
  useEffect(() => {
    if (!highlightMessageId || !conversation || isLoading) return
    // 2026-10-23: a newer search jump already moved on. Skip this
    // highlight and remove it from the URL, or it would scroll back to
    // the older hit (lib/searchNavigation.ts).
    const dropStale = () => {
      consumeStaleUrlHighlight()
      setSearchParams((prev) => {
        if (prev.get('highlight') !== highlightMessageId && prev.get('m') !== highlightMessageId) {
          return prev
        }
        const next = new URLSearchParams(prev)
        next.delete('highlight')
        next.delete('m')
        next.delete('focus')
        return next
      }, { replace: true })
    }
    if (isStaleUrlHighlight(highlightMessageId)) {
      dropStale()
      return
    }
    // Focus the detail pane and select the highlighted message
    setFocusArea('detail')
    const msgIdx = messages.findIndex((m) => m.uuid === highlightMessageId)
    if (msgIdx !== -1) {
      setSelectedMessageIndex(msgIdx)
    }

    // Step 1: tell the virtualizer to mount the target's row. If the
    // target isn't in visibleMessages (e.g. filtered by Tools toggle),
    // we still try the querySelector path below — it'll just no-op.
    const visIdx = visibleMessages.findIndex((m) => m.uuid === highlightMessageId)
    if (visIdx !== -1 && !isJsdom) {
      virtualizer.scrollToIndex(visIdx, { align: 'center' })
    }

    // Step 2-4: poll for mount, then apply the existing highlight UX.
    // Cancelable via the cleanup return so a fast subsequent
    // navigation supersedes this one.
    let cancelled = false
    let rafId = 0
    const startedAt = performance.now()
    const POLL_BUDGET_MS = 600
    const tryFindAndApply = () => {
      if (cancelled) return
      // A newer search jump may start while this poll waits for the mount.
      if (isStaleUrlHighlight(highlightMessageId)) {
        dropStale()
        return
      }
      const element = document.querySelector<HTMLElement>(
        `[data-message-uuid="${highlightMessageId}"]`,
      )
      if (element) {
        // Distance-gated scroll + post-settle correction. See
        // `scrollBubbleIntoView` docstring for the 15K-msg lazy-image
        // layout-shift bug this fixes. Still runs post-virtualization
        // because virtualizer.scrollToIndex doesn't compensate for
        // images decoding into the swept region post-scroll.
        scrollBubbleIntoView(element)
        element.classList.add('ring-2', 'ring-yellow-400', 'ring-offset-2')
        // Cross-conversation Enter: see commit 113da97 council note.
        // The highlight effect runs after the new ConversationPage
        // mounts, so this is the safe place to move keyboard focus
        // too. Bubbles have tabIndex={-1}.
        //
        // 2026-05-23: gated on `?focus=0` opt-out. The search auto-
        // promote effect (live-preview UX) uses `&focus=0` so the
        // user can keep typing in the search input without focus
        // being stolen. User-initiated nav (Cmd+G / Enter / card-
        // click) omits the param and still focuses the bubble.
        if (shouldFocusOnHighlight) {
          element.focus()
        }
        scheduleHighlightClear(() => {
          element.classList.remove('ring-2', 'ring-yellow-400', 'ring-offset-2')
          // Clear highlight/m/focus params from URL but preserve
          // everything else. 2026-05-23 race guard (council Q4):
          // if a newer navigation already replaced the highlight
          // target while our 2s timer was pending, only clean up
          // the params if the CURRENT URL still references the
          // bubble we just flashed. Otherwise the cleanup would
          // wipe the newer navigation's `highlight=` and the next
          // navigation's ConversationPage effect would never fire.
          setSearchParams((prev) => {
            const stillOurs =
              prev.get('highlight') === highlightMessageId ||
              prev.get('m') === highlightMessageId
            if (!stillOurs) {
              return prev
            }
            const next = new URLSearchParams(prev)
            next.delete('highlight')
            next.delete('m')
            next.delete('focus')
            return next
          }, { replace: true })
        }, 2000)
        return
      }
      if (performance.now() - startedAt > POLL_BUDGET_MS) {
        // Bounded polling — give up rather than spin. Dev-only warn
        // would be noisy; the URL cleanup will still fire on the next
        // navigation. Silent no-op matches the pre-virt behavior when
        // the bubble wasn't findable.
        return
      }
      rafId = requestAnimationFrame(tryFindAndApply)
    }
    rafId = requestAnimationFrame(tryFindAndApply)

    return () => {
      cancelled = true
      cancelAnimationFrame(rafId)
    }
    // oxlint-disable-next-line react-doctor/exhaustive-deps -- same rationale as react-hooks/exhaustive-deps below; scheduleHighlightClear is a fresh closure per render and including it would re-fire the highlight pipeline on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- scheduleHighlightClear is the return value of useUnmountSafeTimer(), which is a fresh closure each render (no useCallback). Including it would re-fire this whole highlight/scroll/focus effect on every render while highlightMessageId is truthy, causing repeated scrollBubbleIntoView calls + URL mutations. The timer callback closes over `element` (DOM ref) and `setSearchParams` (already a dep); the schedule call itself is fire-and-forget. Same pattern as FilterContext.tsx:291, ManageFiltersModal.tsx:603, SearchPanel.tsx:109.
  }, [highlightMessageId, conversation, isLoading, setSearchParams, setFocusArea, messages, setSelectedMessageIndex, visibleMessages, virtualizer, isJsdom, shouldFocusOnHighlight])
}
