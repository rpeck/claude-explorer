/**
 * UX.md → "Signed-in account (sidebar footer)", "Freshness".
 *
 * A failed identity lookup (offline start) is retried by the backend after
 * 60 s. The badge must follow within about a minute, not after the 5-minute
 * staleTime that suits a successful lookup.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'

import { server } from '../mocks/server'
import { useAccount } from '../../hooks/useAccount'

const OFFLINE = 'Could not reach claude.ai to look up the signed-in account.'

function account(error: string | null) {
  return {
    authenticated: true,
    account_uuid: error ? null : 'a-1',
    email: null,
    full_name: error ? null : 'Ada Lovelace',
    display_name: null,
    org_name: null,
    error,
  }
}

function Probe() {
  const { data } = useAccount()
  return <span data-testid="account">{data ? data.error ?? data.full_name : 'loading'}</span>
}

function wrap(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('useAccount freshness', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('refetches about a minute after a failed lookup, and stops once it succeeds', async () => {
    const responses = [account(OFFLINE), account(null)]
    let calls = 0
    server.use(
      http.get('/api/account', () => {
        const body = responses[Math.min(calls, responses.length - 1)]
        calls += 1
        return HttpResponse.json(body)
      }),
    )

    render(wrap(<Probe />))
    await advance(0)
    await vi.waitFor(() => expect(screen.getByTestId('account')).toHaveTextContent(OFFLINE))
    expect(calls).toBe(1)

    await advance(61_000)
    await vi.waitFor(() => expect(screen.getByTestId('account')).toHaveTextContent('Ada Lovelace'))
    expect(calls).toBe(2)

    // Success: back to the long staleTime, no more polling.
    await advance(3 * 61_000)
    expect(calls).toBe(2)
  })

  it('does not poll a successful lookup', async () => {
    let calls = 0
    server.use(
      http.get('/api/account', () => {
        calls += 1
        return HttpResponse.json(account(null))
      }),
    )

    render(wrap(<Probe />))
    await advance(0)
    await vi.waitFor(() => expect(screen.getByTestId('account')).toHaveTextContent('Ada Lovelace'))

    await advance(4 * 61_000)
    expect(calls).toBe(1)
  })
})
