/**
 * UX.md → "Signed-in account (sidebar footer)".
 *
 * Pins the corrupt-credentials state: `/api/account` answers 503 with a
 * `detail` reason. The badge shows "Account unavailable" and the reason,
 * instead of an empty row that hides the cause.
 */
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'

import { server } from '../mocks/server'
import { AccountBadge } from '../../components/layout/AccountBadge'

const CORRUPT_DETAIL =
  'Credentials file is corrupt: Expecting value: line 1 column 1 (char 0). ' +
  'Re-run capture to refresh credentials.'

function wrap(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('AccountBadge', () => {
  it('shows "Account unavailable" and the reason when credentials are corrupt (503)', async () => {
    let calls = 0
    server.use(
      http.get('/api/account', () => {
        calls += 1
        return HttpResponse.json({ detail: CORRUPT_DETAIL }, { status: 503 })
      }),
    )

    render(wrap(<AccountBadge />))

    const region = screen.getByRole('region', { name: 'Signed-in account' })
    const primary = await within(region).findByText('Account unavailable')
    expect(primary).toHaveAttribute('title', 'Account unavailable')
    const secondary = within(region).getByText(CORRUPT_DETAIL)
    expect(secondary).toHaveAttribute('title', CORRUPT_DETAIL)
    // A corrupt file does not fix itself: one request, no retry storm.
    expect(calls).toBe(1)
  })

  it('keeps the row empty for any other endpoint error', async () => {
    let calls = 0
    server.use(
      http.get('/api/account', () => {
        calls += 1
        return new HttpResponse('boom', { status: 500 })
      }),
    )

    render(wrap(<AccountBadge />))

    const region = screen.getByRole('region', { name: 'Signed-in account' })
    await expect.poll(() => calls).toBeGreaterThan(0)
    expect(region).toHaveTextContent('')
    expect(within(region).queryByText('Account unavailable')).toBeNull()
  })

  it('shows the account when the lookup succeeds', async () => {
    server.use(
      http.get('/api/account', () =>
        HttpResponse.json({
          authenticated: true,
          account_uuid: 'a-1',
          email: 'ada@example.com',
          full_name: 'Ada Lovelace',
          display_name: null,
          org_name: 'Engines',
          error: null,
        }),
      ),
    )

    render(wrap(<AccountBadge />))

    const region = screen.getByRole('region', { name: 'Signed-in account' })
    expect(await within(region).findByText('Ada Lovelace')).toBeInTheDocument()
    expect(within(region).getByText('ada@example.com · Engines')).toBeInTheDocument()
  })
})
