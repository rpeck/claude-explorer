/**
 * B52 (issue #52): the message bubble shows what Claude Code wrote as what
 * it is, and names the model of each reply.
 *
 *   - The sender label (`data-testid="message-sender"`) is
 *     `replyLabel(assistant_label, message.model)`: "Claude Opus 5.5",
 *     "Claude Code" for `<synthetic>`, "Codex gpt-5.6-codex".
 *   - A Desktop or Codex reply (`model_from_conversation: true`) has the
 *     tooltip "From the conversation's model setting. <Source> does not
 *     record the model of each reply."
 *   - `command_output` shows under its command in a monospace block
 *     (`data-testid="command-output"`, label "Claude Code"). Over 20 lines,
 *     only the first 20 show, with "Show all". stderr has `data-error="true"`.
 *   - A shell message (`shell_command`) is one block labelled "Shell".
 *   - A notice (`notice_kind`) is a folded row labelled with its kind.
 *   - "Copy message as Markdown" uses the reply label.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act, within } from '../utils'
import { MessageBubble } from '../../components/message/MessageBubble'
import { TreeView } from '../../components/branch/TreeView'
import { mockConversationTree } from '../mocks/data'
import { serveApp } from '../sources/fakeRegistry'
import type { ConversationSource, ConversationTree, Message, MessageNode } from '../../lib/types'

// The bubble's providers ask for the preferences and the bookmarks.
beforeEach(() => serveApp({}))

function msg(over: Record<string, unknown>): Message {
  return {
    uuid: 'm-1',
    sender: 'assistant',
    text: 'A reply.',
    content: [{ type: 'text', text: 'A reply.' }],
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-01T10:00:00Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
    ...over,
  } as Message
}

function marker(over: Record<string, unknown>): Message {
  return msg({
    uuid: 'marker-1',
    sender: 'human',
    text: 'Session: /model',
    content: [{ type: 'text', text: 'Session: /model' }],
    is_command_marker: true,
    slash_command: '/model',
    ...over,
  })
}

function bubble(message: Message, source: ConversationSource = 'CLAUDE_CODE') {
  return render(<MessageBubble message={message} conversationId="conv-b52" conversationSource={source} />)
}

function sender(): HTMLElement {
  return screen.getByTestId('message-sender')
}

/** Absent, or in the DOM but hidden (for example in a closed <details>). */
function expectHidden(text: string | RegExp): void {
  const el = screen.queryByText(text)
  if (el) expect(el).not.toBeVisible()
}

function isMonospace(el: HTMLElement): boolean {
  return el.closest('pre, code, .font-mono') !== null
}

const DESKTOP_TOOLTIP =
  "From the conversation's model setting. Claude Desktop does not record the model of each reply."
const CODEX_TOOLTIP = "From the conversation's model setting. Codex does not record the model of each reply."

// ---------------------------------------------------------------------------
// The reply label
// ---------------------------------------------------------------------------

describe('the reply label names the model', () => {
  it('a reply with model claude-opus-5-5 is labelled "Claude Opus 5.5"', () => {
    bubble(msg({ model: 'claude-opus-5-5' }))
    expect(sender()).toHaveTextContent(/^Claude Opus 5\.5$/)
  })

  it('a reply with model claude-fable-5-1 is labelled "Claude Fable 5.1"', () => {
    bubble(msg({ model: 'claude-fable-5-1' }))
    expect(sender()).toHaveTextContent(/^Claude Fable 5\.1$/)
  })

  it('a <synthetic> reply is labelled "Claude Code"', () => {
    bubble(msg({ model: '<synthetic>' }))
    expect(sender()).toHaveTextContent(/^Claude Code$/)
  })

  it('a reply without a model is labelled with the assistant label alone', () => {
    bubble(msg({}))
    expect(sender()).toHaveTextContent(/^Claude$/)
  })

  it('a Codex reply is labelled "Codex" plus the model id', () => {
    bubble(msg({ model: 'gpt-5.6-codex', model_from_conversation: true }), 'CODEX')
    expect(sender()).toHaveTextContent(/^Codex gpt-5\.6-codex$/)
  })

  it('a human message is still labelled "You"', () => {
    bubble(msg({ sender: 'human', text: 'Hi.', content: [{ type: 'text', text: 'Hi.' }] }))
    expect(sender()).toHaveTextContent(/^You$/)
  })

  it('a Desktop reply with the conversation model has the D5 tooltip', () => {
    bubble(msg({ model: 'claude-sonnet-4-6', model_from_conversation: true }), 'CLAUDE_AI')
    expect(sender()).toHaveTextContent(/^Claude Sonnet 4\.6$/)
    expect(sender()).toHaveAttribute('title', DESKTOP_TOOLTIP)
  })

  it('a Codex reply with the conversation model has the D5 tooltip with "Codex"', () => {
    bubble(msg({ model: 'gpt-5.6-codex', model_from_conversation: true }), 'CODEX')
    expect(sender()).toHaveAttribute('title', CODEX_TOOLTIP)
  })

  it('a Claude Code reply, whose model comes from its row, has no D5 tooltip', () => {
    bubble(msg({ model: 'claude-opus-5-5', model_from_conversation: false }))
    expect(sender().getAttribute('title') ?? '').not.toContain("conversation's model setting")
  })

  it('the branch tree labels a reply with its model', () => {
    const withModel = (node: MessageNode): MessageNode => ({
      ...node,
      message: node.message.sender === 'assistant' ? ({ ...node.message, model: 'claude-opus-5-5' } as Message) : node.message,
      children: node.children.map(withModel),
    })
    const tree: ConversationTree = { ...mockConversationTree, root_messages: mockConversationTree.root_messages.map(withModel) }
    render(<TreeView tree={tree} onSelectPath={vi.fn()} source="CLAUDE_CODE" />)
    expect(screen.getAllByText('Claude Opus 5.5')).toHaveLength(3)
  })
})

describe('"Copy message as Markdown" uses the reply label', () => {
  beforeEach(() => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
  })

  it('the copied Markdown says "Claude Opus 5.5"', async () => {
    bubble(msg({ model: 'claude-opus-5-5' }))
    await act(async () => {
      fireEvent.click(screen.getByTitle('Copy message as Markdown'))
      await Promise.resolve()
    })
    const copied = vi.mocked(navigator.clipboard.writeText).mock.calls[0][0]
    expect(copied).toContain('**Claude Opus 5.5:**')
  })

  it('the copied Markdown of a Codex reply says "Codex gpt-5.6-codex"', async () => {
    bubble(msg({ model: 'gpt-5.6-codex', model_from_conversation: true }), 'CODEX')
    await act(async () => {
      fireEvent.click(screen.getByTitle('Copy message as Markdown'))
      await Promise.resolve()
    })
    const copied = vi.mocked(navigator.clipboard.writeText).mock.calls[0][0]
    expect(copied).toContain('**Codex gpt-5.6-codex:**')
  })
})

// ---------------------------------------------------------------------------
// Command output
// ---------------------------------------------------------------------------

const lines = (n: number) => Array.from({ length: n }, (_, i) => `output line ${i + 1}`).join('\n')

describe('command output shows under its command', () => {
  it('the output is in a monospace block labelled "Claude Code", under the command badge', () => {
    bubble(marker({ command_output: 'Set model to Fable 5.1 (claude-fable-5-1)' }))
    const block = screen.getByTestId('command-output')
    expect(within(block).getByText('Claude Code')).toBeInTheDocument()
    const text = within(block).getByText(/Set model to Fable 5\.1/)
    expect(isMonospace(text)).toBe(true)
    const badge = screen.getByTestId('slash-command-badge')
    expect(badge.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a marker without output has no output block', () => {
    bubble(marker({ command_output: null }))
    expect(screen.getByTestId('slash-command-badge')).toBeInTheDocument()
    expect(screen.queryByTestId('command-output')).toBeNull()
  })

  it('output over 20 lines is folded, and "Show all" opens it', () => {
    bubble(marker({ command_output: lines(30) }))
    const block = screen.getByTestId('command-output')
    expect(within(block).getByText(/output line 20/)).toBeVisible()
    expectHidden(/output line 21\b/)
    fireEvent.click(within(block).getByRole('button', { name: 'Show all' }))
    expect(within(block).getByText(/output line 30/)).toBeVisible()
  })

  it('output of exactly 20 lines shows in full, with no "Show all"', () => {
    bubble(marker({ command_output: lines(20) }))
    const block = screen.getByTestId('command-output')
    expect(within(block).getByText(/output line 20/)).toBeVisible()
    expect(within(block).queryByRole('button', { name: 'Show all' })).toBeNull()
  })

  it('stderr output is marked as an error', () => {
    bubble(marker({ slash_command: '/login', text: 'Session: /login', command_output: 'Login failed', command_output_is_error: true }))
    expect(screen.getByTestId('command-output')).toHaveAttribute('data-error', 'true')
  })

  it('stdout output is not marked as an error', () => {
    bubble(marker({ command_output: 'Done.', command_output_is_error: false }))
    expect(screen.getByTestId('command-output')).not.toHaveAttribute('data-error', 'true')
  })
})

// ---------------------------------------------------------------------------
// Shell commands and notices
// ---------------------------------------------------------------------------

describe('a shell command is one block labelled "Shell"', () => {
  const shell = () =>
    msg({
      uuid: 'shell-1',
      sender: 'human',
      text: 'ls -la\nfile_a.txt',
      content: [{ type: 'text', text: 'ls -la\nfile_a.txt' }],
      shell_command: 'ls -la',
      shell_output: 'file_a.txt\nfile_b.txt',
    })

  it('the block holds the label, the command, and the output in monospace', () => {
    bubble(shell())
    const block = screen.getByTestId('shell-block')
    expect(within(block).getByText('Shell')).toBeInTheDocument()
    expect(isMonospace(within(block).getByText(/ls -la/))).toBe(true)
    expect(isMonospace(within(block).getByText(/file_b\.txt/))).toBe(true)
  })

  it('a shell block is not labelled "You"', () => {
    bubble(shell())
    expect(screen.getByTestId('shell-block')).toBeInTheDocument()
    expect(screen.queryByText('You')).toBeNull()
  })
})

const NOTICES: Array<[string, string]> = [
  ['task-notification', 'Task notification'],
  ['bash-notification', 'Bash notification'],
  ['scheduled-task', 'Scheduled task'],
  ['system-reminder', 'System reminder'],
]

describe('a notice is a folded row labelled with its kind', () => {
  for (const [kind, label] of NOTICES) {
    it(`${kind}: the row says "${label}", hides its body until a click, and is not "You"`, () => {
      const body = `Background job finished ${kind} body`
      bubble(msg({ uuid: `n-${kind}`, sender: 'human', text: body, content: [{ type: 'text', text: body }], notice_kind: kind }))
      const row = screen.getByTestId('notice-row')
      expect(row).toHaveAttribute('data-kind', kind)
      expectHidden(body)
      fireEvent.click(within(row).getByRole('button', { name: new RegExp(label) }))
      expect(within(row).getByText(body)).toBeVisible()
      expect(screen.queryByText('You')).toBeNull()
    })
  }
})
