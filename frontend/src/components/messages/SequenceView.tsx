/**
 * The Sequence view of the Messages tab: one lane per session, time goes
 * down, one arrow per message. Plain SVG for the arrows; the lane heads are
 * HTML so that their menus are buttons.
 */
import { useState } from 'react'
import type { PeerGraphMessage, PeerGraphSession } from '@/lib/types'
import { SessionStateChip } from './SessionStateChip'
import { messageId, messageTarget, sessionLabel, type LaneModel } from './model'

const LANE_W = 168
const ROW_H = 36
const TOP = 12

export interface LaneActions {
  togglePin: (key: string) => void
  hide: (key: string) => void
  toggleCollapse: (key: string) => void
  expandFold: (key: string) => void
  pin: (key: string) => void
}

interface SequenceViewProps {
  model: LaneModel
  messages: PeerGraphMessage[]
  sessions: Map<string, PeerGraphSession>
  centerKey: string | null
  actions: LaneActions
  onNavigate: (href: string) => void
}

function LaneMenu({ laneKey, pinned, collapsed, actions }: {
  laneKey: string
  pinned: boolean
  collapsed: boolean
  actions: LaneActions
}) {
  const [open, setOpen] = useState(false)
  const item = 'block w-full px-3 py-1 text-left text-xs hover:bg-zinc-100 dark:hover:bg-zinc-700'
  const run = (fn: (key: string) => void) => () => {
    setOpen(false)
    fn(laneKey)
  }
  return (
    <span className="relative">
      <button
        type="button"
        data-testid="messages-lane-menu"
        aria-label="Lane actions"
        aria-haspopup="menu"
        aria-expanded={open}
        className="rounded px-1 text-zinc-500 hover:bg-zinc-300 dark:hover:bg-zinc-600"
        onClick={() => setOpen((v) => !v)}
      >
        …
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-20 mt-1 w-28 rounded border border-zinc-200 bg-white py-1 shadow dark:border-zinc-700 dark:bg-zinc-800">
          <button type="button" role="menuitem" className={item} onClick={run(actions.togglePin)}>
            {pinned ? 'Unpin' : 'Pin'}
          </button>
          <button type="button" role="menuitem" className={item} onClick={run(actions.hide)}>
            Hide
          </button>
          <button type="button" role="menuitem" className={item} onClick={run(actions.toggleCollapse)}>
            {collapsed ? 'Expand' : 'Collapse'}
          </button>
        </div>
      )}
    </span>
  )
}

export function SequenceView({ model, messages, sessions, centerKey, actions, onNavigate }: SequenceViewProps) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const laneCount = model.lanes.length + (model.other.length > 0 ? 1 : 0)
  const rows = messages
    .map((m, i) => ({ m, id: messageId(m, i), from: model.laneOf(m.from_key), to: model.laneOf(m.to_key) }))
    .filter((r): r is typeof r & { from: number; to: number } => r.from !== null && r.to !== null && r.from !== r.to)
  const width = laneCount * LANE_W
  const height = TOP * 2 + Math.max(1, rows.length) * ROW_H
  const cx = (lane: number) => lane * LANE_W + LANE_W / 2
  const head = 'flex min-w-0 flex-col gap-0.5 rounded bg-zinc-200 px-2 py-1 text-xs dark:bg-zinc-700'

  // One scroll box for both directions: the lane heads stick to its top.
  return (
    <div className="h-full overflow-auto" data-testid="messages-sequence">
      <div style={{ width }}>
      <div
        style={{ width, display: 'grid', gridTemplateColumns: `repeat(${laneCount}, ${LANE_W}px)` }}
        className="sticky top-0 z-10 bg-white pb-1 dark:bg-zinc-950"
      >
        {model.lanes.map((lane) => (
          <div key={lane.session.key} className="px-1">
            <div data-testid="messages-lane" data-session-key={lane.session.key} className={head}>
              {/* The name has the whole first line; the full name is the tooltip. */}
              <span className="truncate font-medium text-zinc-800 dark:text-zinc-100" title={sessionLabel(lane.session)}>
                {sessionLabel(lane.session)}
              </span>
              <span className="flex items-center gap-1">
                {lane.pinned && <span title="Pinned">📌</span>}
                <SessionStateChip state={lane.session.state} />
                <span className="flex-1" />
                <LaneMenu laneKey={lane.session.key} pinned={lane.pinned} collapsed={lane.collapsed} actions={actions} />
              </span>
            </div>
            {lane.foldCount > 0 && (
              <button
                type="button"
                data-testid="messages-fold"
                className="mt-1 w-full rounded border border-dashed border-zinc-300 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-100 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-800"
                onClick={() => actions.expandFold(lane.session.key)}
              >
                +{lane.foldCount} sessions
              </button>
            )}
          </div>
        ))}
        {model.other.length > 0 && (
          <div className="relative px-1">
            <button
              type="button"
              data-testid="messages-other-lane"
              className={`${head} w-full text-zinc-700 dark:text-zinc-200`}
              onClick={() => setPickerOpen((v) => !v)}
            >
              {model.other.length} other sessions
            </button>
            {pickerOpen && (
              <div
                data-testid="messages-other-picker"
                className="absolute right-0 z-20 mt-1 max-h-64 w-56 overflow-y-auto rounded border border-zinc-200 bg-white py-1 shadow dark:border-zinc-700 dark:bg-zinc-800"
              >
                {model.other.map((s) => (
                  <button
                    key={s.key}
                    type="button"
                    className="block w-full truncate px-3 py-1 text-left text-xs hover:bg-zinc-100 dark:hover:bg-zinc-700"
                    onClick={() => {
                      setPickerOpen(false)
                      actions.pin(s.key)
                    }}
                  >
                    {sessionLabel(s)}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      <svg width={width} height={height} className="text-zinc-500 dark:text-zinc-400" role="img" aria-label="Messages between sessions">
        <defs>
          <marker id="messages-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>
        {Array.from({ length: laneCount }, (_, i) => (
          <line key={i} x1={cx(i)} x2={cx(i)} y1={0} y2={height} stroke="currentColor" strokeOpacity={0.25} strokeDasharray="4 4" />
        ))}
        {rows.map(({ m, id, from, to }, i) => {
          const y = TOP + (i + 0.5) * ROW_H
          const target = messageTarget(m, sessions, centerKey)
          const label = m.kind === 'idle_notice' ? 'idle notice' : m.kind === 'subagent' ? 'subagent' : (m.text ?? '').split('\n')[0]
          return (
            <g
              key={id}
              data-testid="messages-arrow"
              data-msg-id={id}
              className={target.href ? 'cursor-pointer hover:text-blue-600' : undefined}
              onClick={target.href ? () => onNavigate(target.href as string) : undefined}
            >
              <title>{target.href ? `${label}` : `${label} (${target.title})`}</title>
              <line
                x1={cx(from)}
                x2={cx(to) + (to > from ? -4 : 4)}
                y1={y}
                y2={y}
                stroke="currentColor"
                strokeWidth={1.5}
                strokeDasharray={m.kind === 'message' ? undefined : '3 3'}
                markerEnd="url(#messages-arrowhead)"
              />
              <text x={(cx(from) + cx(to)) / 2} y={y - 5} textAnchor="middle" fontSize={10} fill="currentColor">
                {label.length > 40 ? `${label.slice(0, 39)}…` : label}
              </text>
            </g>
          )
        })}
      </svg>
      </div>
    </div>
  )
}
