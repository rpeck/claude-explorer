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
  /** The folder's identity: a project_path in path mode, else the name. */
  key: string
  /** The folder header's text. */
  name: string
  /** The folder header's tooltip (the display path in path mode). */
  title?: string
  convs: ConversationListItem[]
}

/** Where a row goes when the list groups by project. */
export interface FolderId {
  key: string
  label: string
  title?: string
}

/** What a "project" is (PLANS/2026.10.26-list-project-key.md). */
export type ProjectKey = 'path' | 'name'

export function isProjectKey(v: unknown): v is ProjectKey {
  return v === 'path' || v === 'name'
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

/** The value of one row for a sort field. Project sorts by the folder's label. */
function sortValue(conv: ConversationListItem, field: SortField, folderLabel: string): number | string {
  switch (field) {
    case 'updated_at':
      return Date.parse(conv.updated_at)
    case 'created_at':
      return Date.parse(conv.created_at)
    case 'name':
      return (conv.name ?? '').toLowerCase()
    case 'project':
      return folderLabel.toLowerCase()
  }
}

/** Name mode (and every row that is not a Claude Code session): the folder name. */
export function nameFolderOf(conv: ConversationListItem): FolderId {
  const name = folderName(conv)
  return { key: name, label: name }
}

/**
 * Path mode: one folder per project_path, named by its label. The labels
 * come from the paths of `inventory`, so that a filter does not change them.
 */
export function pathFolderOf(
  inventory: ConversationListItem[],
  home: string | undefined,
): (conv: ConversationListItem) => FolderId {
  const labels = pathLabels(claudeCodePaths(inventory), home)
  return (conv) => {
    if (conv.source !== 'CLAUDE_CODE') return nameFolderOf(conv)
    const path = conv.project_path
    if (!path) return { key: 'Unknown Project', label: 'Unknown Project' }
    return { key: path, label: labels.get(path) ?? displayPath(path, home), title: displayPath(path, home) }
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
  folderOf: (conv: ConversationListItem) => FolderId = nameFolderOf,
): Folder[] {
  const byKey = new Map<string, { id: FolderId; rows: ConversationListItem[] }>()
  for (const conv of convs) {
    const id = folderOf(conv)
    let bucket = byKey.get(id.key)
    if (!bucket) {
      bucket = { id, rows: [] }
      byKey.set(id.key, bucket)
    }
    bucket.rows.push(conv)
  }
  const sign = order === 'desc' ? -1 : 1
  const keyed = Array.from(byKey.values(), ({ id, rows }) => {
    let sortKey = sortValue(rows[0], field, id.label)
    for (const row of rows) {
      const value = sortValue(row, field, id.label)
      if (compare(value, sortKey) * sign < 0) sortKey = value
    }
    const ordered = [...rows.filter((c) => c.is_starred), ...rows.filter((c) => !c.is_starred)]
    return { id, sortKey, convs: ordered }
  })
  keyed.sort(
    (a, b) => compare(a.sortKey, b.sortKey) * sign || compare(a.id.label.toLowerCase(), b.id.label.toLowerCase()),
  )
  return keyed.map(({ id, convs: rows }) => ({
    key: id.key,
    name: id.label,
    ...(id.title !== undefined && { title: id.title }),
    convs: rows,
  }))
}

/** Row UUIDs in display order, folder by folder, without collapsed folders. */
export function displayOrder(folders: Folder[], collapsed: Set<string>): string[] {
  return folders.filter((f) => !collapsed.has(f.key)).flatMap((f) => f.convs.map((c) => c.uuid))
}

export function matchesProjectFilter(conv: ConversationListItem, filter: ProjectFilter): boolean {
  if (filter.path) return conv.project_path === filter.path
  if (filter.project) return !!conv.project_name && conv.project_name.toLowerCase() === filter.project.toLowerCase()
  return true
}

export function isRecent(conv: ConversationListItem, nowMs: number, hours: number): boolean {
  return Date.parse(conv.updated_at) >= nowMs - hours * HOUR_MS
}

const SEPARATOR = /[\\/]/

/**
 * A path as the user reads it: inside the home folder it starts with `~`
 * (`~/Source/clientco/ai`); any other path stays as it is. Works with `/`
 * and with `\\` (Windows).
 */
export function displayPath(path: string, home: string | undefined): string {
  if (!home) return path
  const h = home.replace(/[\\/]+$/, '')
  const p = path.replace(/[\\/]+$/, '')
  if (p === h) return '~'
  if (h && p.startsWith(h) && SEPARATOR.test(p.charAt(h.length))) return '~' + p.slice(h.length)
  return path
}

function claudeCodePaths(convs: ConversationListItem[]): string[] {
  const paths = new Set<string>()
  for (const c of convs) if (c.source === 'CLAUDE_CODE' && c.project_path) paths.add(c.project_path)
  return [...paths]
}

/**
 * Path-mode labels: the last 2 parts of each display path. When two or
 * more labels are the same, each of them grows by one part, until every
 * label is unique or a path has no parts left. A display path with 2
 * parts or fewer is its own label (`~/foo`, `/tmp`).
 */
function pathLabels(paths: string[], home: string | undefined): Map<string, string> {
  const items = paths.map((path) => {
    const shown = displayPath(path, home)
    return { path, shown, parts: shown.split(SEPARATOR).filter(Boolean), n: 2 }
  })
  const labelOf = (i: (typeof items)[number]) =>
    i.parts.length <= i.n ? i.shown : i.parts.slice(-i.n).join('/')
  for (;;) {
    const groups = new Map<string, typeof items>()
    for (const i of items) {
      const label = labelOf(i)
      const group = groups.get(label)
      if (group) group.push(i)
      else groups.set(label, [i])
    }
    let grew = false
    for (const group of groups.values()) {
      if (group.length < 2) continue
      for (const i of group) {
        if (i.parts.length > i.n) {
          i.n += 1
          grew = true
        }
      }
    }
    if (!grew) break
  }
  return new Map(items.map((i) => [i.path, labelOf(i)]))
}

/** One entry of the project picker. */
export interface ProjectEntry {
  /** What a pick sets: the project_path (path mode) or the name (name mode). */
  id: string
  label: string
  /** The second line: the display path, or "N folders" in name mode. */
  detail: string
  /** The tooltip: every display path of the entry, one per line. */
  title: string
  latest: number
}

/**
 * The picker's entries from the inventory (the loaded Claude Code
 * sessions), newest first by each entry's latest session; a tie sorts by
 * label, A to Z, ignoring case.
 */
export function projectEntries(
  convs: ConversationListItem[],
  key: ProjectKey,
  home: string | undefined,
): ProjectEntry[] {
  const groups = new Map<string, { id: string; paths: Set<string>; latest: number }>()
  for (const conv of convs) {
    if (conv.source !== 'CLAUDE_CODE' || !conv.project_path) continue
    const id = key === 'path' ? conv.project_path : conv.project_name
    if (!id) continue
    const groupKey = key === 'path' ? id : id.toLowerCase()
    const at = Date.parse(conv.updated_at)
    const group = groups.get(groupKey)
    if (!group) groups.set(groupKey, { id, paths: new Set([conv.project_path]), latest: at })
    else {
      group.paths.add(conv.project_path)
      if (at > group.latest) group.latest = at
    }
  }
  const labels = key === 'path' ? pathLabels([...groups.keys()], home) : null
  const entries = Array.from(groups.values(), ({ id, paths, latest }) => {
    const shown = [...paths].map((p) => displayPath(p, home)).sort()
    const label = labels ? (labels.get(id) ?? shown[0]) : id
    const detail = shown.length === 1 ? shown[0] : `${shown.length} folders`
    return { id, label, detail, title: shown.join('\n'), latest }
  })
  return entries.sort((a, b) => b.latest - a.latest || compare(a.label.toLowerCase(), b.label.toLowerCase()))
}

/** Typing in the picker: the label, the second line, or a path contains the text. */
export function entryMatches(entry: ProjectEntry, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [entry.label, entry.detail, entry.title].some((text) => text.toLowerCase().includes(q))
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

/**
 * How many list filters differ from their defaults, for the "N active"
 * count on the "Filters and Sorting" header
 * (PLANS/2026.10.26-sidebar-filters-accordion.md). Sorting and grouping do
 * not count: they order the list; they hide no rows.
 */
export function activeFilterCount(f: {
  namedFilter: boolean
  source: SourceFilter
  workspace: string | null
  project: string
  path: string
  recentOnly: boolean
  showEmpty: boolean
  showArchived: boolean
}): number {
  return [
    f.namedFilter,
    f.source !== 'all',
    !!f.workspace,
    !!(f.project || f.path),
    f.recentOnly,
    f.showEmpty,
    f.showArchived,
  ].filter(Boolean).length
}
