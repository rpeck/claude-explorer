/**
 * Issue #90: the Show Code Mode toggle reaches the backend. Search and
 * every export carry `include_code_mode` (the backend default is false).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'

const ENVELOPE = { results: [], total_messages_matched: 0, returned_messages: 0, truncated: false }

describe('api — include_code_mode plumbing', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () => new Response(JSON.stringify(ENVELOPE), { status: 200 }),
    )
  })
  afterEach(() => fetchSpy.mockRestore())

  const url = () => String(fetchSpy.mock.calls[0][0])

  it('GET search sets include_code_mode=true only when on', async () => {
    await api.search('q', 'all', 'snippet', 'updated_at', 'desc', undefined, true, true, true)
    expect(url()).toContain('include_code_mode=true')
    fetchSpy.mockClear()
    await api.search('q', 'all', 'snippet', 'updated_at', 'desc', undefined, true, true, false)
    expect(url()).not.toContain('include_code_mode')
  })

  it('POST search sends include_code_mode in the body', async () => {
    await api.search('q', 'all', 'snippet', 'updated_at', 'desc', { conversationUuids: ['a'] }, true, true, true)
    const init = fetchSpy.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body)).include_code_mode).toBe(true)
  })

  it.each([
    ['exportMarkdown', () => api.exportMarkdown('c', true, false, true)],
    ['exportMarkdownBundle', () => api.exportMarkdownBundle('c', true, 'commonmark', false, true)],
    ['exportPdf', () => api.exportPdf('c', true, undefined, false, true)],
  ])('%s carries include_code_mode', async (_name, call) => {
    await call()
    expect(url()).toContain('include_code_mode=true')
  })

  it('exports default to include_code_mode=false', async () => {
    await api.exportMarkdown('c', true)
    expect(url()).toContain('include_code_mode=false')
  })
})
