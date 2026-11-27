/**
 * PeerSessionsRow — the "Messages with:" row of the details (issue #91,
 * UX.md "Messages between sessions"). It follows the CodexHistoryLinks rules:
 * a peer in the archive is a link that opens it; another peer is plain text.
 * The list comes with the conversation (`peers`), so it needs no other call.
 */
import { Link } from 'react-router'
import type { ConversationDetail } from '@/lib/types'

const ROW = 'flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-zinc-400 dark:text-zinc-500'
const LINK = 'text-blue-600 hover:underline dark:text-blue-400'

export function PeerSessionsRow({ conversation }: { conversation: ConversationDetail }) {
  const peers = conversation.peers ?? []
  if (peers.length === 0) return null
  return (
    <div className={ROW} data-testid="peer-sessions-row">
      <span className="text-zinc-500">Messages with:</span>
      {peers.map((p, i) =>
        p.link_state === 'linked' && p.conversation_uuid ? (
          <Link key={p.conversation_uuid} to={`/conversations/${p.conversation_uuid}`} className={LINK}>
            {p.title || p.name || p.conversation_uuid}
          </Link>
        ) : (
          <span key={`${p.name ?? ''}-${i}`}>{p.name}</span>
        ),
      )}
    </div>
  )
}
