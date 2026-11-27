import { Link } from 'react-router'
import type { PeerRef } from '@/lib/types'

/**
 * Messages between sessions (issue #91, UX.md "Messages between sessions").
 * A received message shows as "Message from @name" and its body, without the
 * envelope that Claude Code wraps around it. The links come from the search
 * index: "linked" is a link, "not_in_archive" says so, and "unknown" (no index)
 * shows neither.
 */

const LINK = 'text-blue-600 hover:underline dark:text-blue-400'
const MUTED = 'text-xs text-zinc-500 dark:text-zinc-400'

function peerHref(conversationUuid: string, messageUuid: string | null): string {
  return messageUuid ? `/conversations/${conversationUuid}?m=${messageUuid}` : `/conversations/${conversationUuid}`
}

/** "From <title> →" or "Sent to <title> →"; "not in the archive"; or nothing. */
export function PeerLink({ peer, prefix }: { peer: PeerRef; prefix: string }) {
  if (peer.link_state === 'linked' && peer.conversation_uuid) {
    return (
      <Link data-testid="peer-link" to={peerHref(peer.conversation_uuid, peer.message_uuid)} className={LINK}>
        {prefix} {peer.title || peer.name || peer.conversation_uuid} →
      </Link>
    )
  }
  if (peer.link_state === 'not_in_archive') {
    return (
      <span data-testid="peer-not-in-archive" className="italic">
        not in the archive
      </span>
    )
  }
  return null
}

interface PeerMessageRowProps {
  peer: PeerRef
  text: string
  /** The conversation of this row, for the idle notice's link to its own send. */
  conversationId?: string
}

export function PeerMessageRow({ peer, text, conversationId }: PeerMessageRowProps) {
  if (peer.kind === 'idle_notice') {
    return (
      <div data-testid="peer-idle-notice" className={`flex flex-wrap items-center gap-x-2 italic ${MUTED}`}>
        <span>{text}</span>
        {peer.notify_message_uuid && conversationId && (
          <Link data-testid="peer-notify-link" to={peerHref(conversationId, peer.notify_message_uuid)} className={LINK}>
            Your request →
          </Link>
        )}
        <PeerLink peer={peer} prefix="From" />
      </div>
    )
  }
  const showMode = peer.mode && peer.mode !== 'prompting'
  return (
    <div
      data-testid="peer-message-row"
      className="flex flex-col gap-1 rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-sm dark:border-violet-800 dark:bg-violet-950"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <span className="font-medium text-violet-800 dark:text-violet-200">
          Message from @{peer.name || 'another session'}
        </span>
        {showMode && (
          <span
            data-testid="peer-mode-chip"
            className="rounded bg-violet-200 px-1.5 text-xs text-violet-900 dark:bg-violet-800 dark:text-violet-100"
          >
            {peer.mode}
          </span>
        )}
        <span className={MUTED}>
          <PeerLink peer={peer} prefix="From" />
        </span>
      </div>
      <div data-testid="peer-message-body" className="whitespace-pre-wrap break-words text-zinc-800 dark:text-zinc-100">
        {text}
      </div>
      {peer.because_text && peer.conversation_uuid && peer.because_message_uuid && (
        <Link
          data-testid="peer-sent-because"
          to={peerHref(peer.conversation_uuid, peer.because_message_uuid)}
          className={`${MUTED} hover:underline`}
        >
          Sent because of: {peer.because_text}
        </Link>
      )}
    </div>
  )
}
