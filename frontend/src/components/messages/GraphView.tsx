/**
 * The Graph view of the Messages tab: one node per lane, one node per folded
 * group ("+N sessions"), and one edge per pair of nodes with the message
 * counts ("3 ⇄ 1", the first number from the upper node). Layout by
 * @dagrejs/dagre. It fits the pane at first, within [GRAPH_MIN_SCALE, 1], so
 * the labels stay readable; below that the pane scrolls. "+", "−", and "Fit"
 * zoom, and a drag scrolls.
 */
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Graph, layout } from '@dagrejs/dagre'
import type { PeerGraphMessage, PeerGraphSession } from '@/lib/types'
import { GRAPH_MIN_SCALE, GRAPH_ZOOM_MAX, GRAPH_ZOOM_MIN, messageTarget, sessionLabel, type LaneModel } from './model'

const NODE_W = 168
const NODE_H = 34
// Label sizes at scale 1; Fit never goes below GRAPH_MIN_SCALE, so they stay readable.
const NODE_FONT = 12
const EDGE_FONT = 11
const OTHER = '__other__'

interface GraphViewProps {
  model: LaneModel
  messages: PeerGraphMessage[]
  sessions: Map<string, PeerGraphSession>
  centerKey: string | null
  onNavigate: (href: string) => void
  onExpandGroup: (laneKey: string) => void
}

interface PairEdge {
  v: string
  w: string
  /** Messages v → w and w → v. */
  forward: PeerGraphMessage[]
  back: PeerGraphMessage[]
}

export function GraphView({ model, messages, sessions, centerKey, onNavigate, onExpandGroup }: GraphViewProps) {
  const laid = useMemo(() => {
    const nodes: { id: string; label: string; group?: string }[] = model.lanes.map((l) => ({
      id: l.session.key,
      label: sessionLabel(l.session),
    }))
    for (const l of model.lanes) {
      if (l.foldCount > 0) nodes.push({ id: `group:${l.session.key}`, label: `+${l.foldCount} sessions`, group: l.session.key })
    }
    if (model.other.length > 0) nodes.push({ id: OTHER, label: `${model.other.length} other sessions` })
    const laneIds = new Set(model.lanes.map((l) => l.session.key))
    const nodeOf = (key: string): string | null => {
      const lane = model.laneOf(key)
      if (lane === null) return null
      if (lane === model.lanes.length) return OTHER
      const group = model.groupOf(key)
      return group && laneIds.has(group) ? `group:${group}` : model.lanes[lane].session.key
    }
    const pairs = new Map<string, PairEdge>()
    for (const m of messages) {
      const a = nodeOf(m.from_key)
      const b = nodeOf(m.to_key)
      if (a === null || b === null || a === b) continue
      const k = a < b ? `${a}|${b}` : `${b}|${a}`
      let e = pairs.get(k)
      if (!e) {
        e = { v: a, w: b, forward: [], back: [] }
        pairs.set(k, e)
      }
      ;(e.v === a ? e.forward : e.back).push(m)
    }
    const g = new Graph()
    g.setGraph({ rankdir: 'TB', nodesep: 24, ranksep: 56, marginx: 8, marginy: 8 })
    g.setDefaultEdgeLabel(() => ({}))
    for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H })
    for (const e of pairs.values()) g.setEdge(e.v, e.w, { width: 40, height: 14 })
    layout(g)
    const info = g.graph()
    const pos = (id: string) => g.node(id) as { x: number; y: number }
    return {
      width: Math.max(1, info.width ?? 0),
      height: Math.max(1, info.height ?? 0),
      nodes: nodes.map((n) => ({ ...n, ...pos(n.id) })),
      edges: [...pairs.values()].map((e) => {
        const points = (g.edge(e.v, e.w) as { points?: { x: number; y: number }[] })?.points ?? []
        // The first count is the direction from the upper node.
        const vUpper = pos(e.v).y <= pos(e.w).y
        const down = vUpper ? e.forward.length : e.back.length
        const up = vUpper ? e.back.length : e.forward.length
        const label = down && up ? `${down} ⇄ ${up}` : down ? `${down} ↓` : `${up} ↑`
        return { ...e, points, label }
      }),
    }
  }, [model, messages])

  const boxRef = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  const fit = useCallback(() => {
    const box = boxRef.current
    const w = box?.clientWidth || laid.width
    const h = box?.clientHeight || laid.height
    setScale(Math.min(1, Math.max(GRAPH_MIN_SCALE, Math.min(w / laid.width, h / laid.height))))
    if (box) box.scrollTo(0, 0)
  }, [laid.width, laid.height])
  useLayoutEffect(() => {
    fit()
  }, [fit])
  const zoom = (f: number) => setScale((s) => Math.min(GRAPH_ZOOM_MAX, Math.max(GRAPH_ZOOM_MIN, s * f)))

  // A drag scrolls the pane. A drag of more than 3 px is no click.
  const drag = useRef<{ px: number; py: number; left: number; top: number } | null>(null)
  const dragged = useRef(false)
  const onPointerDown = (e: React.PointerEvent) => {
    const box = boxRef.current
    if (!box) return
    drag.current = { px: e.clientX, py: e.clientY, left: box.scrollLeft, top: box.scrollTop }
    dragged.current = false
  }
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current
    const box = boxRef.current
    if (!d || !box) return
    const dx = e.clientX - d.px
    const dy = e.clientY - d.py
    if (Math.abs(dx) + Math.abs(dy) > 3) dragged.current = true
    box.scrollLeft = d.left - dx
    box.scrollTop = d.top - dy
  }
  const endDrag = () => {
    drag.current = null
  }
  const click = (fn: () => void) => () => {
    if (!dragged.current) fn()
  }
  const btn = 'h-6 min-w-6 rounded border border-zinc-300 bg-white px-1.5 text-xs hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:bg-zinc-800'

  return (
    <div data-testid="messages-graph" className="relative h-full">
      <div className="absolute right-4 top-2 z-10 flex gap-1">
        <button type="button" className={btn} aria-label="Zoom in" onClick={() => zoom(1.25)}>
          +
        </button>
        <button type="button" className={btn} aria-label="Zoom out" onClick={() => zoom(0.8)}>
          −
        </button>
        <button type="button" className={btn} onClick={fit}>
          Fit
        </button>
      </div>
      <div
        ref={boxRef}
        className="h-full cursor-grab select-none overflow-auto active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
      >
      <svg
        width={laid.width * scale}
        height={laid.height * scale}
        viewBox={`0 0 ${laid.width} ${laid.height}`}
        className="text-zinc-500 dark:text-zinc-400"
        role="img"
        aria-label="Sessions and message counts"
      >
        <defs>
          {/* A fixed size: with the default units, a thick edge draws a huge head over its label. */}
          <marker id="messages-graph-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>
        <g>
          {laid.edges.map((e) => {
            const pts = e.points
            if (pts.length === 0) return null
            const mid = pts[Math.floor(pts.length / 2)]
            const first = e.forward[0] ?? e.back[0]
            const target = messageTarget(first, sessions, centerKey)
            return (
              <g
                key={`${e.v}|${e.w}`}
                data-testid="messages-graph-edge"
                aria-label={`${e.label}: ${target.title}`}
                className={target.href ? 'cursor-pointer hover:text-blue-600' : undefined}
                onClick={target.href ? click(() => onNavigate(target.href as string)) : undefined}
              >
                <polyline
                  points={pts.map((p) => `${p.x},${p.y}`).join(' ')}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={Math.min(1 + (e.forward.length + e.back.length) / 2, 6)}
                  markerEnd={e.forward.length ? 'url(#messages-graph-arrowhead)' : undefined}
                  markerStart={e.back.length ? 'url(#messages-graph-arrowhead)' : undefined}
                />
                <text x={mid.x + 4} y={mid.y - 4} fontSize={EDGE_FONT} fill="currentColor">
                  {e.label}
                </text>
              </g>
            )
          })}
          {laid.nodes.map((n) => (
            <g
              key={n.id}
              data-testid="messages-graph-node"
              data-session-key={n.id}
              data-group={n.group ? 'true' : undefined}
              aria-label={n.label}
              transform={`translate(${n.x - NODE_W / 2},${n.y - NODE_H / 2})`}
              className={n.group ? 'cursor-pointer' : undefined}
              onClick={n.group ? click(() => onExpandGroup(n.group as string)) : undefined}
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={6}
                strokeDasharray={n.group ? '4 3' : undefined}
                className="fill-zinc-100 stroke-zinc-400 dark:fill-zinc-800 dark:stroke-zinc-500"
              />
              <text x={NODE_W / 2} y={NODE_H / 2 + 4} textAnchor="middle" fontSize={NODE_FONT} className="fill-zinc-800 dark:fill-zinc-100">
                {n.label.length > 22 ? `${n.label.slice(0, 21)}…` : n.label}
              </text>
              {n.label.length > 22 && <title>{n.label}</title>}
            </g>
          ))}
        </g>
      </svg>
      </div>
    </div>
  )
}
