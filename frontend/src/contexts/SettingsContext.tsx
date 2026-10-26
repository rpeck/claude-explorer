/* eslint-disable react-refresh/only-export-components -- safe: context Provider, hook, and three runtime predicates (isTheme/isKeyboardMode/isMarkdownExportMode) co-located by intent. The predicates pair-narrow the Settings unions; splitting them into a separate file would force every consumer to track two imports. HMR fast refresh falls back to full reload for this file; no runtime impact. */
import { createContext, use, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { isProjectKey, type ProjectKey } from '@/lib/conversationListView'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { SortField, SortOrder } from '@/lib/types'
import { usePreferences } from '@/hooks/usePreferences'

// P1.1 (2026-05-30) — One-shot tombstone migration for orphan markdown
// preference keys. The 2026-05-29 MarkdownExportMode unification deleted
// `markdownBundleImages` (boolean) and `markdownDialect` (string) from
// SettingsContext, SettingsPage, and MarkdownExportDialog. The new code
// never reads or writes them, but existing users still carry those keys
// in `~/.claude-explorer/preferences.json`. Silently abandoning them
// forecloses ever reusing the same key names (a V2 string-valued
// `markdownBundleImages` would be ambiguous with the old boolean shape).
// One PATCH at first mount with the new code tombstones them to `null`
// and flips a sentinel mirroring the `FilterContext._migratedV1` pattern
// so future mounts skip the work.
const ORPHAN_MIGRATION_SENTINEL_KEY = '_migratedOrphanKeysV1'
const ORPHAN_KEYS = ['markdownBundleImages', 'markdownDialect'] as const

export type Theme = 'light' | 'dark' | 'system'
export type KeyboardMode = 'emacs' | 'vim'

// Markdown export mode — the SINGLE source of truth for how the
// Markdown export should be packaged. Before unification (2026-05-29)
// this lived only in MarkdownExportDialog and was shadowed by orphan
// `markdownBundleImages` + `markdownDialect` keys the Settings page
// wrote but nothing else read. Now the Settings Export section and
// the dialog's "Save as default" both write/read this same key.
export type MarkdownExportMode =
  | 'inline'
  | 'bundle-commonmark'
  | 'bundle-obsidian'

// Runtime predicates for the closed string unions above. Radix
// `RadioGroup.onValueChange` hands callers a `string`, not the
// narrow union — the old `setX(value as Theme)` callsites in
// SettingsPage were runtime lies. These predicates let SettingsPage
// (and any other consumer of an `(value: string)` callback) reject
// unknown values instead of writing garbage to a typed setter.
const THEMES: readonly Theme[] = ['light', 'dark', 'system']
const KEYBOARD_MODES: readonly KeyboardMode[] = ['emacs', 'vim']
const MARKDOWN_EXPORT_MODES: readonly MarkdownExportMode[] = [
  'inline',
  'bundle-commonmark',
  'bundle-obsidian',
]

export function isTheme(v: unknown): v is Theme {
  return typeof v === 'string' && (THEMES as readonly string[]).includes(v)
}

export function isKeyboardMode(v: unknown): v is KeyboardMode {
  return typeof v === 'string' && (KEYBOARD_MODES as readonly string[]).includes(v)
}

export function isMarkdownExportMode(v: unknown): v is MarkdownExportMode {
  return (
    typeof v === 'string' &&
    (MARKDOWN_EXPORT_MODES as readonly string[]).includes(v)
  )
}

interface SettingsContextType {
  // Display settings
  showToolCalls: boolean
  setShowToolCalls: (show: boolean) => void
  expandAllTools: boolean
  setExpandAllTools: (expand: boolean) => void
  showPhantomSessions: boolean
  setShowPhantomSessions: (show: boolean) => void
  // D8 (Cowork, 2026-05-25): toggle for hidden archived Cowork sessions.
  // Persisted via usePreferences like showPhantomSessions.
  showArchivedSessions: boolean
  setShowArchivedSessions: (show: boolean) => void
  hideCompactMarkers: boolean
  setHideCompactMarkers: (hide: boolean) => void
  // Sort and group settings
  sortField: SortField
  setSortField: (field: SortField) => void
  sortOrder: SortOrder
  setSortOrder: (order: SortOrder) => void
  groupByProject: boolean
  setGroupByProject: (group: boolean) => void
  // Conversation List: show only sessions active in the last N hours
  // (UX.md → "Conversation List filters"). Default off ("Forever"); the
  // hours stay 24, so that a later pick of "1 day" means one day.
  listRecentOnly: boolean
  setListRecentOnly: (on: boolean) => void
  listRecentHours: number
  setListRecentHours: (hours: number) => void
  // What a "project" is in the list's picker and folders: one folder path
  // (default) or one folder name (PLANS/2026.10.26-list-project-key.md).
  listProjectKey: ProjectKey
  setListProjectKey: (key: ProjectKey) => void
  // Whether the sidebar's "Filters and Sorting" section is open.
  sidebarFiltersOpen: boolean
  setSidebarFiltersOpen: (open: boolean) => void
  // Theme settings
  theme: Theme
  setTheme: (theme: Theme) => void
  effectiveTheme: 'light' | 'dark'
  // Keyboard settings
  keyboardMode: KeyboardMode
  setKeyboardMode: (mode: KeyboardMode) => void
  // Right-pane tab
  rightPaneTab: 'search' | 'bookmarks'
  setRightPaneTab: (tab: 'search' | 'bookmarks') => void
  // Markdown export mode — unified 2026-05-29. Drives both the
  // Settings page Export section AND the conversation dialog's
  // pre-selected radio + "Save as default" write.
  markdownExportMode: MarkdownExportMode
  setMarkdownExportMode: (mode: MarkdownExportMode) => void
}

const SettingsContext = createContext<SettingsContextType | null>(null)

// Natural sort order for each field
function getDefaultSortOrder(field: SortField): SortOrder {
  switch (field) {
    case 'updated_at':
    case 'created_at':
      return 'desc' // Most recent first
    case 'name':
    case 'project':
      return 'asc' // Alphabetical
    default:
      return 'desc'
  }
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  // Ephemeral (per-session) toggles — not persisted anywhere.
  const [showToolCalls, setShowToolCalls] = useState(false)
  const [expandAllTools, setExpandAllTools] = useState(false)

  // P3d: showPhantomSessions persists via usePreferences (was ephemeral
  // useState before). Server PATCH + localStorage mirror under
  // 'showPhantomSessions'.
  const [showPhantomSessions, setShowPhantomSessions] = usePreferences<boolean>(
    'showPhantomSessions',
    false,
  )

  // D8: same persistence path as showPhantomSessions. Default false
  // so a fresh install hides archived Cowork sessions by default.
  const [showArchivedSessions, setShowArchivedSessions] = usePreferences<boolean>(
    'showArchivedSessions',
    false,
  )

  // P3c — persisted prefs migrated to dual-read/dual-write via
  // usePreferences. The localStorage *keys* are the same legacy strings
  // (e.g. 'theme', 'keyboardMode') so existing browser sessions keep
  // working seamlessly. The local mirror is kept on purpose during the
  // soak window — the hook PATCHes the server AND writes localStorage.
  const [hideCompactMarkers, setHideCompactMarkers] = usePreferences<boolean>(
    'hideCompactMarkers',
    false,
  )
  const [rightPaneTab, setRightPaneTab] = usePreferences<'search' | 'bookmarks'>(
    'rightPaneTab',
    'search',
  )
  const [markdownExportMode, setMarkdownExportMode] = usePreferences<MarkdownExportMode>(
    'markdownExportMode',
    'inline',
  )
  const [sortField, setSortFieldRaw] = usePreferences<SortField>(
    'sortField',
    'updated_at',
  )
  const [sortOrder, setSortOrder] = usePreferences<SortOrder>('sortOrder', 'desc')
  const [groupByProject, setGroupByProject] = usePreferences<boolean>(
    'groupByProject',
    false,
  )
  // Default off since 2026-10-26: opening the app to find an old
  // conversation must not need a filter change first.
  const [listRecentOnly, setListRecentOnly] = usePreferences<boolean>('listRecentOnly', false)
  // The sidebar's "Filters and Sorting" section: closed by default; the
  // user's choice is kept across reloads and restarts.
  const [sidebarFiltersOpen, setSidebarFiltersOpen] = usePreferences<boolean>('sidebarFiltersOpen', false)
  const [listRecentHours, setListRecentHours] = usePreferences<number>('listRecentHours', 24)
  const [rawListProjectKey, setListProjectKey] = usePreferences<ProjectKey>('listProjectKey', 'path')
  // A missing or unknown saved value means "path".
  const listProjectKey: ProjectKey = isProjectKey(rawListProjectKey) ? rawListProjectKey : 'path'
  const [theme, setTheme] = usePreferences<Theme>('theme', 'system')
  const [keyboardMode, setKeyboardMode] = usePreferences<KeyboardMode>(
    'keyboardMode',
    'emacs',
  )

  // Setting sortField also flips sortOrder to its natural direction —
  // preserve that legacy UX. Both writes go through usePreferences, so
  // both PATCH the server and mirror localStorage.
  //
  // `useCallback` is load-bearing for the value-object memo below: a
  // fresh function identity each render would defeat the memo and let
  // the whole context value object churn even when no preferences
  // changed.
  const setSortField = useCallback(
    (field: SortField) => {
      setSortFieldRaw(field)
      setSortOrder(getDefaultSortOrder(field))
    },
    [setSortFieldRaw, setSortOrder],
  )

  // P1.1: orphan-key migration. We need the raw envelope (NOT a sliced
  // value from usePreferences) to decide whether to fire — both to read
  // the sentinel and to detect whether either orphan key is non-null on
  // disk. Subscribing via useQuery here is cheap: the query is already
  // cached under `['preferences']` by every usePreferences consumer, so
  // we share its fetched envelope without re-issuing the GET.
  //
  // Mirrors the `FilterContext._migratedV1` pattern: ref guards within
  // a single mount; the on-disk sentinel guards future mounts. Failure
  // resets the ref so the next mount retries — silent server unavail
  // doesn't permanently strand the orphan keys.
  const qc = useQueryClient()
  const didMigrateOrphanKeysRef = useRef(false)
  const { data: prefsEnvelope } = useQuery<{ version: number; data: Record<string, unknown> }>({
    queryKey: ['preferences'],
    queryFn: async ({ signal }) => {
      const r = await fetch('/api/preferences', { signal })
      if (!r.ok) throw new Error(`prefs GET ${r.status}`)
      return r.json() as Promise<{ version: number; data: Record<string, unknown> }>
    },
    staleTime: 5 * 60 * 1000,
    retry: 1,
  })
  useEffect(() => {
    if (didMigrateOrphanKeysRef.current) return
    if (!prefsEnvelope) return
    const data = prefsEnvelope.data
    if (data[ORPHAN_MIGRATION_SENTINEL_KEY] === true) return
    // Only fire if at least one orphan key is present and non-null on
    // disk — a fresh install with neither key shouldn't be charged a
    // PATCH just to write the sentinel.
    const hasOrphanData = ORPHAN_KEYS.some((k) => data[k] !== undefined && data[k] !== null)
    if (!hasOrphanData) return

    didMigrateOrphanKeysRef.current = true
    void (async () => {
      const payload: Record<string, unknown> = {
        [ORPHAN_MIGRATION_SENTINEL_KEY]: true,
      }
      for (const k of ORPHAN_KEYS) payload[k] = null
      try {
        await fetch('/api/preferences', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ data: payload }),
        })
      } catch {
        /* best effort — next mount will retry */
        didMigrateOrphanKeysRef.current = false
        return
      }
      // Invalidate so the cache reflects the tombstoned values + sentinel
      // without a hard refresh.
      qc.invalidateQueries({ queryKey: ['preferences'] })
    })()
  }, [prefsEnvelope, qc])

  const [systemPrefersDark, setSystemPrefersDark] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
  )

  // Listen for system theme changes
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e: MediaQueryListEvent) => setSystemPrefersDark(e.matches)
    mediaQuery.addEventListener('change', handler)
    return () => mediaQuery.removeEventListener('change', handler)
  }, [])

  // Compute effective theme
  const effectiveTheme = useMemo(() => {
    if (theme === 'system') {
      return systemPrefersDark ? 'dark' : 'light'
    }
    return theme
  }, [theme, systemPrefersDark])

  // 2026-05-22 perf fix (defense-in-depth alongside per-key `select` in
  // usePreferences.ts): memoize the context value so consumers like
  // MessageBubble (which reads useSettings() in its hot render path,
  // MessageBubble.tsx:50) don't get force-rerendered every time
  // SettingsProvider itself rerenders. A bare object literal here
  // would change identity on every render and punch through every
  // consumer's `React.memo` via context-invalidation. Each field is
  // listed explicitly so a future addition that forgets to thread its
  // value/setter into the deps list will surface as a stale-data bug
  // in dev rather than a silent memo-defeat.
  const value = useMemo<SettingsContextType>(
    () => ({
      showToolCalls,
      setShowToolCalls,
      expandAllTools,
      setExpandAllTools,
      showPhantomSessions,
      setShowPhantomSessions,
      showArchivedSessions,
      setShowArchivedSessions,
      hideCompactMarkers,
      setHideCompactMarkers,
      sortField,
      setSortField,
      sortOrder,
      setSortOrder,
      groupByProject,
      setGroupByProject,
      listRecentOnly,
      setListRecentOnly,
      listRecentHours,
      setListRecentHours,
      listProjectKey,
      setListProjectKey,
      sidebarFiltersOpen,
      setSidebarFiltersOpen,
      theme,
      setTheme,
      effectiveTheme,
      keyboardMode,
      setKeyboardMode,
      rightPaneTab,
      setRightPaneTab,
      markdownExportMode,
      setMarkdownExportMode,
    }),
    [
      showToolCalls,
      expandAllTools,
      showPhantomSessions,
      setShowPhantomSessions,
      showArchivedSessions,
      setShowArchivedSessions,
      hideCompactMarkers,
      setHideCompactMarkers,
      sortField,
      setSortField,
      sortOrder,
      setSortOrder,
      groupByProject,
      setGroupByProject,
      listRecentOnly,
      setListRecentOnly,
      listRecentHours,
      setListRecentHours,
      listProjectKey,
      setListProjectKey,
      sidebarFiltersOpen,
      setSidebarFiltersOpen,
      theme,
      setTheme,
      effectiveTheme,
      keyboardMode,
      setKeyboardMode,
      rightPaneTab,
      setRightPaneTab,
      markdownExportMode,
      setMarkdownExportMode,
    ],
  )

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  )
}

export function useSettings() {
  // Phase 3: React 19 use() replaces useContext().
  const context = use(SettingsContext)
  if (!context) {
    throw new Error('useSettings must be used within a SettingsProvider')
  }
  return context
}
