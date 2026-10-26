import { describe, expect, it } from 'vitest'
import {
  activeFilterCount,
  buildFolders,
  displayOrder,
  displayPath,
  entryMatches,
  groupingApplies,
  isRecent,
  matchesProjectFilter,
  pathFolderOf,
  projectEntries,
  recentLabel,
  recentOptions,
} from './conversationListView'
import type { ConversationListItem, SortField, SortOrder } from './types'

function conv(over: Partial<ConversationListItem> & { uuid: string }): ConversationListItem {
  return {
    name: over.uuid,
    model: 'm',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    is_starred: false,
    message_count: 1,
    has_branches: false,
    source: 'CLAUDE_CODE',
    project_path: '/p/x',
    project_name: 'x',
    ...over,
  }
}

// Three folders. Except for the Project sort, the folder-name order
// (alpha < beta < gamma) differs from the expected order in every case.
// The input order (beta, gamma, alpha by first row) matches only one case.
const ROWS = [
  conv({ uuid: 'b1', project_name: 'beta', name: 'Mango', created_at: '2026-01-05T00:00:00Z', updated_at: '2026-01-02T00:00:00Z' }),
  conv({ uuid: 'g1', project_name: 'gamma', name: 'apple', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-09T00:00:00Z' }),
  conv({ uuid: 'a1', project_name: 'alpha', name: 'zebra', created_at: '2026-01-03T00:00:00Z', updated_at: '2026-01-04T00:00:00Z' }),
  conv({ uuid: 'a2', project_name: 'alpha', name: 'Kiwi', created_at: '2026-01-02T00:00:00Z', updated_at: '2026-01-05T00:00:00Z' }),
  conv({ uuid: 'g2', project_name: 'gamma', name: 'pear', created_at: '2026-01-04T00:00:00Z', updated_at: '2026-01-03T00:00:00Z' }),
]

function folderNames(field: SortField, order: SortOrder): string[] {
  return buildFolders(ROWS, field, order).map((f) => f.name)
}

describe('buildFolders: a folder takes the value of its first session in the chosen order', () => {
  it.each([
    // updated_at: alpha max 01-05 min 01-04; beta 01-02; gamma max 01-09 min 01-03
    ['updated_at', 'desc', ['gamma', 'alpha', 'beta']],
    ['updated_at', 'asc', ['beta', 'gamma', 'alpha']],
    // created_at: alpha max 01-03 min 01-02; beta 01-05; gamma max 01-04 min 01-01
    ['created_at', 'desc', ['beta', 'gamma', 'alpha']],
    ['created_at', 'asc', ['gamma', 'alpha', 'beta']],
    // name, ignoring case: alpha max zebra min kiwi; beta mango; gamma max pear min apple
    ['name', 'desc', ['alpha', 'gamma', 'beta']],
    ['name', 'asc', ['gamma', 'alpha', 'beta']],
    // project: the folder name
    ['project', 'desc', ['gamma', 'beta', 'alpha']],
    ['project', 'asc', ['alpha', 'beta', 'gamma']],
  ] as const)('%s %s', (field, order, expected) => {
    expect(folderNames(field, order)).toEqual(expected)
  })

  it('breaks a tie by folder name, A to Z', () => {
    const tie = [
      conv({ uuid: 'z', project_name: 'zed', updated_at: '2026-01-01T00:00:00Z' }),
      conv({ uuid: 'a', project_name: 'ann', updated_at: '2026-01-01T00:00:00Z' }),
    ]
    expect(buildFolders(tie, 'updated_at', 'desc').map((f) => f.name)).toEqual(['ann', 'zed'])
    expect(buildFolders(tie, 'updated_at', 'asc').map((f) => f.name)).toEqual(['ann', 'zed'])
  })

  it('puts starred rows first in a folder and keeps the input order otherwise', () => {
    const rows = [
      conv({ uuid: 'r1', project_name: 'p' }),
      conv({ uuid: 'r2', project_name: 'p', is_starred: true }),
      conv({ uuid: 'r3', project_name: 'p' }),
    ]
    expect(buildFolders(rows, 'updated_at', 'desc')[0].convs.map((c) => c.uuid)).toEqual(['r2', 'r1', 'r3'])
  })

  it('names folders like before: project, workspace, or untagged', () => {
    const rows = [
      conv({ uuid: 'c', project_name: null }),
      conv({ uuid: 'd', source: 'CLAUDE_AI', project_name: null, organization_name: 'Acme' }),
      conv({ uuid: 'e', source: 'CLAUDE_AI', project_name: null, organization_id: '1234567890ab' }),
      conv({ uuid: 'f', source: 'CLAUDE_AI', project_name: null }),
    ]
    expect(buildFolders(rows, 'project', 'asc').map((f) => f.name)).toEqual([
      'Acme',
      'Unknown Project',
      'Untagged (re-fetch to assign workspace)',
      'Workspace (12345678)',
    ])
  })
})

describe('displayOrder', () => {
  it('lists rows folder by folder and skips collapsed folders', () => {
    const folders = buildFolders(ROWS, 'project', 'asc')
    expect(displayOrder(folders, new Set())).toEqual(['a1', 'a2', 'b1', 'g1', 'g2'])
    expect(displayOrder(folders, new Set(['beta']))).toEqual(['a1', 'a2', 'g1', 'g2'])
  })
})

describe('matchesProjectFilter', () => {
  const foo = conv({ uuid: '1', project_path: '/b/foo', project_name: 'Foo' })
  const desktop = conv({ uuid: '2', source: 'CLAUDE_AI', project_path: null, project_name: null })

  it('passes everything without a filter', () => {
    expect(matchesProjectFilter(foo, { project: '', path: '' })).toBe(true)
    expect(matchesProjectFilter(desktop, { project: '', path: '' })).toBe(true)
  })
  it('matches a project by name, ignoring case', () => {
    expect(matchesProjectFilter(foo, { project: 'foo', path: '' })).toBe(true)
    expect(matchesProjectFilter(foo, { project: 'bar', path: '' })).toBe(false)
  })
  it('matches a path exactly', () => {
    expect(matchesProjectFilter(foo, { project: '', path: '/b/foo' })).toBe(true)
    expect(matchesProjectFilter(foo, { project: '', path: '/a/foo' })).toBe(false)
    expect(matchesProjectFilter(foo, { project: '', path: '/b/Foo' })).toBe(false)
  })
  it('never matches a row without a project', () => {
    expect(matchesProjectFilter(desktop, { project: 'foo', path: '' })).toBe(false)
    expect(matchesProjectFilter(desktop, { project: '', path: '/b/foo' })).toBe(false)
  })
})

describe('isRecent', () => {
  const now = Date.parse('2026-10-25T12:00:00Z')
  it('keeps a row updated within N hours, and drops an older one', () => {
    expect(isRecent(conv({ uuid: 'a', updated_at: '2026-10-24T13:00:00Z' }), now, 24)).toBe(true)
    expect(isRecent(conv({ uuid: 'b', updated_at: '2026-10-24T11:00:00Z' }), now, 24)).toBe(false)
    expect(isRecent(conv({ uuid: 'c', updated_at: '2026-10-24T11:00:00Z' }), now, 48)).toBe(true)
  })
})

describe('recentOptions', () => {
  const labels = (o: { label: string }[]) => o.map((x) => x.label)

  it('lists the 10 windows in order, with their hours', () => {
    const opts = recentOptions(true, 24)
    expect(labels(opts)).toEqual([
      '8 hours', '1 day', '3 days', '1 week', '2 weeks',
      '1 month', '3 months', '6 months', '1 year', 'Forever',
    ])
    expect(opts.map((o) => o.hours)).toEqual([8, 24, 72, 168, 336, 720, 2160, 4320, 8760, null])
  })

  it('adds a saved hours value that is not in the table, in its place by size', () => {
    const opts = recentOptions(true, 48)
    expect(labels(opts).slice(0, 4)).toEqual(['8 hours', '1 day', '48 hours', '3 days'])
    expect(recentOptions(true, 1).map((o) => o.label)[0]).toBe('1 hour')
    expect(recentOptions(true, 99999).map((o) => o.label).slice(-2)).toEqual(['99999 hours', 'Forever'])
  })

  it('adds nothing when the filter is off or the hours are in the table', () => {
    expect(recentOptions(false, 48)).toHaveLength(10)
    expect(recentOptions(true, 336)).toHaveLength(10)
  })

  it('names the selected window', () => {
    expect(recentLabel(true, 24)).toBe('1 day')
    expect(recentLabel(true, 48)).toBe('48 hours')
    expect(recentLabel(false, 24)).toBe('Forever')
  })
})

describe('groupingApplies', () => {
  it('groups only where the checkbox shows', () => {
    expect(groupingApplies(true, 'all')).toBe(true)
    expect(groupingApplies(true, 'CLAUDE_CODE')).toBe(true)
    expect(groupingApplies(true, 'CLAUDE_AI')).toBe(false)
    expect(groupingApplies(true, 'CLAUDE_COWORK')).toBe(false)
    expect(groupingApplies(false, 'all')).toBe(false)
  })
})

describe('displayPath', () => {
  it('shows a path inside home with ~', () => {
    expect(displayPath('/Users/me/Source/clientco/ai', '/Users/me')).toBe('~/Source/clientco/ai')
    expect(displayPath('/Users/me', '/Users/me')).toBe('~')
    expect(displayPath('/Users/me/', '/Users/me')).toBe('~')
  })
  it('leaves other paths as they are', () => {
    expect(displayPath('/Users/meow/x', '/Users/me')).toBe('/Users/meow/x')
    expect(displayPath('/opt/x', '/Users/me')).toBe('/opt/x')
    expect(displayPath('/opt/x', undefined)).toBe('/opt/x')
  })
  it('works with Windows paths', () => {
    expect(displayPath('C:\\Users\\me\\Source\\ai', 'C:\\Users\\me')).toBe('~\\Source\\ai')
  })
})

describe('projectEntries, path mode', () => {
  const home = '/Users/me'
  const rows = [
    conv({ uuid: '1', project_path: '/Users/me/Source/clientco/ai', project_name: 'ai', updated_at: '2026-01-03T00:00:00Z' }),
    conv({ uuid: '2', project_path: '/Users/me/Work/clientco/ai', project_name: 'ai', updated_at: '2026-01-09T00:00:00Z' }),
    conv({ uuid: '3', project_path: '/Users/me/Source/other/ai', project_name: 'ai', updated_at: '2026-01-05T00:00:00Z' }),
    conv({ uuid: '4', project_path: '/Users/me/Source/clientco/ai', project_name: 'ai', updated_at: '2026-01-04T00:00:00Z' }),
    conv({ uuid: '5', project_path: '/Users/me/foo', project_name: 'foo', updated_at: '2026-01-01T00:00:00Z' }),
    conv({ uuid: '6', project_path: '/opt/tools/x', project_name: 'x', updated_at: '2026-01-02T00:00:00Z' }),
    conv({ uuid: '7', source: 'CLAUDE_AI', project_path: null, project_name: null }),
  ]

  it('gives one entry per path, labels that grow until unique, newest first', () => {
    const e = projectEntries(rows, 'path', home)
    expect(e.map((x) => [x.id, x.label, x.detail])).toEqual([
      ['/Users/me/Work/clientco/ai', 'Work/clientco/ai', '~/Work/clientco/ai'],
      ['/Users/me/Source/other/ai', 'other/ai', '~/Source/other/ai'],
      ['/Users/me/Source/clientco/ai', 'Source/clientco/ai', '~/Source/clientco/ai'],
      ['/opt/tools/x', 'tools/x', '/opt/tools/x'],
      ['/Users/me/foo', '~/foo', '~/foo'],
    ])
  })

  it('keeps a path of 2 parts or fewer whole, also outside home', () => {
    const two = [conv({ uuid: 's', project_path: '/srv/web', project_name: 'web' })]
    expect(projectEntries(two, 'path', home).map((x) => x.label)).toEqual(['/srv/web'])
  })

  it('matches typing against the label and the path, ignoring case', () => {
    const e = projectEntries(rows, 'path', home)
    expect(e.filter((x) => entryMatches(x, 'work')).map((x) => x.label)).toEqual(['Work/clientco/ai'])
    expect(e.filter((x) => entryMatches(x, 'AI')).length).toBe(3)
    expect(e.filter((x) => entryMatches(x, '~/S')).length).toBe(2)
  })
})

describe('projectEntries, name mode', () => {
  it('joins folders with the same name, and says how many', () => {
    const rows = [
      conv({ uuid: '1', project_path: '/Users/me/Source/clientco/ai', project_name: 'ai', updated_at: '2026-01-03T00:00:00Z' }),
      conv({ uuid: '2', project_path: '/Users/me/Work/clientco/ai', project_name: 'ai', updated_at: '2026-01-09T00:00:00Z' }),
      conv({ uuid: '3', project_path: '/Users/me/foo', project_name: 'foo', updated_at: '2026-01-10T00:00:00Z' }),
    ]
    const e = projectEntries(rows, 'name', '/Users/me')
    expect(e.map((x) => [x.id, x.label, x.detail])).toEqual([
      ['foo', 'foo', '~/foo'],
      ['ai', 'ai', '2 folders'],
    ])
    expect(e[1].title).toBe('~/Source/clientco/ai\n~/Work/clientco/ai')
    expect(entryMatches(e[1], 'work')).toBe(true)
  })
})

describe('buildFolders, keyed by path', () => {
  it('makes one folder per path, named by its label, with the path as the title', () => {
    const rows = [
      conv({ uuid: 'a', project_path: '/Users/me/Source/clientco/ai', project_name: 'ai', updated_at: '2026-01-03T00:00:00Z' }),
      conv({ uuid: 'b', project_path: '/Users/me/Work/clientco/ai', project_name: 'ai', updated_at: '2026-01-09T00:00:00Z' }),
    ]
    const folders = buildFolders(rows, 'updated_at', 'desc', pathFolderOf(rows, '/Users/me'))
    expect(folders.map((f) => [f.key, f.name, f.title])).toEqual([
      ['/Users/me/Work/clientco/ai', 'Work/clientco/ai', '~/Work/clientco/ai'],
      ['/Users/me/Source/clientco/ai', 'Source/clientco/ai', '~/Source/clientco/ai'],
    ])
    expect(displayOrder(folders, new Set(['/Users/me/Work/clientco/ai']))).toEqual(['a'])
    // The Project sort uses the label.
    expect(buildFolders(rows, 'project', 'asc', pathFolderOf(rows, '/Users/me')).map((f) => f.name)).toEqual([
      'Source/clientco/ai',
      'Work/clientco/ai',
    ])
  })
})

describe('activeFilterCount', () => {
  const none = {
    namedFilter: false, source: 'all' as const, workspace: null, project: '', path: '',
    recentOnly: false, showEmpty: false, showArchived: false,
  }
  it('is 0 with the defaults', () => {
    expect(activeFilterCount(none)).toBe(0)
  })
  it('counts each filter that hides rows, one at a time', () => {
    expect(activeFilterCount({ ...none, namedFilter: true })).toBe(1)
    expect(activeFilterCount({ ...none, source: 'CLAUDE_CODE' })).toBe(1)
    expect(activeFilterCount({ ...none, workspace: 'org-1' })).toBe(1)
    expect(activeFilterCount({ ...none, project: 'ai' })).toBe(1)
    expect(activeFilterCount({ ...none, path: '/w/ai' })).toBe(1)
    expect(activeFilterCount({ ...none, recentOnly: true })).toBe(1)
    expect(activeFilterCount({ ...none, showEmpty: true })).toBe(1)
    expect(activeFilterCount({ ...none, showArchived: true })).toBe(1)
  })
  it('adds them up', () => {
    expect(activeFilterCount({ ...none, namedFilter: true, source: 'CLAUDE_AI', recentOnly: true, showEmpty: true })).toBe(4)
  })
})
