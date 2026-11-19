/**
 * N8-ui: the conversation header of a Codex session (plan §4.5, "The web UI
 * header"; D8, D14).
 *
 *   - "Thread ID", "Codex name" (or "none"), and "Codex home", each with a
 *     copy button. The user pastes the thread ID into `codex resume <id>`.
 *   - The Codex home shows as `~/...` (`source_home`); its copy button copies
 *     the absolute path (`source_home_abs`).
 *   - The `notices` show in the header, above the messages, for any source.
 *   - Codex has projects (descriptor flag), so the project path shows.
 *   - Other sources keep today's header: no Codex rows, the "UUID" button.
 */
import { createElement, type ComponentType } from 'react'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { serveApp } from '../sources/fakeRegistry'
import {
  HOME_ABS,
  HOME_SHOWN,
  THREAD_ID,
  clearLocalStorage,
  codexDetail,
  detail,
  importModule,
  setCodexRegistry,
} from './codexHelpers'

const TITLES = {
  thread: 'Click to copy thread ID',
  name: 'Click to copy Codex name',
  home: 'Click to copy Codex home',
  uuid: 'Click to copy UUID',
  project: 'Click to copy project path',
}

async function renderHeader(conversation: Record<string, unknown>) {
  const { AppProviders } = await importModule<{ AppProviders: ComponentType<{ children: React.ReactNode }> }>(
    '@/test/sources/harness',
  )
  const { ConversationHeader } = await importModule<{ ConversationHeader: ComponentType<Record<string, unknown>> }>(
    '@/components/conversation/ConversationHeader',
  )
  serveApp({})
  render(
    createElement(AppProviders, null, createElement(ConversationHeader, { conversation, onOpenTree: vi.fn() })),
  )
  await screen.findByRole('heading', { level: 1 })
}

function stubClipboard() {
  const spy = vi.fn<(text: string) => Promise<void>>(() => Promise.resolve())
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: spy } })
  return spy
}

async function click(el: HTMLElement) {
  await act(async () => {
    fireEvent.click(el)
  })
}

beforeEach(() => {
  clearLocalStorage()
  setCodexRegistry()
})

describe('Codex header rows', () => {
  it('shows the labels "Thread ID", "Codex name", and "Codex home"', async () => {
    await renderHeader(codexDetail())
    expect(screen.getByText(/Thread ID/)).toBeInTheDocument()
    expect(screen.getByText(/Codex name/)).toBeInTheDocument()
    expect(screen.getByText(/Codex home/)).toBeInTheDocument()
  })

  it('the thread ID button shows the thread id and copies it exactly', async () => {
    const writeText = stubClipboard()
    await renderHeader(codexDetail())
    const btn = screen.getByTitle(TITLES.thread)
    expect(btn).toHaveTextContent(THREAD_ID)
    await click(btn)
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText).toHaveBeenCalledWith(THREAD_ID)
  })

  it('a Codex session has the thread ID button instead of the UUID button', async () => {
    await renderHeader(codexDetail())
    expect(screen.getByTitle(TITLES.thread)).toBeInTheDocument()
    expect(screen.queryByTitle(TITLES.uuid)).toBeNull()
  })

  it('the Codex name button shows source_name and copies it exactly', async () => {
    const writeText = stubClipboard()
    await renderHeader(codexDetail({ name: 'Title from state_5', source_name: 'My Codex name' }))
    const btn = screen.getByTitle(TITLES.name)
    expect(btn).toHaveTextContent('My Codex name')
    await click(btn)
    expect(writeText).toHaveBeenCalledWith('My Codex name')
  })

  it('with no Codex name, the row says "none" and has no copy button', async () => {
    await renderHeader(codexDetail({ name: 'Codex session 2026-11-01', source_name: null }))
    const label = screen.getByText(/Codex name/)
    const row = label.closest('div') as HTMLElement
    expect(within(row).getByText('none')).toBeInTheDocument()
    expect(screen.queryByTitle(TITLES.name)).toBeNull()
  })

  it('the Codex home shows the ~/ form and copies the absolute path', async () => {
    const writeText = stubClipboard()
    await renderHeader(codexDetail())
    const btn = screen.getByTitle(TITLES.home)
    expect(btn).toHaveTextContent(HOME_SHOWN)
    expect(btn).not.toHaveTextContent(HOME_ABS)
    await click(btn)
    expect(writeText).toHaveBeenCalledWith(HOME_ABS)
  })

  it('with no absolute home path, the Codex home button copies source_home', async () => {
    const writeText = stubClipboard()
    await renderHeader(codexDetail({ source_home_abs: null }))
    await click(screen.getByTitle(TITLES.home))
    expect(writeText).toHaveBeenCalledWith(HOME_SHOWN)
  })

  // Passes today through the N2 has_projects flag; it pins that Codex keeps it.
  it('a Codex session shows its project path (has_projects)', async () => {
    await renderHeader(codexDetail())
    expect(screen.getByTitle(TITLES.project)).toHaveTextContent('/home/fixture/work/parser')
  })
})

describe('Notices in the header', () => {
  it('a Codex session shows each notice in the notice list', async () => {
    const notices = ['incomplete: compacted', '1 older rollout is kept in the archive']
    await renderHeader(codexDetail({ notices }))
    const list = screen.getByTestId('conversation-notices')
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent?.trim())).toEqual(notices)
  })

  it('a session of another source with notices shows them too', async () => {
    await renderHeader(detail({ source: 'CLAUDE_CODE', notices: ['Cloud sync: writes are off'] }))
    expect(within(screen.getByTestId('conversation-notices')).getByText('Cloud sync: writes are off')).toBeInTheDocument()
  })

  it('no notices: no notice list', async () => {
    await renderHeader(codexDetail({ notices: [] }))
    expect(screen.queryByTestId('conversation-notices')).toBeNull()
  })
})

describe('Other sources keep their header', () => {
  for (const source of ['CLAUDE_AI', 'CLAUDE_CODE', 'CLAUDE_COWORK']) {
    it(`${source}: no Codex rows, and the UUID button stays`, async () => {
      await renderHeader(detail({ source, source_name: null, source_home: null, source_home_abs: null, notices: [] }))
      expect(screen.getByTitle(TITLES.uuid)).toBeInTheDocument()
      expect(screen.queryByTitle(TITLES.thread)).toBeNull()
      expect(screen.queryByText(/Thread ID|Codex name|Codex home/)).toBeNull()
    })
  }
})
