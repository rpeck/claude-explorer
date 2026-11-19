/**
 * N2 (PLANS/2026.11.18-codex-source.md §4.1, "Frontend"): the source
 * filter values and the list grouping rules read the registry flags.
 *
 *   - SOURCE_FILTERS (types.ts): "all" plus every registry id.
 *   - groupingApplies, folderName, pathFolderOf, projectEntries
 *     (conversationListView.ts): a source groups by project exactly when
 *     its descriptor has `has_projects`.
 *
 * Each `xfail` test changes the flags (or adds a source) and expects the
 * rule to follow. The plain tests pin today's behavior with the real
 * registry: N2 must not change it.
 */
import { describe, expect, it } from 'vitest'
import type { ConversationListItem, SourceFilter } from '@/lib/types'
import { FAKE, REAL, setRealRegistry, setRegistry, withFake, withFlags, xfail } from './fakeRegistry'

function row(overrides: Partial<Record<keyof ConversationListItem, unknown>>): ConversationListItem {
  return {
    uuid: 'u-1',
    name: 'A session',
    model: 'm',
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-01T10:00:00Z',
    is_starred: false,
    message_count: 2,
    has_branches: false,
    source: 'CLAUDE_AI',
    ...overrides,
  } as unknown as ConversationListItem
}

const asFilter = (v: string) => v as unknown as SourceFilter

describe('isSourceFilter reads the registry', () => {
  it('accepts "all" and every real source id', async () => {
    setRealRegistry()
    const { isSourceFilter } = await import('@/lib/types')
    expect(isSourceFilter('all')).toBe(true)
    for (const d of REAL) expect(isSourceFilter(d.id), d.id).toBe(true)
  })

  xfail('accepts a source id that only the registry names', async () => {
    setRegistry(withFake())
    const { isSourceFilter } = await import('@/lib/types')
    expect(isSourceFilter(FAKE.id)).toBe(true)
  })

  xfail('rejects a source id that the registry does not name', async () => {
    setRegistry(REAL.filter((d) => d.id !== 'CLAUDE_COWORK').map((d) => ({ ...d })))
    const { isSourceFilter } = await import('@/lib/types')
    expect(isSourceFilter('CLAUDE_COWORK')).toBe(false)
    expect(isSourceFilter('CLAUDE_CODE')).toBe(true)
  })
})

describe('groupingApplies reads has_projects', () => {
  it('real registry: groups under all, undefined, and Claude Code only', async () => {
    setRealRegistry()
    const { groupingApplies } = await import('@/lib/conversationListView')
    expect(groupingApplies(true, 'all')).toBe(true)
    expect(groupingApplies(true, undefined)).toBe(true)
    expect(groupingApplies(true, 'CLAUDE_CODE')).toBe(true)
    expect(groupingApplies(true, 'CLAUDE_AI')).toBe(false)
    expect(groupingApplies(true, 'CLAUDE_COWORK')).toBe(false)
    expect(groupingApplies(false, 'CLAUDE_CODE')).toBe(false)
    expect(groupingApplies(false, 'all')).toBe(false)
  })

  xfail('groups under a new source that has projects', async () => {
    setRegistry(withFake())
    const { groupingApplies } = await import('@/lib/conversationListView')
    expect(groupingApplies(true, asFilter(FAKE.id))).toBe(true)
  })

  it('does not group under a new source without projects', async () => {
    setRegistry([...REAL.map((d) => ({ ...d })), { ...FAKE, has_projects: false }])
    const { groupingApplies } = await import('@/lib/conversationListView')
    expect(groupingApplies(true, asFilter(FAKE.id))).toBe(false)
    expect(groupingApplies(true, 'CLAUDE_CODE')).toBe(true)
  })

  xfail('Claude Code without has_projects does not group', async () => {
    setRegistry(withFlags('CLAUDE_CODE', { has_projects: false }))
    const { groupingApplies } = await import('@/lib/conversationListView')
    expect(groupingApplies(true, 'CLAUDE_CODE')).toBe(false)
  })

  xfail('Cowork with has_projects groups', async () => {
    setRegistry(withFlags('CLAUDE_COWORK', { has_projects: true }))
    const { groupingApplies } = await import('@/lib/conversationListView')
    expect(groupingApplies(true, 'CLAUDE_COWORK')).toBe(true)
  })
})

describe('folderName reads has_projects', () => {
  it('real registry: Claude Code goes in its project, the others in their workspace', async () => {
    setRealRegistry()
    const { folderName } = await import('@/lib/conversationListView')
    expect(folderName(row({ source: 'CLAUDE_CODE', project_name: 'proj' }))).toBe('proj')
    expect(folderName(row({ source: 'CLAUDE_CODE' }))).toBe('Unknown Project')
    expect(
      folderName(row({ source: 'CLAUDE_COWORK', project_name: 'sess', organization_name: 'Org B' })),
    ).toBe('Org B')
    expect(folderName(row({ source: 'CLAUDE_AI', organization_name: 'Org A' }))).toBe('Org A')
  })

  xfail('a new source with projects goes in its project', async () => {
    setRegistry(withFake())
    const { folderName } = await import('@/lib/conversationListView')
    expect(folderName(row({ source: FAKE.id, project_name: 'fake-proj', organization_name: 'Org' }))).toBe(
      'fake-proj',
    )
    expect(folderName(row({ source: FAKE.id, organization_name: 'Org' }))).toBe('Unknown Project')
  })

  xfail('Claude Code without has_projects goes in its workspace', async () => {
    setRegistry(withFlags('CLAUDE_CODE', { has_projects: false }))
    const { folderName } = await import('@/lib/conversationListView')
    expect(folderName(row({ source: 'CLAUDE_CODE', project_name: 'proj', organization_name: 'Org C' }))).toBe(
      'Org C',
    )
  })
})

describe('pathFolderOf reads has_projects', () => {
  const HOME = '/home/u'

  it('real registry: a Claude Code row gets a path folder; a Cowork row does not', async () => {
    setRealRegistry()
    const { pathFolderOf } = await import('@/lib/conversationListView')
    const code = row({ source: 'CLAUDE_CODE', project_path: '/home/u/src/a/b', project_name: 'b' })
    const cowork = row({ source: 'CLAUDE_COWORK', project_path: '/sessions/vm1', organization_name: 'Org B' })
    const folderOf = pathFolderOf([code, cowork], HOME)
    expect(folderOf(code)).toEqual({ key: '/home/u/src/a/b', label: 'a/b', title: '~/src/a/b' })
    expect(folderOf(cowork)).toEqual({ key: 'Org B', label: 'Org B' })
  })

  xfail('a new source with projects gets a path folder with a short label', async () => {
    setRegistry(withFake())
    const { pathFolderOf } = await import('@/lib/conversationListView')
    const fake = row({ source: FAKE.id, project_path: '/home/u/work/x/y', project_name: 'y' })
    const folderOf = pathFolderOf([fake], HOME)
    expect(folderOf(fake)).toEqual({ key: '/home/u/work/x/y', label: 'x/y', title: '~/work/x/y' })
  })

  xfail('a new source with projects and no path goes in "Unknown Project"', async () => {
    setRegistry(withFake())
    const { pathFolderOf } = await import('@/lib/conversationListView')
    const fake = row({ source: FAKE.id, organization_name: 'Org' })
    expect(pathFolderOf([fake], HOME)(fake)).toEqual({ key: 'Unknown Project', label: 'Unknown Project' })
  })

  xfail('Claude Code without has_projects goes in its workspace folder', async () => {
    setRegistry(withFlags('CLAUDE_CODE', { has_projects: false }))
    const { pathFolderOf } = await import('@/lib/conversationListView')
    const code = row({ source: 'CLAUDE_CODE', project_path: '/home/u/src/a/b', organization_name: 'Org C' })
    expect(pathFolderOf([code], HOME)(code)).toEqual({ key: 'Org C', label: 'Org C' })
  })
})

describe('projectEntries reads has_projects', () => {
  const HOME = '/home/u'

  it('real registry: only Claude Code rows make picker entries', async () => {
    setRealRegistry()
    const { projectEntries } = await import('@/lib/conversationListView')
    const rows = [
      row({ uuid: 'c', source: 'CLAUDE_CODE', project_path: '/home/u/src/a/b', project_name: 'b' }),
      row({ uuid: 'w', source: 'CLAUDE_COWORK', project_path: '/sessions/vm1', project_name: 'vm1' }),
      row({ uuid: 'd', source: 'CLAUDE_AI', project_path: '/home/u/d', project_name: 'd' }),
    ]
    expect(projectEntries(rows, 'path', HOME).map((e) => e.id)).toEqual(['/home/u/src/a/b'])
    expect(projectEntries(rows, 'name', HOME).map((e) => e.id)).toEqual(['b'])
  })

  xfail('rows of a new source with projects make picker entries', async () => {
    setRegistry(withFake())
    const { projectEntries } = await import('@/lib/conversationListView')
    const rows = [
      row({ uuid: 'c', source: 'CLAUDE_CODE', project_path: '/home/u/src/a/b', project_name: 'b',
        updated_at: '2026-11-02T00:00:00Z' }),
      row({ uuid: 'f', source: FAKE.id, project_path: '/home/u/work/x/y', project_name: 'y',
        updated_at: '2026-11-01T00:00:00Z' }),
    ]
    expect(projectEntries(rows, 'path', HOME).map((e) => e.id)).toEqual(['/home/u/src/a/b', '/home/u/work/x/y'])
    expect(projectEntries(rows, 'name', HOME).map((e) => e.id)).toEqual(['b', 'y'])
  })

  xfail('Claude Code rows without has_projects make no picker entries', async () => {
    setRegistry(withFlags('CLAUDE_CODE', { has_projects: false }))
    const { projectEntries } = await import('@/lib/conversationListView')
    const rows = [row({ source: 'CLAUDE_CODE', project_path: '/home/u/src/a/b', project_name: 'b' })]
    expect(projectEntries(rows, 'path', HOME)).toEqual([])
  })
})
