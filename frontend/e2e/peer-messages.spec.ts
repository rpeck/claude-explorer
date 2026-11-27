import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'
import type { Page } from '@playwright/test'
import type { ConversationDetail, ConversationSummary, Message } from '../src/lib/types'

/**
 * L1 (issue #91, part 1): messages between sessions in the Conversation Pane.
 * The contract (orchestrator, 2026-11-27):
 *
 *   - A received message is a row (data-testid "peer-message-row") with
 *     "Message from @<name>" and the body; the raw envelope does not show.
 *   - "peer-link" ("From <title> →") opens the other session at the send
 *     (?m=<message_uuid>).
 *   - "peer-not-in-archive" says "not in the archive".
 *   - The header Details row "Messages with:" (data-testid
 *     "peer-sessions-row") links each peer in the archive.
 *
 * mockBackend serves the payloads below. The console gate of TESTING.md
 * §5.15 runs for every test.
 */


const A = 'a1000000-0000-4000-8000-0000000000a1'
const B = 'b1000000-0000-4000-8000-0000000000b1'
const E = 'e1000000-0000-4000-8000-0000000000e1'
const PING = 'Synthetic ping from A.'
const TITLES: Record<string, string> = {
  [A]: 'Peers: the sender session',
  [B]: 'Peers: the receiver session',
  [E]: 'Peers: a session with a lost sender',
}

function summary(uuid: string): ConversationSummary {
  return makeSummary({
    uuid,
    name: TITLES[uuid],
    source: 'CLAUDE_CODE',
    message_count: 2,
    human_message_count: 1,
    project_path: '/home/fixture/peer',
    project_name: 'peer',
  })
}

// The new fields, until the types have them; the casts keep tsc green.
function peerRef(fields: Record<string, unknown>): Record<string, unknown> {
  return {
    kind: 'received', msg_id: null, name: null, conversation_uuid: null, message_uuid: null, title: null,
    link_state: 'linked', mode: 'prompting', because_text: null, because_message_uuid: null,
    notify_message_uuid: null, ...fields,
  }
}

function msg(fields: Record<string, unknown> & { uuid: string }): Message {
  return makeMessage(fields as unknown as Partial<Message> & { uuid: string })
}

function detail(uuid: string, messages: Message[], peers: Record<string, unknown>[]): ConversationDetail {
  return makeDetail(summary(uuid), messages, { peers } as unknown as Partial<ConversationDetail>)
}

const details: Record<string, ConversationDetail> = {
  [A]: detail(A, [
    msg({ uuid: 'a-prompt', sender: 'human', text: 'Send the ping to B.', content: [{ type: 'text', text: 'Send the ping to B.' }] }),
    msg({
      uuid: 'a-send', sender: 'assistant', text: 'Sent.', parent_message_uuid: 'a-prompt',
      content: [{ type: 'text', text: 'Sent.' }],
    }),
  ], [{ conversation_uuid: B, name: 'fixture-peer-b', title: TITLES[B], link_state: 'linked' }]),
  [B]: detail(B, [
    msg({ uuid: 'b-prompt', sender: 'human', text: 'Wait for a message.', content: [{ type: 'text', text: 'Wait for a message.' }] }),
    msg({
      uuid: 'b-row', sender: 'human', text: PING, parent_message_uuid: 'b-prompt',
      content: [{ type: 'text', text: PING }],
      peer_ref: peerRef({
        msg_id: '11111111-0000-4000-8000-0000000000ab', name: 'fixture-peer-a', conversation_uuid: A,
        message_uuid: 'a-send', title: TITLES[A], because_text: 'Send the ping to B.', because_message_uuid: 'a-prompt',
      }),
    }),
  ], [{ conversation_uuid: A, name: 'fixture-peer-a', title: TITLES[A], link_state: 'linked' }]),
  [E]: detail(E, [
    msg({ uuid: 'e-prompt', sender: 'human', text: 'Wait for messages.', content: [{ type: 'text', text: 'Wait for messages.' }] }),
    msg({
      uuid: 'e-row', sender: 'human', text: 'Synthetic message from a lost session.', parent_message_uuid: 'e-prompt',
      content: [{ type: 'text', text: 'Synthetic message from a lost session.' }],
      peer_ref: peerRef({ name: 'fixture-peer-x', link_state: 'not_in_archive', mode: 'acceptEdits' }),
    }),
  ], [{ conversation_uuid: null, name: 'fixture-peer-x', title: null, link_state: 'not_in_archive' }]),
}

const conversations = [summary(A), summary(B), summary(E)]

async function open(page: Page, uuid: string) {
  await withNetRetry(page, () => page.goto(`/conversations/${uuid}`))
  await expect(page.locator('header').first().getByRole('heading', { level: 1, name: TITLES[uuid] })).toBeVisible()
}

test.describe('Messages between sessions (L1)', () => {
  test('a received message shows as a "Message from" row with the body', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, details })
    await open(page, B)
    const row = page.getByTestId('peer-message-row')
    await expect(row).toContainText('Message from @fixture-peer-a')
    await expect(row.getByTestId('peer-message-body')).toHaveText(PING)
    await expect(row.getByTestId('peer-sent-because')).toContainText('Sent because of: Send the ping to B.')
  })

  test('the "From" link opens the sender session at the send', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, details })
    await open(page, B)
    const link = page.getByTestId('peer-message-row').getByTestId('peer-link')
    await expect(link).toHaveText(`From ${TITLES[A]} →`)
    await link.click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${A}\\?m=a-send$`))
    await expect(page.locator('header').first().getByRole('heading', { level: 1, name: TITLES[A] })).toBeVisible()
  })

  test('a sender that is not in the archive says so, with no link', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, details })
    await open(page, E)
    const row = page.getByTestId('peer-message-row')
    await expect(row.getByTestId('peer-not-in-archive')).toHaveText('not in the archive')
    await expect(row.getByTestId('peer-link')).toHaveCount(0)
    await expect(row.getByTestId('peer-mode-chip')).toHaveText('acceptEdits')
  })

  test('the "Messages with:" row links the peer and opens it', async ({ page, mockBackend }) => {
    await mockBackend({ conversations, details })
    await open(page, A)
    const peers = page.locator('header').first().getByTestId('peer-sessions-row')
    await expect(peers).toContainText('Messages with:')
    const link = peers.getByRole('link', { name: TITLES[B] })
    await expect(link).toHaveAttribute('href', `/conversations/${B}`)
    await link.click()
    await expect(page.getByTestId('peer-message-row')).toContainText('Message from @fixture-peer-a')
  })
})
