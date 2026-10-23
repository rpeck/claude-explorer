import {
  createContext,
  use,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
  type ReactNode,
} from 'react'
import { useConversations, useSearch } from '@/hooks/useConversations'
import { useSourceFilter } from '@/contexts/SourceFilterContext'
import { useSearchPin } from '@/contexts/SearchPinContext'
import { useBookmarks } from '@/contexts/BookmarkContext'
import { useSettings } from '@/contexts/SettingsContext'
import { useFilters } from '@/contexts/FilterContext'
import { applyActiveFilter } from '@/lib/filterEngine'
import { usePreferences } from '@/hooks/usePreferences'
import type { SearchResult, SnippetFragment, SortField, SortOrder } from '@/lib/types'
import { cancelScrollCorrections } from '@/lib/scrollBubbleIntoView'

export interface SearchMatch {
  conversationUuid: string
  messageUuid: string // 'title' for title-only matches
  conversationName: string
  snippet: string
  matchStart: number
  matchEnd: number
  sender: string
  createdAt: string | null
  // Fallback timestamps used when the match has no per-message created_at
  // (e.g. title-only matches, or stale-cache responses from before the
  // backend began emitting per-message timestamps). Mirror backend behavior
  // in search.py: updated_at sort falls back to conversation_updated_at,
  // created_at sort falls back to conversation_created_at.
  conversationUpdatedAt: string
  conversationCreatedAt: string
  /** Phase-2 Workstream A: structured highlight fragments from the
   *  FTS5 fast path. Null when the backend emitted the legacy
   *  snippet/match_start/match_end triple only (linear-scan fallback,
   *  context_size='full'). The renderer prefers fragments when present
   *  because the backend's bm25-driven snippet() picks the densest
   *  multi-token cluster — better than the legacy first-token-only
   *  highlight. */
  fragments?: SnippetFragment[] | null
}

export type SearchContextSize = 'snippet' | 'full'

/**
 * Pure narrow-the-stale-results helper extracted from SearchPanelProvider
 * (2026-05-18 council audit). Returns the subset of `rawResults` whose
 * conversation name OR at least one message snippet matches every
 * needle parsed from `query`. Quoted-phrase queries match the inner
 * literal phrase; whitespace-separated queries enforce AND-of-tokens
 * (V1 polish 2026-05-14 semantics).
 *
 * Null-safety (mirror of backend H1-H4): `r.conversation_name` and
 * `m.snippet` are typed `string` but the wire format can drift
 * (older on-disk JSONs, partial serialization) and surface `null` at
 * runtime. Without the `?? ''` guards, the unguarded `.toLowerCase()`
 * calls threw `TypeError: Cannot read properties of null` and
 * white-screened the search panel. Mirrors the backend
 * `(data.get(k) or "").lower()` invariant.
 */
// eslint-disable-next-line react-refresh/only-export-components -- safe: pure helper co-located with SearchPanelProvider. HMR fast refresh falls back to full reload; no runtime impact.
export function narrowSearchResults(
  rawResults: SearchResult[] | undefined,
  query: string,
): SearchResult[] {
  if (!rawResults) return []
  if (!query || query.length < 2) return rawResults
  const trimmed = query.trim()
  let needles: string[]
  if (
    trimmed.length >= 3 &&
    trimmed.startsWith('"') &&
    trimmed.endsWith('"')
  ) {
    // Phrase mode — strip quotes; match the literal inner phrase.
    const inner = trimmed.slice(1, -1).trim().toLowerCase()
    needles = inner ? [inner] : []
  } else {
    needles = trimmed.toLowerCase().split(/\s+/).filter(Boolean)
  }
  if (needles.length === 0) return rawResults
  const filtered: SearchResult[] = []
  for (const r of rawResults) {
    const nameLower = (r.conversation_name ?? '').toLowerCase()
    const nameHit = needles.every((n) => nameLower.includes(n))
    const msgMatches = (r.matching_messages ?? []).filter((m) => {
      const snippetLower = (m.snippet ?? '').toLowerCase()
      return needles.every((n) => snippetLower.includes(n))
    })
    if (msgMatches.length > 0 || nameHit) {
      filtered.push({
        ...r,
        matching_messages: msgMatches.length > 0 ? msgMatches : r.matching_messages,
      })
    }
  }
  return filtered
}

/**
 * Provenance for the most recent activeMatchIndex change.
 *   - `'auto'`: set by the auto-promote effect when results arrive.
 *     Downstream consumers (the SearchPanel activeMatchIndex effect)
 *     navigate the conversation pane WITHOUT moving DOM focus, so the
 *     user can keep typing in the search input (live-preview UX).
 *   - `'user'`: set by Cmd+G / Cmd+Shift+G / Enter-in-input / click on
 *     a result card. Downstream MOVES DOM focus to the matching bubble
 *     so Cmd+C copies the message body.
 *
 * Two state cells (`activeMatchIndex` + `activeMatchSource`) are
 * updated as a pair; React 18 batches both state updates so the
 * downstream effect runs exactly once per change. See
 * frontend/e2e/search-typing-focus.spec.ts for the user-observable
 * contract.
 */
export type ActiveMatchSource = 'auto' | 'user'

/**
 * Bug 3 (2026-05-26) — sentinel UUID stored in the demonstrated-focus
 * ref when the user manually wheel/touch-scrolled the conversation pane
 * without clicking a specific bubble. The recenter priority chain in
 * `ConversationPage.markPendingRecenter` reads the ref's UUID and feeds
 * it to `scrollBubbleIntoView`; when the UUID is this sentinel, the DOM
 * lookup returns null (no message has this UUID) and the recenter
 * effect early-returns, leaving the viewer wherever the user scrolled
 * to. That is the spec'd "manual scroll means preserve scroll position"
 * behavior.
 *
 * Real message UUIDs are 36-char hex strings; collision with this
 * literal is effectively impossible.
 */
export const MANUAL_SCROLL_SENTINEL_UUID = '__manual_scroll__'

/**
 * Shallow-structural equality on the `scope` object emitted from
 * `SearchPanelProvider`. Returns true iff both sides are undefined,
 * OR both are objects with the same conversationUuid + projectPath +
 * organizationId + conversationUuids (array deep-equal).
 *
 * Used to bail out of the `scope` useMemo when its content hasn't
 * changed, even if one of the inputs (pinScope, passingUuids) got a
 * new object identity. Without this, the reset effect downstream
 * (deps include `scope`) fires spuriously and triggers the Cmd+G
 * jump-back race (2026-05-24 user report).
 *
 * Exported for unit testing — vitest covers identity-stability for
 * the {undefined, undefined}, {empty obj, undefined}, and
 * {same content, different ref} cases.
 */
// eslint-disable-next-line react-refresh/only-export-components -- safe: pure helper co-located with SearchPanelProvider. HMR fast refresh falls back to full reload; no runtime impact.
export function scopeShapesEqual(
  a:
    | {
        conversationUuid?: string
        projectPath?: string
        organizationId?: string | null
        conversationUuids?: string[]
      }
    | undefined,
  b:
    | {
        conversationUuid?: string
        projectPath?: string
        organizationId?: string | null
        conversationUuids?: string[]
      }
    | undefined,
): boolean {
  if (a === b) return true
  if (a === undefined || b === undefined) return false
  if (a.conversationUuid !== b.conversationUuid) return false
  if (a.projectPath !== b.projectPath) return false
  if (a.organizationId !== b.organizationId) return false
  const ua = a.conversationUuids
  const ub = b.conversationUuids
  if (ua === ub) return true
  if (ua === undefined || ub === undefined) return false
  if (ua.length !== ub.length) return false
  for (let i = 0; i < ua.length; i++) {
    if (ua[i] !== ub[i]) return false
  }
  return true
}

interface SearchPanelContextType {
  isOpen: boolean
  query: string
  contextSize: SearchContextSize
  sortField: SortField
  sortOrder: SortOrder
  activeMatchIndex: number // -1 = no active match
  activeMatchSource: ActiveMatchSource
  flatMatches: SearchMatch[]
  results: SearchResult[] // Raw grouped results (for section headers w/ timestamps)
  // 2026-05-16 (plan §B): truncation envelope. Surfaced through the
  // context so SearchPanel can render a footer when the FTS5 LIMIT
  // clipped the response. `totalMatched` is the FTS5 COUNT(*) at the
  // message level (NOT conversation rollup count); `returnedMatches`
  // is the actual count of body-snippet rows the backend emitted.
  // `truncated` is the convenience predicate `returnedMatches <
  // totalMatched`.
  totalMatched: number
  returnedMatches: number
  truncated: boolean
  isLoading: boolean
  /** True whenever the search is in flight OR the input is debouncing
   *  toward a new query. SearchPanel uses this to show "Searching…"
   *  instead of "No matches" while the user waits for slow responses
   *  (manual finding 2026-05-03). */
  isSearching: boolean
  open: () => void
  close: () => void
  toggle: () => void
  setQuery: (q: string) => void
  setContextSize: (s: SearchContextSize) => void
  setSortField: (f: SortField) => void
  setSortOrder: (o: SortOrder) => void
  nextMatch: () => void
  prevMatch: () => void
  /**
   * Set the active match index. The optional `source` arg defaults to
   * `'user'` because most callers (Cmd+G handlers, card clicks) are
   * user-initiated; the auto-promote effect passes `'auto'` explicitly.
   */
  setActiveMatchIndex: (i: number, source?: ActiveMatchSource) => void
  // Bumped by callers (Cmd+F handler) that want the SearchPanel to
  // re-focus its input even if the panel is already open. SearchPanel
  // listens to this counter via a useEffect and refocuses the input
  // whenever the value changes. Cmd+F is "find" muscle memory; the user
  // must be able to type the query immediately, regardless of whether
  // the panel was already open.
  focusRequestSeq: number
  requestFocus: () => void
  /**
   * Bug 3 (2026-05-26) — demonstrated-focus latch. Mutated synchronously
   * via `markDemonstratedFocus` and `clearDemonstratedFocus`. The
   * auto-promote effect reads this ref's current value (not via the
   * context value) so wheel/touch bursts do NOT churn the Provider
   * identity. ConversationPage's `markPendingRecenter` priority chain
   * also reads the ref directly, for synchronous click -> toggle
   * sequencing.
   *
   * Values:
   *   - `null` — no demonstrated focus; auto-promote is armed.
   *   - real UUID — user clicked that bubble; recenter targets it.
   *   - `MANUAL_SCROLL_SENTINEL_UUID` — user wheel/touch-scrolled;
   *     recenter early-returns (sentinel matches no DOM element), so
   *     viewer stays where the user scrolled to.
   */
  demonstratedFocusUuidRef: RefObject<string | null>
  markDemonstratedFocus: (uuid: string | null) => void
  clearDemonstratedFocus: () => void
}

const SearchPanelContext = createContext<SearchPanelContextType | null>(null)

export function SearchPanelProvider({ children }: { children: ReactNode }) {
  const { sourceFilter, organizationId } = useSourceFilter()
  const { scope: pinScope } = useSearchPin()
  const { bookmarks } = useBookmarks()
  // History: from 2026-05-11 to 2026-10-21 the "Show tool calls" pref
  // also scoped the search, so the sidebar showed only hits that the
  // pane could render. See `includeToolCalls` below for why it stopped.
  //
  // 2026-05-26: same plumbing for the "Show Compactions" toggle. The
  // mapping `includeCompactions = !hideCompactMarkers` flips the
  // semantic ("hide=true means don't include").
  const { hideCompactMarkers } = useSettings()
  // 2026-10-21: search ALWAYS includes tool content. Until then, hidden
  // tool calls meant include_tool_calls=false, because a hit in a hidden
  // tool block could not be navigated. Now the active hit's bubble shows
  // the tool block that holds the match (components/search/toolReveal.ts),
  // so every hit is navigable, as the user asked (UX.md §4).
  const includeToolCalls = true
  const includeCompactions = !hideCompactMarkers
  // 2026-05-14 sidebar-scope propagation: the active-filter graph lives
  // in FilterContext, the sidebar applies it client-side via
  // applyActiveFilter(). For search to honor the SAME predicate, we
  // need the conversation list (post source + workspace) and the filter
  // state. The list comes from useConversations with the same scope
  // params the sidebar uses; we then map+filter to UUIDs.
  const { filtersState } = useFilters()
  // Only fetch the conversation list when there's an enabled active
  // filter (we need it to resolve filter→UUIDs). With no active filter,
  // SearchPanel doesn't need the list, and an unconditional fetch on
  // mount tripped net::ERR_NETWORK_CHANGED in Playwright page.reload()
  // calls (2026-05-15). The Sidebar still fetches independently — same
  // queryKey, so when the filter eventually activates the cache is
  // already warm via TanStack Query dedupe.
  const needsConversationList = !!filtersState.activeId &&
    !!filtersState.nodes[filtersState.activeId]?.enabled
  const conversationsQuery = useConversations(
    {
      source: sourceFilter,
      organization_id: organizationId ?? undefined,
    },
    { enabled: needsConversationList },
  )

  // P3e: dual-read/dual-write via usePreferences. The hook resolves
  // value as server.data[key] ?? localStorage[key] ?? fallback, and
  // setValue mirrors to BOTH server (PATCH /api/preferences) and
  // localStorage under the SAME legacy key — so existing browser
  // sessions keep working without a key rename.
  const [isOpen, setIsOpenPref] = usePreferences<boolean>(
    'searchPanel.isOpen',
    false,
  )
  const [contextSize, setContextSizePref] = usePreferences<SearchContextSize>(
    'searchPanel.contextSize',
    'snippet',
  )
  const [sortField, setSortFieldPref] = usePreferences<SortField>(
    'searchPanel.sortField',
    'updated_at',
  )
  const [sortOrder, setSortOrderPref] = usePreferences<SortOrder>(
    'searchPanel.sortOrder',
    'desc',
  )
  const [query, setQueryState] = useState<string>('')
  const [activeMatchIndex, setActiveMatchIndexState] = useState<number>(-1)

  // Bug 3 (2026-05-26) — demonstrated-focus latch. See type-level docs
  // on `demonstratedFocusUuidRef` for the contract. Ref-only (no state
  // mirror): wheel events fire many times per trackpad burst; a state
  // mirror would invalidate the Provider value identity on every fire
  // and re-render every `useSearchPanel()` consumer. The auto-promote
  // effect re-runs whenever `activeMatchIndex` flips to -1 (the reset
  // effect's job on every queryKey change), which is the exact moment
  // the gate matters; reading `.current` inline at that re-fire is
  // sufficient. Council 2026-05-26 (gpt-5.2 thinking_mode=high).
  const demonstratedFocusUuidRef = useRef<string | null>(null)

  const markDemonstratedFocus = useCallback((uuid: string | null) => {
    demonstratedFocusUuidRef.current = uuid
    // 2026-10-23: the user's click or manual scroll also ends any pending
    // correction of the previous search jump, which would otherwise
    // scroll back to the hit and undo the user's choice.
    if (uuid !== null) cancelScrollCorrections()
  }, [])

  const clearDemonstratedFocus = useCallback(() => {
    demonstratedFocusUuidRef.current = null
  }, [])
  // Provenance flag for the most recent activeMatchIndex change. See
  // ActiveMatchSource docs above. Default `'user'` is a safe initial
  // value: when the index is -1 there's no downstream navigation, so
  // the field is dormant; first real value comes from either the
  // auto-promote effect ('auto') or a Cmd+G/click ('user').
  const [activeMatchSource, setActiveMatchSourceState] =
    useState<ActiveMatchSource>('user')
  // Cmd+F focus-request counter. Bumping it triggers SearchPanel's
  // focus-input useEffect even when isOpen hasn't changed.
  const [focusRequestSeq, setFocusRequestSeq] = useState(0)

  // 2026-05-14 sidebar-scope propagation: the set of UUIDs that PASS the
  // active-filter graph. undefined → no constraint (filter is null OR
  // passes everything). Empty array → "filter excludes everything"
  // (backend short-circuits to empty results).
  //
  // Performance: applyActiveFilter is glob/regex on conversation NAME;
  // for a 1500-conv corpus this is a sub-millisecond loop. Re-runs
  // only when the conversation list or filter state changes.
  //
  // The "no-op short-circuit" (passing.length === total.length) keeps
  // us from sending a payload at all when the filter is permissive.
  // It does NOT save us from "almost all" cases (1499/1500 passing) —
  // for those, api.search switches to POST automatically.
  const conversationsList = conversationsQuery.data
  const passingUuids = useMemo<string[] | undefined>(() => {
    if (!conversationsList) return undefined
    if (!filtersState.activeId) return undefined
    const activeNode = filtersState.nodes[filtersState.activeId]
    if (!activeNode || !activeNode.enabled) return undefined
    const passing = conversationsList.filter((c) =>
      applyActiveFilter(c.name, filtersState),
    )
    if (passing.length === conversationsList.length) return undefined
    return passing.map((c) => c.uuid)
  }, [conversationsList, filtersState])

  // Pin scope passed to useSearch. Pinned conversation/project is the
  // explicit user signal and always wins over the sidebar Source filter.
  // Bookmarks-as-scope plumbing exists end-to-end (api.search /
  // /api/search) for a future Starred sidebar value; not wired here yet.
  //
  // 2026-05-14 sidebar-scope propagation: organizationId (workspace
  // dropdown) and conversationUuids (active-filter set) compose into the
  // same scope object. queryKey changes when any of these flip → search
  // auto re-fires (spec invariant I4). When the user has pinned a
  // conversation, that pin remains the most-specific filter on the
  // BACKEND side (search.py strips conversation_uuids when
  // conversation_uuid is set), so we still pass the workspace and the
  // filter set without breaking the precedence rule.
  // 2026-05-24 (Cmd+G jump-back fix, defense-in-depth alongside the
  // upstream `scopesEqual` stabilization in SearchPinContext):
  // memoize across renders by SHALLOW-STRUCTURAL equality. Without
  // this, every recompute of the factory (triggered by ANY of the
  // deps' identity changing — pinScope, passingUuids, even the same-
  // value organizationId on a usePreferences refetch) produces a NEW
  // object literal. That fresh identity invalidates the activeMatch
  // reset effect downstream (whose deps include `scope`), drops the
  // index back to -1, fires auto-promote → user yanked back to match
  // 1 ("jumps then jumps back" bug, user report 2026-05-24).
  //
  // We use a ref to retain the last STABLE value; the factory
  // computes the candidate, structurally compares to the prior, and
  // returns the prior reference when they match. `useMemo` alone
  // doesn't help because its identity-equality check on the deps
  // never sees them as equal when they're new objects on every call.
  const lastScopeRef = useRef<{
    conversationUuid?: string
    projectPath?: string
    organizationId?: string | null
    conversationUuids?: string[]
  } | undefined>(undefined)
  const scope = useMemo(() => {
    const out: {
      conversationUuid?: string
      projectPath?: string
      organizationId?: string | null
      conversationUuids?: string[]
    } = {}
    if (pinScope.kind === 'conversation') out.conversationUuid = pinScope.uuid
    if (pinScope.kind === 'project') out.projectPath = pinScope.path
    if (organizationId) out.organizationId = organizationId
    if (passingUuids !== undefined) out.conversationUuids = passingUuids
    // Return undefined when there's truly no constraint, so the cache
    // key stays minimal for the common case.
    const candidate = Object.keys(out).length === 0 ? undefined : out
    // eslint-disable-next-line react-hooks/refs -- Identity-stabilizer pattern: structurally compare candidate to the last STABLE scope and reuse the prior reference when they match. useMemo's identity-equality on deps never sees pinScope/passingUuids as equal (new objects every parent render), so without this ref-read the queryKey for useSearch would change every render → cancel + refire → cursor reset cascade (see PLANS/POSTMORTEM-search-typing-lag-2026-05-22.md). Reading + writing during useMemo is the documented React idiom for cached-value-with-structural-equality.
    const prev = lastScopeRef.current
    if (scopeShapesEqual(prev, candidate)) {
      return prev
    }
    // eslint-disable-next-line react-hooks/refs -- See ref-read rationale on the matching read above; the write is the cache-population half of the same identity-stabilizer pattern.
    lastScopeRef.current = candidate
    return candidate
  }, [pinScope, organizationId, passingUuids])
  void bookmarks

  // Fetch search results (updated useSearch signature accepts contextSize).
  // `rawResponse` is the wrapped SearchResponse envelope; the per-conv
  // rollup lives at `.results`, and the truncation metadata
  // (total_messages_matched, returned_messages, truncated) lives at the
  // top level. We pass the envelope numbers through to consumers via the
  // SearchPanelContext value below so SearchPanel can render the footer.
  const { data: rawResponse, isLoading, isSearching } = useSearch(
    query,
    sourceFilter,
    contextSize,
    sortField,
    sortOrder,
    scope,
    includeToolCalls,
    includeCompactions,
  )

  const rawResults = rawResponse?.results

  // Client-side filter: while the user keeps typing (debounced query hasn't
  // caught up yet), narrow the stale results locally so additional characters
  // feel instantaneous. Also covers the case where conversation name or
  // message snippet contains the new query substring.
  //
  // Multi-word AND semantics (V1 polish 2026-05-14): when the query contains
  // whitespace we require every WORD (case-insensitive substring) to appear
  // in the snippet — NOT the literal whole-query substring. With AND-of-tokens
  // backend semantics, scattered tokens within a snippet are valid hits, so
  // a literal `.includes(lower)` check would falsely drop them. Quoted phrase
  // queries (`"foo bar baz"`) keep the literal-substring contract by stripping
  // the quotes and matching the inner phrase.
  //
  // In snippet mode this can still produce false negatives (one token may live
  // outside the ±150 char snippet window). Those matches reappear once the
  // debounced backend call resolves. Acceptable tradeoff for instant feel.
  //
  // Extracted as exported pure function `narrowSearchResults` for direct
  // unit testing of the null-safety contract (2026-05-18 council audit,
  // mirror of backend H1-H4): `r.conversation_name.toLowerCase()` and
  // `m.snippet.toLowerCase()` previously crashed if the wire format
  // surfaced null for those fields despite the TypeScript type saying
  // `string`. The exported function lets the test feed null fixtures
  // without standing up the full SearchPanelProvider dependency graph.
  const results = useMemo<SearchResult[]>(
    () => narrowSearchResults(rawResults, query),
    [rawResults, query],
  )

  // Envelope passthroughs — exposed via context so SearchPanel can
  // render the truncation footer. Defaults match the "no response yet"
  // state (server hasn't responded; we don't know what was matched).
  const totalMatched = rawResponse?.total_messages_matched ?? 0
  const returnedMatches = rawResponse?.returned_messages ?? 0
  const truncated = rawResponse?.truncated ?? false

  // Flatten results into navigable matches. The order is conversation-major,
  // message-minor — exactly the shape the backend returns from
  // `_sort_results` in `backend/search.py:_sort_results`. The backend is
  // the SINGLE SOURCE OF TRUTH for sort field + sort order; the frontend
  // does NOT re-sort.
  //
  // Sort contract (V1 polish 2026-05-14, Bug B SECOND fix):
  //   * Conversation-level sort key is `conversation_updated_at` (or
  //     `conversation_created_at`) directly — matching the date label
  //     the UI renders in each result card.
  //   * Within a conversation, messages sort by their own `created_at`
  //     (null → conversation_updated_at fallback). That's intentional:
  //     multiple matched messages inside the same conv group should
  //     appear in time order under the conv's title.
  //   * The previous version used `max(matched_msg.created_at)` as the
  //     conv-level key. That inverted the visible date column for any
  //     conv where the matched body was older than the conv's most
  //     recent activity. See backend/tests/test_search_sort_by_conversation_updated_at.py
  //     for the pin.
  //
  // `sortField`/`sortOrder` are NOT dependencies of this useMemo because
  // the backend already sorted `results`; the queryKey on `useSearch`
  // re-fires the request whenever they change, and `results` is re-derived
  // from `rawResults`.
  const flatMatches = useMemo<SearchMatch[]>(() => {
    const matches: SearchMatch[] = []
    for (const result of results) {
      // 2026-05-18 council audit: defensive `?? []` mirrors the
      // narrowSearchResults guard. Even when the query is short and
      // narrowSearchResults short-circuits to rawResults unchanged, a
      // wire-format drift surfacing matching_messages=null would
      // crash here at `.filter(...)`. Same mirror of backend
      // `(data.get(k) or "").lower()` pattern.
      const allMatches = result.matching_messages ?? []
      const messageMatches = allMatches.filter(
        (m) => m.message_uuid !== 'title'
      )
      // 2026-05-18: conversation_name may surface null at runtime
      // despite the TypeScript type; coalesce to '' so downstream
      // string consumers (SearchPanel render path) get a string.
      const conversationName = result.conversation_name ?? ''
      if (messageMatches.length > 0) {
        for (const msg of messageMatches) {
          matches.push({
            conversationUuid: result.conversation_uuid,
            messageUuid: msg.message_uuid,
            conversationName,
            snippet: msg.snippet,
            matchStart: msg.match_start,
            matchEnd: msg.match_end,
            sender: msg.sender,
            createdAt: msg.created_at,
            conversationUpdatedAt: result.conversation_updated_at,
            conversationCreatedAt: result.conversation_created_at,
            fragments: msg.fragments ?? null,
          })
        }
      } else {
        const titleMatch = allMatches.find(
          (m) => m.message_uuid === 'title'
        )
        matches.push({
          conversationUuid: result.conversation_uuid,
          messageUuid: 'title',
          conversationName,
          snippet: titleMatch?.snippet ?? conversationName,
          matchStart: titleMatch?.match_start ?? 0,
          matchEnd: titleMatch?.match_end ?? 0,
          sender: titleMatch?.sender ?? '',
          createdAt: null,
          conversationUpdatedAt: result.conversation_updated_at,
          conversationCreatedAt: result.conversation_created_at,
          fragments: titleMatch?.fragments ?? null,
        })
      }
    }
    return matches
  }, [results])

  // Reset activeMatchIndex (back to -1, "no active match") whenever
  // any input that invalidates the result set changes. The expanded
  // dep list — `query`, `sortField`, `sortOrder`, `contextSize`,
  // `scope`, `includeCompactions` — exactly mirrors
  // useSearch's queryKey inputs, so we re-arm the auto-promote effect
  // on the same edges that trigger a real backend re-fetch.
  //
  // The reset+auto-promote pair is load-bearing for the live-preview
  // UX: when the user types "needle" → results land → auto-promote
  // index 0; then the user changes sort order → results re-arrive →
  // auto-promote index 0 again.
  //
  // It must NOT re-promote on incidental react-query refetches
  // (refetchOnWindowFocus is `true` globally — see lib/queryClient.ts).
  // Those refetches DON'T change the queryKey, so `scope` etc. are
  // referentially stable, this effect doesn't re-fire, the reset to
  // -1 doesn't happen, and the auto-promote effect's `=== -1` guard
  // continues to suppress re-firing. Pinned by
  // e2e/search-auto-focus.spec.ts:129 "does NOT yank the user back".
  // oxlint-disable-next-line react-doctor/no-derived-state-effect -- The "use a key prop" recommendation doesn't apply: this state lives in a context provider that wraps the entire app (sibling of the router), not on a child component that could be keyed. The reset is intentional per the long block comment above and pinned by e2e/search-auto-focus.spec.ts. See the TODO inline for the React 19 migration plan.
  useEffect(() => {
    // react-doctor-disable-next-line react-doctor/no-chain-state-updates -- Phase 2: triggering edge is async (queryKey change + scope flips originating in OTHER providers), not a single user event. Moving this reset into the originating event handlers would miss non-local edges (pin, filter, workspace switch). Council verified the postmortem-pinned semantics hold; restructure would risk re-introducing the search-typing-lag.
    setActiveMatchIndexState(-1) // eslint-disable-line react-hooks/set-state-in-effect -- TODO React 19 migration: hoist activeMatchIndex alongside flatMatches as derived state. Today this resets a single int once per query-equivalent change — bounded cascade.
  }, [query, sortField, sortOrder, contextSize, scope, includeCompactions])

  // V1 polish: auto-focus the first match when results land for a fresh
  // query. Without this, the user types "needle", sees results in the
  // sidebar, but the conversation panel doesn't move — they have to
  // press Cmd+G or click a card to bootstrap navigation.
  //
  // Each gate is load-bearing:
  //   * !isSearching — wait for debounce + network to settle so we
  //     don't jump mid-keystroke (debounce in useSearch is 200ms,
  //     non-zero — verified before shipping).
  //   * flatMatches.length > 0 — don't waste a re-render on empty
  //     results.
  //   * activeMatchIndex === -1 — auto-promote ONCE per stable-query
  //     cycle; never overwrite the user's Cmd+G navigation when results
  //     refresh later (e.g., a new conversation arrives).
  //   * demonstratedFocusUuidRef.current === null — Bug 3 (2026-05-26)
  //     gate. When the user clicked a bubble or wheel/touch-scrolled
  //     the conv pane BEFORE the refetch landed, the ref is non-null
  //     and the auto-promote is suppressed. The user must explicitly
  //     navigate (Cmd+G, Enter, card click, or typing a new query) to
  //     re-arm. The ref is intentionally NOT in the dep list — refs
  //     don't subscribe. The effect re-fires when `activeMatchIndex`
  //     flips to -1 (the reset effect's job on every queryKey change),
  //     which is the moment the gate is consulted.
  //
  // The existing SearchPanel.tsx effect picks up this index change AND
  // the source flag, and calls navigateToMatch with `focus: false`
  // for source='auto' so DOM focus stays in the search input while
  // the conversation pane scrolls + flashes the yellow ring.
  // react-doctor-disable-next-line react-doctor/no-effect-chain -- Phase 2: this IS the demonstrated-focus arbitration the postmortem (PLANS/POSTMORTEM-search-typing-lag-2026-05-22.md) protects. The chain (reset effect above sets activeMatchIndex=-1, this effect reacts) is intentional and gated; pinned by e2e/search-auto-focus.spec.ts and search-match-focus-mismatch.spec.ts.
  useEffect(() => {
    if (
      !isSearching &&
      flatMatches.length > 0 &&
      activeMatchIndex === -1 &&
      demonstratedFocusUuidRef.current === null
    ) {
      // react-doctor-disable-next-line react-doctor/no-chain-state-updates -- Phase 2: source MUST be set before index so downstream subscribers see the 'auto' flag at the moment they observe the index change. Cannot be batched into a useReducer without restructuring the downstream navigateToMatch effect (large risk per postmortem; deferred). NOTE: ESLint's react-hooks/set-state-in-effect does NOT currently flag setStates inside a conditional branch of an effect, so no matching eslint-disable is needed (verified 2026-05-30).
      setActiveMatchSourceState('auto')
      // react-doctor-disable-next-line react-doctor/no-chain-state-updates -- Phase 2: see line above.
      setActiveMatchIndexState(0)
    }
  }, [isSearching, flatMatches.length, activeMatchIndex])

  // Bug 3 (2026-05-26) — re-arm auto-promote whenever the user types a
  // new query. Each new query is a fresh search session; stale "I
  // clicked a bubble during the LAST search" demonstrated focus must
  // not bleed across sessions. Distinct from the reset-to-`-1` effect
  // above: that one fires on ALL queryKey changes (toggle, sort, etc.)
  // and must NOT clear demonstrated focus on a toggle (the whole point
  // of the Bug 3 fix).
  useEffect(() => {
    demonstratedFocusUuidRef.current = null
  }, [query])

  const open = useCallback(() => {
    setIsOpenPref(true)
  }, [setIsOpenPref])

  const close = useCallback(() => {
    setIsOpenPref(false)
  }, [setIsOpenPref])

  const toggle = useCallback(() => {
    setIsOpenPref(!isOpen)
  }, [isOpen, setIsOpenPref])

  const setQuery = useCallback((q: string) => {
    setQueryState(q)
  }, [])

  const setContextSize = useCallback(
    (s: SearchContextSize) => {
      setContextSizePref(s)
    },
    [setContextSizePref],
  )

  const setSortField = useCallback(
    (f: SortField) => {
      setSortFieldPref(f)
    },
    [setSortFieldPref],
  )

  const setSortOrder = useCallback(
    (o: SortOrder) => {
      setSortOrderPref(o)
    },
    [setSortOrderPref],
  )

  const setActiveMatchIndex = useCallback(
    (i: number, source: ActiveMatchSource = 'user') => {
      // Order matters here: set the source BEFORE the index so that
      // any synchronous downstream subscriber that observes the index
      // change has already seen the new source on the same commit.
      // React 18 batches both into one render.
      setActiveMatchSourceState(source)
      setActiveMatchIndexState(i)
    },
    [],
  )

  const nextMatch = useCallback(() => {
    if (flatMatches.length === 0) return
    // Bug 3 (2026-05-26) — Cmd+G is an explicit search-nav gesture, NOT
    // a demonstrated-focus signal. Clear the latch BEFORE the state
    // update so a subsequent refetch (e.g. the user toggles Show
    // Compactions immediately after Cmd+G) re-arms auto-promote
    // correctly. Without this clear, the same toggle that the user
    // intended to "just narrow my results" would silently suppress
    // auto-promote forever within the session.
    demonstratedFocusUuidRef.current = null
    // Cmd+G is always user-initiated; pin source='user' so the
    // downstream effect moves DOM focus to the bubble (Cmd+C path).
    setActiveMatchSourceState('user')
    setActiveMatchIndexState((prev) => {
      if (prev < 0) return 0
      return (prev + 1) % flatMatches.length
    })
  }, [flatMatches.length])

  const prevMatch = useCallback(() => {
    if (flatMatches.length === 0) return
    // Same Bug 3 clear as `nextMatch` — Cmd+Shift+G is the symmetric
    // explicit nav gesture.
    demonstratedFocusUuidRef.current = null
    setActiveMatchSourceState('user')
    setActiveMatchIndexState((prev) => {
      if (prev < 0) return flatMatches.length - 1
      return (prev - 1 + flatMatches.length) % flatMatches.length
    })
  }, [flatMatches.length])

  const requestFocus = useCallback(() => {
    // 2026-05-22 perf fix: the original implementation ALWAYS bumped
    // focusRequestSeq, which triggered a Provider-wide re-render. On
    // the 16K-message conversation that re-rendered ConversationPage
    // and walked the reconciler through 20K message bubbles — even
    // with React.memo, the walk cost ~7.5 s of main-thread time
    // before the input could actually receive focus.
    //
    // Fast path: when the input is already mounted (panel open + tab
    // is search), focus it DIRECTLY with no state change. No
    // Provider re-render, no reconciliation, sub-millisecond focus.
    //
    // Slow path: the input isn't mounted yet → open the panel + bump
    // the seq so SearchPanel's effect retries the focus after the
    // mount paints.
    // Target SPECIFICALLY the SearchPanel's input (not the sidebar's
    // "Search titles and projects" input, which would match a generic
    // placeholder-substring selector). The SearchPanel input carries
    // `data-search-panel-input` (set in SearchPanel.tsx) for this
    // unambiguous lookup.
    const mountedInput = document.querySelector<HTMLInputElement>(
      'input[data-search-panel-input]'
    )
    if (isOpen && mountedInput) {
      mountedInput.focus()
      mountedInput.select()
      return
    }
    if (!isOpen) {
      setIsOpenPref(true)
    }
    setFocusRequestSeq((n) => n + 1)
  }, [isOpen, setIsOpenPref])

  // 2026-05-22 perf fix (mirror of the SettingsContext fix in
  // commit e0cc917): memoize the context value object so its identity
  // is stable across renders that don't change any of its fields.
  // Without this, every `setQuery` keystroke rebuilt the value as a
  // new inline literal, notifying every `useSearchPanel()` consumer
  // (ConversationPage, useKeyboardShortcuts, Sidebar). ConversationPage
  // re-rendering on every keystroke walks its 20K-MessageBubble .map()
  // through the reconciler — even with memo'd bubbles, the walk costs
  // ~600ms per keystroke and blocks the 200ms debounce timer for the
  // /api/search fetch. Symptom: typing felt like 10s before any
  // backend log line appeared, with the backend itself only taking
  // 3-4s once the request actually landed.
  //
  // The deps list is explicit (not via `useMemo` with no deps) so a
  // future addition to the value object catches at type-check time.
  const value = useMemo(
    () => ({
      isOpen,
      query,
      contextSize,
      sortField,
      sortOrder,
      activeMatchIndex,
      activeMatchSource,
      flatMatches,
      results,
      totalMatched,
      returnedMatches,
      truncated,
      isLoading,
      isSearching,
      open,
      close,
      toggle,
      setQuery,
      setContextSize,
      setSortField,
      setSortOrder,
      nextMatch,
      prevMatch,
      setActiveMatchIndex,
      focusRequestSeq,
      requestFocus,
      // Bug 3 (2026-05-26) — demonstrated-focus latch. Ref identity is
      // stable across renders (React guarantees `useRef` returns the
      // SAME object every render), and the callbacks are useCallback
      // with empty deps, so adding these three fields does NOT
      // invalidate the value identity except when one of the OTHER
      // fields above changes. Wheel-burst-triggered mutations of
      // `.current` do not flip identity.
      demonstratedFocusUuidRef,
      markDemonstratedFocus,
      clearDemonstratedFocus,
    }),
    [
      isOpen, query, contextSize, sortField, sortOrder,
      activeMatchIndex, activeMatchSource, flatMatches, results, totalMatched,
      returnedMatches, truncated, isLoading, isSearching,
      open, close, toggle, setQuery, setContextSize, setSortField,
      setSortOrder, nextMatch, prevMatch, setActiveMatchIndex,
      focusRequestSeq, requestFocus,
      markDemonstratedFocus, clearDemonstratedFocus,
    ],
  )

  return (
    <SearchPanelContext.Provider value={value}>
      {children}
    </SearchPanelContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components -- safe: context Provider + hook co-located by convention. HMR fast refresh falls back to full reload; no runtime impact.
export function useSearchPanel() {
  // Phase 3: React 19 use() replaces useContext().
  const context = use(SearchPanelContext)
  if (!context) {
    throw new Error('useSearchPanel must be used within a SearchPanelProvider')
  }
  return context
}

