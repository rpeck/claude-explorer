// Conversation types

export type ConversationSource = 'CLAUDE_AI' | 'CLAUDE_CODE' | 'CLAUDE_COWORK'

export interface SubagentSummary {
  uuid: string
  agent_id: string
  name: string
  model: string
  created_at: string
  updated_at: string
  message_count: number
}

// Skinny per-row payload served by `/api/conversations`. Mirrors the
// backend's `ConversationListItem` (backend/models.py). Three fields
// from `ConversationSummary` (summary, human_message_count, git_branch)
// are intentionally absent here because:
//
//   - `summary` is only consumed by the backend's server-side
//     `?search=` filter (runs BEFORE the projection) and by MCP
//     `export_session`. The sidebar never renders it.
//   - `human_message_count` is only consumed by MCP `list_sessions`.
//     The sidebar never renders it.
//   - `git_branch` is only rendered by the conversation detail page,
//     which goes through `ConversationDetail` (which still extends
//     `ConversationSummary`, where the field stays).
//
// See PLANS/SPLIT_CONVERSATION_SCHEMA.md.
export interface ConversationListItem {
  uuid: string
  name: string
  model: string
  created_at: string
  updated_at: string
  is_starred: boolean
  message_count: number
  has_branches: boolean
  source: ConversationSource
  project_path?: string | null
  project_name?: string | null
  // cowork-multi-org C6: workspace metadata. Null for legacy untagged
  // JSONs that haven't been re-fetched yet.
  organization_id?: string | null
  organization_name?: string | null
  subagents?: SubagentSummary[]
  // D8 (Cowork, 2026-05-25): sidecar.isArchived mirror. False for
  // Desktop + Claude Code. Server default-hides archived sessions;
  // when present and true, the sidebar can render an "Archived"
  // badge under the "Show archived" toggle.
  is_archived?: boolean
}

// Full per-conversation payload. Returned by `GET /api/conversations/{uuid}`
// and extended by `ConversationDetail`. The three fields below
// (`summary`, `human_message_count`, `git_branch`) are STRIPPED from
// the `/api/conversations` LIST wire format (see `ConversationListItem`
// above) but stay on this interface because the per-conversation
// endpoint, MCP tools, and the detail-page render path all read them.
export interface ConversationSummary {
  uuid: string
  name: string
  // Stays on ConversationSummary because the backend's
  // /api/conversations?search= filter matches against it (server-side,
  // BEFORE the ConversationListItem projection runs) and MCP
  // `export_session` threads it through the sliced ConversationDetail.
  // Stripped from `ConversationListItem` — the sidebar never renders it.
  summary: string
  model: string
  created_at: string
  updated_at: string
  is_starred: boolean
  message_count: number
  // Stays on ConversationSummary for the MCP server's public
  // `list_sessions` output (mcp_server/SPEC.md). Stripped from
  // `ConversationListItem` — not consumed by the frontend.
  human_message_count: number
  has_branches: boolean
  source: ConversationSource
  project_path?: string | null
  project_name?: string | null
  // Stays on ConversationSummary for ConversationPage's Details
  // disclosure (ConversationDetail extends this interface, so the
  // field must live here). Stripped from `ConversationListItem` —
  // the sidebar never renders it.
  git_branch?: string | null
  // cowork-multi-org C6: workspace metadata. Null for legacy untagged
  // JSONs that haven't been re-fetched yet.
  organization_id?: string | null
  organization_name?: string | null
  subagents?: SubagentSummary[]
  // D8 (Cowork, 2026-05-25): sidecar.isArchived mirror. False for
  // Desktop + Claude Code. Server default-hides archived sessions;
  // when present and true, the sidebar can render an "Archived"
  // badge under the "Show archived" toggle.
  is_archived?: boolean
}

// cowork-multi-org C6: workspace selector source.
export interface Org {
  org_id: string
  name: string | null
  is_primary: boolean
}

export interface OrgsResponse {
  authenticated: boolean
  orgs: Org[]
}

// `/api/account`: the Anthropic account the captured credentials belong
// to. Mirrors `AccountResponse` in backend/models.py. `error` is set when
// credentials exist but the identity lookup failed.
export interface AccountResponse {
  authenticated: boolean
  account_uuid: string | null
  email: string | null
  full_name: string | null
  display_name: string | null
  org_name: string | null
  error: string | null
}

export interface ImageBlockSource {
  type: 'base64' | 'url'
  // base64
  media_type?: string
  data?: string
  // url variant (claude.ai sometimes uses this for hosted refs)
  url?: string
}

export interface ContentBlock {
  // 'thinking' carries Claude's internal reasoning text (CC + extended-
  // thinking-enabled Desktop). The V1 viewer has no `case 'thinking':`
  // renderer in MessageBubble.tsx — these blocks are silently dropped
  // from the rendered output (paired with the backend search exclusion
  // in search.py:_extract_searchable_text). The type is included here
  // so the field round-trips through the API without TypeScript
  // narrowing it to `never`, and so any future "Show thinking"
  // affordance can branch on it without re-widening the union.
  //
  // Pydantic↔TS drift note (Task B, 2026-05-18): the backend
  // `ContentBlock.type` is `str` (any string), while this TS union is
  // closed. Mismatch is intentional: if Anthropic ships a new block
  // type, the BE passes it through and every consumer (MessageBubble,
  // lib/utils.ts, ConversationLightboxContext) routes through a
  // `default: return null` branch — runtime safe. Do NOT tighten the
  // BE to `Literal[...]`: that would reject new block types at the
  // ingest layer and break /api/conversations on the next Anthropic
  // release. Add the new variant to this TS union when you're ready
  // to render it.
  type: 'text' | 'tool_use' | 'tool_result' | 'image' | 'thinking'
  text?: string
  name?: string // tool_use
  input?: unknown // tool_use
  content?: ContentBlock[] // tool_result
  // image content blocks (Claude Code embeds images inline as base64
  // here, alongside a sibling text block that carries the "[Image #N]"
  // marker).
  source?: ImageBlockSource
}

// Image asset metadata (thumbnail or preview variant). Returned by
// claude.ai's chat_conversations API inside Message.files / files_v2.
export interface ImageAsset {
  url: string
  file_variant?: string
  primary_color?: string
  image_width?: number
  image_height?: number
}

export interface ImageFile {
  file_kind: 'image' | string
  file_uuid: string
  file_name: string
  created_at: string
  thumbnail_url?: string
  thumbnail_asset?: ImageAsset
  preview_asset?: ImageAsset
  // Other file_kind values (e.g. 'document') exist; they're carried through
  // but the renderer only treats file_kind === 'image' specially.
}

// Pydantic↔TS contract note (Task B drift audit, 2026-05-18): the
// fields below marked optional (`?:`) — `files_v2`,
// `is_command_marker`, `is_prelude`, `assistant_canned_response_consumed`,
// `slash_command` — are ALWAYS emitted by the backend (Pydantic
// defaults at construction time). The `?` marker is a deliberate
// defensive lie that keeps frontend mock construction friction-free.
// Consumers MUST treat these as possibly-absent (`?? false` / `?? []`)
// because the type system says so, even though the runtime guarantee
// is stronger. See `backend/models.py:Message` for the full rationale.
export interface Message {
  uuid: string
  sender: 'human' | 'assistant'
  text: string
  content: ContentBlock[]
  created_at: string
  updated_at: string
  truncated: boolean
  parent_message_uuid: string | null
  attachments: unknown[]
  files: ImageFile[]
  // claude.ai sometimes ships a v2 array alongside the legacy 'files'
  // with overlapping entries. Render after deduping by file_uuid.
  files_v2?: ImageFile[]
  // CC-only flags emitted by the backend's collapse/fold/prelude passes
  // for slash-command boilerplate (V1 polish, 2026-05-12). Absent on
  // Desktop messages and on regular CC turns.
  //   - is_command_marker: this row is the synthetic "Session: /foo"
  //     marker that replaces a /exit-style triplet.
  //   - is_prelude: this marker is part of the LEADING run of session
  //     prelude markers. The ConversationPage hides these by default
  //     and surfaces them via <SessionPreludeAffordance />.
  //   - assistant_canned_response_consumed: the marker absorbed CC's
  //     literal "No response requested." reply. Carried for debugging
  //     / future UI surfacing; renderers currently ignore it.
  is_command_marker?: boolean
  is_prelude?: boolean
  assistant_canned_response_consumed?: boolean
  // Slash-command name (e.g. "/coding", "/plan") for CC command-marker
  // rows; null/undefined for any other message. V1 polish round 3,
  // 2026-05-12 — paired with the args-preservation change that puts the
  // user's real prompt text into `Message.text` when the command carried
  // `<command-args>`. The MessageBubble renders a muted
  // `<SlashCommandBadge command="/coding" />` above the body whenever
  // this is truthy. Render guard MUST be `if (message.slash_command)`
  // so empty-string / null / undefined are all skipped.
  slash_command?: string | null
}

export interface CompactMarker {
  message_uuid: string
  summary_text: string
  timestamp: string
  kind: 'auto' | 'manual'
  user_prompt: string | null
}

export interface ConversationDetail extends ConversationSummary {
  messages: Message[]
  current_leaf_message_uuid: string
  file_path?: string | null
  compact_markers?: CompactMarker[]
  // CC-only count of LEADING is_prelude messages (V1 polish, 2026-05-12).
  // 0 for Desktop conversations and for CC conversations whose first
  // message isn't a slash-command marker.
  prelude_hidden_count?: number
  // D9 (Cowork, 2026-05-25): sidecar.error — a plain string the Cowork
  // harness writes when a session ended unexpectedly. The detail view
  // renders an alert banner above the message stream when non-null.
  // Null for Desktop + Claude Code (no session-fault field).
  error?: string | null
  // D10 (Cowork, 2026-05-25): sidecar.cwd — the VM sandbox path
  // (e.g. /sessions/<vm>). Detail view renders as plain text labeled
  // "Sandbox path". Null for non-Cowork sources.
  sandbox_path?: string | null
}

export interface MessageNode {
  message: Message
  children: MessageNode[]
}

export interface ConversationTree {
  uuid: string
  root_messages: MessageNode[]
  active_path: string[]
}

// Search types

/**
 * Structured highlight fragment (Phase-2 Workstream A wire-format
 * addition). The FTS5 fast path emits a list of these so the
 * renderer can wrap matches in `<mark>` without parsing inline
 * HTML and without a sanitizer dependency. Each fragment is
 * either plain text (`mark: false`) or a highlighted match
 * (`mark: true`); concatenating `fragment.text` in order
 * reconstructs the rendered snippet.
 */
export interface SnippetFragment {
  text: string
  mark: boolean
}

export interface MessageSnippet {
  message_uuid: string
  sender: string
  snippet: string
  match_start: number
  match_end: number
  created_at: string | null
  /** Structured highlight fragments populated by the FTS5 fast path
   *  (context_size='snippet'). null on the linear-scan fallback and
   *  on context_size='full' responses; in that case the renderer
   *  falls back to the legacy snippet + match_start/match_end +
   *  live-query token scan logic. */
  fragments?: SnippetFragment[] | null
}

export interface SearchResult {
  conversation_uuid: string
  conversation_name: string
  conversation_updated_at: string
  conversation_created_at: string
  project_name: string | null
  matching_messages: MessageSnippet[]
}

/**
 * Wrapped /api/search response. Mirrors backend `SearchResponse`
 * (backend/models.py). The four fields tell the UI when the FTS5
 * fast path's LIMIT clipped the result set so the sidebar can render
 * a truncation footer instead of silently capping.
 *
 *   * `results` keeps the existing per-conversation rollup. The
 *     SearchPanel renders the same MessageSnippet cards it always
 *     did; the envelope is purely additive.
 *   * `total_messages_matched` is the exact FTS5 COUNT(*) under the
 *     query's WHERE clauses (message-level, not conversation-level).
 *   * `returned_messages` is the number of body-match rows the FTS5
 *     query returned, capped at the HTTP route's LIMIT (1000).
 *   * `truncated` is derived as `returned_messages <
 *     total_messages_matched`. Carried explicitly so callers don't
 *     have to recompute.
 */
export interface SearchResponse {
  results: SearchResult[]
  total_messages_matched: number
  returned_messages: number
  truncated: boolean
}

/**
 * Runtime predicate for the `/api/search` envelope.
 *
 * Replaces `res.json() as Promise<SearchResponse>` casts in `lib/api.ts`:
 * the cast was a runtime lie — TypeScript trusted it, but a backend
 * shape regression would propagate undefined through `results.map(...)`
 * and crash the SearchPanel with no actionable error.
 *
 * Validates only the 4 top-level required fields. Per-result shape
 * (SearchResult, MessageSnippet) is downstream of this gate; if the
 * envelope is right but a result is malformed, downstream rendering
 * still throws — but the error will be local to a single card, not the
 * whole panel.
 */
export function isSearchResponse(v: unknown): v is SearchResponse {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return false
  const obj = v as Record<string, unknown>
  return (
    Array.isArray(obj.results) &&
    typeof obj.total_messages_matched === 'number' &&
    typeof obj.returned_messages === 'number' &&
    typeof obj.truncated === 'boolean'
  )
}

// Filter types

export type SourceFilter = 'all' | 'CLAUDE_AI' | 'CLAUDE_CODE' | 'CLAUDE_COWORK'

export type SortField = 'updated_at' | 'created_at' | 'name' | 'project'
export type SortOrder = 'asc' | 'desc'

// Runtime predicates for the closed string unions above. shadcn
// `<Select>` (and Radix RadioGroup) hand callers a `string` from
// onValueChange, not the narrow union — the old `setX(v as SortField)`
// callsites in Sidebar / SearchPanel were runtime lies. Use these
// predicates to silently reject unknown values instead of writing
// garbage to a typed setter (which would propagate into the backend
// query string, e.g. `?source=garbage`).
const SOURCE_FILTERS: readonly SourceFilter[] = ['all', 'CLAUDE_AI', 'CLAUDE_CODE', 'CLAUDE_COWORK']
const SORT_FIELDS: readonly SortField[] = [
  'updated_at',
  'created_at',
  'name',
  'project',
]

export function isSourceFilter(v: unknown): v is SourceFilter {
  return typeof v === 'string' && (SOURCE_FILTERS as readonly string[]).includes(v)
}

export function isSortField(v: unknown): v is SortField {
  return typeof v === 'string' && (SORT_FIELDS as readonly string[]).includes(v)
}

export interface ConversationFilters {
  search?: string
  starred?: boolean
  model?: string
  source?: SourceFilter
  sort?: SortField
  sortOrder?: SortOrder
  includePhantom?: boolean
  // D8 (Cowork, 2026-05-25): include archived Cowork sessions
  // (sidecar.isArchived=true). Defaults to false server-side; the
  // sidebar's "Show archived" toggle flips this true.
  showArchived?: boolean
  // cowork-multi-org C6: filter conversations by workspace.
  organization_id?: string
}

// Bookmark types

export interface Bookmark {
  id: string
  conversation_id: string
  message_uuid: string
  source: 'claude_code' | 'claude_desktop'
  created_at: string
  note: string
  snippet: string
}

// Config types

export interface AppConfig {
  data_dir: string
  // Layer 3 of PLANS/2026.05.18-config-corruption-safe-mode.md.
  //
  // Mirrors `Settings.config_corrupt_reason` on the backend. ``null``
  // when ``config.json`` parsed cleanly; a one-line human-readable
  // string (path + exception name + message) when it didn't. The
  // ``ConfigCorruptionBanner`` component renders this verbatim at the
  // top of the app shell — Layer 2's writer gates refuse mutations
  // while this is non-null, so users need to know the path of the
  // file to fix.
  config_corrupt_reason: string | null
  // The installed release (`mcp_server.__version__`), shown in the
  // Settings page's About section. Optional: servers before 2026-10-21
  // do not send it.
  version?: string
}

export interface AppConfigStats extends AppConfig {
  conversation_count: number
}

// PLANS/2026.05.26-watcher-install-detection.md Phase 3:
// Shape of /api/health/watcher. The WatcherMissingBanner reads this
// and renders preventatively when `installed: false`.
export interface WatcherHealth {
  installed: boolean
  platform: string
  install_command: string
  docs_url: string
}

// API Error

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}