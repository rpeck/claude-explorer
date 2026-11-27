/**
 * The Graph view of the Messages tab: one node per lane, one edge per pair
 * of lanes and direction, with the message count. Layout by @dagrejs/dagre.
 */
import { useMemo } from 'react'
import { Graph, layout } from '@dagrejs/dagre'
import type { PeerGraphMessage, PeerGraphSession } from '@/lib/types'
import { messageTarget, sessionLabel, type LaneModel } from './model'

const NODE_W = 160
const NODE_H = 32

interface GraphViewProps {
  model: LaneModel
  messages: PeerGraphMessage[]
  sessions: Map<string, PeerGraphSession>
  centerKey: string | null
  onNavigate: (href: string) => void
}

export function GraphView({ model, messages, sessions, centerKey, onNavigate }: GraphViewProps) {
  const laid = useMemo(() => {
    const nodes = model.lanes.map((l) => ({ id: l.session.key, label: sessionLabel(l.session) }))
    if (model.other.length > 0) nodes.push({ id: '__other__', label: `${model.other.length} other sessions` })
    const idOf = (lane: number) => nodes[lane].id
    const edges = new Map<string, { v: string; w: string; messages: PeerGraphMessage[] }>()
    for (const m of messages) {
      const a = model.laneOf(m.from_key)
      const b = model.laneOf(m.to_key)
      if (a === null || b === null || a === b) continue
      const k = `${a}>${b}`
      const e = edges.get(k) ?? { v: idOf(a), w: idOf(b), messages: [] }
      e.messages.push(m)
      edges.set(k, e)
    }
    const g = new Graph({ multigraph: false })
    g.setGraph({ rankdir: 'TB', nodesep: 24, ranksep: 56, marginx: 8, marginy: 8 })
    g.setDefaultEdgeLabel(() => ({}))
    for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H })
    for (const e of edges.values()) g.setEdge(e.v, e.w, { width: 24, height: 14 })
    layout(g)
    const info = g.graph()
    return {
      width: (info.width ?? 0) + 16,
      height: (info.height ?? 0) + 16,
      nodes: nodes.map((n) => ({ ...n, ...(g.node(n.id) as { x: number; y: number }) })),
      edges: [...edges.values()].map((e) => ({
        ...e,
        points: ((g.edge(e.v, e.w) as { points?: { x: number; y: number }[] })?.points ?? []),
      })),
    }
  }, [model, messages])

  return (
    <div className="overflow-auto" data-testid="messages-graph">
      <svg width={laid.width} height={laid.height} className="text-zinc-500 dark:text-zinc-400" role="img" aria-label="Sessions and message counts">
        <defs>
          <marker id="messages-graph-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>
        {laid.edges.map((e) => {
          const pts = e.points
          if (pts.length === 0) return null
          const mid = pts[Math.floor(pts.length / 2)]
          const first = e.messages[0]
          const target = messageTarget(first, sessions, centerKey)
          return (
            <g
              key={`${e.v}>${e.w}`}
              data-testid="messages-graph-edge"
              className={target.href ? 'cursor-pointer hover:text-blue-600' : undefined}
              onClick={target.href ? () => onNavigate(target.href as string) : undefined}
            >
              <title>{target.href ? target.title : `${target.title}`}</title>
              <polyline
                points={pts.map((p) => `${p.x},${p.y}`).join(' ')}
                fill="none"
                stroke="currentColor"
                strokeWidth={Math.min(1 + e.messages.length / 2, 6)}
                markerEnd="url(#messages-graph-arrowhead)"
              />
              <text x={mid.x + 4} y={mid.y - 4} fontSize={11} fill="currentColor">
                {e.messages.length}
              </text>
            </g>
          )
        })}
        {laid.nodes.map((n) => (
          <g key={n.id} data-testid="messages-graph-node" data-session-key={n.id} transform={`translate(${n.x - NODE_W / 2},${n.y - NODE_H / 2})`}>
            <rect width={NODE_W} height={NODE_H} rx={6} className="fill-zinc-100 stroke-zinc-400 dark:fill-zinc-800 dark:stroke-zinc-500" />
            <text x={NODE_W / 2} y={NODE_H / 2 + 4} textAnchor="middle" fontSize={11} className="fill-zinc-800 dark:fill-zinc-100">
              {n.label.length > 24 ? `${n.label.slice(0, 23)}…` : n.label}
            </text>
            <title>{n.label}</title>
          </g>
        ))}
      </svg>
    </div>
  )
}
