import React, { useCallback, useEffect, useMemo, useRef } from 'react'
import { useLocation } from 'react-router'
import { Search, X, User, Bot, FileText, ArrowUpDown, Bookmark as BookmarkIcon, Loader2, Pin, HelpCircle } from 'lucide-react'
import { useSearchPanel, type SearchMatch } from '@/contexts/SearchPanelContext'
import { useSearchPin } from '@/contexts/SearchPinContext'
import { useNavigateToMatch } from '@/components/search/navigateToMatch'
import { computeHighlightRanges } from '@/components/search/highlightRanges'
import { useSettings } from '@/contexts/SettingsContext'
import { BookmarksPanel } from '@/components/bookmarks/BookmarksPanel'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { cn, formatDate } from '@/lib/utils'
import { isSortField, type SortField } from '@/lib/types'

const SORT_OPTIONS: { value: SortField; label: string }[] = [
  { value: 'updated_at', label: 'Last Activity' },
  { value: 'created_at', label: 'Start Time' },
  { value: 'name', label: 'Title' },
  { value: 'project', label: 'Project' },
]

// 2026-05-22 perf fix: hard caps on how many result cards we render.
//
// Each card runs HighlightedSnippet (regex-driven <mark> wrapping) on
// its body text. Rendering 1000 full-mode cards (each 10K-100K chars)
// blocked the main thread for ~10 s on a 16K-msg conversation; the
// Snippet/Full toggle felt broken. Snippet mode's ~200-char cards
// are cheaper individually but 500+ of them still cost seconds.
//
// Caps apply to DOM rendering only — flatMatches stays intact for
// keyboard-nav (Cmd+G) and other logic. The footer below the list
// surfaces the cap when it applies.
//
// Snippet cap is higher than Full because each card is ~50× smaller.
const FULL_MODE_CARD_CAP = 50
const SNIPPET_MODE_CARD_CAP = 100

/**
 * Right-side search overlay panel. Always mounted; slides off-screen via
 * `translate-x-full` when closed so React Query state and input focus are
 * preserved across open/close cycles.
 *
 * This component does NOT register its own keyboard shortcuts — Task D's
 * centralized handler owns Cmd+K / Cmd+G / Escape.
 */
export function SearchPanel() {
  const {
    isOpen,
    query,
    contextSize,
    sortField,
    sortOrder,
    activeMatchIndex,
    activeMatchSource,
    flatMatches,
    totalMatched,
    returnedMatches,
    truncated,
    isLoading,
    isSearching,
    close,
    setQuery,
    setContextSize,
    setSortField,
    setSortOrder,
    setActiveMatchIndex,
    focusRequestSeq,
  } = useSearchPanel()

  const navigateToMatch = useNavigateToMatch()
  const { rightPaneTab, setRightPaneTab } = useSettings()
  const { scope: pinScope, unpin } = useSearchPin()
  const location = useLocation()

  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const activeCardRef = useRef<HTMLButtonElement | null>(null)

  // Auto-focus the input whenever the panel opens, AND whenever a Cmd+F
  // focus-request comes in (focusRequestSeq bumped). The latter handles
  // the case where the panel is already open but the user pressed Cmd+F
  // from elsewhere on the page expecting "find" muscle memory to put
  // them in the search box.
  //
  // The rightPaneTab dep matters because the search input is only
  // mounted when rightPaneTab === 'search' (see line ~238). Cmd+F's
  // handler also forces the tab to 'search', and we need this effect
  // to re-run AFTER the tab change paints so inputRef.current is no
  // longer null.
  useEffect(() => {
    if (!isOpen) return
    if (rightPaneTab !== 'search') return
    const id = window.setTimeout(() => {
      const input = inputRef.current
      // Already in the input: the user typed before this timer ran (a
      // busy main thread, or a fast typist). select() would select those
      // keys and the next key would replace them: NEEDLE_X became
      // EDLE_X (2026-10-24). Select only when this moves the focus.
      if (!input || document.activeElement === input) return
      input.focus()
      input.select()
    }, 0)
    return () => window.clearTimeout(id)
  }, [isOpen, focusRequestSeq, rightPaneTab])

  // Scroll the active match card into view whenever activeMatchIndex
  // changes, AND auto-navigate to the active match. Article line 109:
  // "If match #7 is in one conversation and match #8 is in another,
  // ⌘+G takes you there anyway; you keep your hands on the keyboard
  // and you keep moving forward." Cmd+G / Cmd+Shift+G change
  // activeMatchIndex; this effect makes them actually navigate to the
  // newly-active match, both in-conversation and cross-conversation.
  //
  // 2026-05-23: branches on `activeMatchSource`. The auto-promote
  // path (source='auto', set by the results-arrival effect in
  // SearchPanelContext) calls navigateToMatch with `focus: false` so
  // typing in the search input is not interrupted mid-keystroke. The
  // user path (source='user', set by Cmd+G / Cmd+Shift+G / Enter /
  // card-click) calls navigateToMatch with default `focus: true` so
  // the bubble owns DOM focus and Cmd+C copies the message body.
  //
  // 2026-05-24: gate AUTO-promote navigation on the user being on a
  // /conversations/* route. Without this gate, a search result landing
  // (e.g. the user typed a query then navigated to /settings before
  // the debounce settled) yanks them back to a /conversations/<uuid>
  // ?highlight=<msg> URL — the "Settings page flashes and disappears"
  // regression captured in the screen recording on 2026-05-24. USER-
  // initiated navigation (Cmd+G, Enter, card click) always wins — the
  // gate applies only to source='auto'. Card-click on the search
  // panel itself is a legitimate "take me there" intent and runs as
  // 'user'.
  useEffect(() => {
    if (activeMatchIndex < 0) return
    const el = activeCardRef.current
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
    const match = flatMatches[activeMatchIndex]
    if (!match) return
    const onConversationRoute = location.pathname.startsWith('/conversations/')
    if (activeMatchSource === 'auto' && !onConversationRoute) {
      // Auto-promote landed while the user was on a non-conversation
      // route (Settings, About, etc.). Update the sidebar visual but
      // do NOT navigate — the user will see the active card highlighted
      // when they return to a conversation page.
      return
    }
    navigateToMatch(match, { focus: activeMatchSource === 'user' })
    // oxlint-disable-next-line react-doctor/exhaustive-deps -- same rationale: discrete-signal effect; runtime body reads location.pathname fresh to gate navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- navigateToMatch + flatMatches + location identity churn on unrelated state; including them would re-fire this effect on every Tools click / route change. The behavior we want is "navigate exactly once per (index, source) pair, IF on a conversation route", which is what the body's runtime read of location.pathname captures while keeping the dep list to the discrete signals.
  }, [activeMatchIndex, activeMatchSource])

  const flatIndexByKey = useMemo(() => {
    const map = new Map<string, number>()
    flatMatches.forEach((m, i) => {
      map.set(`${m.conversationUuid}:${m.messageUuid}:${m.matchStart}`, i)
    })
    return map
  }, [flatMatches])

  const handleCardClick = (match: SearchMatch) => {
    const key = `${match.conversationUuid}:${match.messageUuid}:${match.matchStart}`
    const idx = flatIndexByKey.get(key) ?? -1
    // 2026-05-23: when the click would actually flip activeMatchIndex
    // or activeMatchSource, let the navigation flow through the
    // index-change effect above (single source of truth, fires
    // exactly once per (index, source) pair). When the user clicks
    // the SAME card that's already active under the same source,
    // React would bail on the state update and the effect wouldn't
    // re-fire — so we navigate manually in that branch.
    if (
      idx !== -1 &&
      (idx !== activeMatchIndex || activeMatchSource !== 'user')
    ) {
      setActiveMatchIndex(idx, 'user')
    } else {
      navigateToMatch(match, { focus: true })
    }
  }

  // Opens the currently active match (or the first one if none is active).
  // Used by Enter both in the input and anywhere else inside the panel.
  //
  // Bug P1.4 (2026-05-04): we used to call setActiveMatchIndex(idx) with the
  // SAME index that was already active (after Cmd+G). React bails out of the
  // state update, so the activeMatchIndex effect (which calls navigateToMatch)
  // does NOT re-fire — Enter became a no-op. Fix: navigateToMatch directly,
  // and explicitly focus the target message bubble (panel stays open).
  const openActiveMatch = useCallback(() => {
    if (flatMatches.length === 0) return
    const idx = activeMatchIndex >= 0 ? activeMatchIndex : 0
    const match = flatMatches[idx]
    if (!match) return
    // 2026-05-23: source='user' so the downstream navigation moves
    // DOM focus to the bubble (matches Cmd+G / card-click behavior).
    if (activeMatchIndex < 0) setActiveMatchIndex(idx, 'user')
    navigateToMatch(match, { focus: true })
    // Belt-and-suspenders: the URL fallback path's focus transfer
    // happens after ConversationPage's highlight effect polls for the
    // bubble. For same-conv same-index Enter where the index doesn't
    // change (effect doesn't re-fire), the navigateToMatch call above
    // already focuses via the fast path. This rAF block is the legacy
    // path that still works for both.
    requestAnimationFrame(() => {
      const el = document.querySelector(
        `[data-message-uuid="${match.messageUuid}"]`
      )
      if (el instanceof HTMLElement) el.focus()
    })
  }, [activeMatchIndex, flatMatches, navigateToMatch, setActiveMatchIndex])

  // Bug B (2026-05-03): use the unified `isSearching` flag (covers
  // first-load + refetch + debounce window) so we never show "No
  // matches" while a request is actually in flight or about to fire.
  const showEmptyTooShort = query.length < 2
  const showEmptyNoResults =
    query.length >= 2 && !isSearching && flatMatches.length === 0
  const showSkeleton = query.length >= 2 && isSearching && flatMatches.length === 0
  const showResults = query.length >= 2 && flatMatches.length > 0
  // Silence unused-var when isLoading falls out of any branch above.
  void isLoading

  return (
    <aside
      role="complementary"
      aria-label="Search panel"
      aria-hidden={!isOpen}
      className={cn(
        'fixed right-0 top-0 z-40 flex h-full w-96 flex-col border-l border-zinc-200 bg-white shadow-xl transition-transform duration-200 dark:border-zinc-800 dark:bg-zinc-900',
        !isOpen && 'translate-x-full'
      )}
    >
      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-zinc-200 px-2 pt-2 dark:border-zinc-800" role="tablist" aria-label="Right pane tabs">
        <button
          type="button"
          role="tab"
          aria-selected={rightPaneTab === 'search'}
          onClick={() => setRightPaneTab('search')}
          className={cn(
            'flex items-center gap-1 rounded-t-md border border-b-0 px-3 py-1 text-xs',
            rightPaneTab === 'search'
              ? 'border-zinc-200 bg-white text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100'
              : 'border-transparent text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200'
          )}
        >
          <Search className="h-3 w-3" />
          Search
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={rightPaneTab === 'bookmarks'}
          onClick={() => setRightPaneTab('bookmarks')}
          className={cn(
            'flex items-center gap-1 rounded-t-md border border-b-0 px-3 py-1 text-xs',
            rightPaneTab === 'bookmarks'
              ? 'border-zinc-200 bg-white text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100'
              : 'border-transparent text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200'
          )}
        >
          <BookmarkIcon className="h-3 w-3" />
          Bookmarks
        </button>
        <div className="flex-1" />
        <button
          type="button"
          onClick={close}
          className="rounded p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          aria-label="Close panel"
          title="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Scope chip — mirrors the conversation-header pin button.
          Rendered OUTSIDE the per-tab gate (P2 2026-05-04 finding):
          pin state is global, and users whose persisted right-pane tab
          is 'bookmarks' would otherwise never see the chip when they
          open the panel — making it look like the pin had no effect. */}
      {pinScope.kind !== 'none' && (
        <div className="border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
          <div
            className="inline-flex max-w-full items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300"
            data-testid="search-scope-chip"
            data-scope-kind={pinScope.kind}
          >
            <Pin className="h-3 w-3 shrink-0" />
            <span className="truncate">
              In:{' '}
              <span className="font-medium">
                {pinScope.kind === 'conversation' ? pinScope.name || 'this conversation' : pinScope.name}
              </span>
            </span>
            <button
              type="button"
              onClick={() => {
                unpin()
                inputRef.current?.focus()
              }}
              aria-label="Clear search pin"
              className="ml-1 rounded p-0.5 hover:bg-blue-100 dark:hover:bg-blue-900"
              data-testid="search-scope-chip-clear"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        </div>
      )}

      {rightPaneTab === 'bookmarks' && <BookmarksPanel />}

      {rightPaneTab === 'search' && (
      <>
      {/* Header: input + context toggle */}
      <div className="border-b border-zinc-200 p-3 dark:border-zinc-800">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
            <input
              ref={inputRef}
              data-search-panel-input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  openActiveMatch()
                }
              }}
              placeholder="Search messages..."
              className="flex h-9 w-full rounded-md border border-zinc-200 bg-transparent pl-9 pr-3 py-1 text-sm shadow-sm transition-colors placeholder:text-zinc-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-400 dark:border-zinc-800 dark:placeholder:text-zinc-400"
              aria-label="Search query"
              data-allow-shortcuts
            />
          </div>
          {/* Query-syntax help icon. Tooltip explains the AND-of-terms vs.
              quoted-phrase distinction so users don't have to discover it by
              trial and error. Keyboard-accessible: focusing the button (Tab)
              opens the tooltip via Radix's focus handling. */}
          <TooltipProvider delayDuration={150}>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label="Search query syntax help"
                  data-testid="search-syntax-help"
                  className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                >
                  <HelpCircle className="h-4 w-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom" align="end" className="max-w-xs leading-relaxed">
                <p className="font-medium">Search syntax</p>
                <p className="mt-1">
                  Multi-word: all words must appear in the same message, in any order.
                </p>
                <p className="mt-1">
                  Wrap in <code className="rounded bg-zinc-100 px-1 dark:bg-zinc-800">&quot;double quotes&quot;</code> to match the exact phrase.
                </p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>

        {/* Sort controls — mirror left sidebar; default updated_at + desc. */}
        <div className="mt-3 flex items-center gap-1">
          <Select
            value={sortField}
            onValueChange={(v: string) => {
              // shadcn Select hands us a string; only accept SortField
              // values. Unknown values are silently rejected (defense
              // in depth — the SelectItem children only supply known
              // values). See lib/types.ts for the predicate.
              if (!isSortField(v)) return
              setSortField(v)
              // Restore focus so Enter still navigates after changing sort.
              window.setTimeout(() => inputRef.current?.focus(), 0)
            }}
          >
            <SelectTrigger className="flex-1 h-7 text-xs">
              <ArrowUpDown className="h-3 w-3 mr-1 text-zinc-400" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <button
            type="button"
            onClick={() => {
              setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')
              inputRef.current?.focus()
            }}
            title={sortOrder === 'asc' ? 'Ascending' : 'Descending'}
            className="h-7 rounded-md px-2 text-xs text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            {sortOrder === 'asc' ? '↑' : '↓'}
          </button>
        </div>

        {/* Context size toggle (segmented control) */}
        <div
          role="radiogroup"
          aria-label="Search context size"
          className="mt-2 inline-flex rounded-md border border-zinc-200 bg-zinc-50 p-0.5 text-xs dark:border-zinc-800 dark:bg-zinc-950"
        >
          <button
            type="button"
            role="radio"
            aria-checked={contextSize === 'snippet'}
            onClick={() => {
              setContextSize('snippet')
              // Return focus to the input so subsequent Enter navigates
              inputRef.current?.focus()
            }}
            className={cn(
              'rounded px-3 py-1 font-medium transition-colors',
              contextSize === 'snippet'
                ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-100'
                : 'text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200'
            )}
          >
            Snippet
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={contextSize === 'full'}
            onClick={() => {
              setContextSize('full')
              inputRef.current?.focus()
            }}
            className={cn(
              'rounded px-3 py-1 font-medium transition-colors',
              contextSize === 'full'
                ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-100'
                : 'text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200'
            )}
          >
            Full
          </button>
        </div>
      </div>

      {/* Match counter. When the backend truncation envelope flags
          `truncated: true` (FTS5 returned more matches than the 1000
          row LIMIT — see HTTP_SEARCH_LIMIT in backend/routers/search.py),
          append `+` to the total so the user knows the count is a floor
          not a ceiling. Detailed "Showing first N of M, refine to see
          the rest" disclosure still lives in the footer below the
          results — this is the at-a-glance hint at the top. */}
      {flatMatches.length > 0 && (
        <div className="border-b border-zinc-200 px-3 py-2 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          <span className="font-medium text-zinc-700 dark:text-zinc-300">
            {activeMatchIndex >= 0 ? activeMatchIndex + 1 : '—'}
          </span>{' '}
          of{' '}
          <span
            className="font-medium text-zinc-700 dark:text-zinc-300"
            title={truncated ? `at least ${flatMatches.length}; true total exceeds the per-query limit` : undefined}
          >
            {flatMatches.length}{truncated ? '+' : ''}
          </span>{' '}
          matches
        </div>
      )}

      {/* aria-live region: Cmd+G changes the active match without moving
          DOM focus; SR users would otherwise miss it. The "or more"
          phrasing mirrors the visible `+` suffix when truncated.

          Phase 1 a11y: <div role="status"> is the standard, idiomatic
          way to create a screen-reader status announcement region.
          React Doctor's prefer-tag-over-role suggests <output>, but
          <output> is a form-associated computed-result element;
          semantically wrong for a generic announcer. Suppress. */}
      {/* react-doctor-disable-next-line react-doctor/prefer-tag-over-role */}
      <div
        role="status"
        aria-live="polite"
        className="sr-only"
        data-testid="search-match-aria-live"
      >
        {activeMatchIndex >= 0 && flatMatches.length > 0
          ? `Match ${activeMatchIndex + 1} of ${flatMatches.length}${truncated ? ' or more' : ''}`
          : ''}
      </div>

      {/* Results area */}
      <div
        ref={listRef}
        className="flex-1 overflow-y-auto p-3"
      >
        {showEmptyTooShort && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-sm text-zinc-500 dark:text-zinc-400">
            <Search className="h-8 w-8 text-zinc-300 dark:text-zinc-700" />
            <p>Type at least 2 characters to search</p>
          </div>
        )}

        {showSkeleton && (
          <div data-search-loading className="space-y-3">
            {/* Explicit "Searching…" copy + spinner so the user knows
                the request is in flight, not that there are zero
                results. (Bug B 2026-05-03 — was only skeletons before;
                e2e relied on visible text.) */}
            <div className="flex items-center justify-center gap-2 py-3 text-sm text-zinc-500 dark:text-zinc-400">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>Searching…</span>
            </div>
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="animate-pulse space-y-2 rounded-md border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-900"
              >
                <div className="h-3 w-2/3 rounded bg-zinc-200 dark:bg-zinc-800" />
                <div className="h-3 w-full rounded bg-zinc-200 dark:bg-zinc-800" />
                <div className="h-3 w-5/6 rounded bg-zinc-200 dark:bg-zinc-800" />
              </div>
            ))}
          </div>
        )}

        {showEmptyNoResults && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-sm text-zinc-500 dark:text-zinc-400">
            <Search className="h-8 w-8 text-zinc-300 dark:text-zinc-700" />
            <p>
              No matches for{' '}
              <span className="font-medium text-zinc-700 dark:text-zinc-300">
                &ldquo;{query}&rdquo;
              </span>
              {pinScope.kind !== 'none' && (
                <>
                  {' '}in{' '}
                  <span className="font-medium text-zinc-700 dark:text-zinc-300">
                    {pinScope.name || 'this scope'}
                  </span>
                </>
              )}
            </p>
            {pinScope.kind !== 'none' && (
              <button
                type="button"
                onClick={() => {
                  unpin()
                  inputRef.current?.focus()
                }}
                className="mt-2 rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 shadow-sm hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
                data-testid="search-unpin-and-search-all"
              >
                Unpin and search all →
              </button>
            )}
          </div>
        )}

        {showResults && (
          <div className="space-y-1.5">
            {/* 2026-05-22 perf fix: cap rendered cards in Full mode at
                FULL_MODE_CARD_CAP. Each full-mode card contains an
                entire message body (~10KB per card) with <mark> nodes
                from HighlightedSnippet. On the 16K-msg conversation,
                rendering 1000 such cards blocks the main thread for
                ~10 s — the user-reported "toggle is slow" cost. The
                cap keeps initial render bounded to ~50 cards; the
                rest are accessible by refining the query OR by
                cycling Cmd+G through the active-match index, which
                operates on the full flatMatches array (the cap
                applies to RENDERING only, not navigation logic).
                Snippet mode is uncapped — its short ±150-char
                snippets are cheap to render. */}
            {(() => {
              const cap =
                contextSize === 'full' ? FULL_MODE_CARD_CAP : SNIPPET_MODE_CARD_CAP
              const visibleMatches = flatMatches.slice(0, cap)
              return visibleMatches.map((match, idx) => {
                const isActive = idx === activeMatchIndex
                const key = `${match.conversationUuid}:${match.messageUuid}:${match.matchStart}`
                return (
                  <ResultCard
                    key={key}
                    ref={isActive ? activeCardRef : undefined}
                    match={match}
                    isActive={isActive}
                    contextSize={contextSize}
                    query={query}
                    onClick={() => handleCardClick(match)}
                  />
                )
              })
            })()}
            {(() => {
              const cap =
                contextSize === 'full' ? FULL_MODE_CARD_CAP : SNIPPET_MODE_CARD_CAP
              if (flatMatches.length <= cap) return null
              return (
                <div
                  data-testid="search-card-cap-footer"
                  className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
                >
                  Showing first {cap.toLocaleString()} of{' '}
                  {flatMatches.length.toLocaleString()} cards. Refine your query
                  to narrow the results.
                </div>
              )
            })()}
            {/* Truncation footer (SEARCH_TOOL_AWARENESS_AND_LIMIT_DISCLOSURE
                plan §B). When the FTS5 fast path returns more matches
                than the route's LIMIT (1000 for /api/search), the backend
                envelope flags `truncated: true`. We surface that here
                so the user knows the result set is partial and can
                refine. Active voice; no em-dash; muted styling matching
                the existing perf-stat footer below the sidebar. */}
            {truncated && (
              <div
                data-testid="search-truncation-footer"
                className="mt-3 rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400"
              >
                Showing first {returnedMatches.toLocaleString()} of{' '}
                {totalMatched.toLocaleString()} message matches. Refine your
                query to see the rest.
              </div>
            )}
          </div>
        )}
      </div>
      </>
      )}
    </aside>
  )
}

interface ResultCardProps {
  match: SearchMatch
  isActive: boolean
  contextSize: 'snippet' | 'full'
  /** Live user query — drives multi-token highlight in the snippet body. */
  query: string
  onClick: () => void
}

const ResultCard = ({
  ref,
  match,
  isActive,
  contextSize,
  query,
  onClick,
}: ResultCardProps & { ref?: React.Ref<HTMLButtonElement> }) => {
  const isTitleMatch = match.messageUuid === 'title'
  const isHuman = match.sender === 'human'
  const Icon = isTitleMatch ? FileText : isHuman ? User : Bot
  const senderLabel = isTitleMatch
    ? 'title'
    : isHuman
      ? 'human'
      : 'assistant'

  return (
    <button
      ref={ref}
      type="button"
      data-result-card
      data-testid="search-result-card"
      onClick={onClick}
      className={cn(
        'group block w-full rounded-md border border-zinc-200 bg-white p-2.5 text-left text-sm shadow-sm transition-colors hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:bg-zinc-800',
        isActive && 'ring-2 ring-inset ring-blue-500'
      )}
    >
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span
          className="flex-1 truncate text-xs font-semibold text-zinc-700 dark:text-zinc-200"
          title={match.conversationName}
        >
          {match.conversationName}
        </span>
        {match.createdAt && (
          <span className="shrink-0 text-[11px] text-zinc-400 dark:text-zinc-500">
            {formatDate(match.createdAt)}
          </span>
        )}
      </div>
      <div className="mb-1 flex items-center gap-1.5 text-xs">
        <Icon
          className={cn(
            'h-3.5 w-3.5 shrink-0',
            isTitleMatch
              ? 'text-zinc-400'
              : isHuman
                ? 'text-blue-500 dark:text-blue-400'
                : 'text-emerald-500 dark:text-emerald-400'
          )}
        />
        <span
          className={cn(
            'font-medium',
            isTitleMatch
              ? 'text-zinc-500 dark:text-zinc-400'
              : isHuman
                ? 'text-blue-600 dark:text-blue-400'
                : 'text-emerald-600 dark:text-emerald-400'
          )}
        >
          {senderLabel}
        </span>
      </div>
      <div
        className={cn(
          'whitespace-pre-wrap break-words text-sm leading-snug text-zinc-700 dark:text-zinc-200',
          contextSize === 'full' && 'max-h-48 overflow-y-auto'
        )}
      >
        <HighlightedSnippet
          text={match.snippet}
          query={query}
          fallbackStart={match.matchStart}
          fallbackEnd={match.matchEnd}
          fragments={match.fragments ?? null}
        />
      </div>
    </button>
  )
}

interface HighlightedSnippetProps {
  text: string
  /** Live user query — every occurrence of every token gets a `<mark>`. */
  query: string
  /** Backend-supplied `match_start` — used as fallback / drift safety. */
  fallbackStart: number
  /** Backend-supplied `match_end` — paired with `fallbackStart`. */
  fallbackEnd: number
  /** Phase-2 Workstream A: structured highlight fragments from the FTS5
   *  fast path. When non-null, the renderer uses them directly — the
   *  backend's bm25-driven snippet() picks better multi-token clusters
   *  than the legacy first-token-only heuristic. When null, falls back
   *  to the live-query token scan (typing keeps highlights fresh under
   *  debounce; same behavior as pre-Phase-2). */
  fragments?: import('@/lib/types').SnippetFragment[] | null
}

/**
 * Renders a snippet with EVERY occurrence of every query token wrapped
 * in `<mark>`. The backend's `match_start`/`match_end` is treated as a
 * fallback range — it's seeded into the highlight set before token-scan
 * so a stemmer/diacritic drift hit (FTS5 matches `running` for query
 * `run`; literal substring scan misses) still shows a yellow band.
 *
 * Multi-token logic lives in
 * `@/components/search/highlightRanges#computeHighlightRanges`; this
 * component owns only the React rendering (interleaved `<>{text}<mark>…
 * </mark>{text}</>`). If the helper returns zero ranges we render plain
 * text (true zero-highlight state — happens when query is empty AND the
 * backend reported no range).
 */
function HighlightedSnippet({
  text,
  query,
  fallbackStart,
  fallbackEnd,
  fragments,
}: HighlightedSnippetProps) {
  // Phase-2 Workstream A: prefer backend-supplied fragments when
  // present. The FTS5 fast path's bm25-driven snippet() picks the
  // densest match cluster across multi-token queries — better than
  // the legacy first-token-only heuristic. The live-query path
  // (computeHighlightRanges) stays as the fallback so typing in
  // the search box re-highlights against the most recent query
  // even if the backend hasn't returned new results yet.
  //
  // We DON'T merge fragments with the live-query scan because the
  // fragments already encode the authoritative highlight signal
  // for the response the user is currently viewing. Merging would
  // produce overlapping marks the user perceives as flicker.
  if (fragments && fragments.length > 0) {
    return (
      <>
        {fragments.map((f, i) =>
          f.mark ? (
            <mark
              key={`f-${i}`}
              className="rounded bg-yellow-200 px-0.5 font-medium dark:bg-yellow-800 dark:text-yellow-50"
            >
              {f.text}
            </mark>
          ) : (
            <React.Fragment key={`f-${i}`}>{f.text}</React.Fragment>
          ),
        )}
      </>
    )
  }

  const ranges = computeHighlightRanges(text, query, fallbackStart, fallbackEnd)

  if (ranges.length === 0) {
    return <>{text}</>
  }

  const segments: React.ReactNode[] = []
  let cursor = 0
  for (let i = 0; i < ranges.length; i++) {
    const { start, end } = ranges[i]
    if (start > cursor) {
      segments.push(text.slice(cursor, start))
    }
    segments.push(
      <mark
        key={`m-${start}-${end}`}
        className="rounded bg-yellow-200 px-0.5 font-medium dark:bg-yellow-800 dark:text-yellow-50"
      >
        {text.slice(start, end)}
      </mark>,
    )
    cursor = end
  }
  if (cursor < text.length) {
    segments.push(text.slice(cursor))
  }
  return <>{segments}</>
}
