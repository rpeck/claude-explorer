/**
 * Issue #96 addendum (PR #97): the agent id in bookmarks (SA-BOOKMARKS.md items 2-4).
 *
 * * `isBookmarked(conversationId, messageUuid, agentId?)` and `toggleBookmark`
 *   use the triple (conversation, agent, message); null and a missing
 *   field are the same.
 * * `MessageBubble` takes `agentId` and `agentDescription` as props (Change 1):
 *   the star and the bookmark use them.
 * * The `b` hotkey on an agent page bookmarks with the agent id and description.
 * * The Bookmarks tab opens an agent bookmark on the agent page with `?m=`,
 *   shows the agent (`data-testid="bookmark-agent"`), and its Markdown export
 *   names the agent.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { useEffect, useState, type ReactNode } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { BookmarkProvider, useBookmarks } from '../../contexts/BookmarkContext'
import { SettingsProvider } from '../../contexts/SettingsContext'
import { KeyboardNavigationProvider } from '../../contexts/KeyboardNavigationContext'
import { MessageBubble } from '../../components/message/MessageBubble'
import { BookmarksPanel } from '../../components/bookmarks/BookmarksPanel'
import { useBookmarkHotkey } from '../../hooks/useBookmarkHotkey'
import type { Bookmark, ConversationDetail, Message } from '../../lib/types'

const downloads = vi.hoisted(() => [] as Blob[])
vi.mock('@/lib/utils', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/lib/utils')>()
  return { ...mod, downloadBlob: (blob: Blob) => { downloads.push(blob) } }
})
vi.mock('@/contexts/SearchPanelContext', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/contexts/SearchPanelContext')>()
  return { ...mod, useSearchPanel: () => ({ close: () => {} }) }
})

const SESSION = '5a6e0000-0000-4000-8000-000000000096'
const AGENT = 'a5a0000000000096c'
const DESCRIPTION = 'Synthetic search agent'
const MSG = 'sa-t1'

type AgentFields = Pick<Bookmark, 'agent_id' | 'agent_description'>

function bookmark(id: string, fields: AgentFields = {}): Bookmark {
  return {
    id,
    conversation_id: SESSION,
    message_uuid: MSG,
    source: 'claude_code',
    created_at: '2026-11-25T10:00:00Z',
    note: '',
    snippet: 'I looked at the files',
    ...fields,
  } as Bookmark
}

/** A stateful /api/bookmarks; returns the POST bodies and the DELETE ids. */
function serveBookmarks(initial: Bookmark[]) {
  let list = [...initial]
  const posts: Record<string, unknown>[] = []
  const deletes: string[] = []
  let n = 0
  server.use(
    http.get('/api/bookmarks', () => HttpResponse.json({ bookmarks: list })),
    http.post('/api/bookmarks', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>
      posts.push(body)
      const created = { ...body, id: `bm-new-${++n}`, created_at: '2026-11-25T11:00:00Z' } as unknown as Bookmark
      list = [...list, created]
      return HttpResponse.json(created, { status: 201 })
    }),
    http.delete('/api/bookmarks/:id', ({ params }) => {
      deletes.push(String(params.id))
      list = list.filter((b) => b.id !== params.id)
      return new HttpResponse(null, { status: 204 })
    }),
    http.get('/api/preferences', () => HttpResponse.json({ version: 1, data: {} })),
    http.patch('/api/preferences', () => HttpResponse.json({ version: 1, data: {} })),
  )
  return { posts, deletes }
}

const seen = { current: { pathname: '', search: '' } }

function LocationCapture() {
  const loc = useLocation()
  useEffect(() => {
    seen.current = { pathname: loc.pathname, search: loc.search }
  }, [loc.pathname, loc.search])
  return null
}

function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }))
  return (
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        <BookmarkProvider>
          <MemoryRouter initialEntries={['/conversations']}>
            <KeyboardNavigationProvider>
              <Routes>
                <Route path="*" element={<>{children}<LocationCapture /></>} />
              </Routes>
            </KeyboardNavigationProvider>
          </MemoryRouter>
        </BookmarkProvider>
      </SettingsProvider>
    </QueryClientProvider>
  )
}

async function contextHook() {
  const hook = renderHook(() => useBookmarks(), { wrapper: Providers })
  await waitFor(() => expect(hook.result.current.isLoaded).toBe(true))
  return hook
}

afterEach(() => {
  downloads.length = 0
})

// ---------------------------------------------------------------------------
// The context: identity is the triple
// ---------------------------------------------------------------------------

describe('BookmarkContext — the agent id (#97)', () => {
  it('isBookmarked matches the triple: an agent bookmark is not a session bookmark', async () => {
    serveBookmarks([bookmark('bm-agent', { agent_id: AGENT, agent_description: DESCRIPTION })])
    const { result } = await contextHook()
    expect(result.current.isBookmarked(SESSION, MSG, AGENT)).toBe(true)
    expect(result.current.isBookmarked(SESSION, MSG)).toBe(false)
    expect(result.current.isBookmarked(SESSION, MSG, null)).toBe(false)
    expect(result.current.isBookmarked(SESSION, MSG, 'aother0000000')).toBe(false)
  })

  it('an old bookmark without the fields is a session bookmark', async () => {
    serveBookmarks([bookmark('bm-old')])
    const { result } = await contextHook()
    expect(result.current.isBookmarked(SESSION, MSG)).toBe(true)
    expect(result.current.isBookmarked(SESSION, MSG, null)).toBe(true)
    expect(result.current.isBookmarked(SESSION, MSG, AGENT)).toBe(false)
  })

  it('toggling an agent bookmark sends agent_id and agent_description, and keeps the session bookmark', async () => {
    const { posts, deletes } = serveBookmarks([bookmark('bm-session')])
    const { result } = await contextHook()
    await act(() => result.current.toggleBookmark({
      conversation_id: SESSION, message_uuid: MSG, source: 'claude_code', note: '', snippet: 'x',
      agent_id: AGENT, agent_description: DESCRIPTION,
    }))
    expect(deletes).toEqual([])
    expect(posts).toHaveLength(1)
    expect(posts[0]).toMatchObject({ agent_id: AGENT, agent_description: DESCRIPTION })
    expect(result.current.isBookmarked(SESSION, MSG, AGENT)).toBe(true)
    expect(result.current.isBookmarked(SESSION, MSG)).toBe(true)
  })

  it('toggling again removes the agent bookmark by the triple, not the session one', async () => {
    const { posts, deletes } = serveBookmarks([
      bookmark('bm-session'),
      bookmark('bm-agent', { agent_id: AGENT, agent_description: DESCRIPTION }),
    ])
    const { result } = await contextHook()
    await act(() => result.current.toggleBookmark({
      conversation_id: SESSION, message_uuid: MSG, source: 'claude_code', note: '', snippet: 'x',
      agent_id: AGENT, agent_description: DESCRIPTION,
    }))
    expect(posts).toEqual([])
    expect(deletes).toEqual(['bm-agent'])
    expect(result.current.isBookmarked(SESSION, MSG)).toBe(true)
  })

  it('toggling a session bookmark removes the session one, not the agent one', async () => {
    const { deletes } = serveBookmarks([
      bookmark('bm-agent', { agent_id: AGENT, agent_description: DESCRIPTION }),
      bookmark('bm-session'),
    ])
    const { result } = await contextHook()
    await act(() => result.current.toggleBookmark({
      conversation_id: SESSION, message_uuid: MSG, source: 'claude_code', note: '', snippet: 'x',
    }))
    expect(deletes).toEqual(['bm-session'])
    expect(result.current.isBookmarked(SESSION, MSG, AGENT)).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// MessageBubble: the agentId prop
// ---------------------------------------------------------------------------

const message = {
  uuid: MSG,
  sender: 'assistant',
  text: 'I looked at the files',
  content: [{ type: 'text', text: 'I looked at the files' }],
  created_at: '2026-11-25T10:00:00Z',
  updated_at: '2026-11-25T10:00:00Z',
  truncated: false,
  parent_message_uuid: null,
  attachments: [],
  files: [],
} as unknown as Message

const Bubble = MessageBubble

function star(): HTMLElement {
  return screen.getByRole('button', { name: /bookmark/i })
}

describe('MessageBubble — agentId (#97)', () => {
  it('on an agent page the star shows the agent bookmark, not the session one', async () => {
    serveBookmarks([bookmark('bm-session')])
    // On the session page the message is bookmarked...
    const { rerender } = render(
      <Providers><Bubble message={message} conversationId={SESSION} conversationSource="CLAUDE_CODE" /></Providers>,
    )
    await waitFor(() => expect(star()).toHaveAttribute('aria-label', 'Remove bookmark'))
    // ...and on the agent page it is not (the comparator sees agentId).
    rerender(<Providers><Bubble message={message} conversationId={SESSION} conversationSource="CLAUDE_CODE" agentId={AGENT} /></Providers>)
    await waitFor(() => expect(star()).toHaveAttribute('aria-label', 'Bookmark message'))
  })

  it('the star on an agent page posts the agent id and description', async () => {
    const { posts } = serveBookmarks([])
    render(
      <Providers>
        <Bubble message={message} conversationId={SESSION} conversationSource="CLAUDE_CODE" agentId={AGENT} agentDescription={DESCRIPTION} />
      </Providers>,
    )
    await waitFor(() => expect(star()).toHaveAttribute('aria-label', 'Bookmark message'))
    fireEvent.click(star())
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toMatchObject({ conversation_id: SESSION, message_uuid: MSG, agent_id: AGENT, agent_description: DESCRIPTION })
    await waitFor(() => expect(star()).toHaveAttribute('aria-label', 'Remove bookmark'))
  })

  it('a new agentDescription alone reaches the star (the memo comparator sees it)', async () => {
    const { posts } = serveBookmarks([])
    const { rerender } = render(
      <Providers>
        <Bubble message={message} conversationId={SESSION} conversationSource="CLAUDE_CODE" agentId={AGENT} agentDescription="Old name" />
      </Providers>,
    )
    await waitFor(() => expect(star()).toHaveAttribute('aria-label', 'Bookmark message'))
    rerender(
      <Providers>
        <Bubble message={message} conversationId={SESSION} conversationSource="CLAUDE_CODE" agentId={AGENT} agentDescription={DESCRIPTION} />
      </Providers>,
    )
    fireEvent.click(star())
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toMatchObject({ agent_id: AGENT, agent_description: DESCRIPTION })
  })
})

// ---------------------------------------------------------------------------
// The b hotkey
// ---------------------------------------------------------------------------

describe('useBookmarkHotkey — agent page (#97)', () => {
  function press(): void {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'b', bubbles: true }))
  }

  function conversation(fields: AgentFields): ConversationDetail {
    return {
      uuid: SESSION,
      source: 'CLAUDE_CODE',
      messages: [{ uuid: MSG, sender: 'assistant', text: 'I looked at the files' }],
      ...fields,
    } as unknown as ConversationDetail
  }

  function mount(conv: ConversationDetail) {
    const toggleBookmark = vi.fn().mockResolvedValue(undefined)
    renderHook(() => useBookmarkHotkey({ conversation: conv, getSelectedMessageId: () => MSG, toggleBookmark }))
    return toggleBookmark
  }

  it('b on an agent page bookmarks with the agent id and description', () => {
    const toggle = mount(conversation({ agent_id: AGENT, agent_description: DESCRIPTION }))
    press()
    expect(toggle).toHaveBeenCalledTimes(1)
    expect(toggle.mock.calls[0][0]).toMatchObject({
      conversation_id: SESSION, message_uuid: MSG, agent_id: AGENT, agent_description: DESCRIPTION,
    })
  })

  it('b on a session page bookmarks without an agent id', () => {
    const toggle = mount(conversation({}))
    press()
    expect(toggle).toHaveBeenCalledTimes(1)
    expect(toggle.mock.calls[0][0].agent_id ?? null).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// The Bookmarks tab
// ---------------------------------------------------------------------------

describe('BookmarksPanel — agent bookmarks (#97)', () => {
  beforeEach(() => {
    seen.current = { pathname: '', search: '' }
  })

  async function renderPanel(list: Bookmark[]) {
    serveBookmarks(list)
    render(<Providers><BookmarksPanel /></Providers>)
    await waitFor(() => expect(document.querySelectorAll('[data-bookmark-item]')).toHaveLength(list.length))
    return Array.from(document.querySelectorAll<HTMLElement>('[data-bookmark-item]'))
  }

  it('a click on an agent bookmark opens the agent page at the message', async () => {
    const [item] = await renderPanel([bookmark('bm-agent', { agent_id: AGENT, agent_description: DESCRIPTION })])
    fireEvent.click(within(item).getByRole('button', { name: 'Open' }))
    await waitFor(() => expect(seen.current.pathname).toBe(`/conversations/${SESSION}/agents/${AGENT}`))
    expect(seen.current.search).toBe(`?m=${MSG}`)
  })

  it('a click on a session bookmark still opens the session page', async () => {
    const [item] = await renderPanel([bookmark('bm-session')])
    fireEvent.click(within(item).getByRole('button', { name: 'Open' }))
    await waitFor(() => expect(seen.current.pathname).toBe(`/conversations/${SESSION}`))
    expect(seen.current.search).toBe(`?m=${MSG}`)
  })

  it('the item shows the agent description, else "Agent <id>"', async () => {
    const [withDesc, withoutDesc] = await renderPanel([
      bookmark('bm-a', { agent_id: AGENT, agent_description: DESCRIPTION }),
      { ...bookmark('bm-b', { agent_id: '5a1e9ac', agent_description: null }), message_uuid: 'sl-t1' },
    ])
    expect(within(withDesc).getByTestId('bookmark-agent')).toHaveTextContent(DESCRIPTION)
    expect(within(withoutDesc).getByTestId('bookmark-agent')).toHaveTextContent('Agent 5a1e9ac')
  })

  it('a session bookmark shows no agent', async () => {
    const [session, agent] = await renderPanel([
      bookmark('bm-session'),
      bookmark('bm-agent', { agent_id: AGENT, agent_description: DESCRIPTION }),
    ])
    expect(within(session).queryByTestId('bookmark-agent')).toBeNull()
    expect(within(agent).getByTestId('bookmark-agent')).toBeInTheDocument()
  })

  it('the Markdown export names the agent of an agent bookmark', async () => {
    await renderPanel([
      bookmark('bm-a', { agent_id: AGENT, agent_description: DESCRIPTION }),
      { ...bookmark('bm-b', { agent_id: '5a1e9ac', agent_description: null }), message_uuid: 'sl-t1' },
    ])
    fireEvent.click(screen.getByRole('button', { name: 'Export to Markdown' }))
    expect(downloads).toHaveLength(1)
    const md = await downloads[0].text()
    expect(md).toContain(DESCRIPTION)
    expect(md).toContain('Agent 5a1e9ac')
  })
})
