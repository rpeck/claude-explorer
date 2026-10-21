import type {
  AccountResponse,
  ConversationListItem,
  ConversationDetail,
  ConversationTree,
  ConversationFilters,
  OrgsResponse,
  SearchResponse,
  SortField,
  SortOrder,
  AppConfig,
  AppConfigStats,
  WatcherHealth,
  Bookmark,
  ApiError as ApiErrorType,
} from './types'
import { ApiError, isSearchResponse } from './types'
import { mockConversations, mockConversationDetails, filterConversations } from './mockData'

const BASE_URL = '/api'

/** How long the Quit button waits for the server to accept the stop. */
export const SHUTDOWN_TIMEOUT_MS = 10_000

// Set to true to use mock data (for development without backend)
const USE_MOCK_DATA = false

// 2026-05-18 (Hunt #5) + 2026-05-20 (S5 T2b): optional AbortSignal
// forwarded into fetch(). Originally plumbed through the two heaviest
// reads (`useConversations` list + `useConversation` detail). S5 T2b
// extended the plumbing to every queryFn — including the static
// endpoints (`getOrgs`, `getConfig`, `getConfigStats`,
// `getConversationTree`, and the standalone `fetchPrefs`) — for
// uniform abort behavior on cold-cache, slow-network, and explicit
// queryClient.cancelQueries() paths. The original Hunt #5 rationale
// ("warm-path latency is shorter than a React lifecycle tick") still
// holds, but the cost of threading is ~25 LOC and the upside is one
// invariant ("every queryFn forwards signal") instead of two.
async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${BASE_URL}${url}`, { signal })
  if (!response.ok) {
    throw new ApiError(response.status, await response.text())
  }
  return response.json()
}

export const api = {
  // `/api/conversations` returns the SKINNY ConversationListItem shape
  // (backend split, see PLANS/SPLIT_CONVERSATION_SCHEMA.md).
  // ConversationListItem strips summary, human_message_count, and
  // git_branch from the wire payload — none are read by the sidebar.
  getConversations: async (
    filters?: ConversationFilters,
    signal?: AbortSignal,
  ): Promise<ConversationListItem[]> => {
    if (USE_MOCK_DATA) {
      // Simulate network delay
      await new Promise((resolve) => setTimeout(resolve, 300))
      return filterConversations(mockConversations, filters?.search)
    }
    const params = new URLSearchParams()
    if (filters?.search) params.set('search', filters.search)
    if (filters?.starred !== undefined) params.set('starred', String(filters.starred))
    if (filters?.model) params.set('model', filters.model)
    if (filters?.source) params.set('source', filters.source)
    if (filters?.sort) params.set('sort', filters.sort)
    if (filters?.sortOrder) params.set('sort_order', filters.sortOrder)
    if (filters?.includePhantom) params.set('include_phantom', 'true')
    if (filters?.showArchived) params.set('show_archived', 'true')
    if (filters?.organization_id) params.set('organization_id', filters.organization_id)
    const query = params.toString()
    return fetchJson<ConversationListItem[]>(
      `/conversations${query ? `?${query}` : ''}`,
      signal,
    )
  },

  getOrgs: (signal?: AbortSignal): Promise<OrgsResponse> =>
    fetchJson<OrgsResponse>('/orgs', signal),

  getAccount: (signal?: AbortSignal): Promise<AccountResponse> =>
    fetchJson<AccountResponse>('/account', signal),

  // Stops the server (sidebar power button). The custom header makes a
  // cross-site page's request need a CORS preflight, which the server never
  // answers; see backend/routers/shutdown.py. The Quit dialog locks while
  // this runs, so a request that never answers must give up with a reason
  // the dialog can show (PR #6 review, 2026-10-19).
  shutdownServer: async (timeoutMs: number = SHUTDOWN_TIMEOUT_MS): Promise<void> => {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    let response: Response
    try {
      response = await fetch(`${BASE_URL}/shutdown`, {
        method: 'POST',
        headers: { 'X-Claude-Explorer-Action': 'shutdown' },
        signal: controller.signal,
      })
    } catch (error) {
      if (controller.signal.aborted) {
        const seconds = Math.max(1, Math.ceil(timeoutMs / 1000))
        throw new Error(
          `The server did not answer within ${seconds} ${seconds === 1 ? 'second' : 'seconds'}.`,
        )
      }
      throw error
    } finally {
      clearTimeout(timer)
    }
    if (!response.ok) {
      throw new ApiError(response.status, await response.text())
    }
  },

  getConversation: async (
    uuid: string,
    leaf?: string,
    signal?: AbortSignal,
  ): Promise<ConversationDetail> => {
    if (USE_MOCK_DATA) {
      await new Promise((resolve) => setTimeout(resolve, 200))
      const detail = mockConversationDetails[uuid]
      if (!detail) {
        throw new ApiError(404, 'Conversation not found')
      }
      return detail
    }
    const qs = leaf ? `?leaf=${encodeURIComponent(leaf)}` : ''
    return fetchJson<ConversationDetail>(`/conversations/${uuid}${qs}`, signal)
  },

  getConversationTree: (
    uuid: string,
    signal?: AbortSignal,
  ): Promise<ConversationTree> =>
    fetchJson<ConversationTree>(`/conversations/${uuid}/tree`, signal),

  search: async (
    query: string,
    source: 'all' | 'CLAUDE_AI' | 'CLAUDE_CODE' | 'CLAUDE_COWORK',
    contextSize: 'snippet' | 'full',
    sort: SortField,
    sortOrder: SortOrder,
    scope: {
      conversationUuid?: string
      projectPath?: string
      bookmarks?: string[]
      // 2026-05-14 (sidebar-scope propagation): Workspace dropdown and
      // active-filter graph propagate to search via these.
      organizationId?: string | null
      // The set of UUIDs that pass the active-filter graph. undefined
      // means "no constraint"; empty array means "filter excludes
      // everything" (backend short-circuits to []). Spec §2.
      conversationUuids?: string[]
    } | undefined,
    // 2026-05-11: REQUIRED, no default. The backend default is True (for
    // backward compat with external scripts hitting /api/search), but every
    // in-app call site MUST pass the user's showToolCalls preference so
    // hits in hidden tool/thinking blocks don't bleed into the sidebar.
    // Making this mandatory means TypeScript catches any new call site
    // that forgets to wire useSettings().showToolCalls.
    includeToolCalls: boolean,
    // 2026-05-26: REQUIRED, no default. Mirrors include_tool_calls — same
    // mandatory-arg pattern means TypeScript catches any new call site
    // that forgets to wire `!useSettings().hideCompactMarkers` (the
    // mapping: `Show Compactions` checkbox checked → includeCompactions
    // = !hideCompactMarkers = !false = true).
    includeCompactions: boolean,
    // 2026-05-18 (Hunt #5): optional AbortSignal. Wired from useSearch's
    // queryFn `({ signal }) => api.search(..., signal)` so a queryKey
    // change (user keeps typing past the 200ms debounce) or an unmount
    // cancels the in-flight `/api/search` request instead of letting the
    // backend spend seconds on FTS-fallback work the user no longer cares
    // about. React Query v5 dedupes by key but does NOT auto-abort the
    // prior in-flight request on key change unless `signal` is wired.
    signal?: AbortSignal,
  ): Promise<SearchResponse> => {
    // Transport choice (spec §2, 2026-05-14): GET CSV is unsafe past
    // ~6 KB of UUIDs. When `conversationUuids` is set AND non-trivially
    // sized, use POST with a JSON body. The backend supports both with
    // identical semantics — only the wire format differs.
    //
    // Threshold: any non-empty conversationUuids list switches to POST.
    // Empty array (filter excludes everything) — POST too, so the backend's
    // empty-set short-circuit fires uniformly. Absent (undefined) — GET.
    const usePost = scope?.conversationUuids !== undefined
    if (usePost) {
      const body: Record<string, unknown> = {
        q: query,
        source,
        context_size: contextSize,
        sort,
        sort_order: sortOrder,
        include_tool_calls: includeToolCalls,
        include_compactions: includeCompactions,
      }
      if (scope?.conversationUuid) body.conversation_uuid = scope.conversationUuid
      if (scope?.projectPath) body.project_path = scope.projectPath
      if (scope?.bookmarks && scope.bookmarks.length > 0) {
        body.bookmarks = scope.bookmarks
      }
      if (scope?.organizationId) body.organization_id = scope.organizationId
      // Always include conversation_uuids in POST (transport invariant:
      // POST is reserved for the active-filter case).
      body.conversation_uuids = scope?.conversationUuids ?? []
      const res = await fetch(`${BASE_URL}/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal,
      })
      if (!res.ok) {
        throw new ApiError(res.status, await res.text())
      }
      const responseBody: unknown = await res.json()
      if (!isSearchResponse(responseBody)) {
        throw new ApiError(500, 'malformed /api/search response envelope')
      }
      return responseBody
    }

    const params = new URLSearchParams({ q: query })
    if (source !== 'all') params.set('source', source)
    if (contextSize !== 'snippet') params.set('context_size', contextSize)
    if (sort !== 'updated_at') params.set('sort', sort)
    if (sortOrder !== 'desc') params.set('sort_order', sortOrder)
    if (scope?.conversationUuid) params.set('conversation_uuid', scope.conversationUuid)
    if (scope?.projectPath) params.set('project_path', scope.projectPath)
    if (scope?.bookmarks && scope.bookmarks.length > 0) {
      params.set('bookmarks', scope.bookmarks.join(','))
    }
    if (scope?.organizationId) params.set('organization_id', scope.organizationId)
    // Only append the query param when filtering — keeps URLs short
    // for the common-case (tool calls visible) request.
    if (!includeToolCalls) params.set('include_tool_calls', 'false')
    // 2026-05-26: same pattern for compactions — only set the param when
    // the user has hidden them, so the URL stays short for the default
    // "show everything" case.
    if (!includeCompactions) params.set('include_compactions', 'false')
    // GET branch — signal threaded through fetchJson so a queryKey change
    // (user keeps typing) aborts the in-flight request mirror-image of the
    // POST branch above. Without this the React Query queryKey-change path
    // dedupes but does not cancel; backend keeps computing.
    const url = `/search?${params.toString()}`
    const response = await fetch(`${BASE_URL}${url}`, { signal })
    if (!response.ok) {
      throw new ApiError(response.status, await response.text())
    }
    const body: unknown = await response.json()
    if (!isSearchResponse(body)) {
      throw new ApiError(500, 'malformed /api/search response envelope')
    }
    return body
  },

  getConfig: (signal?: AbortSignal): Promise<AppConfig> =>
    fetchJson<AppConfig>('/config', signal),

  getConfigStats: (signal?: AbortSignal): Promise<AppConfigStats> =>
    fetchJson<AppConfigStats>('/config/stats', signal),

  // PLANS/2026.05.26-watcher-install-detection.md Phase 3.
  getWatcherHealth: (signal?: AbortSignal): Promise<WatcherHealth> =>
    fetchJson<WatcherHealth>('/health/watcher', signal),

  exportMarkdown: (
    uuid: string,
    showToolCalls: boolean = true,
    includeCompact: boolean = false,
  ): Promise<Response> =>
    fetch(
      `${BASE_URL}/conversations/${uuid}/export/markdown?include_tools=${showToolCalls}&include_compact=${includeCompact}`,
    ),

  // Issue #4 — Markdown bundle (zip with conversation.md + images/).
  exportMarkdownBundle: (
    uuid: string,
    showToolCalls: boolean = true,
    dialect: 'commonmark' | 'obsidian' = 'commonmark',
    includeCompact: boolean = false,
  ): Promise<Response> =>
    fetch(
      `${BASE_URL}/conversations/${uuid}/export/markdown-bundle?include_tools=${showToolCalls}&dialect=${dialect}&include_compact=${includeCompact}`,
    ),

  exportPdf: (
    uuid: string,
    showToolCalls: boolean = true,
    signal?: AbortSignal,
    includeCompact: boolean = false,
  ): Promise<Response> =>
    fetch(
      `${BASE_URL}/conversations/${uuid}/export/pdf?include_tools=${showToolCalls}&include_compact=${includeCompact}`,
      { signal },
    ),

  exportAllMarkdown: (includeCompact: boolean = false): Promise<Response> =>
    fetch(`${BASE_URL}/export/all/markdown?include_compact=${includeCompact}`),

  // Fetch operations (Claude Desktop only).
  //
  // The inline `Promise<{...}>` mirrors the backend `FetchStatus`
  // Pydantic model in `backend/routers/fetch.py`. Inline (not in
  // `lib/types.ts`) because `api.ts` is the only consumer; per the
  // Task B Pydantic↔TS drift audit (2026-05-18, Decision Record #8),
  // we leave it co-located until a second consumer appears.
  getFetchStatus: async (): Promise<{
    has_credentials: boolean
    credentials_path: string
    output_dir: string
    existing_count: number
    credentials_age_days: number | null
    // True while a fetch or Refresh runs (a new one would get HTTP 409).
    // Optional: older servers do not send it.
    fetch_in_progress?: boolean
  }> => fetchJson('/fetch/status'),

  forceRefetchConversation: async (uuid: string): Promise<{ uuid: string; status: string; name: string }> => {
    const r = await fetch(`${BASE_URL}/fetch/conversation/${uuid}`, { method: 'POST' })
    if (!r.ok) {
      // Build-9 Bug 3: surface the backend's friendly `detail` string,
      // not the raw `{"detail":"..."}` JSON. The route now returns
      // user-facing copy in `detail` for 404/401/503; falling back to
      // the raw text only when the body isn't valid JSON.
      const raw = await r.text()
      let message = raw
      try {
        const parsed = JSON.parse(raw)
        if (typeof parsed?.detail === 'string') {
          message = parsed.detail
        }
      } catch {
        // Body isn't JSON — keep the raw text.
      }
      throw new ApiError(r.status, message)
    }
    return r.json()
  },

  // Returns EventSource for SSE streaming
  startFetch: (incremental: boolean = true): EventSource => {
    const params = new URLSearchParams()
    if (!incremental) params.set('incremental', 'false')
    return new EventSource(`${BASE_URL}/fetch/start?${params.toString()}`)
  },

  // Build-9: combined capture + fetch pipeline (the Refresh button's owner).
  startRefresh: (incremental: boolean = true): EventSource => {
    const params = new URLSearchParams()
    if (!incremental) params.set('incremental', 'false')
    return new EventSource(`${BASE_URL}/fetch/refresh?${params.toString()}`)
  },

  // Bookmarks (Build-4)
  listBookmarks: async (): Promise<Bookmark[]> => {
    const r = await fetch(`${BASE_URL}/bookmarks`)
    if (!r.ok) throw new ApiError(r.status, await r.text())
    const body = (await r.json()) as { bookmarks: Bookmark[] }
    return body.bookmarks
  },

  createBookmark: async (input: Omit<Bookmark, 'id' | 'created_at'>): Promise<Bookmark> => {
    const r = await fetch(`${BASE_URL}/bookmarks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    })
    if (!r.ok) throw new ApiError(r.status, await r.text())
    return r.json()
  },

  updateBookmark: async (id: string, partial: Partial<Pick<Bookmark, 'note' | 'snippet'>>): Promise<Bookmark> => {
    const r = await fetch(`${BASE_URL}/bookmarks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(partial),
    })
    if (!r.ok) throw new ApiError(r.status, await r.text())
    return r.json()
  },

  deleteBookmark: async (id: string): Promise<void> => {
    const r = await fetch(`${BASE_URL}/bookmarks/${id}`, { method: 'DELETE' })
    if (!r.ok) throw new ApiError(r.status, await r.text())
  },
}

export type { ApiErrorType }