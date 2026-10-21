/**
 * The Refresh pipeline's EventSource lifecycle. Contract: UX.md → "Quit
 * (power button)".
 *
 *   - Unmounting the provider (Quit replaces the whole app) closes a running
 *     Refresh stream. An open stream made the server's graceful shutdown
 *     wait for it, and its later onerror ran setState and an error toast
 *     in an unmounted tree.
 *   - Quit closes the stream before it marks the server stopped.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'

import { api } from '../../lib/api'
import * as errorToastModule from '../../lib/errorToast'
import * as serverStopped from '../../lib/serverStopped'
import { FetchPipelineProvider, useFetchPipeline } from '../../contexts/FetchPipelineContext'
import { QuitButton } from '../../components/layout/QuitButton'

/** Mimics a real EventSource: a closed source dispatches nothing. */
class FakeEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  closed = false
  closedWhileServerStopped: boolean | null = null

  close(): void {
    if (this.closed) return
    this.closed = true
    this.closedWhileServerStopped = serverStopped.isServerStopped()
  }

  emit(data: object): void {
    if (this.closed) return
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }

  fail(): void {
    if (this.closed) return
    this.onerror?.(new Event('error'))
  }
}

let source: FakeEventSource

function StartButton() {
  const { startRefresh, isRunning } = useFetchPipeline()
  return (
    <button type="button" onClick={() => startRefresh()}>
      {isRunning ? 'running' : 'start'}
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

describe('FetchPipelineProvider stream lifecycle', () => {
  it('closes a running Refresh stream on unmount, and its errors do not reach the dead tree', async () => {
    const toastSpy = vi.spyOn(errorToastModule, 'errorToast')
    const user = userEvent.setup()
    const view = render(wrap(<StartButton />))

    await user.click(screen.getByRole('button', { name: 'start' }))
    expect(screen.getByRole('button', { name: 'running' })).toBeInTheDocument()
    expect(source.closed).toBe(false)

    view.unmount()

    expect(source.closed).toBe(true)
    source.fail()
    expect(toastSpy).not.toHaveBeenCalled()
  })
})

describe('QuitButton with a running Refresh', () => {
  it('closes the Refresh stream before it marks the server stopped', async () => {
    vi.spyOn(api, 'shutdownServer').mockResolvedValue(undefined)
    const markSpy = vi.spyOn(serverStopped, 'markServerStopped')
    const user = userEvent.setup()
    render(
      wrap(
        <>
          <StartButton />
          <QuitButton />
        </>,
      ),
    )

    await user.click(screen.getByRole('button', { name: 'start' }))
    await user.click(screen.getByRole('button', { name: 'Quit Claude Explorer' }))
    const dialog = await screen.findByRole('dialog', { name: 'Quit Claude Explorer?' })
    await user.click(within(dialog).getByRole('button', { name: 'Quit' }))

    await waitFor(() => expect(markSpy).toHaveBeenCalled())
    expect(source.closed).toBe(true)
    expect(source.closedWhileServerStopped).toBe(false)
  })
})
