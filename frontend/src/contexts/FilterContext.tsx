/**
 * FilterContext — composable named filters (CF1, 2026-05-07).
 *
 * Replaces the old flat `Filter[] + activeFilterIds[]` model with a graph
 * (`FiltersState`) keyed by filter id. The graph is persisted under one
 * preferences key, `'filters'`, via the existing `usePreferences` hook
 * (server-of-record + localStorage mirror).
 *
 * Two migration phases live here, both gated by their own sentinel so we
 * can roll out independently:
 *
 *   v0 → v1 (CF1): if the legacy keys `savedFilters` / `activeFilterIds`
 *   are present AND `filters._migratedV1 !== true`:
 *     1. Each legacy filter -> AtomFilter (drop pinned/target).
 *     2. Build a 'default-migrated' Group containing the previously-pinned
 *        atom IDs; activeId = 'default-migrated' if any were pinned, else
 *        null.
 *     3. PATCH the new blob AND explicitly null savedFilters /
 *        activeFilterIds. The backend's per-key overwrite leaves
 *        untouched any key not in the payload, so explicit null is
 *        required to clear them.
 *     4. Set _migratedV1 = true so subsequent mounts skip migration.
 *
 *   v1 → v2 (CFR1, 2026-05-07): atoms used to carry
 *   `polarity: 'include' | 'exclude'`. v2 renames this to
 *   `behavior: 'show-only' | 'hide'` (1:1: include → show-only,
 *   exclude → hide). Groups DO NOT carry behavior (council convergence —
 *   they remain pure boolean combinators). Migration runs iff
 *   `_migratedV1 === true` AND `_migratedV2 !== true` AND any node still
 *   carries `polarity` / lacks `behavior`. The migration:
 *     1. For each atom node: derive `behavior` from `polarity` (or
 *        default to 'show-only' if neither is present). Drop `polarity`.
 *     2. Groups are passed through unchanged (no behavior is ever added).
 *     3. Set _migratedV2 = true.
 *     4. PATCH only the `filters` blob (no other keys touched).
 *   The migration is idempotent: re-running yields the same shape and
 *   only PATCHes once per page load (a module-level ref guards
 *   StrictMode double-mount).
 *
 * Both migrations are idempotent under React StrictMode double-mount:
 * module-level refs track "have we sent the migration PATCH this load",
 * and the on-disk sentinels guard future page loads.
 */

import { createContext, use, useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { FilterNode, FiltersState, AtomFilter, GroupFilter, FilterId } from '@/lib/filterEngine'
import { usePreferences } from '@/hooks/usePreferences'

interface LegacyFilter {
  id: string
  name: string
  patterns: string[]
  polarity: 'include' | 'exclude'
  mode: 'glob' | 'regex'
  target: 'title'
  pinned?: boolean
}

interface FilterContextType {
  filtersState: FiltersState
  setActiveId: (id: FilterId | null) => void
  addNode: (node: FilterNode) => void
  updateNode: (id: FilterId, partial: Partial<FilterNode>) => void
  removeNode: (id: FilterId) => void
  /**
   * CF3: persistently dismiss the one-time migration banner. Writes
   * `migrationBannerDismissed: true` into the same `filters` blob (so the
   * sentinel and the dismiss flag travel together) via usePreferences,
   * which handles the server PATCH and localStorage mirror.
   */
  dismissMigrationBanner: () => void
}

const FilterContext = createContext<FilterContextType | null>(null)

const FILTERS_KEY = 'filters'
const LEGACY_FILTERS_KEY = 'savedFilters'
const LEGACY_ACTIVE_KEY = 'activeFilterIds'
const MIGRATED_GROUP_ID = 'default-migrated'
const MIGRATED_GROUP_NAME = 'Default (migrated)'

const INITIAL_STATE: FiltersState = { nodes: {}, activeId: null, _migratedV1: false }

function migrateLegacy(
  legacyFilters: LegacyFilter[],
  legacyActiveIds: string[],
): FiltersState {
  const nodes: Record<FilterId, FilterNode> = {}
  for (const lf of legacyFilters) {
    // v1 atoms carried `polarity: 'include' | 'exclude'`. CFR1 renames to
    // `behavior: 'show-only' | 'hide'` directly during legacy migration —
    // a fresh-from-v0 user skips the v1→v2 step entirely. The mapping is
    // 1:1 (include → show-only, exclude → hide).
    const atom: AtomFilter = {
      type: 'atom',
      id: lf.id,
      name: lf.name,
      enabled: true,
      patterns: Array.isArray(lf.patterns) ? lf.patterns : [],
      behavior: lf.polarity === 'exclude' ? 'hide' : 'show-only',
      mode: lf.mode,
      target: 'title',
    }
    nodes[atom.id] = atom
  }
  // The set of pinned atoms goes into the default-migrated group. Union
  // with any legacyActiveIds so an explicit "active" wins over `pinned`
  // on a per-id basis (matches the old seeding semantics).
  const pinnedIds = legacyFilters.filter((f) => f.pinned).map((f) => f.id)
  const groupChildren = Array.from(new Set([...pinnedIds, ...legacyActiveIds])).filter(
    (id) => id in nodes,
  )
  // Groups are pure boolean combinators in v2 — no behavior. The default
  // group composes children via match='all' (preserves v1 semantics: a
  // legacy "all-of pinned exclude atoms" group keeps its meaning under
  // compose-passes because each atom still returns its own keep/drop and
  // the group ANDs them).
  const group: GroupFilter = {
    type: 'group',
    id: MIGRATED_GROUP_ID,
    name: MIGRATED_GROUP_NAME,
    enabled: true,
    match: 'all',
    childIds: groupChildren,
  }
  nodes[MIGRATED_GROUP_ID] = group
  return {
    nodes,
    activeId: groupChildren.length > 0 ? MIGRATED_GROUP_ID : null,
    _migratedV1: true,
    _migratedV2: true,
  }
}

/**
 * v1 → v2 migration: atoms get `behavior` derived from `polarity`; groups
 * are passed through. Idempotent: an already-v2 atom (no `polarity`,
 * `behavior` present) round-trips unchanged.
 */
function migrateV1toV2(state: FiltersState): FiltersState {
  const nextNodes: Record<FilterId, FilterNode> = {}
  for (const [id, node] of Object.entries(state.nodes)) {
    if (node.type === 'atom') {
      // The legacy shape on disk may still have `polarity`. We strip it
      // explicitly (don't spread the legacy keys forward).
      const legacy = node as AtomFilter & { polarity?: 'include' | 'exclude' }
      const behavior: AtomFilter['behavior'] =
        legacy.behavior ??
        (legacy.polarity === 'exclude' ? 'hide' : 'show-only')
      const migrated: AtomFilter = {
        type: 'atom',
        id: node.id,
        name: node.name,
        enabled: node.enabled,
        patterns: Array.isArray(node.patterns) ? node.patterns : [],
        behavior,
        mode: node.mode,
        target: 'title',
      }
      nextNodes[id] = migrated
    } else {
      // Groups: drop any stray `behavior` if a previous attempt had
      // written one (defense in depth — v2 groups never carry behavior).
      const legacy = node as GroupFilter & { behavior?: unknown }
      const migrated: GroupFilter = {
        type: 'group',
        id: node.id,
        name: node.name,
        enabled: node.enabled,
        match: legacy.match,
        childIds: Array.isArray(legacy.childIds) ? legacy.childIds : [],
      }
      nextNodes[id] = migrated
    }
  }
  return {
    ...state,
    nodes: nextNodes,
    _migratedV2: true,
  }
}

/**
 * "Does this state still carry v1-shape atoms?" — true if any atom node
 * has the legacy `polarity` key OR is missing the `behavior` key. Used
 * to decide whether the v1→v2 migration needs to run on mount.
 */
function needsV2Migration(state: FiltersState): boolean {
  if (state._migratedV2 === true) return false
  for (const node of Object.values(state.nodes)) {
    if (node.type !== 'atom') continue
    const legacy = node as AtomFilter & { polarity?: unknown }
    if ('polarity' in legacy) return true
    if (legacy.behavior === undefined) return true
  }
  // No v1-shape atoms found, but we still flip the sentinel below to
  // mark this state as v2-migrated so subsequent mounts skip the check.
  return false
}

export function FilterProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [filtersState, setFiltersState] = usePreferences<FiltersState>(FILTERS_KEY, INITIAL_STATE)

  // Read legacy keys via the same hook so they participate in the same
  // server fetch / localStorage mirror chain. We never write to these,
  // except via the explicit-null clear inside the migration PATCH.
  const [legacyFilters] = usePreferences<LegacyFilter[] | null>(LEGACY_FILTERS_KEY, null)
  const [legacyActive] = usePreferences<string[] | null>(LEGACY_ACTIVE_KEY, null)

  // Idempotency guards: avoid re-PATCHing during React StrictMode's double
  // mount and across re-renders inside a single page load. The on-disk
  // sentinels guard future loads. Two refs because the v0→v1 and v1→v2
  // migrations are independent — a fresh-from-v0 user does both, a user
  // already on v1 does only v2.
  const didMigrateV1Ref = useRef(false)
  const didMigrateV2Ref = useRef(false)

  // v0 → v1 migration (CF1): legacy `savedFilters` / `activeFilterIds` →
  // composable graph. The migrated blob already carries _migratedV2:true
  // because migrateLegacy() builds atoms with `behavior` directly (no
  // `polarity` ever appears in newly-migrated data).
  useEffect(() => {
    if (didMigrateV1Ref.current) return
    if (filtersState._migratedV1) return

    const hasLegacyFilters = Array.isArray(legacyFilters) && legacyFilters.length > 0
    const hasLegacyActive = Array.isArray(legacyActive) && legacyActive.length > 0

    if (!hasLegacyFilters && !hasLegacyActive) {
      // No legacy, nothing to migrate. We don't flip the sentinel here so
      // a future install with legacy keys would still migrate.
      return
    }

    didMigrateV1Ref.current = true
    // Hunt #2: hasLegacyFilters / hasLegacyActive already prove these
    // are non-null arrays (Array.isArray && .length > 0), but TS can't
    // narrow back to `legacyFilters` / `legacyActive` from the local
    // booleans. Use `?? []` to make the fallback explicit without the
    // `!` non-null assertion.
    const migrated = migrateLegacy(
      hasLegacyFilters ? legacyFilters ?? [] : [],
      hasLegacyActive ? legacyActive ?? [] : [],
    )
    // Single atomic PATCH that:
    //   - writes the new `filters` blob (with _migratedV1: true), AND
    //   - explicitly nulls the legacy keys to clear them server-side.
    // The backend's per-key overwrite leaves untouched any key not
    // present in the payload, so omitting `savedFilters` /
    // `activeFilterIds` would leave them on disk; explicit null is
    // required to clear them.
    void migratePatch(migrated).then(() => {
      // Invalidate the prefs query so subscribers (this provider + every
      // other usePreferences caller) re-fetch and observe the migrated
      // blob without a hard reload.
      qc.invalidateQueries({ queryKey: ['preferences'] })
    })
    // We deliberately depend ONLY on the inputs that decide whether to
    // migrate. Re-running on every render would cause an infinite write.
  }, [filtersState._migratedV1, legacyFilters, legacyActive, qc])

  // v1 → v2 migration (CFR1): `polarity` → `behavior` rename on atoms.
  // Runs iff the v0→v1 step has already landed AND the on-disk blob
  // still carries v1-shape atoms. We don't touch the legacy keys here —
  // those were cleared by the v1 migration and may already be absent.
  useEffect(() => {
    if (didMigrateV2Ref.current) return
    if (!filtersState._migratedV1) return
    if (filtersState._migratedV2) return
    if (!needsV2Migration(filtersState)) {
      // Nothing to rewrite, but flip the sentinel so we stop re-checking
      // on every render. PATCH only the sentinel (no node mutations).
      didMigrateV2Ref.current = true
      void v2SentinelOnlyPatch({ ...filtersState, _migratedV2: true }).then(() => {
        qc.invalidateQueries({ queryKey: ['preferences'] })
      })
      return
    }

    didMigrateV2Ref.current = true
    const migrated = migrateV1toV2(filtersState)
    void v2MigratePatch(migrated).then(() => {
      qc.invalidateQueries({ queryKey: ['preferences'] })
    })
    // We deliberately depend ONLY on the sentinels — including the full
    // `filtersState` would re-fire on every addNode/updateNode/removeNode
    // (the ref guard makes it harmless but wasteful). The body still
    // reads filtersState fresh via closure capture each render.
    // oxlint-disable-next-line react-doctor/exhaustive-deps -- same rationale: sentinel-keyed migration effect; full filtersState read fresh from closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtersState._migratedV1, filtersState._migratedV2, qc])

  // A DOM signal for e2e tests: the two migration effects above have run
  // for the sentinels that this render holds (effects run in source order),
  // so any migration PATCH has been sent. Tests wait for it instead of a
  // fixed delay (preferences-filter-migration.spec.ts, 2026-10-24).
  useEffect(() => {
    const root = document.documentElement.dataset
    root.filtersMigration = `${filtersState._migratedV1 ? 'v1' : '-'}${filtersState._migratedV2 ? 'v2' : '-'}`
    return () => {
      delete root.filtersMigration
    }
  }, [filtersState._migratedV1, filtersState._migratedV2])

  const setActiveId = useCallback(
    (id: FilterId | null) => {
      setFiltersState({ ...filtersState, activeId: id })
    },
    [filtersState, setFiltersState],
  )

  const addNode = useCallback(
    (node: FilterNode) => {
      setFiltersState({
        ...filtersState,
        nodes: { ...filtersState.nodes, [node.id]: node },
      })
    },
    [filtersState, setFiltersState],
  )

  const updateNode = useCallback(
    (id: FilterId, partial: Partial<FilterNode>) => {
      const existing = filtersState.nodes[id]
      if (!existing) return
      // Hunt #2: discriminated-union spread loses the `type` linkage,
      // so TS can't prove `{ ...existing, ...partial }` satisfies
      // FilterNode (the prior code reached for `as FilterNode`).
      // Switch on the existing node's discriminant and rebuild with
      // the matching narrow type. Re-asserting `type` last preserves
      // the invariant that an atom stays an atom and a group stays a
      // group even if a caller accidentally passes a `partial` with a
      // mismatched `type` field.
      let merged: FilterNode
      if (existing.type === 'atom') {
        merged = { ...existing, ...partial, type: 'atom' } as AtomFilter
      } else {
        merged = { ...existing, ...partial, type: 'group' } as GroupFilter
      }
      setFiltersState({
        ...filtersState,
        nodes: { ...filtersState.nodes, [id]: merged },
      })
    },
    [filtersState, setFiltersState],
  )

  const removeNode = useCallback(
    (id: FilterId) => {
      const nextNodes = { ...filtersState.nodes }
      delete nextNodes[id]
      // Strip the id from every group's childIds.
      for (const k of Object.keys(nextNodes)) {
        const n = nextNodes[k]
        if (n.type === 'group' && n.childIds.includes(id)) {
          nextNodes[k] = { ...n, childIds: n.childIds.filter((c) => c !== id) }
        }
      }
      const nextActive = filtersState.activeId === id ? null : filtersState.activeId
      setFiltersState({
        ...filtersState,
        nodes: nextNodes,
        activeId: nextActive,
      })
    },
    [filtersState, setFiltersState],
  )

  const dismissMigrationBanner = useCallback(() => {
    setFiltersState({ ...filtersState, migrationBannerDismissed: true })
  }, [filtersState, setFiltersState])

  const value = useMemo<FilterContextType>(
    () => ({
      filtersState,
      setActiveId,
      addNode,
      updateNode,
      removeNode,
      dismissMigrationBanner,
    }),
    [filtersState, setActiveId, addNode, updateNode, removeNode, dismissMigrationBanner],
  )

  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components -- safe: context Provider + hook co-located by convention. HMR fast refresh falls back to full reload for this file; no runtime impact.
export function useFilters(): FilterContextType {
  // Phase 3: React 19 use() replaces useContext().
  const ctx = use(FilterContext)
  if (!ctx) throw new Error('useFilters must be used within a FilterProvider')
  return ctx
}

// ---------------------------------------------------------------------------
// Migration PATCH helper
// ---------------------------------------------------------------------------

/**
 * Issue a single PATCH that writes the migrated `filters` blob AND
 * explicitly nulls the legacy keys, so the backend's per-key overwrite
 * clears them. Bypasses usePreferences' single-key setter so we can send
 * three keys atomically. The TanStack-Query cache picks up the new state
 * on the next GET; we also mirror the new filters blob to localStorage so
 * a refresh-before-GET path still sees the migrated value.
 */
async function migratePatch(migrated: FiltersState): Promise<void> {
  // Mirror to localStorage so a tab that loads before the GET refetch
  // still sees the migrated state through usePreferences' fallback. Also
  // clear the legacy mirrors so a hard offline refresh immediately after
  // migrating doesn't resurrect them.
  try {
    window.localStorage.setItem(FILTERS_KEY, JSON.stringify(migrated))
    window.localStorage.removeItem(LEGACY_FILTERS_KEY)
    window.localStorage.removeItem(LEGACY_ACTIVE_KEY)
  } catch {
    /* best effort */
  }
  try {
    await fetch('/api/preferences', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        data: {
          [FILTERS_KEY]: migrated,
          [LEGACY_FILTERS_KEY]: null,
          [LEGACY_ACTIVE_KEY]: null,
        },
      }),
    })
  } catch {
    /* best effort — localStorage mirror keeps the new blob alive */
  }
}

/**
 * v1 → v2 PATCH: writes only the `filters` blob (no legacy-key clears,
 * since the v1 migration already nulled them — or they were never
 * present). The migrated blob carries `_migratedV2: true` so subsequent
 * mounts skip this step. localStorage mirror updated for hard-refresh
 * resilience.
 */
async function v2MigratePatch(migrated: FiltersState): Promise<void> {
  try {
    window.localStorage.setItem(FILTERS_KEY, JSON.stringify(migrated))
  } catch {
    /* best effort */
  }
  try {
    await fetch('/api/preferences', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        data: { [FILTERS_KEY]: migrated },
      }),
    })
  } catch {
    /* best effort */
  }
}

/**
 * Sentinel-only PATCH: when the v1 blob is already v2-shaped (e.g., a
 * fresh post-CF1 user who never had v1-polarity atoms because the seed
 * was empty), we still flip `_migratedV2` so we stop re-checking. No
 * node mutations.
 */
async function v2SentinelOnlyPatch(migrated: FiltersState): Promise<void> {
  return v2MigratePatch(migrated)
}
