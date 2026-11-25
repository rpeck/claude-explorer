/**
 * ConversationMessageStream — the scrollable message column, including:
 *
 *   - Cowork session-error banner (Cowork transcripts that ended in a
 *     fault expose `conversation.error` in the sidecar; we render
 *     the banner above the stream so the reader knows the transcript
 *     is incomplete).
 *   - ConversationLightboxProvider wrapper (binds the image-lightbox
 *     keyboard navigation context to this conversation's images).
 *   - Scroll-area `<div>` with `handleScroll` + demonstrated-focus
 *     capture handlers (`onWheelCapture`/`onTouchStartCapture` mark
 *     the user as "demonstrated focus" so the auto-promote refetch
 *     gate doesn't yank them back to the first search match).
 *   - SessionPreludeAffordance toggle for CC sessions that opened
 *     with /exit (the prelude markers are hidden by default).
 *   - Dual-path render:
 *       * jsdom (vitest) — non-virtualized `.map()` so the 386 existing
 *         vitest tests that mount ConversationPage still work.
 *       * Production / Playwright — virtualizer-driven absolute-
 *         positioned rows with a combined ref that runs both
 *         `virtualizer.measureElement` AND the cached-per-id
 *         `getSetRef` factory from the page (used by Expand/Collapse
 *         all tools' anchor capture).
 *   - `messagesEndRef` sentinel div at the bottom (target of
 *     `scrollToBottom` fallback when visibleMessages is empty).
 *
 * Critical ownership note: `scrollAreaRef` and `messagesEndRef` STAY
 * owned by ConversationPage. Both are read by page-level effects /
 * callbacks (the post-toggle recenter effect, scrollToTop/Bottom,
 * toolbar handlers) AND by this component. Forwarding them as props
 * keeps the page in charge of ref lifecycle without coupling the
 * stream to the page's other consumers.
 *
 * Extracted from ConversationPage.tsx (2026-05-31, Commit 8 of
 * PLANS/2026.05.31-conversationpage-decomposition.md). Behavior-preserving.
 */
import type { RefObject } from 'react'
import type { Virtualizer } from '@tanstack/react-virtual'
import { ConversationLightboxProvider } from '@/contexts/ConversationLightboxContext'
import { SessionPreludeAffordance } from '@/components/conversation/SessionPreludeAffordance'
import { renderBubbleRow } from '@/components/conversation/renderBubbleRow'
import type { Message, ConversationDetail, CompactMarker as CompactMarkerType } from '@/lib/types'
import type { FocusArea } from '@/contexts/KeyboardNavigationContext'

interface ConversationMessageStreamProps {
  // Data
  conversation: ConversationDetail
  visibleMessages: readonly Message[]
  messages: { uuid: string; sender: string }[]
  // Refs (owned by parent, forwarded)
  scrollAreaRef: RefObject<HTMLDivElement | null>
  messagesEndRef: RefObject<HTMLDivElement | null>
  // Virtualization
  virtualizer: Virtualizer<HTMLDivElement, Element>
  isJsdom: boolean
  // Ref factory + click/wheel handlers
  getSetRef: (uuid: string) => (el: HTMLDivElement | null) => void
  handleScroll: (e: React.UIEvent<HTMLDivElement>) => void
  markDemonstratedFocus: (uuid: string | null) => void
  manualScrollSentinelUuid: string
  // Prelude affordance
  preludeHiddenCount: number
  showPrelude: boolean
  onTogglePrelude: () => void
  // renderBubbleRow deps (the bag that travels into every bubble)
  getSelectedMessageId: () => string | null
  focusArea: FocusArea
  compactMarkerByUuid: Map<string, { marker: CompactMarkerType; index: number }>
  compactMarkers: readonly CompactMarkerType[]
  activeCompactIdx: number | null
  focusCompactMarker: (index: number) => void
  highlightMessageId: string | null
  activeMatchUuid: string | null
  deferredSearchQuery: string
  showToolCalls: boolean
  showCodeMode: boolean
  expandAllTools: boolean
  setSelectedMessageIndex: (i: number) => void
  // ScrollControls render slot (the absolute-positioned jump
  // buttons live inside the same `relative` wrapper as the
  // scroll-area, so they can pin to `bottom-6`).
  scrollControls: React.ReactNode
}

export function ConversationMessageStream({
  conversation,
  visibleMessages,
  messages,
  scrollAreaRef,
  messagesEndRef,
  virtualizer,
  isJsdom,
  getSetRef,
  handleScroll,
  markDemonstratedFocus,
  manualScrollSentinelUuid,
  preludeHiddenCount,
  showPrelude,
  onTogglePrelude,
  getSelectedMessageId,
  focusArea,
  compactMarkerByUuid,
  compactMarkers,
  activeCompactIdx,
  focusCompactMarker,
  highlightMessageId,
  activeMatchUuid,
  deferredSearchQuery,
  showToolCalls,
  showCodeMode,
  expandAllTools,
  setSelectedMessageIndex,
  scrollControls,
}: ConversationMessageStreamProps) {
  return (
    <>
      {/* D9 (Cowork): session-error banner. Cowork's audit log can
          end with a session-level fault recorded in sidecar.error
          (e.g. "The session ended unexpectedly."). Render once
          above the message stream so the reader knows the
          transcript is incomplete. */}
      {conversation.error && (
        <div
          data-testid="cowork-error-banner"
          role="alert"
          className="border-b border-amber-300 bg-amber-50 px-6 py-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
        >
          <span className="font-medium">Session ended with an error:</span>{' '}
          <span>{conversation.error}</span>
        </div>
      )}

      {/* Messages */}
      <ConversationLightboxProvider messages={conversation.messages}>
        <div className="relative flex-1 overflow-hidden">
          <div
            ref={scrollAreaRef}
            data-testid="message-stream"
            className="h-full overflow-y-auto p-6"
            onScroll={handleScroll}
            // Bug 3 (2026-05-26) — manual scroll demonstrates focus, so a
            // subsequent search refetch (e.g. Show Compactions toggle)
            // does NOT auto-promote the viewer back to the first match.
            // Listen to wheel + touchstart (user-initiated) and NOT to
            // scroll (fires on programmatic scrollIntoView too — would
            // false-positive on the search-hit landing path). Use Capture
            // variants so nested scroll regions (CompactMarker's expanded
            // body, etc.) don't swallow the event before we see it.
            onWheelCapture={() =>
              markDemonstratedFocus(manualScrollSentinelUuid)
            }
            onTouchStartCapture={() =>
              markDemonstratedFocus(manualScrollSentinelUuid)
            }
          >
            <div className="mx-auto max-w-3xl">
              <SessionPreludeAffordance
                hiddenCount={preludeHiddenCount}
                expanded={showPrelude}
                onToggle={onTogglePrelude}
              />
              {isJsdom ? (
                // jsdom fallback path (vitest only): render all bubbles
                // non-virtualized so the existing 386 vitest tests that
                // mount ConversationPage still work. The `space-y-6`
                // visual gap that the pre-virt code relied on is supplied
                // back here. Real browsers take the virtualized branch
                // below.
                <div className="space-y-6">
                  {visibleMessages.map((message) =>
                    renderBubbleRow(message, {
                      getSetRef,
                      getSelectedMessageId,
                      focusArea,
                      compactMarkerByUuid,
                      compactMarkers,
                      activeCompactIdx,
                      focusCompactMarker,
                      highlightMessageId,
                      activeMatchUuid,
                      deferredSearchQuery,
                      conversation,
                      showToolCalls,
                      showCodeMode,
                      expandAllTools,
                      messages,
                      setSelectedMessageIndex,
                      markDemonstratedFocus,
                    }),
                  )}
                </div>
              ) : (
                // Virtualized path (production / Playwright). Each rendered
                // row gets a combined ref that runs BOTH
                // `virtualizer.measureElement` (ResizeObserver-driven
                // height correction) AND our cached-per-id `getSetRef`
                // (anchor capture in Expand/Collapse all tools). Row
                // wrappers are absolutely positioned inside the total-size
                // spacer at the virtualizer-computed `translateY`.
                //
                // `space-y-6` is dropped because absolute-positioned
                // siblings ignore margin collapsing; each row's bottom
                // padding (`pb-6`) carries the 24 px gap into its measured
                // height instead — measureElement picks that up correctly.
                <div
                  style={{
                    height: `${virtualizer.getTotalSize()}px`,
                    width: '100%',
                    position: 'relative',
                  }}
                >
                  {virtualizer.getVirtualItems().map((vi) => {
                    const message = visibleMessages[vi.index]
                    if (!message) return null
                    // Combined ref: forward the DOM node to BOTH the
                    // virtualizer (so it measures the row) and getSetRef
                    // (so anchor capture / Expand-all-tools still has the
                    // wrapper ref).
                    const cachedSetRef = getSetRef(message.uuid)
                    const combinedRef = (el: HTMLDivElement | null) => {
                      virtualizer.measureElement(el)
                      cachedSetRef(el)
                    }
                    return (
                      <div
                        key={vi.key}
                        data-index={vi.index}
                        ref={combinedRef}
                        className="pb-6"
                        style={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          width: '100%',
                          transform: `translateY(${vi.start}px)`,
                        }}
                      >
                        {/* oxlint-disable-next-line react-doctor/no-render-in-render -- `renderBubbleRow` is a top-level helper in renderBubbleRow.tsx, NOT an inline component constructor. Extracting to a real `<BubbleRow ... />` would force a React.memo wrap to recover reconciliation parity, but its `deps` object carries fields whose identity churns every render (compactMarkerByUuid, conversation, messages array, callbacks). The component boundary would therefore re-render every row anyway while adding a memo-comparator cost per row. The current call-shape preserves the same reconciliation tree React already sees, with no extra component boundary. The compact/bubble inner components ARE memoized — that's where the perf savings live. */}
                        {renderBubbleRow(message, {
                          getSetRef: null, // already attached on wrapper above
                          getSelectedMessageId,
                          focusArea,
                          compactMarkerByUuid,
                          compactMarkers,
                          activeCompactIdx,
                          focusCompactMarker,
                          highlightMessageId,
                          activeMatchUuid,
                          deferredSearchQuery,
                          conversation,
                          showToolCalls,
                          showCodeMode,
                          expandAllTools,
                          messages,
                          setSelectedMessageIndex,
                          markDemonstratedFocus,
                          unwrapped: true, // suppress the inner ref-wrapper div
                        })}
                      </div>
                    )
                  })}
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          </div>

          {scrollControls}
        </div>
      </ConversationLightboxProvider>
    </>
  )
}
