/**
 * Pure logic for the Conversation List view: the project / path filter,
 * the recent-sessions filter, the folders of "Group sessions by project",
 * their order, and the display order that keyboard navigation follows.
 *
 * Spec: PLANS/2026.10.25-sidebar-list-filters.md and UX.md → "Conversation
 * List filters". Kept free of React so that vitest can pin each rule.
 */
import type { ConversationFilters, ConversationListItem, SortField, SortOrder, SourceFilter } from './types'

/**
 * The `/api/conversations` query of the list. ConversationList and the
 * Sidebar's project select both use it, so React Query serves both from
 * one cache entry (the key leaves out `search`, which filters locally).
 */
export function listQueryFilters(o: {
  searchQuery?: string
  sourceFilter?: SourceFilter
  includePhantom?: boolean
  showArchived?: boolean
  organizationId?: string | null
  sortField: SortField
  sortOrder: SortOrder
}): ConversationFilters {
  return {
    ...(o.searchQuery && { search: o.searchQuery }),
    ...(o.sourceFilter && o.sourceFilter !== 'all' && { source: o.sourceFilter }),
    ...(o.includePhantom && { includePhantom: true }),
    ...(o.showArchived && { showArchived: true }),
    ...(o.organizationId && { organization_id: o.organizationId }),
    sort: o.sortField,
    sortOrder: o.sortOrder,
  }
}

export interface Folder {
  name: string
  convs: ConversationListItem[]
}

export interface ProjectFilter {
  /** `?project=`: a `project_name`, matched ignoring case. */
  project: string
  /** `?path=`: a `project_path`, matched exactly. */
  path: string
}

const HOUR_MS = 60 * 60 * 1000

/** The folder that a row goes in (the same names as before 2026-10-25). */
export function folderName(conv: ConversationListItem): string {
  if (conv.source === 'CLAUDE_CODE') return conv.project_name || 'Unknown Project'
  if (conv.organization_name) return conv.organization_name
  // mitm-only-captured org: the same fallback as the Sidebar selector.
  if (conv.organization_id) return `Workspace (${conv.organization_id.slice(0, 8)})`
  return 'Untagged (re-fetch to assign workspace)'
}

/** The value of one row for a sort field. Project sorts by folder name. */
function sortValue(conv: ConversationListItem, field: SortField): number | string {
  switch (field) {
    case 'updated_at':
      return Date.parse(conv.updated_at)
    case 'created_at':
      return Date.parse(conv.created_at)
    case 'name':
      return (conv.name ?? '').toLowerCase()
    case 'project':
      return folderName(conv).toLowerCase()
  }
}

function compare(a: number | string, b: number | string): number {
  return a < b ? -1 : a > b ? 1 : 0
}

/**
 * Group rows into folders and sort the folders. A folder takes the value
 * of its first session in the chosen order: the largest value for a
 * descending sort, the smallest for an ascending sort. A tie sorts by
 * folder name, A to Z. Inside a folder, starred rows come first; otherwise
 * the input order stays.
 */
export function buildFolders(
  convs: ConversationListItem[],
  field: SortField,
  order: SortOrder,
): Folder[] {
  const byName = new Map<string, ConversationListItem[]>()
  for (const conv of convs) {
    const name = folderName(conv)
    let bucket = byName.get(name)
    if (!bucket) {
      bucket = []
      byName.set(name, bucket)
    }
    bucket.push(conv)
  }
  const sign = order === 'desc' ? -1 : 1
  const keyed = Array.from(byName, ([name, rows]) => {
    let key = sortValue(rows[0], field)
    for (const row of rows) {
      const value = sortValue(row, field)
      if (compare(value, key) * sign < 0) key = value
    }
    return { name, key, convs: [...rows.filter((c) => c.is_starred), ...rows.filter((c) => !c.is_starred)] }
  })
  keyed.sort((a, b) => compare(a.key, b.key) * sign || compare(a.name.toLowerCase(), b.name.toLowerCase()))
  return keyed.map(({ name, convs: rows }) => ({ name, convs: rows }))
}

/** Row UUIDs in display order, folder by folder, without collapsed folders. */
export function displayOrder(folders: Folder[], collapsed: Set<string>): string[] {
  return folders.filter((f) => !collapsed.has(f.name)).flatMap((f) => f.convs.map((c) => c.uuid))
}

export function matchesProjectFilter(conv: ConversationListItem, filter: ProjectFilter): boolean {
  if (filter.path) return conv.project_path === filter.path
  if (filter.project) return !!conv.project_name && conv.project_name.toLowerCase() === filter.project.toLowerCase()
  return true
}

export function isRecent(conv: ConversationListItem, nowMs: number, hours: number): boolean {
  return Date.parse(conv.updated_at) >= nowMs - hours * HOUR_MS
}

/**
 * Each project name once (the first spelling wins), newest first: a
 * project sorts by the latest `updated_at` of its sessions, so the
 * projects that the user works in now come first. A tie sorts by name,
 * A to Z, ignoring case.
 */
export function projectOptions(convs: ConversationListItem[]): string[] {
  const seen = new Map<string, { name: string; latest: number }>()
  for (const conv of convs) {
    const name = conv.project_name
    if (!name) continue
    const key = name.toLowerCase()
    const at = Date.parse(conv.updated_at)
    const entry = seen.get(key)
    if (!entry) seen.set(key, { name, latest: at })
    else if (at > entry.latest) entry.latest = at
  }
  return Array.from(seen.values())
    .sort((a, b) => b.latest - a.latest || compare(a.name.toLowerCase(), b.name.toLowerCase()))
    .map((e) => e.name)
}

export interface RecentOption {
  label: string
  /** null = "Forever" (the recent filter is off). */
  hours: number | null
}

/** The time dropdown's windows (PLANS/2026.10.26-list-filter-dropdowns.md). */
export const RECENT_WINDOWS: readonly RecentOption[] = [
  { label: '8 hours', hours: 8 },
  { label: '1 day', hours: 24 },
  { label: '3 days', hours: 72 },
  { label: '1 week', hours: 168 },
  { label: '2 weeks', hours: 336 },
  { label: '1 month', hours: 720 },
  { label: '3 months', hours: 2160 },
  { label: '6 months', hours: 4320 },
  { label: '1 year', hours: 8760 },
  { label: 'Forever', hours: null },
]

function hoursLabel(hours: number): string {
  return hours === 1 ? '1 hour' : `${hours} hours`
}

/**
 * The dropdown's options. A saved number of hours that is not a window
 * (from the old "Last [N] h" field) gets its own option, in its place by
 * size, so the dropdown can show what the filter does.
 */
export function recentOptions(recentOnly: boolean, hours: number): RecentOption[] {
  if (!recentOnly || RECENT_WINDOWS.some((w) => w.hours === hours)) return [...RECENT_WINDOWS]
  const at = RECENT_WINDOWS.findIndex((w) => w.hours === null || w.hours > hours)
  return [...RECENT_WINDOWS.slice(0, at), { label: hoursLabel(hours), hours }, ...RECENT_WINDOWS.slice(at)]
}

/** The label of the window that the saved preferences select. */
export function recentLabel(recentOnly: boolean, hours: number): string {
  if (!recentOnly) return 'Forever'
  return RECENT_WINDOWS.find((w) => w.hours === hours)?.label ?? hoursLabel(hours)
}

/**
 * The list groups only where the "Group sessions by project" checkbox
 * shows: the sources "All Conversations" and "Claude Code". Under Claude
 * Desktop or Cowork, a hidden checkbox must not keep the list grouped.
 */
export function groupingApplies(groupByProject: boolean, source: SourceFilter | undefined): boolean {
  return groupByProject && (source === undefined || source === 'all' || source === 'CLAUDE_CODE')
}
