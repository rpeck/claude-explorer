/**
 * PrsRow — the "PRs:" row of the details (issue #93, UX.md "Tickets"): the
 * session's GitHub PRs from its pr-link records, each a link to the PR.
 * Nothing when the session has none.
 */
import { splitTicketKey } from './ticketKey'

const ROW = 'flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-zinc-400 dark:text-zinc-500'
const LINK = 'text-blue-600 hover:underline dark:text-blue-400'

export function PrsRow({ prKeys }: { prKeys?: string[] }) {
  // Built from the checked key ("owner/repo#85"), never from a message URL.
  const links = (prKeys ?? []).flatMap((k) => {
    const parts = splitTicketKey(k)
    return parts?.repo ? [{ key: k, url: `https://github.com/${parts.repo}/pull/${parts.number}` }] : []
  })
  if (links.length === 0) return null
  return (
    <div className={ROW} data-testid="prs-row">
      <span className="text-zinc-500">PRs:</span>
      {links.map(({ key, url }) => (
        <a key={key} href={url} target="_blank" rel="noopener noreferrer" className={LINK} data-testid="pr-link">
          {key}
        </a>
      ))}
    </div>
  )
}
