import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Star, GitBranch, ChevronRight, Bot, FolderCode, ChevronDown } from 'lucide-react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useConversations } from '@/hooks/useConversations'
import { useKeyboardNavigation } from '@/contexts/KeyboardNavigationContext'
import { Badge } from '@/components/ui/badge'
import { cn, formatDate } from '@/lib/utils'
import { applyActiveFilter, patternMatches, type FilterMode } from '@/lib/filterEngine'
import { useFilters } from '@/contexts/FilterContext'
import { useSearchPin } from '@/contexts/SearchPinContext'
import { SourceBadge } from '@/components/conversation/SourceBadge'
import { useNow } from '@/hooks/useNow'
import {
  buildFolders,
  displayOrder,
  nameFolderOf,
  pathFolderOf,
  groupingApplies,
  isRecent,
  listQueryFilters,
  matchesProjectFilter,
  recentLabel,
} from '@/lib/conversationListView'
// Import the skinny list-payload type under an alias to avoid colliding
// with the `ConversationListItem` component defined below in this file.
// The post-split `/api/conversations` returns ConversationListItem[]
// (no summary, no human_message_count, no git_branch) — none of which
// this component reads. See PLANS/SPLIT_CONVERSATION_SCHEMA.md.
import type { ProjectKey } from '@/lib/conversationListView'
import type {
  ConversationListItem as ConvListItem,
  SubagentSummary,
  SourceFilter,
  SortField,
  SortOrder,
} from '@/lib/types'

interface ConversationListProps {
  searchQuery?: string
  sourceFilter?: SourceFilter
  includePhantom?: boolean
  // D8 (Cowork, 2026-05-25): show archived sessions in sidebar.
  showArchived?: boolean
  sortField?: SortField
  sortOrder?: SortOrder
  groupByProject?: boolean
  projectSlug?: string
  // `?path=`: one exact project_path (UX.md → "Conversation List filters").
  projectPath?: string
  // Show only rows active in the last N hours; null = off.
  recentHours?: number | null
  // Empty-state actions: clear the project / path filter, turn off the
  // recent filter. The Sidebar owns both states.
  onShowAllProjects?: () => void
  onShowOlder?: () => void
  // What a folder is when grouped: one project_path (default) or one name.
  projectKey?: ProjectKey
  // The home folder, for ~/... folder tooltips.
  homeDir?: string
  titleFilter?: string
  titleFilterMode?: FilterMode
  // cowork-multi-org C6: workspace filter (null = "All workspaces").
  organizationId?: string | null
}

/* ---------- virtualization tuning ---------- */
// Row height measured live at ~83 px (lucide icons + 3-line layout). The
// virtualizer auto-corrects via `measureElement` once each row mounts;
// the estimate is only used for the initial scrollable height + first
// render of off-screen rows. Slight under-estimate is fine, slight
// over-estimate causes scroll-jump on initial render — so we err low.
const ROW_HEIGHT = 83
// Header rows ("Starred", group headers) and dividers are shorter.
const HEADER_HEIGHT = 34
const DIVIDER_HEIGHT = 17
// Overscan keeps a small buffer above + below the viewport so arrow-key
// nav doesn't reveal blank gaps mid-frame. 8 rows ≈ 1 viewport-height of
// safety on a typical 700-1000 px sidebar.
const OVERSCAN = 8

/* ---------- flat-view item model ----------
 *
 * The flat (non-grouped) view virtualizes a single mixed list of
 * heterogeneously-sized items: an optional "Starred" header, the
 * starred rows, a divider, then the unstarred rows. Each item gets a
 * stable `key` so React's reconciliation tracks it correctly across
 * scroll-driven mount/unmount cycles. */

type FlatItem =
  | { kind: 'header'; key: string; label: string }
  | { kind: 'divider'; key: string }
  | { kind: 'conv'; key: string; conv: ConvListItem }

function buildFlatItems(
  starred: ConvListItem[],
  unstarred: ConvListItem[],
): FlatItem[] {
  const items: FlatItem[] = []
  if (starred.length > 0) {
    items.push({ kind: 'header', key: '__starred_header', label: 'Starred' })
    for (const c of starred) {
      items.push({ kind: 'conv', key: `s-${c.uuid}`, conv: c })
    }
    items.push({ kind: 'divider', key: '__starred_divider' })
  }
  for (const c of unstarred) {
    items.push({ kind: 'conv', key: `u-${c.uuid}`, conv: c })
  }
  return items
}

export function ConversationList({
  searchQuery,
  sourceFilter,
  includePhantom,
  showArchived,
  sortField = 'updated_at',
  sortOrder = 'desc',
  groupByProject = false,
  projectSlug,
  projectPath,
  recentHours = null,
  onShowAllProjects,
  onShowOlder,
  projectKey = 'path',
  homeDir,
  titleFilter,
  titleFilterMode = 'glob',
  organizationId,
}: ConversationListProps) {
  const { uuid: selectedUuid } = useParams()
  const navigate = useNavigate()
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const { selectedIndex, setSelectedIndex, setConversationIds, focusArea, setNavSource } = useKeyboardNavigation()
  const { filtersState, setActiveId } = useFilters()
  const { scope: pinScope } = useSearchPin()
  // Whether an active filter is currently constraining the list (used for
  // the "all hidden by filter" empty-state copy).
  const activeNode = filtersState.activeId ? filtersState.nodes[filtersState.activeId] : null
  const hasActiveFilter = Boolean(activeNode && activeNode.enabled)

  const isInScope = (conv: ConvListItem): boolean => {
    if (pinScope.kind === 'none') return true
    if (pinScope.kind === 'conversation') return conv.uuid === pinScope.uuid
    return (conv.project_path ?? '') === pinScope.path
  }
  const filters = listQueryFilters({
    searchQuery,
    sourceFilter,
    includePhantom,
    showArchived,
    organizationId,
    sortField,
    sortOrder,
  })
  const { data: rawConversations, isLoading, error } = useConversations(filters)
  // The inventory for folder labels: the same query without the local
  // title search (one cache entry), so typing a search does not rename
  // the folders.
  const { data: inventory } = useConversations({ ...filters, search: undefined })
  const now = useNow(recentHours != null)
  // The list groups only where the checkbox shows (Sidebar): a hidden
  // checkbox must not keep the list grouped under Desktop or Cowork.
  const grouped = groupingApplies(groupByProject, sourceFilter)

  const conversations = useMemo(() => {
    if (!rawConversations) return rawConversations
    let list = rawConversations
    if (projectSlug || projectPath) {
      const projectFilter = { project: projectSlug ?? '', path: projectPath ?? '' }
      list = list.filter((c) => matchesProjectFilter(c, projectFilter))
    }
    if (recentHours != null) {
      list = list.filter((c) => isRecent(c, now, recentHours))
    }
    if (titleFilter) {
      list = list.filter((c) => patternMatches(c.name, titleFilter, titleFilterMode))
    }
    // CF1: composable-graph evaluator. applyActiveFilter handles the null
    // active id, missing/disabled active node, and cycle defense.
    list = list.filter((c) => applyActiveFilter(c.name, filtersState))
    return list
  }, [rawConversations, projectSlug, projectPath, recentHours, now, titleFilter, titleFilterMode, filtersState])

  const folders = useMemo(
    () => {
      if (!grouped || !conversations) return null
      const folderOf = projectKey === 'path' ? pathFolderOf(inventory ?? conversations, homeDir) : nameFolderOf
      return buildFolders(conversations, sortField, sortOrder, folderOf)
    },
    [grouped, conversations, sortField, sortOrder, projectKey, inventory, homeDir],
  )

  // Row UUIDs in display order. Keyboard navigation (j/k, arrows) walks
  // this list, so it must match what the user sees: folder by folder
  // without collapsed folders when grouped, else starred first.
  //
  // Keyed by content: the recent filter's clock makes a new
  // `conversations` array once a minute, and a new ID array each time
  // would re-run the sync below and undo the user's keyboard selection.
  const orderedIdsKey = useMemo(() => {
    if (!conversations) return null
    if (folders) return displayOrder(folders, collapsedGroups).join('\n')
    return [
      ...conversations.filter((c) => c.is_starred),
      ...conversations.filter((c) => !c.is_starred),
    ].map((c) => c.uuid).join('\n')
  }, [conversations, folders, collapsedGroups])
  const orderedIds = useMemo(
    () => (orderedIdsKey === null ? null : orderedIdsKey ? orderedIdsKey.split('\n') : []),
    [orderedIdsKey],
  )

  // Register conversation IDs with navigation context (in display order)
  useEffect(() => {
    if (orderedIds) setConversationIds(orderedIds)
  }, [orderedIds, setConversationIds])

  // Sync selectedIndex with the currently viewed conversation (from URL)
  // Only runs when the URL or conversation list changes, NOT when selectedIndex changes
  // (otherwise it would override keyboard navigation).
  //
  // A filter can hide the open conversation (no exceptions; UX.md →
  // "Conversation List filters"). Then no row is selected (-1). A stale
  // index would select another row, and ConversationPage would show its
  // "Press Enter to open" hint in place of the open conversation.
  useEffect(() => {
    if (selectedUuid && orderedIds) {
      setSelectedIndex(orderedIds.indexOf(selectedUuid))
    }
  }, [selectedUuid, orderedIds, setSelectedIndex])

  if (isLoading) {
    return <ConversationListSkeleton />
  }

  if (error) {
    return (
      <div className="p-4 text-sm text-red-500">
        Failed to load conversations
      </div>
    )
  }

  if (!conversations || conversations.length === 0) {
    const totalLoaded = rawConversations?.length ?? 0
    const hiddenBy = [
      hasActiveFilter && `filter "${activeNode?.name}"`,
      titleFilter && 'a URL title filter',
      projectSlug && `project=${projectSlug}`,
      projectPath && `path=${projectPath}`,
      recentHours != null && `last ${recentLabel(true, recentHours)}`,
    ].filter(Boolean)
    if (totalLoaded > 0 && hiddenBy.length > 0) {
      const buttonClass =
        'rounded border border-zinc-300 bg-white px-3 py-1 text-xs hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:bg-zinc-800'
      return (
        <div className="p-4 text-sm text-zinc-600 dark:text-zinc-400">
          <div className="mb-2">
            All {totalLoaded} conversations hidden by {hiddenBy.join(' and ')}.
          </div>
          <div className="flex flex-wrap gap-2">
            {hasActiveFilter && (
              <button type="button" onClick={() => setActiveId(null)} className={buttonClass}>
                Clear active filter
              </button>
            )}
            {(projectSlug || projectPath) && onShowAllProjects && (
              <button
                type="button"
                onClick={onShowAllProjects}
                className={buttonClass}
                data-testid="list-empty-clear-project"
              >
                Show all projects
              </button>
            )}
            {recentHours != null && onShowOlder && (
              <button
                type="button"
                onClick={onShowOlder}
                className={buttonClass}
                data-testid="list-empty-show-older"
              >
                Show older sessions
              </button>
            )}
          </div>
        </div>
      )
    }
    return (
      <div className="p-4 text-sm text-zinc-500">
        {searchQuery ? 'No conversations found' : 'No conversations yet'}
      </div>
    )
  }

  // Toggle group collapse
  const toggleGroup = (groupName: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(groupName)) {
        next.delete(groupName)
      } else {
        next.add(groupName)
      }
      return next
    })
  }

  // Helper to check if a conversation is keyboard-selected (uses display order)
  const isKeyboardSelected = (uuid: string) => {
    if (focusArea !== 'list') return false
    return (orderedIds ?? []).indexOf(uuid) === selectedIndex
  }

  // Group by project if enabled
  if (folders) {
    // Folders: project (Claude Code) or workspace (Claude.ai). The names
    // and the folder order live in lib/conversationListView.ts
    // (buildFolders): a folder takes the value of its first session in
    // the chosen sort order (UX.md → "Conversation List filters").
    //
    // This path is intentionally NOT virtualized: per-group collapse
    // already gives the user a coarse-grained "hide rows I don't care
    // about" lever, and the cross-group ordering + collapse-state
    // bookkeeping would double the implementation complexity for a
    // view most users don't keep open. If grouped corpora start
    // hitting their own first-paint cliff we'll come back. (Phase 2.2
    // virtualization scope, OPTIMIZE_FIRST_PAINT.md.)
    return (
      <div className="flex flex-col">
        {folders.map(({ key: groupKey, name: groupName, title: groupTitle, convs: groupConvs }) => {
          const isCollapsed = collapsedGroups.has(groupKey)

          return (
            <div key={groupKey}>
              <button
                type="button"
                onClick={() => toggleGroup(groupKey)}
                data-testid="list-group-header"
                data-group-name={groupName}
                title={groupTitle}
                aria-expanded={!isCollapsed}
                className="flex w-full items-center gap-2 px-4 py-2 text-xs font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
              >
                <ChevronDown
                  className={cn(
                    'h-3 w-3 transition-transform',
                    isCollapsed && '-rotate-90'
                  )}
                />
                {/* Source/tenant orthogonality (P1-1): icon driven by
                    source, not group label. A group consisting entirely of
                    CLAUDE_AI conversations gets the canonical Desktop
                    SourceBadge (row variant — blue MessageSquare via the
                    SOURCE_PRESETS map); anything else (CLAUDE_CODE or
                    mixed) gets the FolderCode. SourceBadge is the single
                    site for the source→icon+color mapping (extracted
                    2026-05-30 P1.2, rubric F). */}
                {groupConvs.every((c) => c.source === 'CLAUDE_AI') ? (
                  <SourceBadge source="CLAUDE_AI" variant="row" />
                ) : (
                  <FolderCode className="h-3 w-3 text-amber-500" />
                )}
                <span className="flex-1 truncate text-left">{groupName}</span>
                <span className="text-zinc-400">({groupConvs.length})</span>
              </button>
              {!isCollapsed && (
                <div className="ml-2 border-l border-zinc-200 dark:border-zinc-700">
                  {/* buildFolders already puts starred rows first. */}
                  {groupConvs.map((conv) => (
                    <ConversationListItem
                      key={conv.uuid}
                      conversation={conv}
                      isSelected={conv.uuid === selectedUuid}
                      isKeyboardSelected={isKeyboardSelected(conv.uuid)}
                      onClick={() => { setNavSource('list'); navigate(`/conversations/${conv.uuid}`) }}
                      showProject={false}
                      outOfScope={!isInScope(conv)}
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    )
  }

  // Flat view (no grouping) — virtualized.
  return (
    <VirtualizedFlatList
      conversations={conversations}
      selectedUuid={selectedUuid}
      onClickConv={(conv) => { setNavSource('list'); navigate(`/conversations/${conv.uuid}`) }}
      isInScope={isInScope}
      isKeyboardSelected={isKeyboardSelected}
    />
  )
}

interface VirtualizedFlatListProps {
  /** The post-filter, post-sort conversation list. Starred/unstarred
   *  partitioning and ordered-IDs derivation happen inside this
   *  component, memoized on `conversations` so the virtualizer's
   *  inputs don't shift identity on every parent render. */
  conversations: ConvListItem[]
  selectedUuid: string | undefined
  onClickConv: (conv: ConvListItem) => void
  isInScope: (conv: ConvListItem) => boolean
  isKeyboardSelected: (uuid: string) => boolean
}

function VirtualizedFlatList({
  conversations,
  selectedUuid,
  onClickConv,
  isInScope,
  isKeyboardSelected,
}: VirtualizedFlatListProps) {
  // Stable references across renders. Without this, the parent's
  // `starred.filter(...)` allocations produce a fresh `items` array on
  // every render and the scroll-to-active effect fires every frame —
  // which causes react-virtual to mount duplicate stale rows at high
  // translateY positions before unmounting them, leaving the deep-
  // linked row off-screen.
  const starred = useMemo(
    () => conversations.filter((c) => c.is_starred),
    [conversations]
  )
  const unstarred = useMemo(
    () => conversations.filter((c) => !c.is_starred),
    [conversations]
  )
  const orderedConversations = useMemo(
    () => [...starred, ...unstarred],
    [starred, unstarred]
  )
  const { selectedIndex, focusArea } = useKeyboardNavigation()
  const parentRef = useRef<HTMLDivElement | null>(null)
  // The sidebar wraps the list in a Radix `<ScrollArea>` (see
  // `Sidebar.tsx`); the actual scroll container is the inner Viewport
  // element Radix injects with `data-radix-scroll-area-viewport`. We
  // resolve it lazily via `getScrollElement` so this component works
  // both inside the Radix ScrollArea (production) and inside a plain
  // overflow:auto container (vitest / Storybook).
  const [scrollEl, setScrollEl] = useState<HTMLElement | null>(null)
  useEffect(() => {
    if (!parentRef.current) return
    // Walk up to the actual scroll container. Two layout shapes:
    //   * Production: the sidebar wraps us in a Radix `<ScrollArea>`,
    //     whose Viewport carries `data-radix-scroll-area-viewport` and
    //     is the element with clientHeight < scrollHeight. ALWAYS
    //     prefer it when present — Radix injects intermediate wrappers
    //     with `overflow-y: scroll` whose clientHeight equals
    //     scrollHeight (they don't actually scroll), so the "first
    //     overflow:scroll ancestor" heuristic picks the wrong node.
    //   * Tests / Storybook: any ancestor whose computed
    //     `overflow-y` is auto/scroll AND whose scrollHeight exceeds
    //     clientHeight (i.e. the element actually scrolls).
    let node: HTMLElement | null = parentRef.current.parentElement
    while (node) {
      if (node.hasAttribute('data-radix-scroll-area-viewport')) {
        // react-doctor-disable-next-line react-doctor/no-initialize-state -- Phase 2: this is post-mount DOM topology discovery (Radix viewport ancestor walk), not state initialization. `useState(node)` is impossible because the node doesn't exist at first render; `useSyncExternalStore` is for external data sources, not ancestor walks. Runs once per mount, fires one re-render to wire the virtualizer.
        setScrollEl(node)
        return
      }
      const cs = getComputedStyle(node)
      if (
        (cs.overflowY === 'auto' || cs.overflowY === 'scroll') &&
        node.scrollHeight > node.clientHeight
      ) {
        // react-doctor-disable-next-line react-doctor/no-initialize-state -- Phase 2: same rationale as line above (DOM topology discovery via ancestor walk).
        setScrollEl(node)
        return
      }
      node = node.parentElement
    }
    // Last-resort fallback: virtualizer falls back to documentElement
    // scrolling. Used only when nothing above the list scrolls — true
    // in vitest where we render the list standalone.
    // react-doctor-disable-next-line react-doctor/no-initialize-state -- Phase 2: see above.
    setScrollEl(document.documentElement)
  }, [])

  const items = useMemo(
    () => buildFlatItems(starred, unstarred),
    [starred, unstarred]
  )

  // When the items array shrinks (e.g. type-to-filter narrows the
  // list), the scrollElement may be parked past the new shorter
  // content. Clamp it back into range so the virtualizer's next
  // getVirtualItems() doesn't return indices that exceed the
  // new items length.
  const prevItemsLenRef = useRef(items.length)
  useEffect(() => {
    if (!scrollEl) return
    if (items.length < prevItemsLenRef.current && scrollEl.scrollTop > 0) {
      // oxlint-disable-next-line react-doctor/no-direct-state-mutation -- `scrollEl` is the live DOM container (HTMLElement). Writing `scrollEl.scrollTop` is the intended imperative scroll-position API; it is NOT a React state mutation. The state slot holds a stable element reference; the property we touch (scrollTop) is a browser-managed mutable field by design.
      scrollEl.scrollTop = 0
    }
    prevItemsLenRef.current = items.length
  }, [items.length, scrollEl])

  const estimateSize = useCallback(
    (index: number) => {
      const it = items[index]
      if (!it) return ROW_HEIGHT
      if (it.kind === 'header') return HEADER_HEIGHT
      if (it.kind === 'divider') return DIVIDER_HEIGHT
      return ROW_HEIGHT
    },
    [items]
  )

  // React 19 / React-Compiler warning: `react-hooks/incompatible-library`
  // — TanStack Virtual's `useVirtualizer()` returns functions that cannot
  // be safely memoized by the compiler, so this component opts out of
  // compiler optimization. That's a library-level constraint, not a fix
  // we can apply locally. The component still works correctly under
  // React 19's concurrent renderer; we just don't get auto-memoization.
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Virtual API is not React Compiler compatible; see rationale above.
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollEl,
    estimateSize,
    overscan: OVERSCAN,
    // Provide a fixed measureElement fallback when ResizeObserver isn't
    // available (SSR / very old browsers). jsdom (vitest) is handled
    // separately below via the `isJsdom` non-virtualized early-return,
    // so this branch is NOT about jsdom.
    measureElement:
      typeof window !== 'undefined' && typeof ResizeObserver !== 'undefined'
        ? undefined
        : () => ROW_HEIGHT,
    // 2026-05-24: React 19 throws on flushSync-during-render. TanStack
    // Virtual's default fires flushSync from its onChange callback
    // (which runs from a ResizeObserver during render). See the same
    // option on ConversationPage.tsx for the full rationale and the
    // settings-flash-and-disappear regression that surfaced it.
    useFlushSync: false,
  })

  // Keep mutable refs to `items` / `orderedConversations` so the
  // scroll effects below can read the latest value WITHOUT putting
  // either in their dependency array. The effects depend only on the
  // user's intent (`selectedIndex`, `selectedUuid`); the items array
  // is read off the ref. This breaks the render → scrollToIndex →
  // scroll event → re-render → scrollToIndex cascade that was
  // producing duplicate stale DOM rows.
  const itemsRef = useRef(items)
  itemsRef.current = items
  const orderedRef = useRef(orderedConversations)
  orderedRef.current = orderedConversations

  // Drive virtualizer scroll on keyboard navigation. The
  // KeyboardNavigationContext sets `selectedIndex` into
  // `orderedConversations`; we map that back to the `items` index
  // (which is offset by the optional "Starred" header) and call
  // `scrollToIndex`. Native `scrollIntoView` does NOT work on
  // off-screen virtualized rows — they don't exist in the DOM yet.
  //
  // Why scrollEl.scrollTop = estimate rather than
  // `virtualizer.scrollToIndex`: same cascade-avoidance reason as
  // the URL deep-link effect below. The virtualizer's
  // scrollToIndex triggers a scroll → re-render → measure cycle
  // that can produce duplicate React-rendered rows when several
  // measurements arrive between commits. Setting scrollTop
  // directly keeps the virtualizer in spectator mode: one scroll
  // event, one re-render, no cascade. The row's own scrollIntoView
  // effect (`isKeyboardSelected` → `scrollIntoView({block:
  // 'nearest'})`) handles the final precision once the row mounts.
  //
  // react-doctor-disable-next-line react-doctor/no-effect-chain -- Phase 2: scrollEl is the post-mount DOM-topology probe result (see the `setScrollEl` ancestor walk above). This effect reacting to scrollEl IS the wiring step, and it cannot be moved into the originating event handler — there is no "originating event"; the scroll element is discovered after mount. The single benign re-render per scrollEl change is one-time and load-bearing for the virtualizer.
  useEffect(() => {
    if (!scrollEl) return
    if (focusArea !== 'list') return
    const ordered = orderedRef.current
    if (selectedIndex < 0 || selectedIndex >= ordered.length) return
    const conv = ordered[selectedIndex]
    const currentItems = itemsRef.current
    const itemsIndex = currentItems.findIndex(
      (it) => it.kind === 'conv' && it.conv.uuid === conv.uuid
    )
    if (itemsIndex < 0) return
    // Only pre-scroll if the target is outside the currently
    // rendered window. `align: 'auto'` semantics: keep the row in
    // view; if it already is, do nothing. We replicate that here.
    const estimated = itemsIndex * ROW_HEIGHT
    const viewportTop = scrollEl.scrollTop
    const viewportBottom = viewportTop + scrollEl.clientHeight
    if (estimated >= viewportTop && estimated + ROW_HEIGHT <= viewportBottom) {
      return
    }
    // oxlint-disable-next-line react-doctor/no-direct-state-mutation -- `scrollEl` is the live DOM container (HTMLElement). Writing `scrollEl.scrollTop` is the intended imperative scroll-position API; it is NOT a React state mutation. See the long comment block above for why we bypass virtualizer.scrollToIndex on purpose.
    scrollEl.scrollTop = Math.max(0, estimated - scrollEl.clientHeight / 3)
  }, [selectedIndex, focusArea, scrollEl])

  // Scroll-to-active when the URL deep-links to a specific conversation
  // (e.g. /conversations/<uuid>). Runs on mount + whenever
  // selectedUuid changes. We rely on `selectedUuid` as the sole
  // trigger (NOT `items`) because each scrollToIndex causes a
  // scroll event that re-renders VirtualizedFlatList, which would
  // otherwise refire this effect every frame and produce a cascade
  // of duplicate DOM mounts at stale measurement positions.
  //
  // After the virtualizer brings the row into the rendered window
  // here, the `ConversationListItem` child's own scrollIntoView
  // effect (keyed on `isSelected`) finishes the job — it pulls the
  // row to its `block: 'nearest'` position inside the Radix
  // viewport. This is the same code path the pre-virtualization
  // implementation used, so the final visual behavior matches.
  //
  // react-doctor-disable-next-line react-doctor/no-effect-chain -- Phase 2: same post-mount-DOM-discovery rationale as the effect above. scrollEl is wiring; reacting to its presence is required to bind the URL deep-link scroll behavior.
  useEffect(() => {
    if (!scrollEl || !selectedUuid) return
    const currentItems = itemsRef.current
    const itemsIndex = currentItems.findIndex(
      (it) => it.kind === 'conv' && it.conv.uuid === selectedUuid
    )
    if (itemsIndex < 0) return
    // Scroll directly via scrollEl.scrollTop using an estimated offset
    // for the target index. Rationale: react-virtual's
    // `scrollToIndex` triggers a scroll → re-render → measure cycle
    // that, under React 18 + StrictMode, can produce a cascade of
    // double-mounted absolutely-positioned rows when the
    // measurement-driven re-render fires while React is still
    // committing the previous batch. Bypassing the API and writing
    // scrollTop ourselves keeps the virtualizer in spectator mode:
    // it sees ONE scroll event and computes ONE new virtual window.
    //
    // After the virtualizer mounts the target row, that row's own
    // `scrollIntoView({ block: 'nearest' })` effect (already on
    // `ConversationListItem`) finishes the centering. We
    // intentionally aim a little ABOVE center (clientHeight / 3) so
    // `nearest` consistently pulls the row down into view rather
    // than fighting our pre-scroll position.
    const estimated = itemsIndex * ROW_HEIGHT
    const offset = Math.max(0, estimated - scrollEl.clientHeight / 3)
    // oxlint-disable-next-line react-doctor/no-direct-state-mutation -- `scrollEl` is the live DOM container (HTMLElement). Writing `scrollEl.scrollTop` is the intended imperative scroll-position API; it is NOT a React state mutation. See the long comment block above for the cascade-avoidance rationale (we deliberately bypass virtualizer.scrollToIndex).
    scrollEl.scrollTop = offset
  }, [scrollEl, selectedUuid])

  // jsdom (vitest) doesn't run layout — getBoundingClientRect always
  // returns zero — so the virtualizer can't measure rows and would
  // render zero items. That would break every existing unit test
  // that asserts on row content. Detect jsdom (its user-agent
  // string carries the literal "jsdom") and fall back to plain
  // non-virtualized rendering. Real browsers don't match.
  // ResizeObserver-presence isn't reliable because src/test/setup.ts
  // polyfills it for cmdk's sake.
  const isJsdom =
    typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent)
  if (isJsdom) {
    return (
      <div className="flex flex-col" data-testid="conversation-list-flat">
        {items.map((item) => {
          if (item.kind === 'header') {
            return (
              <div
                key={item.key}
                className="px-4 py-2 text-xs font-medium text-zinc-500 dark:text-zinc-400"
              >
                {item.label}
              </div>
            )
          }
          if (item.kind === 'divider') {
            return (
              <div
                key={item.key}
                className="mx-4 my-2 border-t border-zinc-200 dark:border-zinc-800"
              />
            )
          }
          return (
            <ConversationListItem
              key={item.key}
              conversation={item.conv}
              isSelected={item.conv.uuid === selectedUuid}
              isKeyboardSelected={isKeyboardSelected(item.conv.uuid)}
              onClick={() => onClickConv(item.conv)}
              outOfScope={!isInScope(item.conv)}
            />
          )
        })}
      </div>
    )
  }

  const virtualItems = virtualizer.getVirtualItems()
  const totalSize = virtualizer.getTotalSize()

  return (
    <div ref={parentRef} className="flex flex-col" data-testid="conversation-list-flat">
      <div
        style={{
          height: `${totalSize}px`,
          width: '100%',
          position: 'relative',
        }}
      >
        {virtualItems.map((vi) => {
          const item = items[vi.index]
          if (!item) return null
          return (
            // Key MUST be `vi.key` (the virtualizer-derived stable
            // key), not our own `item.key`. With our item.key, when
            // items[index] changes UUID across renders the React
            // key changes too, but the virtualizer's render still
            // emits the same `data-index` — React's reconciler then
            // treats them as different children for the same array
            // slot and accumulates orphaned DOM rows under React 18
            // concurrent commit. Using `vi.key` keeps the React-side
            // and virtualizer-side identity aligned.
            <div
              key={vi.key}
              data-index={vi.index}
              ref={virtualizer.measureElement}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                transform: `translateY(${vi.start}px)`,
              }}
            >
              {item.kind === 'header' && (
                <div className="px-4 py-2 text-xs font-medium text-zinc-500 dark:text-zinc-400">
                  {item.label}
                </div>
              )}
              {item.kind === 'divider' && (
                <div className="mx-4 my-2 border-t border-zinc-200 dark:border-zinc-800" />
              )}
              {item.kind === 'conv' && (
                <ConversationListItem
                  conversation={item.conv}
                  isSelected={item.conv.uuid === selectedUuid}
                  isKeyboardSelected={isKeyboardSelected(item.conv.uuid)}
                  onClick={() => onClickConv(item.conv)}
                  outOfScope={!isInScope(item.conv)}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

interface ConversationListItemProps {
  conversation: ConvListItem
  isSelected: boolean
  isKeyboardSelected: boolean
  onClick: () => void
  showProject?: boolean
  outOfScope?: boolean
}

function ConversationListItem({
  conversation,
  isSelected,
  isKeyboardSelected,
  onClick,
  showProject = true,
  outOfScope = false,
}: ConversationListItemProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const itemRef = useRef<HTMLDivElement>(null)
  const subagents = conversation.subagents || []
  const hasSubagents = subagents.length > 0

  // Scroll keyboard-selected item into view. Note: in the virtualized
  // flat-view path, the virtualizer's `scrollToIndex` has already
  // brought the row into the visible window, so this `scrollIntoView`
  // is a no-op on already-visible rows and a precision nudge when the
  // virtualizer's `align: 'auto'` left the row at the very top/bottom
  // edge. In the grouped (non-virtualized) path this is the only
  // mechanism that keeps the keyboard cursor in view.
  useEffect(() => {
    if (isKeyboardSelected && itemRef.current) {
      itemRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [isKeyboardSelected])

  // Also scroll URL-selected item into view (e.g., when Cmd+G navigates
  // cross-conversation and focusArea is still 'search')
  useEffect(() => {
    if (isSelected && itemRef.current) {
      itemRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [isSelected])

  // Keep the selected row in view when the list's viewport changes size.
  // Opening "Filters and Sorting" (or its saved state arriving from the
  // server after the first render) shrinks the list, and a row near the
  // bottom then sat below the visible area (2026-10-26). Only the
  // selected row that is mounted watches, so a row that the user
  // scrolled far away from (unmounted by the virtualizer) cannot pull the
  // list back. The height is read when watching starts: the browser can
  // deliver the first callback late, after the list already shrank, so
  // "skip the first callback" missed exactly this case.
  useEffect(() => {
    const el = itemRef.current
    const viewport = el?.closest<HTMLElement>('[data-radix-scroll-area-viewport]')
    if (!isSelected || !el || !viewport || typeof ResizeObserver === 'undefined') return
    let lastHeight = viewport.clientHeight
    const observer = new ResizeObserver(() => {
      const height = viewport.clientHeight
      if (height !== lastHeight) {
        el.scrollIntoView({ block: 'nearest' })
      }
      lastHeight = height
    })
    observer.observe(viewport)
    return () => observer.disconnect()
  }, [isSelected])

  return (
    <div>
      {/* Phase 1 a11y: the row already has role="button", tabIndex={0},
          and Enter/Space onKeyDown — fully keyboard-accessible. Oxlint's
          prefer-tag-over-role suggests converting to a real <button>,
          but the row needs a stable ref for scrollIntoView (above), and
          the cell contains nested interactive children (the children
          rendered below include a "view subagents" button). A <button>
          element with nested interactive descendants is invalid HTML.
          Suppress with rationale. */}
      {/* react-doctor-disable-next-line react-doctor/prefer-tag-over-role */}
      <div
        ref={itemRef}
        role="button"
        tabIndex={0}
        onClick={onClick}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onClick()
          }
        }}
        data-out-of-scope={outOfScope ? 'true' : 'false'}
        className={cn(
          'flex w-full cursor-pointer flex-col gap-1 px-4 py-3 text-left transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800',
          isSelected && 'bg-zinc-100 dark:bg-zinc-800',
          isKeyboardSelected && 'ring-2 ring-inset ring-blue-400 dark:ring-blue-500',
          outOfScope && 'opacity-40'
        )}
      >
        <div className="flex items-start gap-2">
          {conversation.is_starred && (
            <Star className="mt-0.5 h-4 w-4 fill-yellow-400 text-yellow-400" />
          )}
          <span className="flex-1 truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
            {conversation.name || 'Untitled'}
          </span>
          {conversation.has_branches && (
            <GitBranch className="h-4 w-4 text-zinc-400" />
          )}
        </div>
        {/* Project name for Claude Code sessions (hide when grouped by project) */}
        {showProject && conversation.source === 'CLAUDE_CODE' && conversation.project_name && (
          <div className="flex items-center gap-1 text-xs text-zinc-500 dark:text-zinc-400">
            <FolderCode className="h-3 w-3 text-amber-500" />
            <span className="truncate">{conversation.project_name}</span>
          </div>
        )}
        <div className="flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
          {/* F12 (2026-05-29) → SourceBadge (2026-05-30 P1.2): the
              three-way source→icon+color map lives in SourceBadge so the
              Sidebar.tsx:230-234 dropdown is the only OTHER place that
              needs to track source-shape changes. */}
          <SourceBadge source={conversation.source} variant="row" />
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
            {conversation.model}
          </Badge>
          <span>{formatDate(conversation.updated_at)}</span>
          <span>{conversation.message_count} msgs</span>
          {hasSubagents && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                setIsExpanded(!isExpanded)
              }}
              className="flex items-center gap-1 rounded px-1 py-0.5 text-purple-600 hover:bg-purple-50 dark:text-purple-400 dark:hover:bg-purple-950"
            >
              <ChevronRight className={cn("h-3 w-3 transition-transform", isExpanded && "rotate-90")} />
              <Bot className="h-3 w-3" />
              <span>{subagents.length}</span>
            </button>
          )}
        </div>
        <div className="truncate font-mono text-[10px] text-zinc-400 dark:text-zinc-500">
          {conversation.uuid}
        </div>
      </div>
      {isExpanded && hasSubagents && (
        <div className="ml-6 border-l-2 border-purple-200 dark:border-purple-800">
          {subagents.map((agent) => (
            <SubagentListItem key={agent.uuid} agent={agent} />
          ))}
        </div>
      )}
    </div>
  )
}

interface SubagentListItemProps {
  agent: SubagentSummary
}

function SubagentListItem({ agent }: SubagentListItemProps) {
  return (
    <div className="flex flex-col gap-0.5 px-4 py-2 text-left">
      <div className="flex items-center gap-2">
        <Bot className="h-3 w-3 text-purple-500" />
        <span className="flex-1 truncate text-xs font-medium text-zinc-700 dark:text-zinc-300">
          {agent.name}
        </span>
      </div>
      <div className="flex items-center gap-2 text-[10px] text-zinc-400 dark:text-zinc-500">
        <span>{agent.message_count} msgs</span>
        <span>{formatDate(agent.updated_at)}</span>
      </div>
    </div>
  )
}

function ConversationListSkeleton() {
  return (
    <div className="flex flex-col gap-1 p-2">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex flex-col gap-2 px-4 py-3">
          <div className="h-4 w-3/4 animate-pulse rounded bg-zinc-200 dark:bg-zinc-800" />
          <div className="h-3 w-1/2 animate-pulse rounded bg-zinc-200 dark:bg-zinc-800" />
        </div>
      ))}
    </div>
  )
}
