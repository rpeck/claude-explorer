/**
 * N8-ui sibling bug: the page called every assistant message "Claude", also
 * for Codex. The descriptor gets `assistant_label` ("Claude" for the three
 * Claude sources, "Codex" for CODEX). The label is
 * `sourceDescriptor(source)?.assistant_label ?? 'Claude'`.
 *
 *   - MessageBubble reads its `conversationSource` prop.
 *   - TreeView and TreeViewModal get a new optional `source` prop, because
 *     the tree payload has no source. The label and the aria-label of each
 *     node use it.
 */
import { createElement, type ComponentType, type ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mockConversationTree, mockMessages } from '../mocks/data'
import { REAL, serveApp, setRegistry } from '../sources/fakeRegistry'
import { CODEX, clearLocalStorage, importModule } from './codexHelpers'

/** The registry as the backend sends it after N8: every source has assistant_label. */
function setLabeledRegistry(): void {
  setRegistry([
    ...REAL.filter((d) => d.id !== CODEX.id).map((d) => ({ ...d, assistant_label: 'Claude' })),
    { ...CODEX, assistant_label: 'Codex' },
  ])
}

const assistantMessage = mockMessages.find((m) => m.sender === 'assistant')!
const UNKNOWN = 'NO_SUCH_SOURCE'
const CLAUDE_SOURCES = ['CLAUDE_AI', 'CLAUDE_CODE', 'CLAUDE_COWORK']

async function renderBubble(conversationSource: string) {
  const { AppProviders } = await importModule<{ AppProviders: ComponentType<{ children: ReactNode }> }>(
    '@/test/sources/harness',
  )
  const { MessageBubble } = await importModule<{ MessageBubble: ComponentType<Record<string, unknown>> }>(
    '@/components/message/MessageBubble',
  )
  serveApp({})
  render(
    createElement(
      AppProviders,
      null,
      createElement(MessageBubble, { message: assistantMessage, conversationId: 'c-1', conversationSource }),
    ),
  )
}

async function renderTree(source: string) {
  const { TreeView } = await importModule<{ TreeView: ComponentType<Record<string, unknown>> }>(
    '@/components/branch/TreeView',
  )
  render(createElement(TreeView, { tree: mockConversationTree, onSelectPath: vi.fn(), source }))
}

/** The node buttons of the assistant messages (aria-label "<label>: <preview>"). */
function assistantNodeLabels(): string[] {
  const assistantPreviews = ['You can use pandas', "Here's how to read a CSV", 'For Excel files']
  return screen
    .getAllByRole('button')
    .map((b) => b.getAttribute('aria-label') ?? '')
    .filter((label) => assistantPreviews.some((p) => label.includes(p)))
}

beforeEach(() => {
  clearLocalStorage()
  setLabeledRegistry()
})

describe('MessageBubble assistant label', () => {
  it('a Codex assistant message says "Codex", not "Claude"', async () => {
    await renderBubble('CODEX')
    expect(await screen.findByText('Codex')).toBeInTheDocument()
    expect(screen.queryByText('Claude')).toBeNull()
  })

  for (const source of CLAUDE_SOURCES) {
    it(`${source}: an assistant message still says "Claude"`, async () => {
      await renderBubble(source)
      expect(await screen.findByText('Claude')).toBeInTheDocument()
    })
  }

  it('a source with no descriptor says "Claude"', async () => {
    await renderBubble(UNKNOWN)
    expect(await screen.findByText('Claude')).toBeInTheDocument()
  })
})

describe('TreeView assistant label', () => {
  it('Codex: each assistant node shows "Codex", and its aria-label starts with "Codex: "', async () => {
    await renderTree('CODEX')
    expect(screen.getAllByText('Codex')).toHaveLength(3)
    expect(screen.queryByText('Claude')).toBeNull()
    const labels = assistantNodeLabels()
    expect(labels).toHaveLength(3)
    for (const label of labels) expect(label.startsWith('Codex: '), label).toBe(true)
  })

  for (const source of CLAUDE_SOURCES) {
    it(`${source}: each assistant node still shows "Claude" in its label and aria-label`, async () => {
      await renderTree(source)
      expect(screen.getAllByText('Claude')).toHaveLength(3)
      const labels = assistantNodeLabels()
      expect(labels).toHaveLength(3)
      for (const label of labels) expect(label.startsWith('Claude: '), label).toBe(true)
    })
  }

  it('a source with no descriptor shows "Claude"', async () => {
    await renderTree(UNKNOWN)
    expect(screen.getAllByText('Claude')).toHaveLength(3)
    for (const label of assistantNodeLabels()) expect(label.startsWith('Claude: '), label).toBe(true)
  })

  it('no source prop (today\'s callers) shows "Claude"', async () => {
    const { TreeView } = await importModule<{ TreeView: ComponentType<Record<string, unknown>> }>(
      '@/components/branch/TreeView',
    )
    render(createElement(TreeView, { tree: mockConversationTree, onSelectPath: vi.fn() }))
    expect(screen.getAllByText('Claude')).toHaveLength(3)
  })
})

describe('TreeViewModal passes the source to the tree', () => {
  it('a Codex modal shows "Codex" on the assistant nodes', async () => {
    const { TreeViewModal } = await importModule<{ TreeViewModal: ComponentType<Record<string, unknown>> }>(
      '@/components/branch/TreeViewModal',
    )
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query')
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(TreeViewModal, { uuid: 'conv-2', isOpen: true, onClose: vi.fn(), onSelectPath: vi.fn(), source: 'CODEX' }),
      ),
    )
    // The human nodes prove that the tree loaded; then only the label is in question.
    expect(await screen.findAllByText('You')).toHaveLength(3)
    expect(screen.getAllByText('Codex')).toHaveLength(3)
  })
})
