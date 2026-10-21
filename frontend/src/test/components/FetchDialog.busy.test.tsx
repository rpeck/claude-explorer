/**
 * A second fetch gets HTTP 409 (PR #8 review, 2026-10-21).
 *
 * The Details modal's "Fetch New" opens an EventSource on /api/fetch/start.
 * While another fetch or Refresh runs, the server answers 409. An
 * EventSource cannot read that status: it only fires `onerror`. The dialog
 * used to show "Connection lost during fetch", which sent the user to
 * check their network. It now asks /api/fetch/status and names the cause.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { server } from '../mocks/server'
import { api } from '../../lib/api'
import { FetchDialog } from '../../components/fetch/FetchDialog'
import { FetchPipelineProvider } from '../../contexts/FetchPipelineContext'
import { FETCH_BUSY_MESSAGE } from '../../components/fetch/FetchToast'

class FakeEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  closed = false
  close() {
    this.closed = true
  }
}

function statusBody(fetchInProgress: boolean) {
  return {
    has_credentials: true,
    credentials_path: '/tmp/credentials.json',
    output_dir: '/tmp/conversations',
    existing_count: 3,
    credentials_age_days: 1,
    fetch_in_progress: fetchInProgress,
  }
}

async function openAndClickFetchNew(inProgressAfterStart: boolean): Promise<FakeEventSource> {
  let calls = 0
  server.use(
    http.get('/api/fetch/status', () => {
      calls += 1
      // First call: the dialog opens (idle). Later calls: after the failure.
      return HttpResponse.json(statusBody(calls > 1 && inProgressAfterStart))
    }),
  )
  const source = new FakeEventSource()
  vi.spyOn(api, 'startFetch').mockReturnValue(source as unknown as EventSource)

  render(
    <QueryClientProvider client={new QueryClient()}>
      <FetchPipelineProvider>
        <FetchDialog isOpen onClose={() => {}} />
      </FetchPipelineProvider>
    </QueryClientProvider>,
  )
  fireEvent.click(await screen.findByRole('button', { name: 'Fetch New' }))
  return source
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('FetchDialog when another fetch runs (409)', () => {
  it('says another fetch is running, not that the connection was lost', async () => {
    const source = await openAndClickFetchNew(true)

    source.onerror?.(new Event('error'))

    expect(await screen.findByText(FETCH_BUSY_MESSAGE)).toBeInTheDocument()
    expect(screen.queryByText(/Connection lost/)).not.toBeInTheDocument()
    expect(source.closed).toBe(true)
  })

  it('still says the connection was lost when no fetch is running', async () => {
    const source = await openAndClickFetchNew(false)

    source.onerror?.(new Event('error'))

    expect(await screen.findByText('Connection lost during fetch')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(FETCH_BUSY_MESSAGE)).not.toBeInTheDocument())
  })
})
