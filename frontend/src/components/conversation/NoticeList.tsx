/**
 * NoticeList — the `notices` of a conversation (Codex plan §4.5), above
 * the messages: for example "incomplete: compacted" or "Imported from
 * Claude Code". They tell the user that the viewer may not show the whole
 * history. Renders nothing for an empty list (every non-Codex session).
 */
import { Info } from 'lucide-react'

interface NoticeListProps {
  notices: string[]
}

export function NoticeList({ notices }: NoticeListProps) {
  if (notices.length === 0) return null
  return (
    <ul
      data-testid="conversation-notices"
      aria-label="Notices"
      className="mt-2 space-y-1 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
    >
      {notices.map((notice, i) => (
        // Notices can repeat, so the index is part of the key.
        <li key={`${i}-${notice}`} className="flex items-start gap-2">
          <Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
          <span>{notice}</span>
        </li>
      ))}
    </ul>
  )
}
