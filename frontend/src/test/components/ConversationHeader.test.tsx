/**
 * ConversationHeader — unit contract.
 *
 * Pins the user-observable surfaces:
 *
 *   1. Title rendering: name shown verbatim; "Untitled" when name is empty.
 *   2. Source badge variant: each of CLAUDE_CODE / CLAUDE_COWORK /
 *      CLAUDE_AI renders the correct SourceBadge (via lucide icon class).
 *   3. Branches button: hidden when has_branches=false; visible and
 *      wires onOpenTree when has_branches=true.
 *   4. Copy buttons (PLANS/2026.10.28-header-copy-buttons.md, unit cases 1
 *      and 6): each value gets a copy button exactly when it exists, for
 *      each source; each button copies its own exact value and names its
 *      own label in the error toast; an unnamed session ("Untitled") has
 *      no title copy button; the title stays plain text.
 *
 * The header takes only `conversation` and `onOpenTree`: each copy button
 * owns its own "copied" state.
 *
 * The header consumes SearchPinContext via the embedded PinScopeButton,
 * so the test wraps in a SearchPinProvider + MemoryRouter (the latter
 * because SearchPinProvider's useLocation needs a router). A Claude Code
 * session with a project path also renders ListFilterButton, which needs
 * SettingsProvider (and so a QueryClientProvider).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { ConversationHeader } from '../../components/conversation/ConversationHeader'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { SearchPinProvider } from '../../contexts/SearchPinContext'
import { SettingsProvider } from '../../contexts/SettingsContext'
import type { ConversationDetail, ConversationSource } from '../../lib/types'

vi.mock('@/lib/errorToast', () => ({
  errorToast: vi.fn(),
}))

import { errorToast } from '@/lib/errorToast'

// SettingsProvider (needed by ListFilterButton) reads /api/preferences.
beforeEach(() => {
  server.use(
    http.get('/api/preferences', () => HttpResponse.json({ version: 1, data: {} })),
    http.patch('/api/preferences', () => HttpResponse.json({ version: 1, data: {} })),
  )
})

function makeConversation(overrides: Partial<ConversationDetail> = {}): ConversationDetail {
  return {
    uuid: 'conv-uuid-1',
    name: 'Test conversation',
    source: 'CLAUDE_AI',
    model: 'claude-opus',
    created_at: '2026-05-30T12:00:00Z',
    updated_at: '2026-05-30T12:00:00Z',
    message_count: 42,
    has_branches: false,
    messages: [],
    compact_markers: [],
    prelude_hidden_count: 0,
    file_path: null,
    project_path: null,
    git_branch: null,
    sandbox_path: null,
    error: null,
    ...overrides,
  } as unknown as ConversationDetail
}

function renderHeader(
  conversation: ConversationDetail,
  callbackOverrides: Partial<{ onOpenTree: () => void }> = {},
) {
  const props = {
    onOpenTree: vi.fn(),
    ...callbackOverrides,
  }
  // A Claude Code session with a project path also renders ListFilterButton,
  // which reads SettingsContext; SettingsProvider needs a QueryClient.
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        <MemoryRouter>
          <SearchPinProvider>
            <ConversationHeader conversation={conversation} {...props} />
          </SearchPinProvider>
        </MemoryRouter>
      </SettingsProvider>
    </QueryClientProvider>,
  )
  return { ...utils, props }
}

describe('ConversationHeader — title', () => {
  it('renders the conversation name as the h1', () => {
    renderHeader(makeConversation({ name: 'My great chat' }))
    expect(screen.getByRole('heading', { level: 1, name: 'My great chat' })).toBeInTheDocument()
  })

  it('falls back to "Untitled" when name is empty', () => {
    renderHeader(makeConversation({ name: '' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Untitled' })).toBeInTheDocument()
  })
})

describe('ConversationHeader — source badge variant', () => {
  const sources: Array<{ source: ConversationSource; iconClass: string }> = [
    { source: 'CLAUDE_CODE', iconClass: 'lucide-terminal' },
    { source: 'CLAUDE_COWORK', iconClass: 'lucide-sparkles' },
    { source: 'CLAUDE_AI', iconClass: 'lucide-message-square' },
  ]
  for (const { source, iconClass } of sources) {
    it(`${source} renders the ${iconClass} icon in the SourceBadge`, () => {
      const { container } = renderHeader(makeConversation({ source }))
      expect(container.querySelector(`.${iconClass}`)).not.toBeNull()
    })
  }
})

describe('ConversationHeader — branches button', () => {
  it('is hidden when has_branches=false', () => {
    renderHeader(makeConversation({ has_branches: false }))
    expect(screen.queryByText('View branches')).toBeNull()
  })

  it('renders when has_branches=true and fires onOpenTree on click', async () => {
    const user = userEvent.setup()
    const { props } = renderHeader(makeConversation({ has_branches: true }))
    const btn = screen.getByText('View branches')
    expect(btn).toBeInTheDocument()
    await user.click(btn)
    expect(props.onOpenTree).toHaveBeenCalledTimes(1)
  })
})

// ---- Copy buttons (PLANS/2026.10.28-header-copy-buttons.md) -------------

const TITLES = {
  name: 'Click to copy session name',
  uuid: 'Click to copy UUID',
  file: 'Click to copy file path',
  project: 'Click to copy project path',
  branch: 'Click to copy git branch',
  sandbox: 'Click to copy sandbox path',
} as const
type Key = keyof typeof TITLES

const LABELS: Record<Key, string> = {
  name: 'session name',
  uuid: 'UUID',
  file: 'file path',
  project: 'project path',
  branch: 'git branch',
  sandbox: 'sandbox path',
}

const CC_NAME = 'Fix the "flaky" test: step 2 & 3'
const CC_UUID = '0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0'
const CC_PROJECT =
  '/Users/tester/Source/a-very-long-project-folder-name-that-the-header-may-truncate/sub/project'
const CC_FILE =
  '/Users/tester/.claude/projects/-Users-tester-Source-a-very-long-project-folder-name/0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0.jsonl'
const CC_BRANCH = 'feat/header-copy-buttons'

function ccConversation(overrides: Partial<ConversationDetail> = {}) {
  return makeConversation({
    source: 'CLAUDE_CODE',
    name: CC_NAME,
    uuid: CC_UUID,
    project_path: CC_PROJECT,
    project_name: 'project',
    git_branch: CC_BRANCH,
    file_path: CC_FILE,
    ...overrides,
  })
}

function coworkConversation(overrides: Partial<ConversationDetail> = {}) {
  return makeConversation({
    source: 'CLAUDE_COWORK',
    name: 'Cowork: tidy the downloads folder',
    uuid: 'local_6f2c1d0e-1111-2222-3333-444455556666',
    sandbox_path: '/sessions/vm-7/work',
    file_path: null,
    ...overrides,
  })
}

function desktopConversation(overrides: Partial<ConversationDetail> = {}) {
  return makeConversation({
    source: 'CLAUDE_AI',
    name: 'Desktop chat about tea',
    uuid: 'aa11bb22-cc33-dd44-ee55-ff6677889900',
    file_path: 'C:\\Users\\tester\\.claude-explorer\\conversations\\aa11bb22.json',
    ...overrides,
  })
}

/** The set of copy buttons the header shows, by tooltip. */
function shownCopyButtons(): Key[] {
  return (Object.keys(TITLES) as Key[]).filter((k) => screen.queryByTitle(TITLES[k]) !== null)
}

function stubClipboard(writeText: (text: string) => Promise<void>) {
  const spy = vi.fn(writeText)
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: spy },
  })
  return spy
}

async function click(el: HTMLElement) {
  await act(async () => {
    fireEvent.click(el)
  })
}

describe('ConversationHeader — which copy buttons show (unit case 1)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('Claude Code: session name, UUID, file path, project path, and git branch', () => {
    renderHeader(ccConversation())
    expect(shownCopyButtons().sort()).toEqual(['branch', 'file', 'name', 'project', 'uuid'])
  })

  it('Claude Code without a branch: no git branch button', () => {
    renderHeader(ccConversation({ git_branch: null }))
    expect(shownCopyButtons().sort()).toEqual(['file', 'name', 'project', 'uuid'])
  })

  it('Claude Code without a file path: no file path button', () => {
    renderHeader(ccConversation({ file_path: null }))
    expect(shownCopyButtons().sort()).toEqual(['branch', 'name', 'project', 'uuid'])
  })

  it('Claude Code without a project path: no project path and no git branch button', () => {
    renderHeader(ccConversation({ project_path: null, git_branch: null }))
    expect(shownCopyButtons().sort()).toEqual(['file', 'name', 'uuid'])
  })

  it('Cowork: session name, UUID, and sandbox path', () => {
    renderHeader(coworkConversation())
    expect(shownCopyButtons().sort()).toEqual(['name', 'sandbox', 'uuid'])
  })

  it('Cowork without a sandbox path: no sandbox path button', () => {
    renderHeader(coworkConversation({ sandbox_path: null }))
    expect(shownCopyButtons().sort()).toEqual(['name', 'uuid'])
  })

  it('Desktop with a file path: session name, UUID, and file path', () => {
    renderHeader(desktopConversation())
    expect(shownCopyButtons().sort()).toEqual(['file', 'name', 'uuid'])
  })

  it('Desktop without a file path: session name and UUID only', () => {
    renderHeader(desktopConversation({ file_path: null }))
    expect(shownCopyButtons().sort()).toEqual(['name', 'uuid'])
  })

  it('the session-name button is icon-only, with the accessible name "Copy session name"', () => {
    renderHeader(ccConversation())
    const btn = screen.getByRole('button', { name: 'Copy session name' })
    expect(btn).toHaveAttribute('title', TITLES.name)
    expect(btn.textContent ?? '').not.toContain(CC_NAME)
    expect(btn.querySelector('.lucide-copy')).not.toBeNull()
  })

  it('the session-name button sits after the title and before the pin button', () => {
    renderHeader(ccConversation())
    const h1 = screen.getByRole('heading', { level: 1, name: CC_NAME })
    const btn = screen.getByRole('button', { name: 'Copy session name' })
    const pin = screen.getByTestId('pin-scope-button')
    expect(h1.compareDocumentPosition(btn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(btn.compareDocumentPosition(pin) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('the title stays plain text: it is not a button and holds no button', () => {
    renderHeader(ccConversation())
    const h1 = screen.getByRole('heading', { level: 1, name: CC_NAME })
    expect(h1.closest('button')).toBeNull()
    expect(h1.querySelector('button')).toBeNull()
  })

  it('the value buttons show their values, and the labels stay in front', () => {
    renderHeader(ccConversation())
    expect(screen.getByTitle(TITLES.project)).toHaveTextContent(CC_PROJECT)
    expect(screen.getByTitle(TITLES.branch)).toHaveTextContent(CC_BRANCH)
    expect(screen.getByTitle(TITLES.uuid)).toHaveTextContent(CC_UUID)
    expect(screen.getByTitle(TITLES.file)).toHaveTextContent(CC_FILE)
  })

  it('Cowork keeps the "Sandbox path:" label before the sandbox path button', () => {
    renderHeader(coworkConversation())
    const label = screen.getByText('Sandbox path:')
    const btn = screen.getByTitle(TITLES.sandbox)
    expect(btn).toHaveTextContent('/sessions/vm-7/work')
    expect(label.compareDocumentPosition(btn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('ConversationHeader — each button copies its own exact value', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  const ccValues: Array<[Key, string]> = [
    ['name', CC_NAME],
    ['uuid', CC_UUID],
    ['file', CC_FILE],
    ['project', CC_PROJECT],
    ['branch', CC_BRANCH],
  ]
  for (const [key, value] of ccValues) {
    it(`Claude Code: the ${LABELS[key]} button writes ${JSON.stringify(value)} and shows the check mark`, async () => {
      const writeText = stubClipboard(() => Promise.resolve())
      renderHeader(ccConversation())
      const btn = screen.getByTitle(TITLES[key])
      expect(btn.querySelector('.text-green-500')).toBeNull()
      await click(btn)
      expect(writeText).toHaveBeenCalledTimes(1)
      expect(writeText).toHaveBeenCalledWith(value)
      expect(btn.querySelector('.text-green-500')).not.toBeNull()
      // Only the clicked button shows the check mark.
      const others = (Object.keys(TITLES) as Key[])
        .filter((k) => k !== key)
        .map((k) => screen.queryByTitle(TITLES[k]))
        .filter((el): el is HTMLElement => el !== null)
      for (const other of others) {
        expect(other.querySelector('.text-green-500')).toBeNull()
      }
    })
  }

  it('Cowork: the sandbox path button writes the sandbox path', async () => {
    const writeText = stubClipboard(() => Promise.resolve())
    renderHeader(coworkConversation())
    await click(screen.getByTitle(TITLES.sandbox))
    expect(writeText).toHaveBeenCalledWith('/sessions/vm-7/work')
  })

  it('Desktop: the file path button writes a Windows path unchanged', async () => {
    const writeText = stubClipboard(() => Promise.resolve())
    renderHeader(desktopConversation())
    await click(screen.getByTitle(TITLES.file))
    expect(writeText).toHaveBeenCalledWith(
      'C:\\Users\\tester\\.claude-explorer\\conversations\\aa11bb22.json',
    )
  })
})

describe('ConversationHeader — a failed write names the right value', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  const cases: Array<[Key, () => ConversationDetail]> = [
    ['name', () => ccConversation()],
    ['uuid', () => ccConversation()],
    ['file', () => ccConversation()],
    ['project', () => ccConversation()],
    ['branch', () => ccConversation()],
    ['sandbox', () => coworkConversation()],
  ]
  for (const [key, make] of cases) {
    it(`the ${LABELS[key]} button shows "Failed to copy ${LABELS[key]} to clipboard." and no check mark`, async () => {
      stubClipboard(() => Promise.reject(new Error('Document is not focused')))
      renderHeader(make())
      const btn = screen.getByTitle(TITLES[key])
      await click(btn)
      expect(errorToast).toHaveBeenCalledTimes(1)
      expect(errorToast).toHaveBeenCalledWith(`Failed to copy ${LABELS[key]} to clipboard.`)
      expect(btn.querySelector('.text-green-500')).toBeNull()
    })
  }
})

describe('ConversationHeader — an unnamed session (unit case 6)', () => {
  it('shows "Untitled" and has no title copy button (empty name)', () => {
    renderHeader(ccConversation({ name: '' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Untitled' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Copy session name' })).toBeNull()
    expect(screen.queryByTitle(TITLES.name)).toBeNull()
    // The other buttons still show: only the title button goes.
    expect(screen.getByTitle(TITLES.uuid)).toBeInTheDocument()
  })

  it('shows "Untitled" and has no title copy button (null name)', () => {
    renderHeader(desktopConversation({ name: null as unknown as string }))
    expect(screen.getByRole('heading', { level: 1, name: 'Untitled' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Copy session name' })).toBeNull()
    expect(screen.queryByTitle(TITLES.name)).toBeNull()
  })
})

describe('ConversationHeader — Cowork sandbox path', () => {
  it('renders the sandbox path with the "Sandbox path:" label for CLAUDE_COWORK', () => {
    renderHeader(
      makeConversation({
        source: 'CLAUDE_COWORK',
        sandbox_path: '/sessions/vm-1',
      }),
    )
    expect(screen.getByTestId('cowork-sandbox-path')).toBeInTheDocument()
    expect(screen.getByText('/sessions/vm-1')).toBeInTheDocument()
    expect(screen.getByText('Sandbox path:')).toBeInTheDocument()
  })

  it('does not render the sandbox row when source is not CLAUDE_COWORK', () => {
    renderHeader(makeConversation({ source: 'CLAUDE_AI', sandbox_path: '/sessions/vm-1' }))
    expect(screen.queryByTestId('cowork-sandbox-path')).toBeNull()
  })
})
