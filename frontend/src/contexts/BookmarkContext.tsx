import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '@/lib/api'
import type { Bookmark } from '@/lib/types'

interface BookmarkContextType {
  bookmarks: Bookmark[]
  isLoaded: boolean
  isBookmarked: (conversationId: string, messageUuid: string, agentId?: string | null) => boolean
  getBookmarksForConversation: (conversationId: string) => Bookmark[]
  toggleBookmark: (input: Omit<Bookmark, 'id' | 'created_at'>) => Promise<void>
  updateBookmarkNote: (id: string, note: string) => Promise<void>
  deleteBookmark: (id: string) => Promise<void>
  reload: () => Promise<void>
}

const BookmarkContext = createContext<BookmarkContextType | null>(null)

// Issue #96: a bookmark is (conversation, agent or none, message); a missing
// agent_id and null are the same.
const sameTarget = (b: Bookmark, conversationId: string, agentId: string | null | undefined, messageUuid: string) =>
  b.conversation_id === conversationId &&
  (b.agent_id ?? null) === (agentId ?? null) &&
  b.message_uuid === messageUuid

export function BookmarkProvider({ children }: { children: ReactNode }) {
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([])
  const [isLoaded, setIsLoaded] = useState(false)

  const reload = useCallback(async () => {
    try {
      const list = await api.listBookmarks()
      setBookmarks(list)
    } catch {
      setBookmarks([])
    } finally {
      setIsLoaded(true)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Initial mount-time data fetch: setState calls inside reload() land on a promise resolution AFTER the render cycle (no synchronous cascade). Standard initial-load idiom for context-owned async state; reload identity is stable via useCallback([]).
    reload()
  }, [reload])

  const isBookmarked = useCallback(
    (conversationId: string, messageUuid: string, agentId?: string | null) =>
      bookmarks.some((b) => sameTarget(b, conversationId, agentId, messageUuid)),
    [bookmarks]
  )

  const getBookmarksForConversation = useCallback(
    (conversationId: string) => bookmarks.filter((b) => b.conversation_id === conversationId),
    [bookmarks]
  )

  const toggleBookmark = useCallback(async (input: Omit<Bookmark, 'id' | 'created_at'>) => {
    const existing = bookmarks.find((b) =>
      sameTarget(b, input.conversation_id, input.agent_id, input.message_uuid)
    )
    if (existing) {
      await api.deleteBookmark(existing.id)
      setBookmarks((prev) => prev.filter((b) => b.id !== existing.id))
      return
    }
    const created = await api.createBookmark(input)
    setBookmarks((prev) => [...prev, created])
  }, [bookmarks])

  const updateBookmarkNote = useCallback(async (id: string, note: string) => {
    const updated = await api.updateBookmark(id, { note })
    setBookmarks((prev) => prev.map((b) => (b.id === id ? updated : b)))
  }, [])

  const deleteBookmarkFn = useCallback(async (id: string) => {
    await api.deleteBookmark(id)
    setBookmarks((prev) => prev.filter((b) => b.id !== id))
  }, [])

  const value = useMemo<BookmarkContextType>(() => ({
    bookmarks,
    isLoaded,
    isBookmarked,
    getBookmarksForConversation,
    toggleBookmark,
    updateBookmarkNote,
    deleteBookmark: deleteBookmarkFn,
    reload,
  }), [bookmarks, isLoaded, isBookmarked, getBookmarksForConversation, toggleBookmark, updateBookmarkNote, deleteBookmarkFn, reload])

  return <BookmarkContext.Provider value={value}>{children}</BookmarkContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components -- safe: context Provider + hook co-located by convention. Splitting would force every consumer to re-import. HMR fast refresh falls back to full reload for this file; no runtime impact.
export function useBookmarks(): BookmarkContextType {
  // Phase 3: React 19 `use()` replaces `useContext()` (drop-in for
  // non-conditional reads; identical subscription semantics).
  const ctx = use(BookmarkContext)
  if (!ctx) throw new Error('useBookmarks must be used within a BookmarkProvider')
  return ctx
}
