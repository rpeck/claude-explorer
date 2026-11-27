/**
 * L1 (issue #91, part 1): the peer message row and the peer links in tool
 * blocks. The contract (orchestrator, 2026-11-27):
 *
 *   - A received row (`peer_ref.kind === 'received'`): data-testid
 *     "peer-message-row", header "Message from @<name>", the body in
 *     "peer-message-body". It is not a "You" bubble, and it shows with Show
 *     Tools off.
 *   - "peer-link": "From <title> →" to /conversations/<uuid>?m=<message_uuid>
 *     (no ?m= without a message_uuid). "peer-not-in-archive": "not in the
 *     archive". With link_state "unknown": neither, only the name.
 *   - "peer-sent-because": "Sent because of: <because_text>", a link to
 *     ?m=<because_message_uuid>.
 *   - "peer-mode-chip": the mode, only when it is set and is not "prompting".
 *   - A sent tool block (SendMessage, a Codex call): "peer-link" with
 *     "Sent to <title> →", or "peer-not-in-archive"; only with the tool block.
 *   - An idle notice: "peer-idle-notice", with "peer-notify-link" to
 *     ?m=<notify_message_uuid> in this conversation, and "peer-link".
 */
import { describe, expect, it } from 'vitest'
import { render, screen, within } from '../utils'
import { MessageBubble } from '../../components/message/MessageBubble'
import type { Message } from '../../lib/types'


const A = 'a1000000-0000-4000-8000-0000000000a1'
const B = 'b1000000-0000-4000-8000-0000000000b1'
const ROOT = '01a21ab1-78fd-7160-90be-db18dbc62756'
const PING = 'Synthetic ping from A.'

// The new field, until Message has it; the cast keeps tsc green.
type PeerRef = {
  kind: 'sent' | 'received' | 'idle_notice'
  msg_id: string | null
  name: string | null
  conversation_uuid: string | null
  message_uuid: string | null
  title: string | null
  link_state: 'linked' | 'not_in_archive' | 'unknown'
  mode: string | null
  because_text: string | null
  because_message_uuid: string | null
  notify_message_uuid: string | null
}

function ref(overrides: Partial<PeerRef> = {}): PeerRef {
  return {
    kind: 'received',
    msg_id: '11111111-0000-4000-8000-0000000000ab',
    name: 'fixture-peer-a',
    conversation_uuid: A,
    message_uuid: 'a-send',
    title: 'Peer A session',
    link_state: 'linked',
    mode: 'prompting',
    because_text: 'Send the ping to B.',
    because_message_uuid: 'a-prompt',
    notify_message_uuid: null,
    ...overrides,
  }
}

function message(fields: Record<string, unknown>): Message {
  return {
    uuid: 'b-row',
    sender: 'human',
    text: PING,
    content: [{ type: 'text', text: PING }],
    created_at: '2026-11-20T09:00:05Z',
    updated_at: '2026-11-20T09:00:05Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
    ...fields,
  } as unknown as Message
}

function received(overrides: Partial<PeerRef> = {}): Message {
  return message({ peer_ref: ref(overrides) })
}

function sendBlock(peer: PeerRef | null, name = 'SendMessage', id = 'toolu_a_send'): Message {
  const use: Record<string, unknown> = { type: 'tool_use', id, name, input: { to: 'fixture-peer-b', message: PING } }
  if (peer) use.peer_ref = peer
  return message({
    uuid: 'a-send',
    sender: 'assistant',
    text: '',
    content: [use, { type: 'tool_result', tool_use_id: id, content: [{ type: 'text', text: '{"success":true}' }] }],
  })
}

function renderBubble(m: Message, props: Record<string, unknown> = {}) {
  return render(<MessageBubble message={m} conversationId={B} conversationSource="CLAUDE_CODE" {...props} />)
}

describe('A received message row', () => {
  it('shows "Message from @<name>" with Show Tools off, and is not a "You" bubble', () => {
    renderBubble(received())
    const row = screen.getByTestId('peer-message-row')
    expect(row).toHaveTextContent('Message from @fixture-peer-a')
    expect(screen.queryByText('You')).toBeNull()
  })

  it('shows the body', () => {
    renderBubble(received())
    expect(screen.getByTestId('peer-message-body')).toHaveTextContent(PING)
  })

  it('links to the send in the other session', () => {
    renderBubble(received())
    const link = within(screen.getByTestId('peer-message-row')).getByTestId('peer-link')
    expect(link).toHaveTextContent('From Peer A session →')
    expect(link).toHaveAttribute('href', `/conversations/${A}?m=a-send`)
  })

  it('links to the session only when there is no message uuid', () => {
    renderBubble(received({ message_uuid: null }))
    expect(screen.getByTestId('peer-link')).toHaveAttribute('href', `/conversations/${A}`)
  })

  it('says "not in the archive" when the sender is not in the archive', () => {
    renderBubble(received({ link_state: 'not_in_archive', conversation_uuid: null, message_uuid: null, title: null,
      because_text: null, because_message_uuid: null }))
    const row = screen.getByTestId('peer-message-row')
    expect(within(row).getByTestId('peer-not-in-archive')).toHaveTextContent('not in the archive')
    expect(within(row).queryByTestId('peer-link')).toBeNull()
  })

  it('shows only the name when the index cannot answer', () => {
    renderBubble(received({ link_state: 'unknown', conversation_uuid: null, message_uuid: null, title: null,
      because_text: null, because_message_uuid: null }))
    const row = screen.getByTestId('peer-message-row')
    expect(row).toHaveTextContent('Message from @fixture-peer-a')
    expect(within(row).queryByTestId('peer-link')).toBeNull()
    expect(within(row).queryByTestId('peer-not-in-archive')).toBeNull()
  })

  it('shows "Sent because of" with a link to the sender\'s prompt', () => {
    renderBubble(received())
    const because = screen.getByTestId('peer-sent-because')
    expect(because).toHaveTextContent('Sent because of: Send the ping to B.')
    const link = because.tagName === 'A' ? because : within(because).getByRole('link')
    expect(link).toHaveAttribute('href', `/conversations/${A}?m=a-prompt`)
  })

  it('has no "Sent because of" line without because_text', () => {
    renderBubble(received({ because_text: null, because_message_uuid: null }))
    expect(screen.getByTestId('peer-message-row')).toBeInTheDocument()
    expect(screen.queryByTestId('peer-sent-because')).toBeNull()
  })

  it('shows the mode chip only for a mode that is not "prompting"', () => {
    const { unmount } = renderBubble(received({ mode: 'acceptEdits' }))
    expect(screen.getByTestId('peer-mode-chip')).toHaveTextContent('acceptEdits')
    unmount()
    renderBubble(received({ mode: 'prompting' }))
    expect(screen.getByTestId('peer-message-row')).toBeInTheDocument()
    expect(screen.queryByTestId('peer-mode-chip')).toBeNull()
  })
})

describe('A sent tool block', () => {
  const sent = ref({ kind: 'sent', name: 'fixture-peer-b', conversation_uuid: B, message_uuid: 'b-row',
    title: 'Peer B session', mode: null, because_text: null, because_message_uuid: null })

  it('a SendMessage block links to the received row', () => {
    renderBubble(sendBlock(sent), { showToolCalls: true, conversationId: A })
    const link = screen.getByTestId('peer-link')
    expect(link).toHaveTextContent('Sent to Peer B session →')
    expect(link).toHaveAttribute('href', `/conversations/${B}?m=b-row`)
  })

  it('a send to a session that is not in the archive says so', () => {
    renderBubble(sendBlock({ ...sent, link_state: 'not_in_archive', conversation_uuid: null, message_uuid: null,
      title: null, name: 'fixture-peer-gone' }), { showToolCalls: true, conversationId: A })
    expect(screen.getByTestId('peer-not-in-archive')).toHaveTextContent('not in the archive')
    expect(screen.queryByTestId('peer-link')).toBeNull()
  })

  it('the link shows only with the tool block', () => {
    const m = sendBlock(sent)
    const { rerender } = renderBubble(m, { conversationId: A })
    expect(screen.queryByTestId('peer-link')).toBeNull()
    rerender(<MessageBubble message={m} conversationId={A} conversationSource="CLAUDE_CODE" showToolCalls />)
    expect(screen.getByTestId('peer-link')).toBeInTheDocument()
  })

  it('a Codex received agent message links to the root thread', () => {
    const got = ref({ name: '/root', conversation_uuid: ROOT, message_uuid: 'root-call', title: 'Codex root',
      msg_id: null, mode: null, because_text: null, because_message_uuid: null })
    renderBubble(sendBlock(got, 'agent_message', 'amsg_1'), { showToolCalls: true, conversationSource: 'CODEX' })
    const link = screen.getByTestId('peer-link')
    expect(link).toHaveTextContent('From Codex root →')
    expect(link).toHaveAttribute('href', `/conversations/${ROOT}?m=root-call`)
  })
})

describe('An idle notice', () => {
  const notice = message({
    uuid: 'a-idle',
    text: '[Cross-session idle notice] "fixture-peer-b", which you asked to be notified about, is idle now.',
    content: [{ type: 'text', text: '[Cross-session idle notice] "fixture-peer-b" is idle now.' }],
    peer_ref: ref({ kind: 'idle_notice', msg_id: null, name: 'fixture-peer-b', conversation_uuid: B,
      message_uuid: null, title: 'Peer B session', mode: null, because_text: null, because_message_uuid: null,
      notify_message_uuid: 'a-send' }),
  })

  it('links to the asker\'s own notify send in this conversation', () => {
    renderBubble(notice, { conversationId: A })
    const row = screen.getByTestId('peer-idle-notice')
    expect(within(row).getByTestId('peer-notify-link')).toHaveAttribute('href', `/conversations/${A}?m=a-send`)
  })

  it('links to the peer session', () => {
    renderBubble(notice, { conversationId: A })
    const row = screen.getByTestId('peer-idle-notice')
    expect(within(row).getByTestId('peer-link')).toHaveAttribute('href', `/conversations/${B}`)
  })
})
