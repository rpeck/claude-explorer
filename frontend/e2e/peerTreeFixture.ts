/**
 * L2 (issue #91, part 2): the synthetic 60-session orchestrator tree as the
 * Messages tab sees it (a PeerGraph payload), for the e2e mock backend.
 *
 * The same shape as `backend/tests/peer_tree_fake.py`:
 *   - level 0: one coordinator (CO);
 *   - level 1: five sub-orchestrators (SUBS) and four direct workers (DIRECT);
 *   - level 2: ten workers under each sub (WORKERS[i]).
 * Each parent sends one message to each child, and each child replies. W11
 * also writes to a peer that is not in the archive; an agent of S1 writes to
 * W15. Every value is synthetic.
 */
import { makeDetail, makeMessage, makeSummary } from './fixtures'
import type { ConversationDetail, ConversationSummary } from '../src/lib/types'

// The new types, until types.ts has them (backend PeerGraph models).
export interface GraphSession {
  key: string
  uuid: string | null
  agent_id: string | null
  title: string
  name: string | null
  source: string
  project_name: string | null
  project_path: string | null
  state: string | null
  updated_at: string
  in_archive: boolean
  hop: number | null
  via_key: string | null
  derived_parent_key: string | null
}

export interface GraphMessage {
  msg_id: string
  kind: 'message' | 'idle_notice' | 'subagent'
  from_key: string
  to_key: string
  from_message_uuid: string | null
  to_message_uuid: string | null
  timestamp: string | null
  text: string
}

export interface PeerGraph {
  scope: 'active' | 'hops'
  center_key: string | null
  hops: number | null
  subagents: boolean
  sessions: GraphSession[]
  messages: GraphMessage[]
}

const sid = (prefix: string, n: number) => `${prefix}-0000-4000-8000-${String(n).padStart(12, '0')}`

export const CO = sid('c0000000', 1)
export const SUBS = [1, 2, 3, 4, 5].map((i) => sid('5b000000', i))
export const DIRECT = [1, 2, 3, 4].map((j) => sid('d0000000', j))
export const WORKERS = SUBS.map((_, i) => Array.from({ length: 10 }, (_, j) => sid('a0000000', (i + 1) * 100 + j + 1)))
export const TREE = [CO, ...SUBS, ...DIRECT, ...WORKERS.flat()]
export const S1 = SUBS[0]
export const W11 = WORKERS[0][0]
export const W12 = WORKERS[0][1]
export const W15 = WORKERS[0][4]
export const AGENT_ID = 's1helper'
export const AGENT_KEY = `${S1}::agent::${AGENT_ID}`
export const GONE_NAME = 'fixture-gone'
export const GONE_KEY = `name:${GONE_NAME}`
export const PROJECT_PATH = '/home/fixture/orchestra'

export function nameOf(uuid: string): string {
  if (uuid === CO) return 'fixture-coordinator'
  if (SUBS.includes(uuid)) return `fixture-sub-${SUBS.indexOf(uuid) + 1}`
  if (DIRECT.includes(uuid)) return `fixture-direct-${DIRECT.indexOf(uuid) + 1}`
  const i = WORKERS.findIndex((ws) => ws.includes(uuid))
  return `fixture-worker-${i + 1}-${WORKERS[i].indexOf(uuid) + 1}`
}

export const titleOf = (uuid: string) => `Orchestra: ${nameOf(uuid)}`

const NOW = Date.now()
const at = (n: number) => new Date(NOW - 3 * 3600_000 + n * 1000).toISOString()

/** Every message of the tree in time order (subagents off: the agent's message is S1's). */
function buildMessages(): GraphMessage[] {
  const out: GraphMessage[] = []
  let n = 0
  const send = (from: string, to: string, text: string) => {
    n += 1
    out.push({
      msg_id: `m-${n}`, kind: 'message', from_key: from, to_key: to,
      from_message_uuid: `${from.slice(0, 8)}-send-${n}`,
      to_message_uuid: to.startsWith('name:') ? null : `${to.slice(0, 8)}-recv-${n}`,
      timestamp: at(n), text,
    })
  }
  const exchange = (parent: string, child: string) => {
    send(parent, child, `Task for ${nameOf(child)}.`)
    send(child, parent, `Done: ${nameOf(child)}.`)
  }
  for (const child of [...SUBS, ...DIRECT]) exchange(CO, child)
  SUBS.forEach((sub, i) => WORKERS[i].forEach((w) => exchange(sub, w)))
  send(W11, GONE_KEY, 'Synthetic message to a gone peer.')
  send(S1, W15, 'Synthetic note from the agent of sub 1.')
  return out
}

export const MESSAGES = buildMessages()
export const AGENT_MSG_ID = MESSAGES[MESSAGES.length - 1].msg_id

export function msgBetween(from: string, to: string): GraphMessage {
  const m = MESSAGES.find((x) => x.from_key === from && x.to_key === to)
  if (!m) throw new Error(`no message ${from} -> ${to}`)
  return m
}

function parentOf(uuid: string): string | null {
  if (uuid === CO) return null
  if (SUBS.includes(uuid) || DIRECT.includes(uuid)) return CO
  const i = WORKERS.findIndex((ws) => ws.includes(uuid))
  return i >= 0 ? SUBS[i] : null
}

function session(key: string, extra: Partial<GraphSession> = {}): GraphSession {
  if (key === GONE_KEY) {
    return {
      key, uuid: null, agent_id: null, title: GONE_NAME, name: GONE_NAME, source: 'CLAUDE_CODE',
      project_name: null, project_path: null, state: null, updated_at: at(0), in_archive: false,
      hop: null, via_key: null, derived_parent_key: W11, ...extra,
    }
  }
  if (key === AGENT_KEY) {
    return {
      key, uuid: S1, agent_id: AGENT_ID, title: 'Help the sub', name: null, source: 'CLAUDE_CODE',
      project_name: 'orchestra', project_path: PROJECT_PATH, state: 'open', updated_at: at(500), in_archive: true,
      hop: null, via_key: null, derived_parent_key: S1, ...extra,
    }
  }
  return {
    key, uuid: key, agent_id: null, title: titleOf(key), name: nameOf(key), source: 'CLAUDE_CODE',
    project_name: 'orchestra', project_path: PROJECT_PATH, state: 'open', updated_at: at(500), in_archive: true,
    hop: null, via_key: null, derived_parent_key: parentOf(key), ...extra,
  }
}

/** GET /api/peers/active: the 60 tree sessions (all open) and the messages between them. */
export function activeGraph(): PeerGraph {
  return {
    scope: 'active', center_key: null, hops: null, subagents: false,
    sessions: TREE.map((k) => session(k)),
    messages: MESSAGES.filter((m) => m.to_key !== GONE_KEY),
  }
}

/** GET /api/conversations/{center}/peers: BFS over the message edges, as the backend does. */
export function hopsGraph(center: string, hops: number | 'all', subagents = false): PeerGraph {
  const msgs: GraphMessage[] = MESSAGES.map((m) =>
    subagents && m.msg_id === AGENT_MSG_ID ? { ...m, from_key: AGENT_KEY } : m,
  )
  if (subagents) {
    msgs.push({
      msg_id: `subagent-${AGENT_ID}`, kind: 'subagent', from_key: S1, to_key: AGENT_KEY,
      from_message_uuid: null, to_message_uuid: null, timestamp: at(499), text: 'Help the sub',
    })
  }
  const limit = hops === 'all' ? Infinity : hops
  const hop = new Map<string, number>([[center, 0]])
  const via = new Map<string, string | null>([[center, null]])
  let frontier = [center]
  for (let d = 1; d <= limit && frontier.length; d++) {
    const next: string[] = []
    for (const k of frontier) {
      for (const m of msgs) {
        const other = m.from_key === k ? m.to_key : m.to_key === k ? m.from_key : null
        if (other && !hop.has(other)) {
          hop.set(other, d)
          via.set(other, k)
          next.push(other)
        }
      }
    }
    frontier = next
  }
  return {
    scope: 'hops', center_key: center, hops: hops === 'all' ? null : hops, subagents,
    sessions: [...hop.keys()].map((k) => session(k, { hop: hop.get(k) ?? null, via_key: via.get(k) ?? null })),
    messages: msgs.filter((m) => hop.has(m.from_key) && hop.has(m.to_key)),
  }
}

export function treeSummaries(state: Record<string, string> = {}): ConversationSummary[] {
  return TREE.map((uuid) =>
    makeSummary({
      uuid, name: titleOf(uuid), source: 'CLAUDE_CODE', project_path: PROJECT_PATH, project_name: 'orchestra',
      message_count: 4, human_message_count: 1, created_at: at(0), updated_at: at(500),
      ...({ state: state[uuid] ?? 'open' } as Partial<ConversationSummary>),
    }),
  )
}

/** A detail per session that holds the message uuids that the tab links to. */
export function treeDetails(): Record<string, ConversationDetail> {
  const out: Record<string, ConversationDetail> = {}
  const [summaries] = [treeSummaries()]
  for (const s of summaries) {
    const mine = MESSAGES.flatMap((m) => [
      m.from_key === s.uuid ? m.from_message_uuid : null,
      m.to_key === s.uuid ? m.to_message_uuid : null,
    ]).filter((u): u is string => Boolean(u))
    const messages = [
      makeMessage({ uuid: `${s.uuid.slice(0, 8)}-prompt`, sender: 'human', text: `Start: ${nameOf(s.uuid)}.`,
        content: [{ type: 'text', text: `Start: ${nameOf(s.uuid)}.` }] }),
      ...mine.map((uuid, i) => makeMessage({ uuid, sender: i % 2 ? 'human' : 'assistant', text: `Row ${uuid}.`,
        content: [{ type: 'text', text: `Row ${uuid}.` }] })),
    ]
    out[s.uuid] = makeDetail(s, messages)
  }
  return out
}
