/**
 * useMessageNavigationRegistry — keyboard-navigation registry +
 * selection-driven auto-scroll for the conversation pane.
 *
 * Three coupled effects, all keyed off the same data (conversation +
 * visibleMessages + showPrelude/showToolCalls filters):
 *
 *   1. UUID-change reset: when the route uuid changes (navigation
 *      to a different conversation), reset selectedMessageIndex to 0
 *      so we don't carry the previous conversation's selection over.
 *      A ref-based prev-uuid guard prevents the effect from firing
 *      on every render.
 *
 *   2. Registry sync: rebuild the keyboard navigation context's
 *      message list whenever conversation.messages, showToolCalls,
 *      or showPrelude change. The `setMessagesAndPinSelection`
 *      variant keeps the selected UUID stable across list resizes
 *      (Tools/prelude toggle changes list length but the user's
 *      focused message survives if it's still in the list).
 *
 *   3. Selection-driven scroll: when selectedMessageIndex changes
 *      AND focus is in the detail pane, scroll the target into
 *      view. Two paths:
 *        - Mounted bubble:   element.scrollIntoView (smooth/center).
 *        - Unmounted bubble: virtualizer.scrollToIndex (the row
 *          mounts via the normal path once the viewport reaches it,
 *          and the subsequent ref-based selection ring renders).
 *      The fallback is critical post-virtualization: Alt+< / Alt+>
 *      and search-hit jumps frequently target rows outside the
 *      ±5-row overscan window. Pinned by
 *      `e2e/keyboard-nav-alt-jump-virtualizer-recovery.spec.ts`.
 *
 * The three effects share data so they live together. Splitting them
 * would either duplicate dependency lists or push them all through
 * a shared internal hook.
 *
 * Extracted from ConversationPage.tsx (2026-05-31, Commit 7a of
 * PLANS/2026.05.31-conversationpage-decomposition.md). Behavior-preserving.
 */
import { useEffect, useRef, type RefObject } from 'react'
import type { Virtualizer } from '@tanstack/react-virtual'
import type { Message, ConversationDetail } from '@/lib/types'
import type { FocusArea, MessageInfo } from '@/contexts/KeyboardNavigationContext'
import { messageHasVisibleContent } from '@/lib/utils'

interface UseMessageNavigationRegistryArgs {
  /** Route uuid — when this flips, selectedMessageIndex resets to 0. */
  uuid: string | undefined
  /** May be undefined during initial load — effects early-return. */
  conversation: ConversationDetail | null | undefined
  /** The filtered list driving virtualizer's index space. */
  visibleMessages: readonly Message[]
  /** Whether the prelude markers are revealed. Filters the registry. */
  showPrelude: boolean
  /** Whether tool-only messages render. Filters the registry. */
  showToolCalls: boolean
  /** The active search hit's message (2026-10-21): registered although
   *  tool calls are hidden, like `computeVisibleMessages`. */
  revealUuid?: string | null
  /** Active focus area. Selection auto-scroll only fires when 'detail'. */
  focusArea: FocusArea
  /** Owned by ConversationPage; uuid → DOM node lookup for scrollIntoView. */
  messageRefs: RefObject<Map<string, HTMLDivElement>>
  /** Virtualizer instance for the unmounted-target fallback path. */
  virtualizer: Virtualizer<HTMLDivElement, Element>
  /** Returns the currently selected message uuid, or null. */
  getSelectedMessageId: () => string | null
  /** Drives the selection-scroll effect's dep list. */
  selectedMessageIndex: number
  /** Reset to 0 on uuid change. */
  setSelectedMessageIndex: (i: number) => void
  /** Used in the cleanup branch of the registry effect (clears on unmount). */
  setMessages: (msgs: MessageInfo[]) => void
  /** Pin-selection variant used on every list rebuild. */
  setMessagesAndPinSelection: (msgs: MessageInfo[]) => void
}

export function useMessageNavigationRegistry({
  uuid,
  conversation,
  visibleMessages,
  showPrelude,
  showToolCalls,
  revealUuid = null,
  focusArea,
  messageRefs,
  virtualizer,
  getSelectedMessageId,
  selectedMessageIndex,
  setSelectedMessageIndex,
  setMessages,
  setMessagesAndPinSelection,
}: UseMessageNavigationRegistryArgs): void {
  // 1) Reset selected message index when a new conversation is opened.
  const prevUuidRef = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (uuid && uuid !== prevUuidRef.current) {
      prevUuidRef.current = uuid
      setSelectedMessageIndex(0)
    }
  }, [uuid, setSelectedMessageIndex])

  // 2) Register visible messages with keyboard navigation context.
  // Issue #2: when the list size changes (e.g. the user toggled the
  // Tools button so tool-only messages appear/disappear), we use the
  // pin-selection variant so the selected message UUID stays the
  // same across the resize instead of drifting to a different
  // message at the same numeric index.
  useEffect(() => {
    if (conversation?.messages) {
      // V1 polish (2026-05-12, council round 2): also exclude `is_prelude`
      // messages when the prelude is collapsed, so arrow-key navigation
      // doesn't try to focus a hidden bubble. When the user clicks "show"
      // the affordance, showPrelude flips and this re-runs, re-including
      // the prelude rows.
      const messageInfos: MessageInfo[] = conversation.messages
        .filter((msg) => {
          if (!showPrelude && msg.is_prelude) return false
          if (revealUuid && msg.uuid === revealUuid) return true
          return messageHasVisibleContent(msg, showToolCalls)
        })
        .map((msg) => ({
          uuid: msg.uuid,
          sender: msg.sender,
        }))
      setMessagesAndPinSelection(messageInfos)
    }
    return () => {
      setMessages([])
    }
  }, [conversation?.messages, showToolCalls, showPrelude, revealUuid, setMessages, setMessagesAndPinSelection])

  // 3) Auto-scroll to selected message.
  //
  // The ref lookup ONLY works for currently-mounted messages. With the
  // virtualizer in play (visibleMessages can be 600+ rows on long CC
  // sessions), any jump that targets an off-screen row — including
  // Alt+< (selectFirstMessage) after paging far down, or Alt+>
  // (selectLastMessage) on first selection — finds `undefined` in
  // messageRefs.current and silently no-ops. The viewport stays
  // wherever it was, the selection ring disappears from view, and the
  // user has to scroll back to find what they jumped to.
  //
  // Fix: when the ref lookup misses, fall back to
  // `virtualizer.scrollToIndex(visIdx, { align: 'center' })`. The
  // virtualizer scrolls without needing the target mounted; the row
  // mounts via the normal path once the viewport reaches it, and the
  // subsequent ref-based selection ring renders automatically. Pinned
  // in tests/keyboard-nav-alt-jump-virtualizer-recovery.spec.ts.
  //
  // Recovery 2026-05-30 REG-6: gate the auto-scroll on a real
  // `selectedMessageIndex` change.
  //
  // Before the gate, the effect also fired whenever `visibleMessages`
  // identity churned (e.g. the Show Compactions toggle reflows
  // `compactMarkers` → recomputes `visibleMessages`) OR `focusArea`
  // flipped to 'detail' (the outer `<div onClick={() =>
  // setFocusArea('detail')}>` in ConversationPage fires on the
  // header-checkbox click via event bubbling). Both edges yanked the
  // user back to the index-0 default selection (a message at scrollTop
  // ≈ 0) even though the user never touched the keyboard. Pinned by
  // `e2e/toggle-preserves-focus-scroll.spec.ts::NEGATIVE PAIR`.
  //
  // First-run handling: on the FIRST time the effect proceeds past the
  // focusArea/conversation guards, we record the current index without
  // scrolling. This avoids the toggle-yank (the click-bubble path
  // flips focusArea AND fires this first-run path immediately) while
  // preserving the user-driven scroll: when the user later presses
  // Alt+< / Alt+>, click navigates a bubble, or Cmd+G jumps to a
  // search hit, `selectedMessageIndex` actually changes from the
  // recorded value → effect scrolls. Deep-link `?highlight=<uuid>`
  // navigation is owned by `useScrollToHighlight`, which calls its
  // own virtualizer.scrollToIndex BEFORE the selectedMessageIndex
  // update reaches this hook — so the deep-link UX is unaffected.
  //
  // We keep the full dep list so the effect re-runs when the
  // virtualizer / visibleMessages / messageRefs identities change —
  // the prev-index guard is the body-level filter that converts those
  // re-runs into no-ops. visibleMessages and messageRefs are STILL
  // read inside the body (when an Alt+jump triggers a real index
  // change), so removing them from the deps would make them stale.
  const prevSelectedMessageIndexRef = useRef<number | null>(null)
  useEffect(() => {
    if (focusArea !== 'detail' || !conversation?.messages) return
    const prev = prevSelectedMessageIndexRef.current
    prevSelectedMessageIndexRef.current = selectedMessageIndex
    if (prev === null || prev === selectedMessageIndex) return
    const selectedId = getSelectedMessageId()
    if (!selectedId) return
    const element = messageRefs.current.get(selectedId)
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    const visIdx = visibleMessages.findIndex((m) => m.uuid === selectedId)
    if (visIdx >= 0) {
      virtualizer.scrollToIndex(visIdx, { align: 'center' })
    }
  }, [
    selectedMessageIndex,
    focusArea,
    conversation?.messages,
    getSelectedMessageId,
    visibleMessages,
    virtualizer,
    messageRefs,
  ])
}
