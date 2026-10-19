/**
 * PR #6 review (2026-10-19): the Quit dialog locks while a shutdown request
 * is pending (Esc, outside click and both buttons are disabled). A request
 * that never answers left the user behind a dialog they could not close.
 * `api.shutdownServer` must give up after a timeout, with a message the
 * dialog can show.
 */

import { describe, it, expect } from 'vitest'
import { delay, http, HttpResponse } from 'msw'
import { server } from '../mocks/server'

import { api, SHUTDOWN_TIMEOUT_MS } from '../../lib/api'

describe('api.shutdownServer timeout', () => {
  it('gives up when the server never answers', async () => {
    server.use(
      http.post('/api/shutdown', async () => {
        await delay('infinite')
        return HttpResponse.json({ status: 'stopping' }, { status: 202 })
      }),
    )
    const started = Date.now()
    await expect(api.shutdownServer(50)).rejects.toThrow(
      'The server did not answer within 1 second.',
    )
    expect(Date.now() - started).toBeLessThan(2000)
  })

  it('still resolves on a normal answer', async () => {
    server.use(
      http.post('/api/shutdown', () => HttpResponse.json({ status: 'stopping' }, { status: 202 })),
    )
    await expect(api.shutdownServer()).resolves.toBeUndefined()
  })

  it('waits 10 seconds by default', () => {
    expect(SHUTDOWN_TIMEOUT_MS).toBe(10_000)
  })
})
