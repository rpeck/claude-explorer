/**
 * L1 (issue #91, part 1): the "Messages with:" row in the header Details.
 * The contract (orchestrator, 2026-11-27):
 *
 *   - data-testid "peer-sessions-row", label "Messages with:", for any source
 *     when `peers` is not empty; no row when it is empty.
 *   - A peer with link_state "linked" is a link to /conversations/<uuid>,
 *     named title || name || uuid, in `peers` order.
 *   - Any other peer ("not_in_archive", "unknown") is plain text with its
 *     name, with no link.
 *   - The header fetches no detail of a peer (as CodexHistoryLinks).
 */
import { createElement, type ComponentType } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '@/test/mocks/server'
import { serveApp } from '@/test/sources/fakeRegistry'
import { clearLocalStorage, codexDetail, detail, importModule, setCodexRegistry } from '@/test/codex/codexHelpers'


const A = 'a1000000-0000-4000-8000-0000000000a1'
const B = 'b1000000-0000-4000-8000-0000000000b1'
const C = 'c1000000-0000-4000-8000-0000000000c1'

function peer(conversation_uuid: string | null, name: string | null, title: string | null, link_state = 'linked') {
  return { conversation_uuid, name, title, link_state }
}

function ccDetail(uuid: string, peers: unknown[]) {
  return detail({ uuid, name: `Session ${uuid.slice(0, 2)}`, source: 'CLAUDE_CODE', project_path: '/home/fixture/peer', peers })
}

function serveNothing(): string[] {
  const details: string[] = []
  server.use(
    http.get('/api/conversations', () => HttpResponse.json([])),
    http.get('/api/conversations/:uuid', ({ params }) => {
      details.push(String(params.uuid))
      return HttpResponse.json({ detail: 'not found' }, { status: 404 })
    }),
  )
  return details
}

async function renderHeader(...conversations: Record<string, unknown>[]) {
  const { AppProviders } = await importModule<{ AppProviders: ComponentType<{ children: React.ReactNode }> }>(
    '@/test/sources/harness',
  )
  const { ConversationHeader } = await importModule<{ ConversationHeader: ComponentType<Record<string, unknown>> }>(
    '@/components/conversation/ConversationHeader',
  )
  serveApp({})
  render(
    createElement(
      AppProviders,
      null,
      ...conversations.map((conversation) =>
        createElement(
          'section',
          { key: String(conversation.uuid), 'data-testid': `header-${String(conversation.uuid)}` },
          createElement(ConversationHeader, { conversation, onOpenTree: vi.fn() }),
        ),
      ),
    ),
  )
  await screen.findAllByRole('heading', { level: 1 })
}

function links(el: HTMLElement): [string, string][] {
  return within(el)
    .queryAllByRole('link')
    .map((a) => [a.textContent?.trim() ?? '', a.getAttribute('href') ?? ''])
}

beforeEach(() => {
  clearLocalStorage()
  setCodexRegistry()
})

describe('Messages with:', () => {
  it('shows the row with its label when peers is not empty', async () => {
    serveNothing()
    await renderHeader(ccDetail(B, [peer(A, 'fixture-peer-a', 'Peer A session')]))
    expect(await screen.findByTestId('peer-sessions-row')).toHaveTextContent('Messages with:')
  })

  it('a linked peer is a link named by its title, in peers order', async () => {
    serveNothing()
    await renderHeader(ccDetail(A, [peer(B, 'fixture-peer-b', 'Peer B session'), peer(C, 'fixture-peer-c', 'Peer C session')]))
    const row = await screen.findByTestId('peer-sessions-row')
    expect(links(row)).toEqual([
      ['Peer B session', `/conversations/${B}`],
      ['Peer C session', `/conversations/${C}`],
    ])
  })

  it('a linked peer without a title is named by its name, then by its uuid', async () => {
    serveNothing()
    await renderHeader(ccDetail(A, [peer(B, 'fixture-peer-b', ''), peer(C, null, null)]))
    const row = await screen.findByTestId('peer-sessions-row')
    expect(links(row).map(([text]) => text)).toEqual(['fixture-peer-b', C])
  })

  it('a peer that is not in the archive, or unknown, is plain text with no link', async () => {
    serveNothing()
    await renderHeader(ccDetail(A, [
      peer(B, 'fixture-peer-b', 'Peer B session'),
      peer(null, 'fixture-peer-gone', null, 'not_in_archive'),
      peer(null, 'fixture-peer-later', null, 'unknown'),
    ]))
    const row = await screen.findByTestId('peer-sessions-row')
    expect(row).toHaveTextContent('fixture-peer-gone')
    expect(row).toHaveTextContent('fixture-peer-later')
    expect(links(row).map(([text]) => text)).toEqual(['Peer B session'])
  })

  it('shows no row when peers is empty', async () => {
    serveNothing()
    // The header beside it proves that the row renders in this harness.
    await renderHeader(ccDetail(A, [peer(B, 'fixture-peer-b', 'Peer B session')]), ccDetail(C, []))
    expect(await within(screen.getByTestId(`header-${A}`)).findByTestId('peer-sessions-row')).toBeInTheDocument()
    expect(within(screen.getByTestId(`header-${C}`)).queryByTestId('peer-sessions-row')).toBeNull()
  })

  it('a Codex thread shows the row too', async () => {
    serveNothing()
    await renderHeader(codexDetail({ child_uuids: [], peers: [peer(A, '/root/src_count', 'Codex worker')] }))
    const row = await screen.findByTestId('peer-sessions-row')
    expect(links(row)).toEqual([['Codex worker', `/conversations/${A}`]])
  })

  it('the header fetches no detail of a peer', async () => {
    const details = serveNothing()
    await renderHeader(ccDetail(A, [peer(B, 'fixture-peer-b', 'Peer B session'), peer(null, 'gone', null, 'not_in_archive')]))
    await screen.findByTestId('peer-sessions-row')
    await waitFor(() => expect(links(screen.getByTestId('peer-sessions-row'))).toHaveLength(1))
    expect(details.filter((u) => u === B)).toEqual([])
  })
})
