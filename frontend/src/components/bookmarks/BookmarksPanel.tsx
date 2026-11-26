import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { Bookmark as BookmarkIcon, Bot, Trash2, ExternalLink, Edit3, Download } from 'lucide-react'
import { useBookmarks } from '@/contexts/BookmarkContext'
import { useSearchPanel } from '@/contexts/SearchPanelContext'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { downloadBlob, formatDate } from '@/lib/utils'
import type { Bookmark } from '@/lib/types'

// Issue #96: a bookmark on an agent transcript opens the agent page.
function bookmarkPath(b: Bookmark): string {
  const page = b.agent_id
    ? `/conversations/${b.conversation_id}/agents/${encodeURIComponent(b.agent_id)}`
    : `/conversations/${b.conversation_id}`
  return `${page}?m=${b.message_uuid}`
}

function agentName(b: Bookmark): string | null {
  if (!b.agent_id) return null
  return b.agent_description || `Agent ${b.agent_id}`
}

function bookmarksToMarkdown(bookmarks: Bookmark[]): string {
  const lines: string[] = ['# Bookmarks', '']
  const byConv = new Map<string, Bookmark[]>()
  for (const b of bookmarks) {
    // Insert-or-get: the previous `byConv.get(id)!.push(b)` was logically
    // safe (preceded by `byConv.set(id, [])`) but relied on a non-null
    // assertion to satisfy the type checker. Hold the bucket reference
    // directly so the type system can see it can't be undefined.
    let bucket = byConv.get(b.conversation_id)
    if (!bucket) {
      bucket = []
      byConv.set(b.conversation_id, bucket)
    }
    bucket.push(b)
  }
  for (const [convId, bms] of byConv.entries()) {
    lines.push(`## Conversation ${convId}`, '')
    for (const b of bms) {
      lines.push(`- **[${b.message_uuid.slice(0, 8)}](${bookmarkPath(b)})**`)
      const agent = agentName(b)
      if (agent) lines.push(`  - Agent: ${agent}`)
      if (b.note) lines.push(`  - Note: ${b.note}`)
      if (b.snippet) lines.push(`  - > ${b.snippet}`)
      lines.push('')
    }
  }
  return lines.join('\n')
}

export function BookmarksPanel() {
  const { bookmarks, deleteBookmark, updateBookmarkNote } = useBookmarks()
  const { close } = useSearchPanel()
  const navigate = useNavigate()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draftNote, setDraftNote] = useState('')

  const grouped = useMemo(() => {
    const byConv = new Map<string, Bookmark[]>()
    for (const b of bookmarks) {
      let bucket = byConv.get(b.conversation_id)
      if (!bucket) {
        bucket = []
        byConv.set(b.conversation_id, bucket)
      }
      bucket.push(b)
    }
    return Array.from(byConv.entries())
  }, [bookmarks])

  const handleOpen = (b: Bookmark) => {
    close()
    navigate(bookmarkPath(b))
  }

  const handleExport = () => {
    const md = bookmarksToMarkdown(bookmarks)
    const blob = new Blob([md], { type: 'text/markdown' })
    downloadBlob(blob, `bookmarks-${new Date().toISOString().slice(0, 10)}.md`)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-200 p-3 dark:border-zinc-800">
        <div className="flex items-center justify-between">
          <div className="text-xs text-zinc-500 dark:text-zinc-400">
            {bookmarks.length} bookmark{bookmarks.length === 1 ? '' : 's'}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={handleExport}
            disabled={bookmarks.length === 0}
            aria-label="Export to Markdown"
          >
            <Download className="h-3 w-3 mr-1" />
            Export to Markdown
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {bookmarks.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-sm text-zinc-500 dark:text-zinc-400">
            <BookmarkIcon className="h-8 w-8 text-zinc-300 dark:text-zinc-700" />
            <p>No bookmarks yet</p>
            <p className="text-xs">Hover over any message and click the star to add one.</p>
          </div>
        )}
        {grouped.map(([convId, bms]) => (
          <div key={convId} className="mb-4">
            <div className="mb-1 truncate text-xs font-semibold text-zinc-700 dark:text-zinc-300">
              Conversation {convId.slice(0, 8)}…
            </div>
            <div className="space-y-1.5">
              {bms.map((b) => (
                // Phase 1 a11y: the wrapper's onClick is a mouse-only
                // convenience that opens the bookmark when you click
                // anywhere on the card. Keyboard users already have a
                // canonical activation path: the inner <button> below.
                // Adding tabIndex+key handlers here would create a
                // duplicate tab stop per bookmark row; worse UX, no
                // a11y win.
                // react-doctor-disable-next-line react-doctor/click-events-have-key-events,react-doctor/no-static-element-interactions
                <div
                  key={b.id}
                  data-bookmark-item
                  onClick={(e) => {
                    // Hunt #2: e.target is EventTarget; .closest() lives on
                    // Element. Guard with instanceof so a non-Element target
                    // doesn't crash the click handler. If the target isn't
                    // an Element, treat the click as a row-open (the
                    // button/input child guard exists to prevent re-opening
                    // when those inner controls are the actual target).
                    if (e.target instanceof Element && e.target.closest('button, input')) return
                    handleOpen(b)
                  }}
                  className="cursor-pointer rounded-md border border-zinc-200 bg-white p-2.5 text-xs shadow-sm hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:bg-zinc-800"
                >
                  <div className="flex items-start justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => handleOpen(b)}
                      className="flex-1 truncate text-left text-zinc-900 dark:text-zinc-100"
                      title="Open message"
                    >
                      {b.snippet || `Message ${b.message_uuid.slice(0, 8)}`}
                    </button>
                    {b.agent_id && (
                      <span
                        className="flex shrink-0 items-center gap-1 truncate text-[11px] text-purple-600 dark:text-purple-400"
                        data-testid="bookmark-agent"
                        title={agentName(b) ?? undefined}
                      >
                        <Bot className="h-3 w-3" />
                        {agentName(b)}
                      </span>
                    )}
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => handleOpen(b)}
                        aria-label="Open"
                        className="p-1 text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                      >
                        <ExternalLink className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setEditingId(b.id)
                          setDraftNote(b.note)
                        }}
                        aria-label="Edit note"
                        className="p-1 text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                      >
                        <Edit3 className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteBookmark(b.id)}
                        aria-label="Delete bookmark"
                        className="p-1 text-zinc-500 hover:text-red-600 dark:text-zinc-400 dark:hover:text-red-400"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                  {editingId === b.id ? (
                    <div className="mt-2 flex gap-1">
                      <Input
                        value={draftNote}
                        onChange={(e) => setDraftNote(e.target.value)}
                        placeholder="Note…"
                        className="h-7 text-xs"
                      />
                      <Button
                        size="sm"
                        className="h-7"
                        onClick={async () => {
                          await updateBookmarkNote(b.id, draftNote)
                          setEditingId(null)
                        }}
                      >
                        Save
                      </Button>
                    </div>
                  ) : (
                    <>
                      {b.note && (
                        <div className="mt-1 text-zinc-700 dark:text-zinc-300">{b.note}</div>
                      )}
                      <div className="mt-1 text-[10px] text-zinc-400">
                        {formatDate(b.created_at)}
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
