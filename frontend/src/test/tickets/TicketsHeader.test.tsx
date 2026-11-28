/**
 * T1 (issue #93): the entrypoint chip and the "Tickets:" row in the
 * conversation header. The contract (orchestrator, 2026-11-27):
 *
 *   - data-testid "entrypoint-chip": the entrypointLabel of the value, with the
 *     raw value as its title attribute; nothing when entrypoint is null or "".
 *   - data-testid "tickets-row" in the header Details, label "Tickets:", one
 *     link per key (data-testid "ticket-link") to /tickets/<owner>/<repo>/<n>,
 *     or /tickets/_/_/<n> with the text "#<n> (unknown repo)" when the repo is
 *     unknown; no row when tickets is empty. A Linear or Jira key ("ENG-123")
 *     links to /tickets/key/<KEY> with the key as its text.

 */
import { createElement, type ComponentType } from 'react'
import { render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '@/test/mocks/server'
import { serveApp } from '@/test/sources/fakeRegistry'
import { clearLocalStorage, codexDetail, detail, importModule, setCodexRegistry } from '@/test/codex/codexHelpers'

const A = 'a9300000-0000-4000-8000-000000000001'
const B = 'a9300000-0000-4000-8000-000000000002'

function ccDetail(uuid: string, fields: Record<string, unknown>) {
  // T1 UI round 3: the "Tickets:" row reads named_keys; by default the tests name every ticket.
  return detail({
    uuid, name: `Session ${uuid.slice(-2)}`, source: 'CLAUDE_CODE', project_path: '/home/fixture/alpha',
    named_keys: fields.tickets, pr_keys: [], ...fields,
  })
}

function serveNothing(): void {
  server.use(
    http.get('/api/conversations', () => HttpResponse.json([])),
    http.get('/api/conversations/:uuid', () => HttpResponse.json({ detail: 'not found' }, { status: 404 })),
  )
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
  serveNothing()
})

describe('entrypoint chip', () => {
  it('shows the label of a known launcher, with the raw value as its title', async () => {
    await renderHeader(ccDetail(A, { entrypoint: 'claude-desktop', tickets: [] }))
    const chip = await screen.findByTestId('entrypoint-chip')
    expect(chip).toHaveTextContent('Desktop')
    expect(chip).toHaveAttribute('title', 'claude-desktop')
  })

  it('shows an unknown launcher as it is', async () => {
    await renderHeader(ccDetail(A, { entrypoint: 'some-new-launcher', tickets: [] }))
    expect(await screen.findByTestId('entrypoint-chip')).toHaveTextContent('some-new-launcher')
  })

  it('shows a Codex originator', async () => {
    await renderHeader(codexDetail({ child_uuids: [], entrypoint: 'codex_cli_rs', tickets: [] }))
    expect(await screen.findByTestId('entrypoint-chip')).toHaveTextContent('Codex CLI')
  })

  it('shows no chip without an entrypoint', async () => {
    // The header beside it proves that the chip renders in this harness.
    await renderHeader(ccDetail(A, { entrypoint: 'cli', tickets: [] }), ccDetail(B, { entrypoint: null, tickets: [] }))
    expect(await within(screen.getByTestId(`header-${A}`)).findByTestId('entrypoint-chip')).toBeInTheDocument()
    expect(within(screen.getByTestId(`header-${B}`)).queryByTestId('entrypoint-chip')).toBeNull()
  })
})

describe('Tickets:', () => {
  it('links each ticket to its page, in order', async () => {
    await renderHeader(ccDetail(A, { entrypoint: 'cli', tickets: ['fixture-org/alpha#84', 'fixture-org/beta#7'] }))
    const row = await screen.findByTestId('tickets-row')
    expect(row).toHaveTextContent('Tickets:')
    expect(links(row)).toEqual([
      ['fixture-org/alpha#84', '/tickets/fixture-org/alpha/84'],
      ['fixture-org/beta#7', '/tickets/fixture-org/beta/7'],
    ])
    expect(within(row).getAllByTestId('ticket-link')).toHaveLength(2)
  })

  it('a ticket with an unknown repo links to the _/_ page', async () => {
    await renderHeader(ccDetail(A, { entrypoint: 'cli', tickets: ['#84'] }))
    const row = await screen.findByTestId('tickets-row')
    expect(links(row)).toEqual([['#84 (unknown repo)', '/tickets/_/_/84']])
  })

  it('a Linear or Jira key links to its key page', async () => {
    await renderHeader(ccDetail(A, { entrypoint: 'cli', tickets: ['ENG-123', 'PROJ-7', 'fixture-org/alpha#84'] }))
    expect(links(await screen.findByTestId('tickets-row'))).toEqual([
      ['ENG-123', '/tickets/key/ENG-123'],
      ['PROJ-7', '/tickets/key/PROJ-7'],
      ['fixture-org/alpha#84', '/tickets/fixture-org/alpha/84'],
    ])
  })

  it('shows no row when tickets is empty', async () => {
    await renderHeader(
      ccDetail(A, { entrypoint: 'cli', tickets: ['fixture-org/alpha#84'] }),
      ccDetail(B, { entrypoint: 'cli', tickets: [] }),
    )
    expect(await within(screen.getByTestId(`header-${A}`)).findByTestId('tickets-row')).toBeInTheDocument()
    expect(within(screen.getByTestId(`header-${B}`)).queryByTestId('tickets-row')).toBeNull()
  })

  it('a Desktop conversation shows the row too', async () => {
    await renderHeader(detail({ uuid: A, name: 'Desktop', source: 'CLAUDE_AI', entrypoint: null, tickets: ['fixture-org/alpha#84'], named_keys: ['fixture-org/alpha#84'], pr_keys: [] }))
    expect(links(await screen.findByTestId('tickets-row'))).toEqual([
      ['fixture-org/alpha#84', '/tickets/fixture-org/alpha/84'],
    ])
  })
})

// T1 UI round 3 (_orchestration/T1-UI.md, V3): PRs apart from tickets.

describe('PRs: (V3)', () => {
  const both = { entrypoint: 'cli', tickets: ['fixture-org/alpha#84', 'fixture-org/alpha#85'] }

  it('the Tickets row lists only named_keys', async () => {
    await renderHeader(ccDetail(A, { ...both, named_keys: ['fixture-org/alpha#84'], pr_keys: ['fixture-org/alpha#85'] }))
    expect(links(await screen.findByTestId('tickets-row'))).toEqual([
      ['fixture-org/alpha#84', '/tickets/fixture-org/alpha/84'],
    ])
  })

  it('the PRs row links each pr_key to its GitHub pull request in a new tab', async () => {
    await renderHeader(ccDetail(A, { ...both, named_keys: ['fixture-org/alpha#84'], pr_keys: ['fixture-org/alpha#85'] }))
    const row = await screen.findByTestId('prs-row')
    expect(row).toHaveTextContent('PRs:')
    expect(links(row)).toEqual([['fixture-org/alpha#85', 'https://github.com/fixture-org/alpha/pull/85']])
    const link = within(row).getByTestId('pr-link')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel') ?? '').toMatch(/noopener/)
    expect(link.getAttribute('rel') ?? '').toMatch(/noreferrer/)
  })

  it('a key that is both a PR and named shows in both rows', async () => {
    await renderHeader(ccDetail(A, {
      entrypoint: 'cli', tickets: ['fixture-org/alpha#85'], named_keys: ['fixture-org/alpha#85'], pr_keys: ['fixture-org/alpha#85'],
    }))
    expect(links(await screen.findByTestId('tickets-row'))).toEqual([['fixture-org/alpha#85', '/tickets/fixture-org/alpha/85']])
    expect(links(await screen.findByTestId('prs-row'))).toEqual([['fixture-org/alpha#85', 'https://github.com/fixture-org/alpha/pull/85']])
  })

  it('a session with only a PR has a PRs row and no Tickets row', async () => {
    await renderHeader(ccDetail(A, { entrypoint: 'cli', tickets: ['fixture-org/alpha#85'], named_keys: [], pr_keys: ['fixture-org/alpha#85'] }))
    expect(await screen.findByTestId('prs-row')).toBeInTheDocument()
    expect(screen.queryByTestId('tickets-row')).toBeNull()
  })

  it('a session without PRs has no PRs row', async () => {
    await renderHeader(ccDetail(A, { entrypoint: 'cli', tickets: ['fixture-org/alpha#84'] }))
    expect(await screen.findByTestId('tickets-row')).toBeInTheDocument()
    expect(screen.queryByTestId('prs-row')).toBeNull()
  })
})
