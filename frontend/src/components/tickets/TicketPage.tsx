/**
 * TicketPage — every session and message that worked on a ticket (issue #93,
 * UX.md "Tickets"). Routes: /tickets/:owner/:repo/:number ("_/_" is the
 * unknown repo) for GitHub, and /tickets/key/:key for a Linear or Jira key.
 *
 * The data is GET /api/tickets/... (backend/tickets.py): sessions newest
 * first, the references that name the ticket, and the PR links. Nothing here
 * calls GitHub, and the start command is only copied, never run.
 */
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { Copy, ExternalLink, Ticket } from 'lucide-react'
import { fetchTicket, fetchTicketByKey } from '@/lib/api'
import { ApiError, type TicketDetail, type TicketRefEntry } from '@/lib/types'
import { useSettings } from '@/contexts/SettingsContext'
import { Button } from '@/components/ui/button'
import { SourceBadge } from '@/components/conversation/SourceBadge'
import { EntrypointChip } from './EntrypointChip'
import { REPO_RE, fillStartCommand, placeholdersInQuotes } from './startCommand'
import { formatFullDate } from '@/lib/utils'

const LINK = 'text-blue-600 hover:underline dark:text-blue-400'

/** "Open in Jira (acme.atlassian.net)", "Open in Linear (acme)"; the host comes from the backend's URL. */
function externalLabel(system: TicketDetail['system'], url: string): string {
  const name = system === 'jira' ? 'Jira' : 'Linear'
  let host = ''
  try {
    const u = new URL(url)
    host = system === 'jira' ? u.host : u.pathname.split('/')[1] ?? ''
    if (system !== 'jira' && host === 'issue') host = '' // linear.app/issue/KEY: no workspace
  } catch {
    host = ''
  }
  return host ? `Open in ${name} (${host})` : `Open in ${name}`
}

/**
 * A title ref whose text equals a message ref's text (a session titled by its
 * first message) shows once: as the message, labeled "title and message".
 * The texts are compared through their snippets, which are equal for equal texts.
 */
function mergeTitleRefs(refs: TicketRefEntry[]): { ref: TicketRefEntry; label: string }[] {
  const titles = new Set(refs.filter((r) => r.kind === 'title').map((r) => r.snippet.trim()))
  const merged = new Set(
    refs.filter((r) => r.kind === 'message' && titles.has(r.snippet.trim())).map((r) => r.snippet.trim()),
  )
  const shown = new Set<string>()
  return refs.flatMap((r) => {
    const text = r.snippet.trim()
    if (r.kind === 'title' && merged.has(text)) return []
    if (r.kind === 'message' && merged.has(text) && !shown.has(text)) {
      shown.add(text)
      return [{ ref: r, label: 'title and message' }]
    }
    return [{ ref: r, label: r.kind }]
  })
}

function startValues(t: TicketDetail) {
  return {
    repo: t.repo,
    issue: t.number,
    title: t.sessions[0]?.name ?? '',
    cwd: t.sessions.find((s) => s.project_path)?.project_path ?? '',
  }
}

export function TicketPage() {
  const { owner = '_', repo = '_', number = '', key } = useParams()
  const { startSessionCommand } = useSettings()
  const [copied, setCopied] = useState(false)
  const query = useQuery({
    queryKey: key ? ['ticket', 'key', key] : ['ticket', owner, repo, number],
    queryFn: ({ signal }) => (key ? fetchTicketByKey(key, signal) : fetchTicket(owner, repo, number, signal)),
    retry: false,
  })
  const unknownRepo = owner === '_' && repo === '_'
  const heading = key ? key.toUpperCase() : unknownRepo ? `#${number} (unknown repo)` : `${owner}/${repo}#${number}`
  const ticket = query.data
  // GitHub: owner and repo only from the checked route key, never from a message URL.
  const githubRepo = !key && !unknownRepo && REPO_RE.test(`${owner}/${repo}`) ? `${owner}/${repo}` : null
  const externalLink = !ticket
    ? null
    : ticket.system === 'github'
      ? githubRepo && /^\d+$/.test(number)
        ? { url: `https://github.com/${githubRepo}/issues/${number}`, label: 'Open in GitHub' }
        : null
      : ticket.url
        ? { url: ticket.url, label: externalLabel(ticket.system, ticket.url) }
        : null

  const onCopy = async () => {
    if (!ticket) return
    await navigator.clipboard.writeText(fillStartCommand(startSessionCommand, startValues(ticket)))
    setCopied(true)
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-6 p-8">
        <div className="flex flex-wrap items-center gap-3">
          <h1
            data-testid="ticket-heading"
            className="flex items-center gap-2 text-2xl font-semibold text-zinc-900 dark:text-zinc-100"
          >
            <Ticket className="h-6 w-6" />
            {heading}
          </h1>
          {startSessionCommand.trim() !== '' && !placeholdersInQuotes(startSessionCommand) && ticket && (
            <Button variant="outline" size="sm" onClick={onCopy} data-testid="start-session-button">
              <Copy className="mr-1 h-4 w-4" />
              Copy start command
            </Button>
          )}
          {externalLink && (
            <a
              href={externalLink.url}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="ticket-external-link"
              className={`inline-flex items-center gap-1 text-sm ${LINK}`}
            >
              {externalLink.label}
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
          {copied && (
            <span role="status" className="text-sm text-green-700 dark:text-green-400">
              Command copied
            </span>
          )}
        </div>

        {query.isLoading && <p className="text-sm text-zinc-500">Loading...</p>}
        {query.error instanceof ApiError && query.error.status === 503 && (
          <p data-testid="ticket-not-ready" className="text-sm text-zinc-500">
            The ticket index is not ready yet.
          </p>
        )}
        {query.error && !(query.error instanceof ApiError && query.error.status === 503) && (
          <p className="text-sm text-red-600 dark:text-red-400">The ticket could not be loaded.</p>
        )}

        {ticket && ticket.sessions.length === 0 && (
          <p data-testid="ticket-empty" className="text-sm text-zinc-500">
            No sessions refer to this ticket.
          </p>
        )}

        {ticket && ticket.prs.length > 0 && (
          <section>
            <h2 className="mb-2 text-sm font-medium text-zinc-700 dark:text-zinc-300">Pull requests</h2>
            <ul className="space-y-1">
              {ticket.prs.map((p) => (
                <li key={p.url}>
                  <a
                    data-testid="ticket-pr"
                    href={p.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`inline-flex items-center gap-1 text-sm ${LINK}`}
                  >
                    {`${p.repository}#${p.number}`}
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}

        {ticket && ticket.sessions.length > 0 && (
          <section>
            <h2 className="mb-2 text-sm font-medium text-zinc-700 dark:text-zinc-300">Sessions</h2>
            <ul className="space-y-3">
              {ticket.sessions.map((s) => (
                <li
                  key={s.conversation_uuid}
                  data-testid="ticket-session"
                  className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800"
                >
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <Link
                      to={`/conversations/${s.conversation_uuid}`}
                      data-testid="ticket-session-link"
                      className={`font-medium ${LINK}`}
                    >
                      {s.name}
                    </Link>
                    <SourceBadge source={s.source} variant="header" />
                    <EntrypointChip entrypoint={s.entrypoint} />
                    {s.state && <span className="text-xs text-zinc-500">{s.state}</span>}
                    {s.machine_label && <span className="text-xs text-zinc-500">{s.machine_label}</span>}
                    <span className="text-xs text-zinc-400">{formatFullDate(s.updated_at)}</span>
                  </div>
                  <ul className="mt-2 space-y-1">
                    {mergeTitleRefs(s.refs).map(({ ref: r, label }, i) => (
                      <li key={`${r.kind}-${r.message_uuid ?? ''}-${i}`} data-testid="ticket-ref" className="text-xs text-zinc-600 dark:text-zinc-400">
                        <span className="mr-1 text-zinc-400">{label}:</span>
                        {r.message_uuid ? (
                          <Link to={`/conversations/${s.conversation_uuid}?m=${encodeURIComponent(r.message_uuid)}`} className={LINK}>
                            {r.snippet}
                          </Link>
                        ) : (
                          <span>{r.snippet}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* The Messages view of #91 (L2), scoped to this ticket's sessions, goes here. */}
      </div>
    </div>
  )
}
