/**
 * The Messages tab of the Activity page (issue #91, UX.md "Messages tab").
 *
 * The default scope is every active session (Running or Open). With a
 * center session, "Around this session" shows the sessions k hops away.
 * The tab has its own filter, separate from the sidebar's active filter.
 * View, scope, k, subagent edges, filter, folds, pins, and hides are saved
 * per center session (one set for "no center").
 */
import { useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useFilters } from '@/contexts/FilterContext'
import { usePreferences } from '@/hooks/usePreferences'
import { GraphView } from './GraphView'
import { ListView } from './ListView'
import { SequenceView, type LaneActions } from './SequenceView'
import {
  buildLanes,
  DEFAULT_PREFS,
  filterSessions,
  type Hops,
  type MessagesPrefs,
  type MessagesView,
} from './model'

const VIEWS: { id: MessagesView; label: string }[] = [
  { id: 'sequence', label: 'Sequence' },
  { id: 'graph', label: 'Graph' },
  { id: 'list', label: 'List' },
]
const HOPS: Hops[] = [1, 2, 3, 4, 5, 'all']

const SELECT =
  'rounded border border-zinc-300 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100'

function toggle(list: string[], key: string): string[] {
  return list.includes(key) ? list.filter((k) => k !== key) : [...list, key]
}

export function MessagesTab({ center }: { center: string | null }) {
  const navigate = useNavigate()
  const { filtersState } = useFilters()
  const [stored, setStored] = usePreferences<Partial<MessagesPrefs>>(`messagesTab:${center ?? 'none'}`, {})
  const prefs: MessagesPrefs = useMemo(() => {
    const merged = { ...DEFAULT_PREFS, ...stored }
    return center ? merged : { ...merged, scope: 'active' }
  }, [stored, center])
  const update = useCallback((patch: Partial<MessagesPrefs>) => setStored({ ...prefs, ...patch }), [prefs, setStored])

  const around = prefs.scope === 'hops' && center !== null
  const { data: graph, isLoading, error } = useQuery({
    queryKey: ['peers', around ? center : null, around ? prefs.hops : null, prefs.subagents],
    queryFn: ({ signal }) =>
      around
        ? api.getConversationPeers(center as string, prefs.hops, prefs.subagents, signal)
        : api.getActivePeers(prefs.subagents, signal),
  })

  const filterId = prefs.filterId && filtersState.nodes[prefs.filterId] ? prefs.filterId : null
  const filtered = useMemo(
    () => (graph ? filterSessions(graph, filtersState, filterId, center) : null),
    [graph, filtersState, filterId, center],
  )
  const model = useMemo(
    () => (filtered && graph ? buildLanes(filtered.kept, prefs, undefined, graph.messages) : null),
    [filtered, prefs, graph],
  )
  const sessions = useMemo(() => new Map((graph?.sessions ?? []).map((s) => [s.key, s])), [graph])

  const actions: LaneActions = useMemo(
    () => ({
      togglePin: (key) => update({ pinned: toggle(prefs.pinned, key) }),
      pin: (key) => update({ pinned: prefs.pinned.includes(key) ? prefs.pinned : [...prefs.pinned, key] }),
      hide: (key) => update({ hidden: prefs.hidden.includes(key) ? prefs.hidden : [...prefs.hidden, key] }),
      toggleCollapse: (key) =>
        prefs.collapsed.includes(key)
          ? update({ collapsed: prefs.collapsed.filter((k) => k !== key) })
          : update({ collapsed: [...prefs.collapsed, key], expanded: prefs.expanded.filter((k) => k !== key) }),
      // "+N sessions": open both the hop folds and a collapsed subtree of the lane.
      expandFold: (key) =>
        update({
          expanded: prefs.expanded.includes(key) ? prefs.expanded : [...prefs.expanded, key],
          collapsed: prefs.collapsed.filter((k) => k !== key),
        }),
    }),
    [prefs, update],
  )

  const filterNames = Object.values(filtersState.nodes).sort((a, b) => a.name.localeCompare(b.name))
  let empty: string | null = null
  if (filtered && model && model.lanes.length === 0 && model.other.length === 0) {
    if (filtered.centerHidden) empty = 'The active filter hides this session.'
    else if (graph && graph.sessions.length > 0 && filtered.kept.length === 0) empty = 'No sessions match the filter.'
    else if (!around && (graph?.sessions.length ?? 0) === 0) empty = 'No active sessions.'
  } else if (filtered?.centerHidden) {
    empty = 'The active filter hides this session.'
  }

  const viewProps = model && graph
    ? { model, messages: graph.messages, sessions, centerKey: center, onNavigate: (href: string) => navigate(href) }
    : null

  return (
    <div data-testid="messages-tab" className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <div className="flex overflow-hidden rounded border border-zinc-300 dark:border-zinc-700" role="group" aria-label="View">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              data-testid={`messages-view-${v.id}`}
              aria-pressed={prefs.view === v.id}
              className={`px-2 py-1 ${prefs.view === v.id ? 'bg-zinc-800 text-white dark:bg-zinc-200 dark:text-zinc-900' : 'hover:bg-zinc-100 dark:hover:bg-zinc-800'}`}
              onClick={() => update({ view: v.id })}
            >
              {v.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1">
          <span className="text-zinc-500">Scope</span>
          <select
            data-testid="messages-scope"
            className={SELECT}
            value={prefs.scope}
            onChange={(e) =>
              update(e.target.value === 'hops' ? { scope: 'hops', hops: 2 } : { scope: 'active' })
            }
          >
            <option value="active">All active sessions</option>
            {center && <option value="hops">Around this session</option>}
          </select>
        </label>
        {around && (
          <label className="flex items-center gap-1">
            <span className="text-zinc-500">Hops</span>
            <select
              data-testid="messages-hops"
              className={SELECT}
              value={String(prefs.hops)}
              onChange={(e) => update({ hops: (e.target.value === 'all' ? 'all' : Number(e.target.value)) as Hops })}
            >
              {HOPS.map((h) => (
                <option key={h} value={String(h)}>
                  {h}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            data-testid="messages-subagents-toggle"
            checked={prefs.subagents}
            onChange={(e) => update({ subagents: e.target.checked })}
          />
          <span>Subagent edges</span>
        </label>
        <label className="flex items-center gap-1">
          <span className="text-zinc-500">Filter</span>
          <select
            data-testid="messages-filter"
            className={SELECT}
            value={filterId ?? ''}
            onChange={(e) => update({ filterId: e.target.value || null })}
          >
            <option value="">No filter</option>
            {filterNames.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name}
              </option>
            ))}
          </select>
        </label>
        {model && model.hiddenCount > 0 && (
          <button
            type="button"
            data-testid="messages-hidden-chip"
            className="rounded-full bg-zinc-200 px-2 py-0.5 hover:bg-zinc-300 dark:bg-zinc-700 dark:hover:bg-zinc-600"
            title="Show the hidden sessions again"
            onClick={() => update({ hidden: [] })}
          >
            Hidden: {model.hiddenCount}
          </button>
        )}
      </div>
      <p data-testid="messages-derived-label" className="text-xs text-zinc-500">
        Order: derived (parent = sender of the first message)
      </p>
      {isLoading && <p className="text-xs text-zinc-500">Loading…</p>}
      {error && <p className="text-xs text-red-600">Could not load the messages.</p>}
      {empty && (
        <p data-testid="messages-empty" className="text-sm text-zinc-500">
          {empty}
        </p>
      )}
      {viewProps && (model?.lanes.length ?? 0) > 0 && (
        <div className="min-h-0 flex-1 overflow-hidden">
          {prefs.view === 'sequence' && <SequenceView {...viewProps} actions={actions} />}
          {prefs.view === 'graph' && <GraphView {...viewProps} onExpandGroup={actions.expandFold} />}
          {prefs.view === 'list' && <ListView {...viewProps} />}
        </div>
      )}
    </div>
  )
}
