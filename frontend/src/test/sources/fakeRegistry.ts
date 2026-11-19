/**
 * N2 test helpers: swap the generated source registry for a test list.
 *
 * The N2 rule is that the frontend reads each per-source rule from the
 * descriptor flags in `sources.generated.json` (or from SOURCE_PRESETS),
 * never from a hard-coded source id. The tests prove it: they change the
 * flags (or add a source) and then expect the UI to follow.
 *
 * Call `setRegistry(list)` first, then import the code under test with
 * `await import(...)`, so that the module graph sees the test list.
 */
import { vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import realDescriptors from '@/lib/sources.generated.json'

export interface Descriptor {
  id: string
  label: string
  short_label: string
  chronological: boolean
  has_projects: boolean
  has_archive_state: boolean
  has_organizations: boolean
  refetchable: boolean
  messages_change_in_place: boolean
}

export const REAL: readonly Descriptor[] = realDescriptors

/** A source that the frontend does not know, with every flag on. */
export const FAKE: Descriptor = {
  id: 'FAKE_SRC',
  label: 'Fake Source',
  short_label: 'Fake',
  chronological: true,
  has_projects: true,
  has_archive_state: true,
  has_organizations: false,
  refetchable: true,
  messages_change_in_place: false,
}

/** The real list with one descriptor's flags changed. */
export function withFlags(id: string, patch: Partial<Descriptor>): Descriptor[] {
  if (!REAL.some((d) => d.id === id)) throw new Error(`no real source ${id}`)
  return REAL.map((d) => (d.id === id ? { ...d, ...patch } : { ...d }))
}

/** The real list plus FAKE at the end (registry order: new sources go last). */
export function withFake(): Descriptor[] {
  return [...REAL.map((d) => ({ ...d })), { ...FAKE }]
}

export function setRegistry(list: Descriptor[]): void {
  vi.resetModules()
  vi.doMock('@/lib/sources.generated.json', () => ({ default: list }))
}

export function setRealRegistry(): void {
  vi.resetModules()
  vi.doUnmock('@/lib/sources.generated.json')
}

/** Serve the preferences and the side requests that the Sidebar makes. */
export function serveApp(prefs: Record<string, unknown>): void {
  // usePreferences reads localStorage before the server. On Node 20 jsdom's
  // localStorage works (on Node 22+ Node's own global hides it), so a value
  // that an earlier test saved would win over `prefs`.
  try {
    window.localStorage?.clear()
  } catch {
    // No usable localStorage: nothing to clear.
  }
  const data = { ...prefs }
  server.use(
    http.get('/api/preferences', () => HttpResponse.json({ version: 1, data })),
    // Merge like the backend: usePreferences puts the PATCH answer in its cache.
    http.patch('/api/preferences', async ({ request }) => {
      const body = (await request.json()) as { data?: Record<string, unknown> }
      Object.assign(data, body.data)
      return HttpResponse.json({ version: 1, data })
    }),
    http.get('/api/orgs', () => HttpResponse.json([])),
    http.get('/api/bookmarks', () => HttpResponse.json([])),
    http.get('/api/account', () => HttpResponse.json({ authenticated: false })),
  )
}
