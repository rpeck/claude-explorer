/**
 * R1 (issue #80 wave, user decision 2026-11-20): the source filter item
 * for every source reads "All Agents", not "All Conversations".
 *
 *   - The trigger shows "All Agents" when the filter is `all`.
 *   - The first item reads "All Agents".
 *   - "All Conversations" shows nowhere in the source filter.
 *   - The value stays `all`: choosing the item saves `sourceFilter: 'all'`.
 */
import { beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { serveApp, setRealRegistry } from './fakeRegistry'

beforeAll(() => {
  // Radix Select needs these to open in jsdom.
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.releasePointerCapture = () => {}
})

async function renderSidebar(sourceFilter: string) {
  setRealRegistry()
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

async function openSourceList(trigger: HTMLElement) {
  await userEvent.setup().click(trigger)
  return screen.findByRole('listbox')
}

describe('Source filter item "All Agents" (R1)', () => {
  it('the trigger reads "All Agents" when the filter is all', async () => {
    const trigger = await renderSidebar('all')
    await waitFor(() => expect(trigger.textContent).toBe('All Agents'))
  })

  it('the first item reads "All Agents"', async () => {
    const trigger = await renderSidebar('all')
    const list = await openSourceList(trigger)
    expect(within(list).getAllByRole('option')[0].textContent).toBe('All Agents')
  })

  it('"All Conversations" shows nowhere in the source filter', async () => {
    const trigger = await renderSidebar('all')
    await waitFor(() => expect(trigger.textContent).not.toBe(''))
    const list = await openSourceList(trigger)
    expect(trigger.textContent).not.toContain('All Conversations')
    expect(list.textContent).not.toContain('All Conversations')
    expect(within(list).getByRole('option', { name: 'All Agents' })).toBeInTheDocument()
  })

  it('choosing "All Agents" saves the value all', async () => {
    const saved: unknown[] = []
    const trigger = await renderSidebar('CLAUDE_CODE')
    // Record each saved sourceFilter, and answer like serveApp.
    server.use(
      http.patch('/api/preferences', async ({ request }) => {
        const body = (await request.json()) as { data?: Record<string, unknown> }
        if (body.data && 'sourceFilter' in body.data) saved.push(body.data.sourceFilter)
        return HttpResponse.json({ version: 1, data: { sidebarFiltersOpen: true, ...body.data } })
      }),
    )
    await waitFor(() => expect(trigger.textContent).toBe('Claude Code'))
    const list = await openSourceList(trigger)
    await userEvent.setup().click(within(list).getByRole('option', { name: 'All Agents' }))
    await waitFor(() => expect(trigger.textContent).toBe('All Agents'))
    await waitFor(() => expect(saved).toContain('all'))
  })
})
