/**
 * TicketsRow — the "Tickets:" row of the details (issue #93, UX.md
 * "Tickets"). One link per ticket key; nothing when the session has none.
 */
import { Link } from 'react-router'
import { ticketLabel, ticketPath } from './ticketKey'

const ROW = 'flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-zinc-400 dark:text-zinc-500'
const LINK = 'text-blue-600 hover:underline dark:text-blue-400'

export function TicketsRow({ tickets }: { tickets?: string[] }) {
  const links = (tickets ?? []).flatMap((k) => {
    const path = ticketPath(k)
    return path ? [{ key: k, path }] : []
  })
  if (links.length === 0) return null
  return (
    <div className={ROW} data-testid="tickets-row">
      <span className="text-zinc-500">Tickets:</span>
      {links.map(({ key, path }) => (
        <Link key={key} to={path} className={LINK} data-testid="ticket-link">
          {ticketLabel(key)}
        </Link>
      ))}
    </div>
  )
}
