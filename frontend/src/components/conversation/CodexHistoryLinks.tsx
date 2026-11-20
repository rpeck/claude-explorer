/**
 * CodexHistoryLinks — the "Parent:" and "Forks and subagents:" rows of the
 * Codex details (UX.md, "Codex history"). Each link opens that conversation.
 *
 * The titles come from one list call of every Codex session, archived ones
 * included. A parent that is not in that list is not in the archive, so it
 * shows as its id with no link. Never fetch a detail to test this: a 404 is
 * a console error.
 */
import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryClient'
import type { ConversationDetail } from '@/lib/types'

const ROW = 'flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-zinc-400 dark:text-zinc-500'
const LINK = 'text-blue-600 hover:underline dark:text-blue-400'
const CODEX_ALL = { source: 'CODEX', showArchived: true } as const

export function CodexHistoryLinks({ conversation }: { conversation: ConversationDetail }) {
  const parent = conversation.parent_uuid
  const children = conversation.child_uuids ?? []
  const { data: sessions } = useQuery({
    queryKey: queryKeys.conversations.list(CODEX_ALL),
    queryFn: ({ signal }) => api.getConversations(CODEX_ALL, signal),
    enabled: Boolean(parent) || children.length > 0,
  })
  if (!parent && children.length === 0) return null

  const names = new Map((sessions ?? []).map((s) => [s.uuid, s.name]))
  const link = (uuid: string) => (
    <Link key={uuid} to={`/conversations/${uuid}`} className={LINK}>
      {names.get(uuid) || uuid}
    </Link>
  )

  return (
    <>
      {parent && (
        <div className={ROW} data-testid="codex-parent-row">
          <span className="text-zinc-500">Parent:</span>
          {names.has(parent) ? link(parent) : <span>{parent}</span>}
        </div>
      )}
      {children.length > 0 && (
        <div className={ROW} data-testid="codex-children-row">
          <span className="text-zinc-500">Forks and subagents:</span>
          {children.map(link)}
        </div>
      )}
    </>
  )
}
