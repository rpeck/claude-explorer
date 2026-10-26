import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { useSetListProjectFilter, useUrlFilters } from '@/hooks/useUrlFilters'
import { useConfig, useConversations } from '@/hooks/useConversations'
import { activeFilterCount, listQueryFilters, projectEntries, recentOptions } from '@/lib/conversationListView'
import { ProjectPicker } from '@/components/layout/ProjectPicker'
import { useFilters } from '@/contexts/FilterContext'
import { ManageFiltersModal } from '@/components/filters/ManageFiltersModal'
import { MigrationBanner } from '@/components/filters/MigrationBanner'
import { Search, Settings, Settings2, Download, MessageSquare, Terminal, RefreshCw, ArrowUpDown, FolderTree, Sun, Moon, Monitor, Filter as FilterIcon, Sparkles, Clock, ChevronDown, SlidersHorizontal } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ConversationList } from '@/components/conversation/ConversationList'
import { FetchDialog } from '@/components/fetch/FetchDialog'
import { AccountBadge } from '@/components/layout/AccountBadge'
import { QuitButton } from '@/components/layout/QuitButton'
import { useFetchPipeline } from '@/contexts/FetchPipelineContext'
import { useSourceFilter } from '@/contexts/SourceFilterContext'
import { useOrgs } from '@/hooks/useOrgs'
import { useSettings } from '@/contexts/SettingsContext'
import { useKeyboardNavigation } from '@/contexts/KeyboardNavigationContext'
import { useSearchPin } from '@/contexts/SearchPinContext'
import { cn } from '@/lib/utils'
import { isSourceFilter, isSortField, type SortField } from '@/lib/types'

interface SidebarProps {
  className?: string
}

const SORT_OPTIONS: { value: SortField; label: string }[] = [
  { value: 'updated_at', label: 'Last Activity' },
  { value: 'created_at', label: 'Start Time' },
  { value: 'name', label: 'Title' },
  { value: 'project', label: 'Project' },
]

export function Sidebar({ className }: SidebarProps) {
  const urlFilters = useUrlFilters()
  const { filtersState, setActiveId } = useFilters()
  const [searchQuery, setSearchQuery] = useState(urlFilters.q)
  const [isManageOpen, setIsManageOpen] = useState(false)
  const { scope: pinScope, unpin: unpinSearch } = useSearchPin()

  // Active picker options: every enabled node, alpha-sorted by name. The
  // sentinel "__none__" maps to activeId=null ("All conversations"); the
  // sentinel "__manage__" opens the Manage Filters modal without changing
  // the current selection (Radix re-clamps `value` to whatever was already
  // active once the dropdown closes).
  const ACTIVE_NONE = '__none__'
  const ACTIVE_MANAGE = '__manage__'
  const pickerOptions = Object.values(filtersState.nodes)
    .filter((n) => n.enabled)
    .sort((a, b) => a.name.localeCompare(b.name))
  // Resolve current selection: if activeId points at a missing or disabled
  // node, render the sentinel so the trigger shows "All conversations".
  const activeNode = filtersState.activeId ? filtersState.nodes[filtersState.activeId] : null
  const pickerValue = activeNode && activeNode.enabled ? activeNode.id : ACTIVE_NONE

  // Keep the search box synced with the URL so deep-links and back/forward both work.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- TODO React 19 migration: drive searchQuery from urlFilters.q (derived) and make the input controlled-via-URL. Today the effect runs once per URL change with no cascade.
    setSearchQuery(urlFilters.q)
  }, [urlFilters.q])
  // Build-9 Bug 1: Refresh button drives the same shared pipeline state
  // that the FetchDialog modal subscribes to. The dialog open state lives
  // in the same context so the toast's "Details" action and the dialog
  // itself stay in sync without prop-drilling.
  const {
    startRefresh,
    isRunning: isPipelineRunning,
    detailsOpen,
    closeDetails,
  } = useFetchPipeline()
  const { sourceFilter, setSourceFilter, organizationId, setOrganizationId } = useSourceFilter()
  const { orgs, showSelector: showWorkspaceSelector } = useOrgs()
  const { focusArea, setFocusArea } = useKeyboardNavigation()
  const {
    showPhantomSessions,
    setShowPhantomSessions,
    showArchivedSessions,
    setShowArchivedSessions,
    sortField,
    setSortField,
    sortOrder,
    setSortOrder,
    groupByProject,
    setGroupByProject,
    listRecentOnly,
    setListRecentOnly,
    listRecentHours,
    listProjectKey,
    sidebarFiltersOpen,
    setSidebarFiltersOpen,
    setListRecentHours,
    theme,
    setTheme,
    effectiveTheme,
  } = useSettings()

  // Project select: the project names of the loaded list, before the
  // project, path, and recent filters, so the options stay put while
  // those filters change. Same query (and cache entry) as ConversationList.
  const setListProjectFilter = useSetListProjectFilter()
  const { data: loadedConversations } = useConversations(
    listQueryFilters({
      sourceFilter,
      includePhantom: showPhantomSessions,
      showArchived: showArchivedSessions,
      organizationId,
      sortField,
      sortOrder,
    }),
  )
  // The home folder makes paths read as ~/... (PLANS/2026.10.26-list-project-key.md).
  const { data: appConfig } = useConfig()
  const homeDir = appConfig?.home_dir
  const projects = useMemo(
    () => projectEntries(loadedConversations ?? [], listProjectKey, homeDir),
    [loadedConversations, listProjectKey, homeDir],
  )

  const activeCount = activeFilterCount({
    namedFilter: pickerValue !== ACTIVE_NONE,
    source: sourceFilter,
    workspace: organizationId,
    project: urlFilters.project,
    path: urlFilters.path,
    recentOnly: listRecentOnly,
    showEmpty: showPhantomSessions,
    showArchived: showArchivedSessions,
  })

  // Time dropdown: "Forever" turns the recent filter off; every other
  // window sets its hours. The two preferences stay as before.
  const RECENT_FOREVER = 'forever'
  const recentChoices = recentOptions(listRecentOnly, listRecentHours)
  const recentValue = listRecentOnly ? String(listRecentHours) : RECENT_FOREVER

  const cycleTheme = () => {
    const themes = ['light', 'dark', 'system'] as const
    const currentIndex = themes.indexOf(theme)
    const nextIndex = (currentIndex + 1) % themes.length
    setTheme(themes[nextIndex])
  }

  const ThemeIcon = theme === 'system' ? Monitor : effectiveTheme === 'dark' ? Moon : Sun

  const handleRefresh = () => {
    startRefresh(true)
  }

  // Phase 1 a11y: the <aside>'s onClick is a passive "claim focus area"
  // shortcut; clicking anywhere in the sidebar marks 'list' as the
  // active focus area so keyboard shortcuts (j/k/Tab) route there.
  // Keyboard users reach the same state by Tab-ing into any of the
  // interactive children (search input, conversation buttons, etc.)
  // which fire their own focus handlers. Adding tabIndex+keyDown to
  // the whole <aside> would make it a tab stop above its children;
  // worse keyboard UX, no a11y win.
  return (
    /* react-doctor-disable-next-line react-doctor/click-events-have-key-events,react-doctor/no-static-element-interactions,react-doctor/no-noninteractive-element-interactions */
    <aside
      onClick={() => setFocusArea('list')}
      className={cn(
        'flex h-full w-80 flex-col border-r border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900',
        focusArea === 'list' && 'ring-2 ring-inset ring-blue-500/50',
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-zinc-200 p-4 dark:border-zinc-800">
        <MessageSquare className="h-6 w-6 text-zinc-700 dark:text-zinc-300" />
        <h1 className="flex-1 text-lg font-semibold text-zinc-900 dark:text-zinc-100">
          Claude Explorer
        </h1>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          onClick={handleRefresh}
          disabled={isPipelineRunning}
          title="Refresh conversation list"
        >
          <RefreshCw className={cn("h-4 w-4", isPipelineRunning && "animate-spin")} />
        </Button>
      </div>

      {/* Search and Filter. On a short screen this block scrolls, and the
          list below keeps its minimum height (UX.md → "Conversation List
          filters" → layout). Without that, the watcher and migration
          banners plus these controls left the list 0-1 px high, and it
          showed no row (CI, 2026-10-25). */}
      <div className="min-h-0 shrink overflow-y-auto p-4 pb-2 space-y-2" data-testid="sidebar-controls">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
          <Input
            placeholder="Search titles and projects"
            value={searchQuery}
            onChange={(e) => {
              const next = e.target.value
              setSearchQuery(next)
              // Title search is global by construction (titles span all
              // conversations). The user typing here signals "broaden",
              // so clear any sticky search-scope pin. (Manual finding
              // 2026-05-04: title-search-clears-pin.)
              if (next && pinScope.kind !== 'none') unpinSearch()
            }}
            className="pl-9"
            data-testid="sidebar-title-search"
          />
        </div>
        {/* "Filters and Sorting": the controls below fold away, closed by
            default, so the list starts near the top (PLANS/2026.10.26-
            sidebar-filters-accordion.md). The header counts the active
            filters, so a closed section still says why the list is short. */}
        <button
          type="button"
          onClick={() => setSidebarFiltersOpen(!sidebarFiltersOpen)}
          aria-expanded={sidebarFiltersOpen}
          aria-controls="sidebar-filters"
          data-testid="sidebar-filters-toggle"
          className="flex w-full items-center gap-2 rounded px-1 py-1 text-xs font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
        >
          <ChevronDown className={cn('h-3 w-3 transition-transform', !sidebarFiltersOpen && '-rotate-90')} />
          <SlidersHorizontal className="h-3 w-3" />
          <span className="flex-1 text-left">Filters and Sorting</span>
          {activeCount > 0 && (
            <span
              className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-700 dark:bg-blue-950 dark:text-blue-300"
              data-testid="sidebar-filters-active-count"
            >
              {activeCount} active
            </span>
          )}
        </button>
        {sidebarFiltersOpen && (
          <div id="sidebar-filters" className="space-y-2" data-testid="sidebar-filters">
          {/* CF1: active-filter picker. Replaces the old chip rail.
              "All conversations" maps to activeId=null. */}
          <Select
            value={pickerValue}
            onValueChange={(v: string) => {
              if (v === ACTIVE_MANAGE) {
                setIsManageOpen(true)
                return
              }
              setActiveId(v === ACTIVE_NONE ? null : v)
            }}
          >
            <SelectTrigger className="w-full" data-testid="active-filter-select">
              <FilterIcon className="h-3 w-3 mr-1 text-zinc-400" />
              <SelectValue placeholder="All conversations" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ACTIVE_NONE}>All conversations</SelectItem>
              {pickerOptions.length > 0 && <SelectSeparator />}
              {pickerOptions.map((node) => (
                <SelectItem key={node.id} value={node.id}>
                  {node.name}
                </SelectItem>
              ))}
              <SelectSeparator />
              <SelectItem value={ACTIVE_MANAGE} data-testid="active-filter-manage">
                <span className="flex items-center gap-2 text-zinc-700 dark:text-zinc-300">
                  <Settings2 className="h-3 w-3" />
                  Manage filters…
                </span>
              </SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={sourceFilter}
            onValueChange={(v: string) => {
              // shadcn Select hands us a string; only accept values in the
              // SourceFilter union. Unknown values are silently rejected
              // (defense in depth — the SelectItem children below only
              // ever supply known values).
              if (isSourceFilter(v)) setSourceFilter(v)
            }}
          >
            <SelectTrigger className="w-full" data-testid="source-filter-select">
              <SelectValue placeholder="Filter by source" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">
                <span className="flex items-center gap-2">
                  All Conversations
                </span>
              </SelectItem>
              <SelectItem value="CLAUDE_AI">
                <span className="flex items-center gap-2">
                  <MessageSquare className="h-3 w-3 text-blue-500" />
                  Claude Desktop
                </span>
              </SelectItem>
              <SelectItem value="CLAUDE_CODE">
                <span className="flex items-center gap-2">
                  <Terminal className="h-3 w-3 text-green-500" />
                  Claude Code
                </span>
              </SelectItem>
              <SelectItem value="CLAUDE_COWORK">
                <span className="flex items-center gap-2">
                  <Sparkles className="h-3 w-3 text-purple-500" />
                  Claude Cowork
                </span>
              </SelectItem>
            </SelectContent>
          </Select>
          {/* cowork-multi-org C6: workspace selector. Reserve the slot to
              prevent layout shift mid-stream (P2-2); render the actual
              <Select> only when there are ≥ 2 orgs. */}
          <div className="h-9">
            {showWorkspaceSelector && (
              <Select
                value={organizationId ?? '__all__'}
                onValueChange={(v: string) => setOrganizationId(v === '__all__' ? null : v)}
              >
                <SelectTrigger className="w-full" data-testid="workspace-select">
                  <SelectValue placeholder="Filter by workspace" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All workspaces</SelectItem>
                  {orgs.map((org) => (
                    <SelectItem key={org.org_id} value={org.org_id}>
                      {org.name ?? `Workspace (${org.org_id.slice(0, 8)})`}
                      {org.is_primary ? ' [primary]' : ''}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          {/* Project / path filter (UX.md → "Conversation List filters").
              Always shown: a hidden control could not clear a filter that
              a URL or the conversation header set. */}
          <ProjectPicker
            entries={projects}
            mode={listProjectKey}
            project={urlFilters.project}
            path={urlFilters.path}
            home={homeDir}
            onChange={setListProjectFilter}
          />
          {/* Sort controls */}
          <div className="flex items-center gap-1">
            <Select
              value={sortField}
              onValueChange={(v: string) => {
                if (isSortField(v)) setSortField(v)
              }}
            >
              <SelectTrigger className="flex-1 h-8 text-xs">
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
            <Button
              variant="ghost"
              size="sm"
              className="h-8 px-2"
              onClick={() => setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')}
              title={sortOrder === 'asc' ? 'Ascending' : 'Descending'}
            >
              {sortOrder === 'asc' ? '↑' : '↓'}
            </Button>
          </div>
          {/* Recent sessions: "Last: 1 day" by default; "Forever" shows all. */}
          <Select
            value={recentValue}
            onValueChange={(v: string) => {
              if (v === RECENT_FOREVER) {
                setListRecentOnly(false)
                return
              }
              setListRecentHours(Number(v))
              setListRecentOnly(true)
            }}
          >
            <SelectTrigger
              className="h-8 w-full text-xs"
              title="Show only sessions with activity in this time"
              data-testid="sidebar-recent-select"
            >
              <Clock className="h-3 w-3 mr-1 text-zinc-400" />
              <span className="mr-1 text-zinc-500">Last:</span>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {recentChoices.map((opt) => (
                <SelectItem key={opt.label} value={opt.hours === null ? RECENT_FOREVER : String(opt.hours)}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="flex items-center justify-between">
            <div className="text-xs text-zinc-500">
              <kbd className="rounded bg-zinc-200 px-1 py-0.5 font-mono text-[10px] dark:bg-zinc-700">
                {navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+K
              </kbd>{' '}
              to search messages
            </div>
            <div className="flex items-center gap-2">
              {/* Group by project toggle - only show when Claude Code sessions are visible.
                  Cowork (autonomous decision #3) is excluded: its project_path is a VM
                  sandbox path like /sessions/<vm>, which doesn't group meaningfully. */}
              {sourceFilter !== 'CLAUDE_AI' && sourceFilter !== 'CLAUDE_COWORK' && (
                <label className="flex items-center gap-1 text-xs text-zinc-500 cursor-pointer" title="Group sessions by project">
                  <input
                    type="checkbox"
                    aria-label="Group sessions by project"
                    checked={groupByProject}
                    onChange={(e) => setGroupByProject(e.target.checked)}
                    className="h-3 w-3 rounded border-zinc-300"
                  />
                  <FolderTree className="h-3 w-3" />
                </label>
              )}
              <label className="flex items-center gap-1 text-xs text-zinc-500 cursor-pointer" title="Show empty sessions created by local commands">
                {/* Phase 1 a11y: WCAG-conformant nested label with sibling
                    text. See SettingsPage for rationale. */}
                {/* oxlint-disable-next-line react-doctor/control-has-associated-label */}
                <input
                  type="checkbox"
                  checked={showPhantomSessions}
                  onChange={(e) => setShowPhantomSessions(e.target.checked)}
                  className="h-3 w-3 rounded border-zinc-300"
                  data-testid="show-phantom-sessions-toggle"
                />
                <span>Empty</span>
              </label>
              {/* D8 (Cowork): Show archived toggle. Only meaningful under
                  the Cowork filter (CC/Desktop have no archived flag) and
                  under 'all' (where an archived Cowork session could be
                  hidden). Hidden under CLAUDE_AI / CLAUDE_CODE to avoid
                  a UI element with no effect. */}
              {(sourceFilter === 'CLAUDE_COWORK' || sourceFilter === 'all') && (
                <label className="flex items-center gap-1 text-xs text-zinc-500 cursor-pointer" title="Show archived Cowork sessions">
                  {/* Phase 1 a11y: nested label with sibling text. */}
                  {/* oxlint-disable-next-line react-doctor/control-has-associated-label */}
                  <input
                    type="checkbox"
                    checked={showArchivedSessions}
                    onChange={(e) => setShowArchivedSessions(e.target.checked)}
                    className="h-3 w-3 rounded border-zinc-300"
                    data-testid="show-archived-sessions-toggle"
                  />
                  <span>Archived</span>
                </label>
              )}
            </div>
          </div>
          </div>
        )}
      </div>

      {/* CF3: one-time migration banner. Placed directly above the
          conversation list — the sidebar is where the conversation list
          lives in this app, and the banner needs to sit where the user
          sees the active-filter selection take effect. Self-gates on
          `filters._migratedV1 && !filters.migrationBannerDismissed`. */}
      <MigrationBanner />

      {/* Conversation List: at least 12rem high, about two rows. */}
      <ScrollArea className="min-h-48 flex-1">
        <ConversationList
          searchQuery={searchQuery}
          sourceFilter={sourceFilter}
          includePhantom={showPhantomSessions}
          showArchived={showArchivedSessions}
          sortField={sortField}
          sortOrder={sortOrder}
          groupByProject={groupByProject}
          projectSlug={urlFilters.project || undefined}
          projectPath={urlFilters.path || undefined}
          recentHours={listRecentOnly ? listRecentHours : null}
          projectKey={listProjectKey}
          homeDir={homeDir}
          onShowAllProjects={() => setListProjectFilter({})}
          onShowOlder={() => setListRecentOnly(false)}
          titleFilter={urlFilters.title || undefined}
          titleFilterMode={urlFilters.filterMode}
          organizationId={organizationId}
        />
      </ScrollArea>

      {/* Manage filters modal */}
      <ManageFiltersModal isOpen={isManageOpen} onClose={() => setIsManageOpen(false)} />

      {/* Signed-in account (UX.md → "Signed-in account (sidebar footer)") */}
      <AccountBadge />

      {/* Footer */}
      <div className="flex items-center justify-between border-t border-zinc-200 p-4 dark:border-zinc-800">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" asChild>
            <Link to="/settings">
              <Settings className="h-4 w-4" />
              <span className="ml-2">Settings</span>
            </Link>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={cycleTheme}
            title={`Theme: ${theme} (click to change)`}
            className="h-8 w-8"
          >
            <ThemeIcon className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex gap-1">
          <Button variant="ghost" size="icon" title="Export All">
            <Download className="h-4 w-4" />
          </Button>
          <QuitButton />
        </div>
      </div>

      {/* Fetch Dialog */}
      <FetchDialog isOpen={detailsOpen} onClose={closeDetails} />
    </aside>
  )
}