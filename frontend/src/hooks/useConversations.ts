import { useMemo, useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryClient'
import type { ConversationFilters, SortField, SortOrder, SourceFilter } from '@/lib/types'

export function useConversations(
  filters?: ConversationFilters,
  options?: { enabled?: boolean },
) {
  const { search, ...serverFilters } = filters ?? {}

  // Fetch the full list without search — stable cache key across keystrokes.
  //
  // `options.enabled` defaults to true (existing callers unchanged). The
  // SearchPanelProvider opt-in to false when there's no active filter,
  // because: (a) the list is only needed to resolve filter→UUIDs, so
  // fetching it when no filter exists is wasted work, and (b) a fetch
  // in-flight during page navigation has been observed to trip
  // Chromium's net::ERR_NETWORK_CHANGED on reload in headless
  // Playwright (2026-05-15 regression diagnosis). Gating the fetch
  // avoids both costs.
  // staleTime/gcTime are inherited from queryClient.setQueryDefaults
  // for the ['conversations', 'list'] key prefix (see lib/queryClient.ts).
  // Task A6 (2026-05-18): list is 30s — refresh aggressively now that the
  // backend warm path is ~87ms.
  const query = useQuery({
    queryKey: queryKeys.conversations.list(serverFilters),
    // 2026-05-18 (Hunt #5): plumb queryFn's AbortSignal into api.getConversations
    // so component unmount cancels the in-flight list fetch. Also defuses the
    // React 19 StrictMode dev-mode double-fire: the first mount's request is
    // aborted when StrictMode immediately remounts, instead of completing,
    // landing in the cache, and triggering the placeholderData/observer
    // settle path twice.
    queryFn: ({ signal }) => api.getConversations(serverFilters, signal),
    enabled: options?.enabled ?? true,
  })

  // Filter client-side — no network round-trip per keystroke.
  // Scope: title (`name`) OR `project_path` only. Intentionally NOT
  // `summary`, because users typing in the sidebar's "Search titles
  // and projects" input expect the placeholder's promise: matches must
  // come from the title or the project path, not the body summary.
  // (P1.2, 2026-05-04.)
  //
  // Null-safety (2026-05-18 council audit, mirror of backend H1-H4):
  // `c.name` is typed `string` but the backend can drift (older on-disk
  // JSONs, partial Pydantic serialization) and surface `null` at
  // runtime. Without `?? ''` here, the unguarded `.toLowerCase()` call
  // throws `TypeError: Cannot read properties of null (reading
  // 'toLowerCase')` and white-screens the sidebar on every keystroke.
  // This mirrors the backend `(data.get(k) or "").lower()` invariant.
  const data = useMemo(() => {
    if (!search?.trim() || !query.data) return query.data
    const lower = search.toLowerCase()
    return query.data.filter(c =>
      (c.name ?? '').toLowerCase().includes(lower) ||
      (c.project_path ?? '').toLowerCase().includes(lower)
    )
  }, [query.data, search])

  return { ...query, data }
}

export function useConversation(uuid: string, leaf?: string) {
  // staleTime is inherited from queryClient.setQueryDefaults for the
  // ['conversations', 'detail'] key prefix (see lib/queryClient.ts).
  // Task A6 (2026-05-18): detail is 5min — previously Infinity, which
  // suppressed refetchOnWindowFocus and let the fetch pipeline's new
  // messages stay invisible until a hard refresh.
  return useQuery({
    queryKey: leaf
      ? [...queryKeys.conversations.detail(uuid), 'leaf', leaf]
      : queryKeys.conversations.detail(uuid),
    // 2026-05-18 (Hunt #5): plumb queryFn's AbortSignal into api.getConversation
    // so navigating between conversations via keyboard (fast back-to-back
    // mounts) cancels the in-flight multi-MB detail fetch for the conversation
    // the user left. Without this the backend keeps serializing a large payload
    // the cache will discard.
    queryFn: ({ signal }) => api.getConversation(uuid, leaf, signal),
    enabled: !!uuid,
  })
}

export function useConversationTree(
  uuid: string,
  options?: { enabled?: boolean },
) {
  // Hunt #5 (2026-05-18): was `staleTime: Infinity`, which is wrong because
  // the tree IS mutable — the fetch pipeline can ingest a new branch for an
  // already-loaded conversation, and `Infinity` would have suppressed
  // refetchOnWindowFocus so the tree modal showed pre-branch state until a
  // hard refresh. 5min mirrors useConversation's setQueryDefaults TTL.
  //
  // 2026-05-23 (Commit 6 — duplicate-fetch fix): added optional
  // `options.enabled` so consumers can gate the query on whether the
  // tree-modal is actually open. Pre-fix, TreeViewModal called this
  // hook UNCONDITIONALLY before its `if (!isOpen) return null` early
  // return — the query fired the moment the component mounted, not
  // when the user clicked "View branches". Combined with React 19
  // StrictMode dev-mode double-mount the same /tree query fired
  // 2× per nav. Default to true to preserve backward compat for any
  // future consumer that doesn't want gating.
  const enabled = (options?.enabled ?? true) && !!uuid
  return useQuery({
    queryKey: queryKeys.conversations.tree(uuid),
    queryFn: ({ signal }) => api.getConversationTree(uuid, signal),
    enabled,
    staleTime: 5 * 60 * 1000,
  })
}

export function useSearch(
  query: string,
  source: SourceFilter = 'all',
  contextSize: 'snippet' | 'full' = 'snippet',
  sort: SortField = 'updated_at',
  sortOrder: SortOrder = 'desc',
  scope: {
    conversationUuid?: string
    projectPath?: string
    bookmarks?: string[]
    // 2026-05-14 sidebar-scope propagation: workspace + active-filter set.
    organizationId?: string | null
    conversationUuids?: string[]
  } | undefined = undefined,
  // 2026-05-11: REQUIRED, no default. Threaded all the way down to the
  // /api/search query param. Mandatory so any future call site is
  // forced (via TypeScript) to wire in useSettings().showToolCalls
  // rather than silently inheriting an unfiltered default — which
  // would re-introduce the sidebar/conv-pane mismatch bug.
  includeToolCalls: boolean,
  // 2026-05-26: REQUIRED, no default. Same mandatory-arg pattern as
  // includeToolCalls. Maps `Show Compactions` checkbox via
  // `includeCompactions = !useSettings().hideCompactMarkers`.
  includeCompactions: boolean,
) {
  const [debouncedQuery, setDebouncedQuery] = useState(query)
  const queryClient = useQueryClient()

  // Debounce the live ``query`` state into ``debouncedQuery`` so the
  // queryKey only changes after the user stops typing for 200 ms. This
  // is the existing behavior — unchanged in 2026-05-22.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQuery(query), 200)
    return () => clearTimeout(id)
  }, [query])

  // 2026-05-22 (council fix, live-Playwright follow-up):
  // cancel any in-flight /api/search request immediately before the
  // NEXT one fires, AND on unmount. ``debouncedQuery`` changing is the
  // exact event where React Query is about to invoke ``queryFn`` for a
  // new key; firing ``cancelQueries`` here both (a) aborts the prior
  // observer's still-running fetch (its key just became stale), and
  // (b) frees the backend threadpool slot for the about-to-start one.
  //
  // Why not co-locate with the debounce ``useEffect`` above:
  // that effect fires on EVERY keystroke (because ``query`` changes
  // every render), but the prior fetch isn't actually in flight on
  // most of those keystrokes — it's still waiting for its own debounce
  // to elapse. Calling cancelQueries on every keystroke is just noise.
  // Firing it on ``debouncedQuery`` change is the precise moment a NEW
  // fetch is about to start AND the OLD fetch (if any) is in flight.
  //
  // Why the explicit cancel is necessary even though
  // ``api.search(..., signal)`` already plumbs the AbortSignal:
  // React Query v5's default ``gcTime: 5min`` keeps the prior query
  // alive in the cache when its observer rebinds to a new key. The
  // prior query's in-flight fetch is allowed to complete so the cache
  // is primed for future observers of the same key. For a search box
  // where the prior key (``q='aardvar'``) will essentially never be
  // re-observed (the user typed past it), that's just wasted backend
  // CPU and a threadpool slot. ``cancelQueries`` overrides the
  // gc-friendly default and aborts the AbortController directly.
  //
  // Prefix match on ``['search']`` covers all search queries
  // regardless of source/contextSize/scope. The only consumer of
  // this prefix today is ``useSearch`` itself, so the broad scope
  // is safe — and intentional (any independent SearchPanel mount
  // would still want this behavior).
  useEffect(() => {
    return () => {
      void queryClient.cancelQueries({ queryKey: ['search'] })
    }
  }, [debouncedQuery, queryClient])

  const queryResult = useQuery({
    // Include includeToolCalls AND includeCompactions in the key so
    // toggling either re-fires the network call and React Query doesn't
    // return a stale cached payload that contains hidden-content snippets.
    queryKey: queryKeys.search(debouncedQuery, source, contextSize, sort, sortOrder, scope, includeToolCalls, includeCompactions),
    // 2026-05-18 (Hunt #5): plumb queryFn's AbortSignal into api.search so
    // a queryKey change OR component unmount cancels the in-flight `/api/search`
    // request. Critical for the FTS-fallback slow path (multi-second) where
    // orphan searches were burning local-backend CPU after the user kept
    // typing past the 200ms debounce.
    queryFn: ({ signal }) =>
      api.search(debouncedQuery, source, contextSize, sort, sortOrder, scope, includeToolCalls, includeCompactions, signal),
    enabled: debouncedQuery.length >= 2,
    staleTime: 60 * 1000, // 1 minute
    // 2026-05-22 (per CLAUDE-TESTING §5.13): keep previous data only for
    // changes WITHIN the same `contextSize` (typing keeps narrowing
    // results visible — the UX that justifies placeholder in the first
    // place). When `contextSize` flips, the previous results have a
    // categorically different shape (snippet ~200 chars vs full message
    // body 10K+ chars) and would create a visual lie: the Snippet/Full
    // toggle's highlight updates immediately, but stale full-mode
    // cards would still be rendered until the new fetch lands. That
    // mismatch IS the "reversed sense" bug the user reported on the
    // 16K-message conversation. queryKey index 3 is contextSize per
    // queryKeys.search signature.
    placeholderData: (previousData, previousQuery) => {
      if (!previousData || !previousQuery) return undefined
      const prevCtx = previousQuery.queryKey[3]
      if (prevCtx !== contextSize) return undefined
      return previousData
    },
  })

  // Bug B (2026-05-03): the SearchPanel needs a unified "is the search
  // in flight or about to be in flight?" signal so it can show a
  // loading affordance instead of a misleading "No matches".
  //
  //   - isLoading: only true on the *first* fetch for a key (TanStack v5).
  //     Subsequent fetches with `placeholderData: keepPreviousData`
  //     leave isLoading=false and surface state via isFetching only.
  //   - isFetching: true whenever any request is in flight (initial OR
  //     refetch).
  //   - The 200ms debounce window is also "search is about to fire"
  //     time, during which we shouldn't claim "No matches" either.
  //
  // We OR all three together into a single flag the consumer can rely
  // on. (We keep the original isLoading on the result so callers that
  // care about first-fetch-only can still discriminate.)
  const debouncing = query.trim() !== debouncedQuery.trim() && query.length >= 2
  const isSearching = queryResult.isLoading || queryResult.isFetching || debouncing

  return { ...queryResult, isSearching }
}

export function useConfigStats() {
  // Slower /config/stats endpoint that populates conversation_count.
  // Use ONLY where the user is willing to wait (e.g. Settings page).
  //
  // Hunt #5 (2026-05-18): was `staleTime: Infinity`, which is wrong because
  // `conversation_count` changes after every `claude-explorer fetch` run
  // and the Settings page is exactly where users go to see "how many
  // conversations do I have?". `Infinity` left the page showing the
  // pre-fetch count after a successful refresh. 60s is a reasonable TTL
  // given the endpoint's cost; window-focus refetch fills the gap.
  return useQuery({
    queryKey: ['config-stats'],
    queryFn: ({ signal }) => api.getConfigStats(signal),
    staleTime: 60 * 1000,
  })
}

export function useConfig() {
  // Layer 3 of PLANS/2026.05.18-config-corruption-safe-mode.md.
  //
  // staleTime was Infinity, which suppressed refetches and would have
  // pinned the corruption banner to its first-fetched value for the
  // life of the tab — defeating the user's "fix config, refresh UI"
  // recovery path. 60s mirrors useConfigStats so the banner clears
  // within a minute of the file being repaired, and the default
  // ``refetchOnWindowFocus: true`` makes the banner clear within
  // ~1 RTT when the user tabs back from the editor.
  //
  // The backend route clears its lru_cache on every /api/config call
  // (backend/routers/config.py:get_config), so a refetch always sees
  // the current on-disk state — no caching layer can stale the
  // banner.
  return useQuery({
    queryKey: queryKeys.config,
    queryFn: ({ signal }) => api.getConfig(signal),
    staleTime: 60 * 1000,
  })
}
