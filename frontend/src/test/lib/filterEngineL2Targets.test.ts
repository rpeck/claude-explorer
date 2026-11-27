/**
 * L2 (issue #91, part 2): the filter targets of the Messages tab.
 *
 * The contract (orchestrator, 2026-11-27; T1 owns the item API):
 *
 *   - `FilterItem` gets the fields messageText, project, source, machine,
 *     updatedAt, and state.
 *   - `targetValues(item, target)` returns the values of one target; an
 *     atom matches when any value matches; no values = no match.
 *   - New targets: message_text, project, source, machine, active_within,
 *     state.
 *   - active_within: a pattern "<n>m", "<n>h", "<n>d", or "<n>w"; a hit when
 *     now - updatedAt <= that time. A bad duration or no updatedAt = no hit.
 *   - `applyFilterById(item, filters, activeId, now?)` evaluates the tab's
 *     own filter, not `filters.activeId`.
 *   - `applyActiveFilter(text, state)` does not change.

 */
import { describe, expect, it } from 'vitest'
import * as engine from '@/lib/filterEngine'
import type { AtomFilter, FilterNode, FiltersState, GroupFilter } from '@/lib/filterEngine'

// The new names, until filterEngine has them; the casts keep tsc green.
interface Item {
  name: string
  messageText?: string[]
  project?: string | null
  source?: string | null
  machine?: string | null
  updatedAt?: string | null
  state?: string | null
}
interface L2Engine {
  targetValues(item: Item, target: string): string[]
  applyFilterById(item: Item, filters: FiltersState, activeId: string | null, now?: number): boolean
}
const E = engine as unknown as L2Engine

const NOW = Date.parse('2026-11-27T12:00:00Z')
const ago = (ms: number) => new Date(NOW - ms).toISOString()
const H = 3600_000

function atom(id: string, target: string, patterns: string[], behavior: 'hide' | 'show-only' = 'show-only'): AtomFilter {
  return { id, name: id, enabled: true, type: 'atom', patterns, behavior, mode: 'glob', target } as unknown as AtomFilter
}

function state(...nodes: FilterNode[]): FiltersState {
  return { nodes: Object.fromEntries(nodes.map((n) => [n.id, n])), activeId: null, _migratedV1: true, _migratedV2: true }
}

function passes(item: Item, node: FilterNode, ...more: FilterNode[]): boolean {
  return E.applyFilterById(item, state(node, ...more), node.id, NOW)
}

const worker: Item = {
  name: 'Orchestra: fixture-worker-1-1',
  messageText: ['Task for fixture-worker-1-1.', 'Done: fixture-worker-1-1.'],
  project: 'orchestra',
  source: 'CLAUDE_CODE',
  machine: 'work-laptop',
  updatedAt: ago(2 * H),
  state: 'open',
}
const bare: Item = { name: 'A Desktop chat' }

describe('targetValues (L2 targets)', () => {
  it('message_text gives every message text', () => {
    expect(E.targetValues(worker, 'message_text')).toEqual(worker.messageText)
  })

  it('project, source, machine, and state give the one field', () => {
    expect(E.targetValues(worker, 'project')).toEqual(['orchestra'])
    expect(E.targetValues(worker, 'source')).toEqual(['CLAUDE_CODE'])
    expect(E.targetValues(worker, 'machine')).toEqual(['work-laptop'])
    expect(E.targetValues(worker, 'state')).toEqual(['open'])
  })

  it('a missing field gives no values', () => {
    for (const target of ['message_text', 'project', 'source', 'machine', 'state']) {
      expect(E.targetValues(bare, target)).toEqual([])
    }
    expect(E.targetValues({ ...worker, project: null }, 'project')).toEqual([])
  })
})

describe('atoms on the L2 targets', () => {
  it('message_text matches when any message matches', () => {
    expect(passes(worker, atom('m', 'message_text', ['Done:*']))).toBe(true)
    expect(passes(worker, atom('m', 'message_text', ['schema']))).toBe(false)
  })

  it('message_text does not match the title', () => {
    expect(passes({ ...worker, messageText: [] }, atom('m', 'message_text', ['fixture-worker']))).toBe(false)
  })

  it('project, source, and machine match their field', () => {
    expect(passes(worker, atom('p', 'project', ['orch']))).toBe(true)
    expect(passes(worker, atom('p', 'project', ['payments']))).toBe(false)
    expect(passes(worker, atom('s', 'source', ['claude_code']))).toBe(true)
    expect(passes(worker, atom('s', 'source', ['CODEX']))).toBe(false)
    expect(passes(worker, atom('w', 'machine', ['work-*']))).toBe(true)
  })

  it('state matches the state id; "Active" is state running or open', () => {
    const active = atom('active', 'state', ['running', 'open'])
    expect(passes(worker, active)).toBe(true)
    expect(passes({ ...worker, state: 'running' }, active)).toBe(true)
    expect(passes({ ...worker, state: 'ended' }, active)).toBe(false)
    expect(passes({ ...worker, state: 'ended_no_exit' }, active)).toBe(false)
  })

  it('show-only drops an item without the field; hide keeps it', () => {
    expect(passes(bare, atom('p', 'project', ['orchestra']))).toBe(false)
    expect(passes(bare, atom('p', 'project', ['orchestra'], 'hide'))).toBe(true)
    expect(passes(bare, atom('m', 'message_text', ['x'], 'hide'))).toBe(true)
  })

  it('hide drops an item whose field matches', () => {
    expect(passes(worker, atom('s', 'state', ['open'], 'hide'))).toBe(false)
  })
})

describe('active_within', () => {
  it('a session updated inside the time matches', () => {
    expect(passes(worker, atom('t', 'active_within', ['3h']))).toBe(true)
    expect(passes(worker, atom('t', 'active_within', ['1d']))).toBe(true)
    expect(passes(worker, atom('t', 'active_within', ['1w']))).toBe(true)
    expect(passes(worker, atom('t', 'active_within', ['121m']))).toBe(true)
  })

  it('a session updated before the time does not match', () => {
    expect(passes(worker, atom('t', 'active_within', ['1h']))).toBe(false)
    expect(passes(worker, atom('t', 'active_within', ['119m']))).toBe(false)
    expect(passes({ ...worker, updatedAt: ago(8 * 24 * H) }, atom('t', 'active_within', ['1w']))).toBe(false)
  })

  it('the limit itself still matches', () => {
    expect(passes(worker, atom('t', 'active_within', ['2h']))).toBe(true)
  })

  it('a bad duration or no updatedAt is no hit', () => {
    for (const bad of ['soon', '2', 'h', '-1h', '2y']) {
      expect(passes(worker, atom('t', 'active_within', [bad]))).toBe(false)
    }
    expect(passes(bare, atom('t', 'active_within', ['1w']))).toBe(false)
    expect(passes(bare, atom('t', 'active_within', ['1w'], 'hide'))).toBe(true)
  })

  it('any of several durations is enough', () => {
    expect(passes(worker, atom('t', 'active_within', ['bad', '1h', '3h']))).toBe(true)
  })
})

describe('applyFilterById', () => {
  it('it uses the given id, not filters.activeId', () => {
    const keep = atom('keep', 'project', ['orchestra'])
    const drop = atom('drop', 'project', ['payments'])
    const s = { ...state(keep, drop), activeId: 'drop' }
    expect(E.applyFilterById(worker, s, 'keep', NOW)).toBe(true)
    expect(E.applyFilterById(worker, { ...s, activeId: 'keep' }, 'drop', NOW)).toBe(false)
  })

  it('a null id, an unknown id, or a disabled node passes every item', () => {
    const drop = atom('drop', 'project', ['payments'])
    const s = state(drop, { ...atom('off', 'project', ['payments']), enabled: false })
    expect(E.applyFilterById(worker, s, null, NOW)).toBe(true)
    expect(E.applyFilterById(worker, s, 'nope', NOW)).toBe(true)
    expect(E.applyFilterById(worker, s, 'off', NOW)).toBe(true)
  })

  it('a group composes the new targets', () => {
    const group: GroupFilter = { id: 'g', name: 'g', enabled: true, type: 'group', match: 'all', childIds: ['p', 'a'] }
    const p = atom('p', 'project', ['orchestra'])
    const a = atom('a', 'state', ['running'])
    expect(passes(worker, group, p, a)).toBe(false)
    expect(passes({ ...worker, state: 'running' }, group, p, a)).toBe(true)
    expect(passes(worker, { ...group, match: 'any' }, p, a)).toBe(true)
  })

  it('a title atom still matches the name', () => {
    expect(passes(worker, atom('t', 'title', ['worker-1-1']))).toBe(true)
    expect(passes(worker, atom('t', 'title', ['coordinator']))).toBe(false)
  })
})

describe('applyActiveFilter (unchanged)', () => {
  it('still matches a title text against the active filter', () => {
    const s = { ...state(atom('t', 'title', ['digest'])), activeId: 't' }
    expect(engine.applyActiveFilter('Daily digest', s)).toBe(true)
    expect(engine.applyActiveFilter('Daily report', s)).toBe(false)
  })
})
