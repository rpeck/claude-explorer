import { QueryClient } from '@tanstack/react-query'

// 2026-05-18 (Task A6): React Query staleTime/gcTime tuning.
//
// Backend warm `/api/conversations` is now ~87ms (was 5s). Refetching
// is cheap, so we want the sidebar to feel fresh on return-to-app
// rather than holding 5-min-stale data.
//
//   - Default staleTime: 30s. Any unrouted query inherits this.
//   - Default gcTime:    10min. Keeps inactive data warm across normal
//     conversation-pane navigation; bias is toward instant back-nav.
//   - conversations.list staleTime:   30s (redundant w/ default; explicit).
//   - conversations.detail staleTime: 5min. Detail rarely changes within a
//     session; Infinity would have suppressed refetchOnWindowFocus, which
//     we want when the fetch pipeline adds new messages out-of-band.
//
// Per-key staleTimes are hoisted to queryClient.setQueryDefaults below
// rather than inlined at the useQuery call sites. Single source of truth,
// statically testable via queryClient.getQueryDefaults().
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000, // 30 seconds
      gcTime: 10 * 60 * 1000, // 10 minutes — React Query v5 (renamed from cacheTime).
      retry: (failureCount, error) => {
        // Don't retry 404s. The previous form used `(error as any).status`
        // which disabled the type checker entirely — a string `"404"` from
        // a malformed error would slip past `=== 404` as false. TS 4.9+'s
        // `in` operator narrows `error` to `Error & Record<'status',
        // unknown>`, so the explicit `typeof` check is now load-bearing.
        if (
          error instanceof Error &&
          'status' in error &&
          typeof error.status === 'number' &&
          error.status === 404
        ) {
          return false
        }
        // Retry connection errors more times (backend might be starting)
        return failureCount < 5
      },
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 10000), // Exponential backoff, max 10s
    },
  },
})

// Per-key staleTime defaults. setQueryDefaults matches by key prefix, so
// `['conversations', 'list', { filters }]` and
// `[...detail(uuid), 'leaf', leaf]` both inherit correctly.
queryClient.setQueryDefaults(['conversations', 'list'], {
  staleTime: 30 * 1000, // 30 seconds
})
queryClient.setQueryDefaults(['conversations', 'detail'], {
  staleTime: 5 * 60 * 1000, // 5 minutes
})

// Query keys factory
export const queryKeys = {
  conversations: {
    all: ['conversations'] as const,
    list: (filters?: object) =>
      ['conversations', 'list', filters] as const,
    detail: (uuid: string) => ['conversations', 'detail', uuid] as const,
    tree: (uuid: string) => ['conversations', 'tree', uuid] as const,
  },
  search: (
    query: string,
    source?: string,
    contextSize?: string,
    sort?: string,
    sortOrder?: string,
    scope?: {
      conversationUuid?: string
      projectPath?: string
      bookmarks?: string[]
      // 2026-05-14 sidebar-scope propagation. organizationId + conversationUuids
      // are part of the queryKey so toggling the workspace dropdown OR the
      // active filter automatically re-fires the search without manual
      // re-issue (spec invariant I4).
      organizationId?: string | null
      conversationUuids?: string[]
    },
    // 2026-05-11: include_tool_calls toggles search scope. Must be part
    // of the key so toggling the UI's "Show tool calls" pref re-fires
    // the network request and the cache doesn't return stale results.
    includeToolCalls?: boolean,
    // 2026-05-26: include_compactions mirrors include_tool_calls — same
    // queryKey-membership rationale.
    includeCompactions?: boolean,
  ) => ['search', query, source, contextSize, sort, sortOrder, scope, includeToolCalls, includeCompactions] as const,
  config: ['config'] as const,
}
// Call after any fetch writes conversations to disk. The backend re-indexes
// search before it reports success, so both caches can refresh at once.
// Without the search invalidation, the Search Pane kept pre-fetch results
// for up to its 60 s staleTime, even when the user repeated the search.
export function invalidateAfterFetch(qc: QueryClient): Promise<void> {
  return Promise.all([
    qc.invalidateQueries({ queryKey: queryKeys.conversations.all }),
    qc.invalidateQueries({ queryKey: ['search'] }),
  ]).then(() => undefined)
}
