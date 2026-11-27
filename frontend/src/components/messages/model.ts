/**
 * The model of the Messages tab (issue #91, UX.md "Messages tab"): which
 * sessions show, in which order, and where each message goes. Pure, so the
 * three views share it.
 *
 * - The tab's own filter (a named filter) and the hidden sessions remove
 *   sessions. The center is no exception.
 * - Around a session, a session 2 or more hops away folds under the hop-1
 *   session that reaches it, until the user expands that lane.
 * - Collapse hides the derived subtree of a lane (parent = the sender of
 *   the first message).
 * - Lanes go depth first by the derived hierarchy, pinned lanes first. After
 *   LANE_LIMIT lanes, the rest share one "N other sessions" lane.
 */
import { applyFilterById, type FilterItem, type FiltersState } from '@/lib/filterEngine'
import type { PeerGraph, PeerGraphMessage, PeerGraphSession } from '@/lib/types'

export type MessagesView = 'sequence' | 'graph' | 'list'
export type MessagesScope = 'active' | 'hops'
export type Hops = 1 | 2 | 3 | 4 | 5 | 'all'

export interface MessagesPrefs {
  view: MessagesView
  scope: MessagesScope
  hops: Hops
  subagents: boolean
  filterId: string | null
  /** Hop-1 lanes whose folded sessions show. */
  expanded: string[]
  /** Lanes whose derived subtree is hidden. */
  collapsed: string[]
  pinned: string[]
  hidden: string[]
}

export const DEFAULT_PREFS: MessagesPrefs = {
  view: 'sequence',
  scope: 'active',
  hops: 2,
  subagents: false,
  filterId: null,
  expanded: [],
  collapsed: [],
  pinned: [],
  hidden: [],
}

export const LANE_LIMIT = 12

export function sessionLabel(s: PeerGraphSession): string {
  return s.title || s.name || s.key
}

/** The fields that the tab's filter tests on one session. */
export function filterItem(s: PeerGraphSession, messages: PeerGraphMessage[]): FilterItem {
  return {
    name: sessionLabel(s),
    messageText: messages
      .filter((m) => (m.from_key === s.key || m.to_key === s.key) && m.text)
      .map((m) => m.text as string),
    project: s.project_name,
    source: s.source,
    updatedAt: s.updated_at,
    state: s.state,
  }
}

/** The sessions that the tab's filter keeps, and whether it removes the center. */
export function filterSessions(
  graph: PeerGraph,
  filters: FiltersState,
  filterId: string | null,
  centerKey: string | null,
  now: number = Date.now(),
): { kept: PeerGraphSession[]; centerHidden: boolean } {
  const kept = graph.sessions.filter((s) => applyFilterById(filterItem(s, graph.messages), filters, filterId, now))
  const centerHidden =
    centerKey !== null && graph.sessions.some((s) => s.key === centerKey) && !kept.some((s) => s.key === centerKey)
  return { kept, centerHidden }
}

export interface Lane {
  session: PeerGraphSession
  /** Sessions folded under this lane: hop 2 and more, and its collapsed subtree. */
  foldCount: number
  /** Sessions under this lane in the derived hierarchy. */
  childCount: number
  pinned: boolean
  collapsed: boolean
  expanded: boolean
}

export interface LaneModel {
  lanes: Lane[]
  /** The sessions of the "N other sessions" lane (index `lanes.length`). */
  other: PeerGraphSession[]
  hiddenCount: number
  /** The lane index of a session key; null when the session does not show. */
  laneOf: (key: string) => number | null
  /** The lane whose "+N sessions" group holds this session (folded or collapsed); null otherwise. */
  groupOf: (key: string) => string | null
}

/**
 * The order of sibling lanes: the earlier first message first (sent or
 * received); a session with no message in scope after them, newest last
 * activity first.
 */
function siblingOrder(messages: PeerGraphMessage[]): (a: PeerGraphSession, b: PeerGraphSession) => number {
  const first = new Map<string, number>()
  for (const m of messages) {
    const t = m.timestamp ? Date.parse(m.timestamp) : NaN
    if (Number.isNaN(t)) continue
    for (const k of [m.from_key, m.to_key]) {
      const cur = first.get(k)
      if (cur === undefined || t < cur) first.set(k, t)
    }
  }
  const last = (s: PeerGraphSession) => (s.updated_at ? Date.parse(s.updated_at) || 0 : 0)
  return (a, b) => {
    const ta = first.get(a.key)
    const tb = first.get(b.key)
    if (ta !== undefined && tb !== undefined) return ta - tb
    if (ta !== undefined) return -1
    if (tb !== undefined) return 1
    return last(b) - last(a)
  }
}

export function buildLanes(
  sessions: PeerGraphSession[],
  prefs: MessagesPrefs,
  limit: number = LANE_LIMIT,
  messages: PeerGraphMessage[] = [],
): LaneModel {
  const bySiblingOrder = siblingOrder(messages)
  const hidden = new Set(prefs.hidden)
  const pinned = new Set(prefs.pinned)
  const expanded = new Set(prefs.expanded)
  const collapsed = new Set(prefs.collapsed)
  const hiddenCount = sessions.filter((s) => hidden.has(s.key)).length
  const visible = sessions.filter((s) => !hidden.has(s.key))
  const byKey = new Map(visible.map((s) => [s.key, s]))

  // Folding: hop 2 and more under the hop-1 session that reaches it.
  const owner = new Map<string, string>()
  const foldCount = new Map<string, number>()
  for (const s of visible) {
    if (s.hop === null || s.hop < 2) continue
    let k: string | null = s.key
    const seen = new Set<string>()
    while (k && !seen.has(k)) {
      seen.add(k)
      const cur = byKey.get(k)
      if (!cur || cur.hop === 1) break
      k = cur.via_key
    }
    const top = k ? byKey.get(k) : undefined
    if (top && top.hop === 1 && !expanded.has(top.key)) {
      owner.set(s.key, top.key)
      foldCount.set(top.key, (foldCount.get(top.key) ?? 0) + 1)
    }
  }

  // The derived hierarchy over the sessions that are not folded.
  const shown = visible.filter((s) => !owner.has(s.key))
  const shownKeys = new Set(shown.map((s) => s.key))
  const parentOf = (s: PeerGraphSession) =>
    s.derived_parent_key && s.derived_parent_key !== s.key && shownKeys.has(s.derived_parent_key)
      ? s.derived_parent_key
      : null
  const children = new Map<string, PeerGraphSession[]>()
  for (const s of shown) {
    const p = parentOf(s)
    if (p) children.set(p, [...(children.get(p) ?? []), s])
  }
  for (const list of children.values()) list.sort(bySiblingOrder)
  const order: PeerGraphSession[] = []
  const into = new Map<string, string>() // collapsed session -> the collapsed lane
  const visited = new Set<string>()
  const walk = (s: PeerGraphSession, collapsedAt: string | null) => {
    if (visited.has(s.key)) return
    visited.add(s.key)
    if (collapsedAt) into.set(s.key, collapsedAt)
    else order.push(s)
    const next = collapsedAt ?? (collapsed.has(s.key) ? s.key : null)
    for (const c of children.get(s.key) ?? []) walk(c, next)
  }
  const sorted = [...shown].sort(bySiblingOrder)
  for (const s of sorted) if (!parentOf(s)) walk(s, null)
  for (const s of sorted) walk(s, null) // a cycle of derived parents has no root
  // Pinned lanes first, in pin order.
  const inOrder = new Map(order.map((s) => [s.key, s]))
  const pinnedLanes = prefs.pinned.map((k) => inOrder.get(k)).filter((s): s is PeerGraphSession => Boolean(s))
  const ordered = [...pinnedLanes, ...order.filter((s) => !pinned.has(s.key))]

  const laneSessions = ordered.length > limit ? ordered.slice(0, limit) : ordered
  const other = ordered.length > limit ? ordered.slice(limit) : []
  const index = new Map(laneSessions.map((s, i) => [s.key, i]))
  const otherKeys = new Set(other.map((s) => s.key))
  const collapsedCount = new Map<string, number>()
  for (const lane of into.values()) collapsedCount.set(lane, (collapsedCount.get(lane) ?? 0) + 1)
  const lanes: Lane[] = laneSessions.map((s) => ({
    session: s,
    foldCount: (foldCount.get(s.key) ?? 0) + (collapsedCount.get(s.key) ?? 0),
    childCount: (children.get(s.key) ?? []).length,
    pinned: pinned.has(s.key),
    collapsed: collapsed.has(s.key),
    expanded: expanded.has(s.key),
  }))
  const groupOf = (key: string): string | null => into.get(owner.get(key) ?? key) ?? owner.get(key) ?? null
  const laneOf = (key: string): number | null => {
    const k = groupOf(key) ?? key
    if (index.has(k)) return index.get(k) as number
    return otherKeys.has(k) ? lanes.length : null
  }
  return { lanes, other, hiddenCount, laneOf, groupOf }
}

/** Where a click on a message goes: the side that is not the center (no center: the receiver). */
export function messageTarget(
  m: PeerGraphMessage,
  sessions: Map<string, PeerGraphSession>,
  centerKey: string | null,
): { href: string | null; title: string } {
  const side = centerKey !== null && m.to_key === centerKey && m.from_key !== centerKey ? 'from' : 'to'
  const s = sessions.get(side === 'to' ? m.to_key : m.from_key)
  if (!s || !s.in_archive || !s.uuid) return { href: null, title: 'not in the archive' }
  const path = s.agent_id
    ? `/conversations/${s.uuid}/agents/${encodeURIComponent(s.agent_id)}`
    : `/conversations/${s.uuid}`
  const mu = side === 'to' ? m.to_message_uuid : m.from_message_uuid
  return { href: mu ? `${path}?m=${encodeURIComponent(mu)}` : path, title: sessionLabel(s) }
}

/** A stable id of a message for `data-msg-id`: the msg_id, else its kind, ends, and place. */
export function messageId(m: PeerGraphMessage, i: number): string {
  return m.msg_id ?? `${m.kind}:${m.from_key}:${m.to_key}:${i}`
}
