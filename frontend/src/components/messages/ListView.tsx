/**
 * The List view of the Messages tab: one row per message, oldest first.
 */
import type { PeerGraphMessage, PeerGraphSession } from '@/lib/types'
import { messageId, messageTarget, sessionLabel, type LaneModel } from './model'

interface ListViewProps {
  model: LaneModel
  messages: PeerGraphMessage[]
  sessions: Map<string, PeerGraphSession>
  centerKey: string | null
  onNavigate: (href: string) => void
}

function label(sessions: Map<string, PeerGraphSession>, key: string): string {
  const s = sessions.get(key)
  return s ? sessionLabel(s) : key
}

export function ListView({ model, messages, sessions, centerKey, onNavigate }: ListViewProps) {
  const rows = messages
    .map((m, i) => ({ m, id: messageId(m, i) }))
    .filter(({ m }) => model.laneOf(m.from_key) !== null && model.laneOf(m.to_key) !== null)
  return (
    <table className="w-full text-left text-xs" data-testid="messages-list">
      <thead className="text-zinc-500">
        <tr>
          <th className="px-2 py-1 font-medium">From</th>
          <th className="px-2 py-1 font-medium">To</th>
          <th className="px-2 py-1 font-medium">Time</th>
          <th className="px-2 py-1 font-medium">Message</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ m, id }) => {
          const target = messageTarget(m, sessions, centerKey)
          return (
            <tr
              key={id}
              data-testid="messages-list-row"
              data-msg-id={id}
              title={target.href ? undefined : target.title}
              className={`border-t border-zinc-200 dark:border-zinc-800 ${target.href ? 'cursor-pointer hover:bg-zinc-100 dark:hover:bg-zinc-800' : ''}`}
              onClick={target.href ? () => onNavigate(target.href as string) : undefined}
            >
              <td className="px-2 py-1">{label(sessions, m.from_key)}</td>
              <td className="px-2 py-1">{label(sessions, m.to_key)}</td>
              <td className="whitespace-nowrap px-2 py-1 text-zinc-500">
                {m.timestamp ? new Date(m.timestamp).toLocaleString() : ''}
              </td>
              <td className="max-w-md truncate px-2 py-1">
                {m.kind === 'idle_notice' ? 'Idle notice' : m.kind === 'subagent' ? 'Subagent' : m.text}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
