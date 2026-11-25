/**
 * Issue #96: the API client reaches the agent transcript route, and every
 * export can name an agent (contract items 7 and 8).
 *
 * `pending` (`it.fails`): the feature is not built yet. Step 3 turns each
 * into a plain `it`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'

// Expected to fail until #96 is built. VITE_RUNXFAIL=1 runs them as plain tests (like pytest --runxfail).
const pending = import.meta.env.VITE_RUNXFAIL ? it : it.fails

const SESSION = '5a6e0000-0000-4000-8000-000000000096'
const AGENT = 'a5a0000000000096c'

// The new API surface does not exist yet; a loose view keeps tsc green.
type Loose = (...args: unknown[]) => Promise<unknown>
const looseApi = api as unknown as Record<string, Loose>

describe('api — agent transcripts (#96)', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () => new Response(JSON.stringify({ uuid: SESSION, agent_id: AGENT, messages: [] }), { status: 200 }),
    )
  })
  afterEach(() => fetchSpy.mockRestore())

  const url = () => String(fetchSpy.mock.calls[0][0])

  pending('getAgentTranscript calls the agent route with the session uuid and the agent id', async () => {
    const body = (await looseApi.getAgentTranscript(SESSION, AGENT)) as { agent_id: string }
    expect(url()).toMatch(new RegExp(`/api/conversations/${SESSION}/subagents/${AGENT}$`))
    expect(body.agent_id).toBe(AGENT)
  })

  pending('getAgentTranscript passes the abort signal', async () => {
    const controller = new AbortController()
    await looseApi.getAgentTranscript(SESSION, AGENT, controller.signal)
    const init = fetchSpy.mock.calls[0][1] as RequestInit | undefined
    expect(init?.signal).toBe(controller.signal)
  })

  pending.each([
    ['exportMarkdown', () => looseApi.exportMarkdown(SESSION, true, false, true, AGENT)],
    ['exportMarkdownBundle', () => looseApi.exportMarkdownBundle(SESSION, true, 'commonmark', false, true, AGENT)],
    ['exportPdf', () => looseApi.exportPdf(SESSION, true, undefined, false, true, AGENT)],
  ])('%s with a last agentId argument adds agent_id', async (_name, call) => {
    await call()
    expect(url()).toContain(`/conversations/${SESSION}/export/`)
    expect(url()).toContain(`&agent_id=${AGENT}`)
  })

  it('an export without agentId has no agent_id', async () => {
    await api.exportMarkdown(SESSION, true, false, true)
    expect(url()).not.toContain('agent_id')
  })
})
