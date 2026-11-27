/**
 * T1 (issue #93): the pure parts of the ticket view. The contract
 * (orchestrator, 2026-11-27):
 *
 *   - fillStartCommand(template, {repo, issue, title, cwd}) in
 *     components/tickets/startCommand.ts. {repo} is "owner/repo" when it is
 *     valid, else "". {issue} is the number. {title} and {cwd} get a backslash
 *     before the backslash, ", $ and the backtick, and each newline becomes a space, so
 *     they are safe inside double quotes. Every occurrence is filled.
 *   - entrypointLabel(value) in components/tickets/entrypoint.ts.
 *   - filterEngine: the 'ticket' and 'entrypoint' targets, targetValues and
 *     applyActiveFilterToItem; applyActiveFilter(text, state) is unchanged.

 */
import { describe, expect, it } from 'vitest'
import { importModule } from '@/test/codex/codexHelpers'

type Values = { repo: string | null; issue: number; title: string; cwd: string }
type Fill = (template: string, values: Values) => string

async function fill(): Promise<Fill> {
  return (await importModule<{ fillStartCommand: Fill }>('@/components/tickets/startCommand')).fillStartCommand
}

const VALUES: Values = { repo: 'fixture-org/alpha', issue: 84, title: 'Fix search', cwd: '/home/fixture/alpha' }

describe('fillStartCommand', () => {
  it('fills every placeholder', async () => {
    const f = await fill()
    expect(f('claude --bg "Fix {repo}#{issue}: {title}" --cwd "{cwd}"', VALUES)).toBe(
      'claude --bg "Fix fixture-org/alpha#84: Fix search" --cwd "/home/fixture/alpha"',
    )
  })

  it('fills a placeholder that occurs twice', async () => {
    const f = await fill()
    expect(f('{issue} and {issue}', VALUES)).toBe('84 and 84')
  })

  it('leaves a template without placeholders as it is', async () => {
    const f = await fill()
    expect(f('claude --cloud', VALUES)).toBe('claude --cloud')
  })

  it('escapes the title for double quotes', async () => {
    const f = await fill()
    const title = 'A "q" $(touch x) `id` back\\slash'
    expect(f('"{title}"', { ...VALUES, title })).toBe('"A \\"q\\" \\$(touch x) \\`id\\` back\\\\slash"')
  })

  it('turns the newlines of the title into spaces', async () => {
    const f = await fill()
    expect(f('"{title}"', { ...VALUES, title: 'line one\nline two' })).toBe('"line one line two"')
  })

  it('escapes the cwd for double quotes', async () => {
    const f = await fill()
    expect(f('"{cwd}"', { ...VALUES, cwd: '/home/fixture/$HOME "x"' })).toBe('"/home/fixture/\\$HOME \\"x\\""')
  })

  it('an unknown repo fills as an empty string', async () => {
    const f = await fill()
    expect(f('[{repo}]#{issue}', { ...VALUES, repo: null })).toBe('[]#84')
  })

  it('a repo that is not owner/repo fills as an empty string', async () => {
    const f = await fill()
    expect(f('[{repo}]', { ...VALUES, repo: 'a/b; rm -rf x' })).toBe('[]')
  })
})

describe('entrypointLabel', () => {
  it('names the known launchers', async () => {
    const { entrypointLabel } = await importModule<{ entrypointLabel: (v: string) => string }>(
      '@/components/tickets/entrypoint',
    )
    const known: Record<string, string> = {
      cli: 'CLI',
      'claude-desktop': 'Desktop',
      'claude-vscode': 'VS Code',
      'sdk-cli': 'SDK',
      'sdk-ts': 'SDK',
      'sdk-py': 'SDK',
      'claude-code-github-action': 'GitHub Action',
      codex_cli_rs: 'Codex CLI',
      codex_exec: 'Codex exec',
      codex_vscode: 'Codex VS Code',
    }
    for (const [value, label] of Object.entries(known)) expect(entrypointLabel(value)).toBe(label)
  })

  it('shows an unknown value as it is', async () => {
    const { entrypointLabel } = await importModule<{ entrypointLabel: (v: string) => string }>(
      '@/components/tickets/entrypoint',
    )
    expect(entrypointLabel('some-new-launcher')).toBe('some-new-launcher')
  })
})

// The new filterEngine API, until the module has it; the casts keep tsc green.
type Item = { name: string; tickets?: string[]; entrypoint?: string | null }
type State = { nodes: Record<string, unknown>; activeId: string | null }
type Engine = {
  targetValues: (item: Item, target: string) => string[]
  applyActiveFilterToItem: (item: Item, state: State) => boolean
  applyActiveFilter: (text: string, state: State) => boolean
}

function atom(target: string, patterns: string[], behavior: 'hide' | 'show-only', id = 'a1') {
  return { id, name: id, enabled: true, type: 'atom', patterns, behavior, mode: 'glob', target }
}

function only(node: ReturnType<typeof atom>): State {
  return { nodes: { [node.id]: node }, activeId: node.id }
}

async function engine(): Promise<Engine> {
  return importModule<Engine>('@/lib/filterEngine')
}

const S1: Item = { name: 'Work on alpha', tickets: ['fixture-org/alpha#84', 'fixture-org/beta#84'], entrypoint: 'cli' }
const S2: Item = { name: 'No ticket', tickets: [], entrypoint: null }
const S3: Item = { name: 'Old list item' }

describe('filterEngine ticket and entrypoint targets', () => {
  it('targetValues gives the values of each target', async () => {
    const { targetValues } = await engine()
    expect(targetValues(S1, 'title')).toEqual(['Work on alpha'])
    expect(targetValues(S1, 'ticket')).toEqual(['fixture-org/alpha#84', 'fixture-org/beta#84'])
    expect(targetValues(S1, 'entrypoint')).toEqual(['cli'])
    expect(targetValues(S2, 'entrypoint')).toEqual([])
    expect(targetValues(S3, 'ticket')).toEqual([])
  })

  it('a show-only ticket atom keeps the sessions of that ticket', async () => {
    const { applyActiveFilterToItem } = await engine()
    const state = only(atom('ticket', ['fixture-org/alpha#84'], 'show-only'))
    expect([S1, S2, S3].map((i) => applyActiveFilterToItem(i, state))).toEqual([true, false, false])
  })

  it('a hide ticket atom drops the sessions of that ticket and keeps the rest', async () => {
    const { applyActiveFilterToItem } = await engine()
    const state = only(atom('ticket', ['fixture-org/beta#*'], 'hide'))
    expect([S1, S2, S3].map((i) => applyActiveFilterToItem(i, state))).toEqual([false, true, true])
  })

  it('a ticket pattern matches the whole key, not a longer number', async () => {
    const { applyActiveFilterToItem } = await engine()
    const item: Item = { name: 'x', tickets: ['fixture-org/alpha#841'] }
    expect(applyActiveFilterToItem(item, only(atom('ticket', ['^fixture-org/alpha#84$'], 'show-only')))).toBe(false)
  })

  it('a ticket atom matches Linear and Jira keys', async () => {
    const { applyActiveFilterToItem } = await engine()
    const state = only(atom('ticket', ['ENG-*'], 'show-only'))
    expect(applyActiveFilterToItem({ name: 'x', tickets: ['ENG-123'] }, state)).toBe(true)
    expect(applyActiveFilterToItem({ name: 'x', tickets: ['PROJ-7', 'fixture-org/alpha#84'] }, state)).toBe(false)
  })

  it('an entrypoint atom matches the raw value', async () => {
    const { applyActiveFilterToItem } = await engine()
    const state = only(atom('entrypoint', ['sdk-*'], 'hide'))
    expect(applyActiveFilterToItem({ name: 'x', entrypoint: 'sdk-cli' }, state)).toBe(false)
    expect(applyActiveFilterToItem({ name: 'x', entrypoint: 'cli' }, state)).toBe(true)
    expect(applyActiveFilterToItem({ name: 'x', entrypoint: null }, state)).toBe(true)
  })

  it('a title atom through the item API matches the name, as before', async () => {
    const { applyActiveFilterToItem, applyActiveFilter } = await engine()
    const state = only(atom('title', ['work*'], 'show-only'))
    expect(applyActiveFilterToItem(S1, state)).toBe(applyActiveFilter(S1.name, state))
    expect(applyActiveFilterToItem(S2, state)).toBe(false)
  })

  it('a group combines a ticket atom and an entrypoint atom', async () => {
    const { applyActiveFilterToItem } = await engine()
    const t = atom('ticket', ['fixture-org/alpha#84'], 'show-only', 't')
    // Glob without a wildcard is a substring match: anchor it, or "cli" matches "sdk-cli".
    const e = atom('entrypoint', ['^cli$'], 'show-only', 'e')
    const g = { id: 'g', name: 'g', enabled: true, type: 'group', match: 'all', childIds: ['t', 'e'] }
    const state: State = { nodes: { t, e, g }, activeId: 'g' }
    expect(applyActiveFilterToItem(S1, state)).toBe(true)
    expect(applyActiveFilterToItem({ ...S1, entrypoint: 'sdk-cli' }, state)).toBe(false)
  })
})
