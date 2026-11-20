/**
 * N2 (PLANS/2026.11.18-codex-source.md §4.1, "Rules that N2 changes"):
 * the Sidebar reads the registry.
 *
 *   - The source filter items: "All Agents", then one item per
 *     registry source, in registry order, with the descriptor `label`
 *     and the SOURCE_PRESETS icon and color.
 *   - The group-by-project toggle: under "all", or a source with
 *     `has_projects`.
 *   - The Archived toggle: under "all", or a source with
 *     `has_archive_state`.
 *
 * The plain tests pin today's Sidebar with the real registry (no visual
 * change). The other tests change the flags or add a source.
 */
import { beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FAKE, serveApp, REAL, setRealRegistry, setRegistry, withFake, withFlags, type Descriptor } from './fakeRegistry'

const GROUP_TOGGLE = 'Group sessions by project'
const ARCHIVED_TOGGLE = 'show-archived-sessions-toggle'

beforeAll(() => {
  // Radix Select needs these to open in jsdom.
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.releasePointerCapture = () => {}
})

/**
 * Render the Sidebar with the filters open and `sourceFilter` set. The
 * source select shows only after the preferences load (the filters are
 * closed by default), and one response holds both values.
 */
async function renderSidebar(sourceFilter: string, list: Descriptor[] | null) {
  if (list) setRegistry(list)
  else setRealRegistry()
  const { AppProviders } = await import('./harness')
  const { Sidebar } = await import('@/components/layout/Sidebar')
  serveApp({ sourceFilter, sidebarFiltersOpen: true })
  render(
    <AppProviders>
      <Sidebar />
    </AppProviders>,
  )
  return screen.findByTestId('source-filter-select')
}

/** The label that the source select must show for `sourceFilter`. */
function labelOf(sourceFilter: string, list: readonly Descriptor[] = REAL): string | undefined {
  return sourceFilter === 'all' ? 'All Agents' : list.find((d) => d.id === sourceFilter)?.label
}

async function openSourceItems(trigger: HTMLElement) {
  await userEvent.setup().click(trigger)
  return screen.findAllByRole('option')
}

describe('Sidebar source filter items', () => {
  it('real registry: "All Agents", then each label in registry order', async () => {
    const trigger = await renderSidebar('all', null)
    const items = await openSourceItems(trigger)
    expect(items.map((o) => o.textContent)).toEqual(['All Agents', ...REAL.map((d) => d.label)])
  })

  it('real registry: each item has the icon and color of its SourceBadge preset', async () => {
    const trigger = await renderSidebar('all', null)
    const { SourceBadge } = await import('@/components/conversation/SourceBadge')
    const items = await openSourceItems(trigger)
    for (const d of REAL) {
      const item = items.find((o) => o.textContent === d.label)
      expect(item, d.id).toBeDefined()
      const itemIcon = item?.querySelector('svg')?.getAttribute('class') ?? ''
      const badge = render(
        <SourceBadge source={d.id as Parameters<typeof SourceBadge>[0]['source']} variant="row" />,
      )
      const badgeIcon = badge.container.querySelector('svg')?.getAttribute('class') ?? ''
      badge.unmount()
      const iconName = (cls: string) => cls.split(' ').find((c) => c.startsWith('lucide-') && c !== 'lucide-icon')
      const color = (cls: string) => cls.split(' ').find((c) => /^text-\w+-\d+$/.test(c))
      expect(iconName(itemIcon), d.id).toBe(iconName(badgeIcon))
      expect(color(itemIcon), d.id).toBe(color(badgeIcon))
    }
  })

  it('a new registry source gets its own item, last, with its label', async () => {
    const trigger = await renderSidebar('all', withFake())
    const items = await openSourceItems(trigger)
    expect(items.map((o) => o.textContent)).toEqual([
      'All Agents',
      ...REAL.map((d) => d.label),
      FAKE.label,
    ])
  })

  it('choosing the new source sets the filter to it', async () => {
    const trigger = await renderSidebar('all', withFake())
    const items = await openSourceItems(trigger)
    const fake = items.find((o) => o.textContent === FAKE.label)
    expect(fake).toBeDefined()
    await userEvent.setup().click(fake as HTMLElement)
    await waitFor(() => expect(trigger.textContent).toBe(FAKE.label))
  })

  it('the source select shows a saved filter of a new source', async () => {
    const trigger = await renderSidebar(FAKE.id, withFake())
    expect(trigger.textContent).toBe(FAKE.label)
  })
})

describe('Sidebar group-by-project toggle reads has_projects', () => {
  it.each([
    ['all', true],
    ['CLAUDE_CODE', true],
    ['CLAUDE_AI', false],
    ['CLAUDE_COWORK', false],
  ] as const)('real registry: under %s the toggle shows: %s', async (filter, shown) => {
    const trigger = await renderSidebar(filter, null)
    await waitFor(() => expect(trigger.textContent).toBe(labelOf(filter)))
    expect(screen.queryByLabelText(GROUP_TOGGLE) !== null).toBe(shown)
  })

  it('Cowork with has_projects shows the toggle', async () => {
    await renderSidebar('CLAUDE_COWORK', withFlags('CLAUDE_COWORK', { has_projects: true }))
    expect(screen.getByLabelText(GROUP_TOGGLE)).toBeInTheDocument()
  })

  it('Claude Code without has_projects hides the toggle', async () => {
    await renderSidebar('CLAUDE_CODE', withFlags('CLAUDE_CODE', { has_projects: false }))
    expect(screen.queryByLabelText(GROUP_TOGGLE)).toBeNull()
  })

  it('a new source without has_projects hides the toggle', async () => {
    await renderSidebar(FAKE.id, [...REAL.map((d) => ({ ...d })), { ...FAKE, has_projects: false }])
    expect(screen.queryByLabelText(GROUP_TOGGLE)).toBeNull()
  })
})

describe('Sidebar Archived toggle reads has_archive_state', () => {
  it.each([
    ['all', true],
    ['CLAUDE_COWORK', true],
    ['CLAUDE_AI', false],
    ['CLAUDE_CODE', false],
  ] as const)('real registry: under %s the toggle shows: %s', async (filter, shown) => {
    const trigger = await renderSidebar(filter, null)
    await waitFor(() => expect(trigger.textContent).toBe(labelOf(filter)))
    expect(screen.queryByTestId(ARCHIVED_TOGGLE) !== null).toBe(shown)
  })

  it('Claude Code with has_archive_state shows the toggle', async () => {
    await renderSidebar('CLAUDE_CODE', withFlags('CLAUDE_CODE', { has_archive_state: true }))
    expect(screen.getByTestId(ARCHIVED_TOGGLE)).toBeInTheDocument()
  })

  it('Cowork without has_archive_state hides the toggle', async () => {
    await renderSidebar('CLAUDE_COWORK', withFlags('CLAUDE_COWORK', { has_archive_state: false }))
    expect(screen.queryByTestId(ARCHIVED_TOGGLE)).toBeNull()
  })

  it('a new source with has_archive_state shows the toggle', async () => {
    await renderSidebar(FAKE.id, withFake())
    expect(screen.getByTestId(ARCHIVED_TOGGLE)).toBeInTheDocument()
  })
})
