/**
 * ConversationHeader — unit contract.
 *
 * Pins the four user-observable surfaces:
 *
 *   1. Title rendering: name shown verbatim; "Untitled" when name is empty.
 *   2. Source badge variant: each of CLAUDE_CODE / CLAUDE_COWORK /
 *      CLAUDE_AI renders the correct SourceBadge (via lucide icon class).
 *   3. Branches button: hidden when has_branches=false; visible and
 *      wires onOpenTree when has_branches=true.
 *   4. Copy buttons (CopyValueButton, 2026-10-28): each one shows only
 *      when its value exists, and a click writes the exact value to the
 *      clipboard and swaps the Copy icon for a Check:
 *      - session name: icon-only, only when the session has a name;
 *      - UUID: always; file_path: when non-null;
 *      - project path + git branch: Claude Code;
 *      - sandbox path: Cowork.
 *      CopyValueButton's own timing and race behavior has its own tests.
 *
 * The header consumes SearchPinContext via the embedded PinScopeButton,
 * so the test wraps in a SearchPinProvider + MemoryRouter (the latter
 * because SearchPinProvider's useLocation needs a router).
 * ListFilterButton is stubbed, because it needs a SettingsProvider.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { ConversationHeader } from '../../components/conversation/ConversationHeader'
import { SearchPinProvider } from '../../contexts/SearchPinContext'
import type { ConversationDetail, ConversationSource } from '../../lib/types'

// ListFilterButton (Claude Code sessions with a project path) reads
// SettingsContext; it has its own tests, so stub it here.
vi.mock('@/components/conversation/ListFilterButton', () => ({
  ListFilterButton: () => null,
}))

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
  const utils = render(
    <MemoryRouter>
      <SearchPinProvider>
        <ConversationHeader conversation={conversation} {...props} />
      </SearchPinProvider>
    </MemoryRouter>,
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

/** Click a copy button, then wait for its Check icon. The clipboard
 *  mock goes in AFTER userEvent.setup(), which installs its own stub. */
async function clickAndExpectCopied(title: string, expected: string) {
  const user = userEvent.setup()
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  })
  const btn = screen.getByTitle(title)
  expect(btn.querySelector('.lucide-copy')).not.toBeNull()
  expect(btn.querySelector('.lucide-check')).toBeNull()
  await user.click(btn)
  expect(writeText).toHaveBeenCalledWith(expected)
  await waitFor(() => expect(btn.querySelector('.lucide-check')).not.toBeNull())
  expect(btn.querySelector('.lucide-copy')).toBeNull()
}

describe('ConversationHeader — session name copy button', () => {
  it('is an icon-only button after the title that copies the name', async () => {
    renderHeader(makeConversation({ name: 'My great chat' }))
    const btn = screen.getByRole('button', { name: 'Copy session name' })
    expect(btn).toHaveAttribute('title', 'Click to copy session name')
    expect(btn.textContent).toBe('')
    await clickAndExpectCopied('Click to copy session name', 'My great chat')
  })

  it('is absent for an unnamed session (D1)', () => {
    renderHeader(makeConversation({ name: '' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Untitled' })).toBeInTheDocument()
    expect(screen.queryByTitle('Click to copy session name')).toBeNull()
  })
})

describe('ConversationHeader — UUID copy button', () => {
  it('renders the UUID text and copies it on click', async () => {
    renderHeader(makeConversation({ uuid: 'abc-123' }))
    expect(screen.getByText('abc-123')).toBeInTheDocument()
    await clickAndExpectCopied('Click to copy UUID', 'abc-123')
  })
})

describe('ConversationHeader — file_path copy button', () => {
  it('is hidden when conversation.file_path is null', () => {
    renderHeader(makeConversation({ file_path: null }))
    expect(screen.queryByTitle('Click to copy file path')).toBeNull()
  })

  it('renders the path text and copies the full path on click', async () => {
    renderHeader(makeConversation({ file_path: '/Users/test/conv.json' }))
    expect(screen.getByText('/Users/test/conv.json')).toHaveClass('truncate', 'max-w-lg')
    await clickAndExpectCopied('Click to copy file path', '/Users/test/conv.json')
  })
})

describe('ConversationHeader — Claude Code project path and git branch', () => {
  it('copies the project path and the branch', async () => {
    renderHeader(
      makeConversation({
        source: 'CLAUDE_CODE',
        project_path: '/Users/test/proj',
        git_branch: 'feat/x',
      }),
    )
    await clickAndExpectCopied('Click to copy project path', '/Users/test/proj')
    await clickAndExpectCopied('Click to copy git branch', 'feat/x')
  })

  it('has no branch button when there is no branch', () => {
    renderHeader(makeConversation({ source: 'CLAUDE_CODE', project_path: '/p', git_branch: null }))
    expect(screen.getByTitle('Click to copy project path')).toBeInTheDocument()
    expect(screen.queryByTitle('Click to copy git branch')).toBeNull()
  })

  it('has neither button for a non-Claude Code session', () => {
    renderHeader(makeConversation({ source: 'CLAUDE_AI', project_path: '/p', git_branch: 'main' }))
    expect(screen.queryByTitle('Click to copy project path')).toBeNull()
    expect(screen.queryByTitle('Click to copy git branch')).toBeNull()
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

  it('copies the sandbox path on click', async () => {
    renderHeader(makeConversation({ source: 'CLAUDE_COWORK', sandbox_path: '/sessions/vm-1' }))
    await clickAndExpectCopied('Click to copy sandbox path', '/sessions/vm-1')
  })

  it('does not render the sandbox row when source is not CLAUDE_COWORK', () => {
    renderHeader(makeConversation({ source: 'CLAUDE_AI', sandbox_path: '/sessions/vm-1' }))
    expect(screen.queryByTestId('cowork-sandbox-path')).toBeNull()
  })
})
