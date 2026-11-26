/**
 * renderBubbleRow — shared single-row renderer for the conversation pane.
 *
 * Used by both the virtualized path (production / Playwright) and the
 * jsdom fallback path (vitest). Keeping a single source of truth for the
 * row JSX prevents the two paths from drifting in subtle ways
 * (search-hit gate, selection click handler, compact-marker prop wiring).
 *
 * `unwrapped: true` skips the inner ref-wrapper div because the
 * virtualized path already wraps each row in an absolute-positioned
 * `translateY` container. `getSetRef` is set to `null` in that case
 * because the cached ref is attached on the outer wrapper.
 *
 * Extracted from ConversationPage.tsx (2026-05-31, Commit 1 of
 * PLANS/2026.05.31-conversationpage-decomposition.md). Behavior-preserving;
 * this stays a PLAIN FUNCTION (not a React component) on purpose. See
 * the `react-doctor-disable no-render-in-render` comment at the
 * ConversationPage call site for the full rationale — promoting to a
 * memoized `<BubbleRow />` would force a memo wrap that re-renders every
 * row anyway given the churning deps (compactMarkerByUuid, conversation,
 * messages array, callbacks), while adding a per-row memo-comparator cost.
 * The bubble/compact inner components ARE memoized; that's where the
 * perf savings live.
 */
import { MessageBubble } from '@/components/message/MessageBubble'
import { CompactMarker } from '@/components/conversation/CompactMarker'
import type { Message, ConversationDetail, CompactMarker as CompactMarkerType } from '@/lib/types'
import type { FocusArea } from '@/contexts/KeyboardNavigationContext'

export interface RenderBubbleRowDeps {
  getSetRef: ((uuid: string) => (el: HTMLDivElement | null) => void) | null
  getSelectedMessageId: () => string | null
  focusArea: FocusArea
  compactMarkerByUuid: Map<string, { marker: CompactMarkerType; index: number }>
  compactMarkers: readonly CompactMarkerType[]
  activeCompactIdx: number | null
  focusCompactMarker: (index: number) => void
  highlightMessageId: string | null
  /** UUID of the message owning the search-panel's active match
   *  (Cmd+G / card-click / auto-promote target). Stable while the
   *  user is reading; only the bubble matching this UUID gets the
   *  live searchQuery for inline <mark> decoration. See the
   *  activeMatchUuid comment on the consumer side for full rationale. */
  activeMatchUuid: string | null
  deferredSearchQuery: string
  conversation: ConversationDetail
  showToolCalls: boolean
  showCodeMode: boolean
  expandAllTools: boolean
  messages: { uuid: string; sender: string }[]
  setSelectedMessageIndex: (i: number) => void
  /** Bug 2 (2026-05-26) / Bug 3 (2026-05-26): when the user clicks a
   *  bubble, record the UUID via this callback so the next post-toggle
   *  recenter (`markPendingRecenter`) targets THIS bubble — not a
   *  stale `activeMatchUuid`. Lives in `SearchPanelContext` so the
   *  same signal also gates the auto-promote effect (Bug 3 suppression
   *  of refetch-driven yank-back). Synchronous (ref-backed) write
   *  inside the callback dodges the React batching race the alternative
   *  state-based fix would have introduced. */
  markDemonstratedFocus: (uuid: string | null) => void
  unwrapped?: boolean
}

export function renderBubbleRow(message: Message, deps: RenderBubbleRowDeps) {
  const {
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
    unwrapped,
  } = deps

  const selectedId = getSelectedMessageId()
  const isSelected = focusArea === 'detail' && message.uuid === selectedId
  const compactEntry = compactMarkerByUuid.get(message.uuid)

  if (compactEntry) {
    const { marker, index } = compactEntry
    const child = (
      <CompactMarker
        marker={marker}
        index={index}
        total={compactMarkers.length}
        isActive={activeCompactIdx === index}
        onPrev={() => focusCompactMarker(index - 1)}
        onNext={() => focusCompactMarker(index + 1)}
        // 2026-05-22: auto-expand when a search-hit highlight is
        // targeting this marker. Users who clicked through to a search
        // match inside a compact bubble's summary text would otherwise
        // land on the collapsed pill and see nothing. Survives
        // virtualization because the `useEffect([forceOpen])` inside
        // CompactMarker fires on mount when `forceOpen=true`.
        forceOpen={highlightMessageId === marker.message_uuid}
        // 2026-05-24 highlight-gate fix: pass the search query ONLY
        // to the active-match marker. Keyed on activeMatchUuid (the
        // sidebar's currently-selected match, stable while the user
        // reads) rather than highlightMessageId (ephemeral URL param
        // cleared after 2 s — would drop highlights mid-read).
        searchQuery={
          marker.message_uuid === activeMatchUuid ? deferredSearchQuery : ''
        }
      />
    )
    if (unwrapped) return child
    return (
      <div key={message.uuid} ref={getSetRef ? getSetRef(message.uuid) : undefined}>
        {child}
      </div>
    )
  }

  const bubble = (
    <MessageBubble
      message={message}
      isKeyboardSelected={isSelected}
      conversationId={conversation.uuid}
      conversationSource={conversation.source}
      agentId={conversation.agent_id ?? null}
      agentDescription={conversation.agent_description ?? null}
      showToolCalls={showToolCalls}
      showCodeMode={showCodeMode}
      expandAllTools={expandAllTools}
      // 2026-05-24 highlight-gate fix: pass the search query ONLY to
      // the active-match bubble (the one Cmd+G / card-click landed
      // on). Keyed on `activeMatchUuid` (the sidebar's currently-
      // selected match — stable while the user reads) rather than
      // `highlightMessageId` (ephemeral URL `?highlight=` param,
      // cleared after the highlight-effect's 2 s timer fires —
      // which used to drop yellow `<mark>`s mid-read). All other
      // bubbles get '' so their React.memo comparator returns true
      // on every debounce settle (preserves the 2026-05-23 perf fix:
      // without this gate, every keystroke flipped searchQuery for
      // 4014 bubbles → 8-9 s long task). The SearchPanel sidebar
      // still shows every match with its own highlights; this gate
      // controls only the in-conversation-pane `<mark>` decoration.
      searchQuery={message.uuid === activeMatchUuid ? deferredSearchQuery : ''}
    />
  )

  const onClickRow = () => {
    const idx = messages.findIndex((m) => m.uuid === message.uuid)
    if (idx !== -1) setSelectedMessageIndex(idx)
    // Bug 2 (2026-05-26): record the user's explicit focus target so the
    // next post-toggle recenter (in ConversationPage's
    // ``markPendingRecenter``) hits the bubble the user actually
    // clicked — not the stale ``activeMatchUuid`` from a prior search
    // hit. Ref-based synchronous write; no React state churn.
    //
    // Bug 3 (2026-05-26): same call also gates the auto-promote effect
    // in `SearchPanelContext` — a subsequent refetch (e.g. Show
    // Compactions toggle) sees the non-null ref and skips the
    // yank-back-to-first-match cycle.
    markDemonstratedFocus(message.uuid)
  }

  if (unwrapped) {
    // Caller already wrapped us in a virtualized div with a ref; we only
    // need an inner click target. Use a fragment-equivalent: a small
    // wrapper carrying the click handler, no ref.
    // Phase 1 a11y: this wrapper exists only to track demonstrated focus
    // for the search anti-yank logic; clicking a bubble counts as
    // "user demonstrated they're looking here". Keyboard users move
    // between bubbles via j/k and copy buttons inside <MessageBubble>;
    // adding tabIndex would add 4051 extra tab stops on a long
    // conversation, terrible UX.
    return (
      /* react-doctor-disable-next-line react-doctor/click-events-have-key-events,react-doctor/no-static-element-interactions */
      <div onClick={onClickRow}>{bubble}</div>
    )
  }
  // Phase 1 a11y: same demonstrated-focus tracking wrapper as above.
  return (
    /* react-doctor-disable-next-line react-doctor/click-events-have-key-events,react-doctor/no-static-element-interactions */
    <div
      key={message.uuid}
      ref={getSetRef ? getSetRef(message.uuid) : undefined}
      onClick={onClickRow}
    >
      {bubble}
    </div>
  )
}
