import { memo, useCallback, useEffect, useMemo, useState } from 'react'
import { User, Bot, ChevronsUpDown, Copy, Check, Star } from 'lucide-react'
import { MarkdownRenderer } from './MarkdownRenderer'
import { MessageAttachments } from './MessageAttachments'
import { SlashCommandBadge } from './SlashCommandBadge'
import { Button } from '@/components/ui/button'
import { useConversationLightbox } from '@/contexts/ConversationLightboxContext'
import { useBookmarks } from '@/contexts/BookmarkContext'
import { useCopyFeedback } from '@/hooks/useCopyFeedback'
import { cn, formatMessageTimestamp, messageToMarkdown, messageHasVisibleContent, isExcludableMarker } from '@/lib/utils'
import { dedupeImageFiles } from '@/lib/imageFiles'
import { errorToast } from '@/lib/errorToast'
import type { Message } from '@/lib/types'
import { collectCcImages } from './blocks/imageCollection'
import { ContentBlockList } from './blocks/ContentBlockRenderer'
import { revealedToolBlockIndexes } from '@/components/search/toolReveal'

interface MessageBubbleProps {
  message: Message
  isKeyboardSelected?: boolean
  conversationId?: string
  conversationSource?: 'CLAUDE_AI' | 'CLAUDE_CODE' | 'CLAUDE_COWORK'
  /** "Show tool calls" toggle from SettingsContext.
   *
   *  PROP, not context (2026-05-23 perf regression fix): the previous
   *  shape called `useSettings()` here. With 4014 bubbles in a 16K-
   *  message conversation, that meant 4014 direct
   *  `useContext(SettingsContext)` consumers. React's useContext
   *  invalidates EVERY consumer on provider value-identity change,
   *  bypassing React.memo. Pressing Cmd+F (which calls
   *  setRightPaneTab('search')) mutated the SettingsContext value
   *  identity and triggered ~3 s of synchronous bubble re-render
   *  before the search input could focus. By threading as a prop,
   *  the bubble participates in React.memo correctly: unrelated
   *  settings changes leave the prop identity-stable and the
   *  comparator returns true. ConversationPage owns the single
   *  useSettings() call at the top of its render and passes the
   *  value down. Default `false` matches the SettingsProvider initial
   *  state — tests and future Storybook fixtures can mount the bubble
   *  with no extra setup. */
  showToolCalls?: boolean
  /** "Expand all tools" toggle from SettingsContext. Same prop-not-
   *  context rationale as `showToolCalls` above. */
  expandAllTools?: boolean
  /** Active full-text search query (Issue 3 follow-up, 2026-05-20;
   *  scope-narrowed 2026-05-23).
   *
   *  Per the 2026-05-23 perf trace, ConversationPage now passes this
   *  ONLY to the bubble whose UUID matches the active
   *  `?highlight=<uuid>` target — all other bubbles receive `""`.
   *  That makes the typing-into-search hot path O(1) re-renders per
   *  debounce settle (one bubble's MarkdownRenderer re-walks markdown)
   *  instead of O(4000). The SearchPanel sidebar still lists every
   *  match with its own highlight; only the in-conversation-pane
   *  decoration is scoped to the active hit.
   *
   *  PROP, not context: same reasoning as `showToolCalls`. The
   *  comparator below includes `searchQuery` so changes to the
   *  active bubble's prop correctly re-render it.
   *
   *  Default `""` (no highlighting) when omitted. */
  searchQuery?: string
}

function MessageBubbleImpl({
  message,
  isKeyboardSelected = false,
  conversationId,
  conversationSource,
  showToolCalls = false,
  expandAllTools = false,
  searchQuery = '',
}: MessageBubbleProps) {
  // Render probe for e2e tests, in development builds only. An effect
  // without deps runs after each commit in which this bubble rendered,
  // and not when React.memo skips it. search-typing-lag.spec.ts counts
  // these to prove that a keystroke does not re-render the list
  // (2026-10-24: it measured wall-clock time before, which varies).
  useEffect(() => {
    if (!import.meta.env.DEV) return
    const w = window as unknown as { __messageBubbleRenders?: number }
    w.__messageBubbleRenders = (w.__messageBubbleRenders ?? 0) + 1
  })
  const isHuman = message.sender === 'human'
  const { isBookmarked, toggleBookmark } = useBookmarks()
  // Hunt #11: useCopyFeedback owns the 2s "Copied" indicator + the
  // setTimeout cleanup-on-unmount contract. The hook stores the timer
  // id in a useRef, clears it before re-arming, and clears it on
  // unmount — all pinned by useCopyFeedback.test.ts AND the
  // MessageBubble.test.tsx "timer cleanup on unmount" suite.
  const { copied, trigger: triggerCopied } = useCopyFeedback()
  const bookmarked = conversationId ? isBookmarked(conversationId, message.uuid) : false

  const handleToggleBookmark = async () => {
    if (!conversationId) return
    await toggleBookmark({
      conversation_id: conversationId,
      message_uuid: message.uuid,
      source: conversationSource === 'CLAUDE_AI' ? 'claude_desktop' : 'claude_code',
      note: '',
      snippet: (message.text || '').slice(0, 140),
    })
  }

  const handleCopyMessage = async () => {
    const markdown = messageToMarkdown(message, showToolCalls)
    // LOW-1 (council follow-up): clipboard.writeText() can reject when
    // the document has lost focus, the browser denies permission, or the
    // origin isn't secure. Without this guard the rejection bubbles up
    // as an unhandled-promise warning and the "Copied" check still
    // flashes — a false-positive confirmation. Only flip the success
    // affordance on a resolved promise; surface failures via errorToast.
    try {
      await navigator.clipboard.writeText(markdown)
      triggerCopied()
    } catch {
      errorToast('Failed to copy to clipboard.')
    }
  }

  // 2026-10-21: a search hit inside a tool block. Only the active-match
  // bubble gets a searchQuery, so only that bubble reveals and expands
  // the tool blocks that hold the match (see toolReveal.ts).
  const revealedToolBlocks = useMemo(
    () => revealedToolBlockIndexes(message, searchQuery),
    [message, searchQuery],
  )
  const hasVisibleContent =
    messageHasVisibleContent(message, showToolCalls) || revealedToolBlocks.length > 0
  const imageFiles = dedupeImageFiles(message)
  const hasImages = imageFiles.length > 0
  const hasToolBlocks = message.content.some((b) => b.type === 'tool_use' || b.type === 'tool_result')
  const [bubbleToolsCollapsed, setBubbleToolsCollapsed] = useState(false)

  // V1 polish cleanup (2026-05-13): Argless command markers
  // (`is_command_marker=true`: `/exit`, `/clear`, `/compact`, prelude rows)
  // are CHROME — muted SlashCommandBadge bubbles that the viewer renders
  // for orientation but that the backend export, search, and full-conversation
  // copy all exclude via `_is_excludable_marker` / `isExcludableMarker`.
  // The per-block hover-revealed action overlay (copy + bookmark buttons
  // below) calls `messageToMarkdown(message, ...)` directly, which BYPASSES
  // the conversation-level `isExcludableMarker` filter. Without this guard,
  // hovering an argless marker bubble and clicking the per-block copy icon
  // would put `**You:**\n\nSession: /exit` on the clipboard — leaking chrome
  // into a user-content surface. Apply the same predicate AT THE OUTERMOST
  // surface (the hover overlay) so the affordance simply isn't offered.
  //
  // Argful markers (`/coding <prose>`, `/plan <prose>`,
  // `is_command_marker=false`) DO render a SlashCommandBadge but carry the
  // user's real prose, so they remain copyable AND bookmarkable — the
  // predicate is keyed on `is_command_marker === true`, not on
  // `slash_command` truthiness.
  //
  // Bookmarks are also hidden for argless chrome: a bookmark whose snippet
  // is "Session: /exit" has no information value and breaks the mental
  // model of bookmarks as a "save meaningful content" affordance.
  // The tool-collapse button is naturally guarded by `hasToolBlocks` —
  // argless markers never carry tool blocks, so no extra guard is needed.
  const isExcludable = isExcludableMarker(message)

  // Issue #1 — Claude Code images (inline base64 content blocks AND
  // `[Image: source: <abs-path>]` text markers) used to open in a new
  // browser tab via window.open(). The spec was that they should pop
  // the same shadcn Dialog lightbox the Desktop image grid uses, so
  // the user gets keyboard nav + download + open-original without
  // losing scroll position.
  //
  // Collect a per-bubble list of CC images in document order so the
  // InlineImageBlock / CcImageMarkerText renderers can all hand the
  // same flat index into a single ImageLightbox instance.
  const ccImageEntries = useMemo(() => collectCcImages(message), [message])
  // Manual finding 2026-05-04 follow-up: ←/→ in the lightbox should
  // walk the entire conversation's images, not just this bubble's.
  // Translate per-bubble click positions to a global catalog index.
  const conversationLightbox = useConversationLightbox()
  const ccImageBaseIndex = useMemo(() => {
    const offset = conversationLightbox.offsetForMessage(message.uuid)
    if (offset < 0) return offset
    // The catalog records Desktop file attachments first (if any),
    // then CC content-block images. Skip past the Desktop count so
    // localCcIdx → globalIdx works.
    return offset + imageFiles.length
  }, [conversationLightbox, message.uuid, imageFiles.length])
  const onOpenCcImage = useCallback(
    (localIdx: number) => {
      if (ccImageBaseIndex < 0) return
      conversationLightbox.openAt(ccImageBaseIndex + localIdx)
    },
    [ccImageBaseIndex, conversationLightbox],
  )

  // Don't render empty bubbles — but a message with image attachments is
  // never empty even if there's no text content.
  if (!hasVisibleContent && !hasImages) {
    return null
  }

  return (
    <div
      data-message-uuid={message.uuid}
      tabIndex={-1}
      {...(bubbleToolsCollapsed ? { 'data-collapsed': '' } : {})}
      className={cn(
        'group flex gap-3 focus:outline-none',
        isHuman ? 'flex-row-reverse' : 'flex-row'
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
          isHuman
            ? 'bg-blue-100 text-blue-600 dark:bg-blue-900 dark:text-blue-300'
            : 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300'
        )}
      >
        {isHuman ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>

      {/* Content */}
      <div
        className={cn(
          'relative flex max-w-[80%] flex-col gap-2 rounded-lg px-4 py-3 transition-all duration-150',
          isHuman
            ? 'bg-blue-50 dark:bg-blue-950'
            : 'bg-zinc-100 dark:bg-zinc-800',
          isKeyboardSelected && 'ring-2 ring-blue-500 ring-offset-2 dark:ring-offset-zinc-900'
        )}
      >
        <div className="absolute -right-2 -top-2 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          {hasToolBlocks && showToolCalls && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 bg-white dark:bg-zinc-700 border border-zinc-200 dark:border-zinc-600 shadow-sm"
              onClick={() => setBubbleToolsCollapsed((v) => !v)}
              title={bubbleToolsCollapsed ? 'Expand tool blocks' : 'Collapse tool blocks'}
              aria-label={bubbleToolsCollapsed ? 'Expand tools' : 'Collapse tools'}
            >
              <ChevronsUpDown className="h-3.5 w-3.5" />
            </Button>
          )}
          {!isExcludable && conversationId && (
            <Button
              variant="ghost"
              size="icon"
              className={cn(
                'h-7 w-7 border bg-white shadow-sm dark:bg-zinc-700',
                bookmarked
                  ? 'border-amber-300 text-amber-500 dark:border-amber-700'
                  : 'border-zinc-200 dark:border-zinc-600'
              )}
              onClick={handleToggleBookmark}
              title={bookmarked ? 'Remove bookmark' : 'Bookmark this message'}
              aria-label={bookmarked ? 'Remove bookmark' : 'Bookmark message'}
            >
              <Star className={cn('h-3.5 w-3.5', bookmarked && 'fill-amber-500')} />
            </Button>
          )}
          {!isExcludable && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 bg-white dark:bg-zinc-700 border border-zinc-200 dark:border-zinc-600 shadow-sm"
              onClick={handleCopyMessage}
              title="Copy message as Markdown"
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-green-500" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </Button>
          )}
        </div>
        {bookmarked && <span data-bookmarked aria-hidden className="hidden" />}

        {/* Header */}
        <div className="flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
          <span className="font-medium">
            {isHuman ? 'You' : 'Claude'}
          </span>
          <span>{formatMessageTimestamp(message.created_at)}</span>
          {message.truncated && (
            <span className="text-amber-600 dark:text-amber-400">
              (truncated)
            </span>
          )}
        </div>

        {/* Slash-command badge (V1 polish round 3, 2026-05-12). Rendered
            ABOVE the body so the user can see which `/foo` produced the
            bubble even when the body is the argful prompt text (e.g.
            "Double-check your plan." for a /coding marker). The truthy
            guard `if (message.slash_command)` correctly skips null,
            undefined, AND the empty string. */}
        {message.slash_command && (
          <SlashCommandBadge command={message.slash_command} />
        )}

        {/* Message content */}
        <div className="text-sm text-zinc-900 dark:text-zinc-100">
          {message.content && message.content.length > 0 ? (
            <ContentBlockList
              content={message.content}
              showToolCalls={showToolCalls && !bubbleToolsCollapsed}
              expandAll={expandAllTools}
              revealedToolBlocks={revealedToolBlocks}
              ccImageEntries={ccImageEntries}
              onOpenCcImage={onOpenCcImage}
              searchQuery={searchQuery}
            />
          ) : (
            <MarkdownRenderer
              content={message.text}
              showToolCalls={showToolCalls && !bubbleToolsCollapsed}
              query={searchQuery}
            />
          )}
        </div>

        {/* Image attachments — always rendered (never gated by tool toggle). */}
        {hasImages && <MessageAttachments message={message} bubbleUuid={message.uuid} />}
      </div>
    </div>
  )
}

// Manual finding 2026-05-04: typing into the SearchPanel input felt
// laggy (~10s) on a 600-message conversation. Root cause: every
// keystroke updates `query` in SearchPanelContext, which re-renders
// every component that reads via useSearchPanel(). ConversationPage
// is one of them; without memo, that cascades to ALL MessageBubble
// children. Wrap the export in React.memo so a re-render of
// ConversationPage doesn't re-render every bubble unless the bubble's
// own props actually changed (message reference, selection flag,
// conversation id/source).
//
// Issue 3 follow-up (2026-05-20): `searchQuery` joins the comparator.
// Without it, ConversationPage threading useDeferredValue(query) would
// be pointless — every deferred-value flip would bypass memo because
// the prop wasn't in the comparator. Including it means bubbles re-
// render ONLY when the deferred query actually changes, and React
// scheduling defers that work behind navigation/scroll.
//
// 2026-05-23 perf regression fix: `showToolCalls` and `expandAllTools`
// join the comparator now that they're props instead of `useSettings()`
// reads. Without them in the comparator, ConversationPage flipping
// "Expand all tools" would silently bypass memo and re-render every
// bubble; the previous incarnation got this for free because the
// useSettings() context subscription bypassed memo on its own (which
// is exactly what we just stopped doing). They must be in BOTH the
// props interface and the comparator deps to preserve correctness.
export const MessageBubble = memo(MessageBubbleImpl, (prev, next) => {
  return (
    prev.message === next.message &&
    prev.isKeyboardSelected === next.isKeyboardSelected &&
    prev.conversationId === next.conversationId &&
    prev.conversationSource === next.conversationSource &&
    prev.showToolCalls === next.showToolCalls &&
    prev.expandAllTools === next.expandAllTools &&
    prev.searchQuery === next.searchQuery
  )
})
