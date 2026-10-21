/**
 * UX.md → "Signed-in account (sidebar footer)", "Freshness".
 *
 * `capture_done` refetches the signed-in account. Refresh can capture new
 * credentials and then fail the fetch; no `complete` event comes then, and
 * the badge kept showing the previous account.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'

import { server } from '../mocks/server'
import { api } from '../../lib/api'
import { FetchPipelineProvider, useFetchPipeline } from '../../contexts/FetchPipelineContext'
import { AccountBadge } from '../../components/layout/AccountBadge'

class FakeEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  closed = false

  close(): void {
    this.closed = true
  }

  emit(data: object): void {
    if (this.closed) return
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

let source: FakeEventSource

function StartButton() {
  const { startRefresh } = useFetchPipeline()
  return (
    <button type="button" onClick={() => startRefresh()}>
      start
    </button>
  )
}

function wrap(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <FetchPipelineProvider>{children}</FetchPipelineProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  source = new FakeEventSource()
  vi.spyOn(api, 'startRefresh').mockImplementation(
    () => source as unknown as EventSource,
  )
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Refresh capture refreshes the account badge', () => {
  it('capture_done refetches the signed-in account even if the fetch then fails', async () => {
    let name = 'Old Account'
    let calls = 0
    server.use(
      http.get('/api/account', () => {
        calls += 1
        return HttpResponse.json({
          authenticated: true,
          account_uuid: 'a-1',
          email: null,
          full_name: name,
          display_name: null,
          org_name: null,
          error: null,
        })
      }),
    )
    const user = userEvent.setup()
    render(
      wrap(
        <>
          <AccountBadge />
          <StartButton />
        </>,
      ),
    )
    const region = screen.getByRole('region', { name: 'Signed-in account' })
    await within(region).findByText('Old Account')
    expect(calls).toBe(1)

    await user.click(screen.getByRole('button', { name: 'start' }))
    name = 'New Account'
    act(() => source.emit({ type: 'capture_start', message: 'Opening browser' }))
    act(() => source.emit({ type: 'capture_done', message: 'Captured' }))
    act(() => source.emit({ type: 'error', message: 'Fetch failed', kind: 'TRANSIENT' }))

    expect(await within(region).findByText('New Account')).toBeInTheDocument()
    expect(calls).toBe(2)
  })
})
