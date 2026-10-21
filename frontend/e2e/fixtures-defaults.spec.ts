import { test, expect, withNetRetry, expectNetworkError } from './fixtures'

/**
 * M1 of the mock-data conversion plan
 * (PLANS/2026.05.06-mock-data-conversion.md).
 *
 * `mockBackend()` must default-mock EVERY backend route the app calls.
 * Today only /api/config, /api/orgs, /api/conversations** are covered;
 * the rest leak to whichever backend is running. Post-P3 (server-side
 * preferences) the leakage means tests hit the user's real
 * ~/.claude-explorer/preferences.json, producing ~30 flakes under
 * PLAYWRIGHT_LIVE_DATA=1.
 *
 * RED-first: each `it()` below calls a route via page.evaluate(fetch...)
 * and asserts the documented default. The whole file must FAIL before
 * the implementation lands, then GREEN once mockBackend is extended.
 *
 * Why we hit the routes via page.evaluate(fetch(...)) rather than
 * page.goto(...): the goal here is to prove that page.route() handlers
 * registered by mockBackend intercept the network — independent of any
 * particular UI surface area. The smoke test is intentionally
 * UI-agnostic so it doesn't drift if components rearrange.
 */

interface FetchResult {
  status: number
  contentType: string | null
  body: string
}

async function fetchOnPage(
  page: import('@playwright/test').Page,
  url: string,
  init?: { method?: string; body?: string; headers?: Record<string, string> },
): Promise<FetchResult> {
  return page.evaluate(
    async ({ u, i }) => {
      const r = await fetch(u, i as RequestInit | undefined)
      const text = await r.text()
      return {
        status: r.status,
        contentType: r.headers.get('content-type'),
        body: text,
      }
    },
    { u: url, i: init },
  )
}

test.describe('mockBackend default routes (M1)', () => {
  test.beforeEach(async ({ page }) => {
    // Need a real document context for fetch() relative URLs to resolve
    // ('/api/...' resolves against the page origin). We use about:blank
    // by routing a fake origin: navigate to data:text/html,<empty> won't
    // accept relative fetches in some Chromium builds, so use an inline
    // http page served via page.route('**/__bootstrap__', ...).
    await page.route('**/__bootstrap__', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: '<!doctype html><html><body>boot</body></html>',
      }),
    )
    await withNetRetry(page, () => page.goto('/__bootstrap__'))
  })

  test('GET /api/config returns AppConfig JSON without conversation_count', async ({ page, mockBackend }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/config')
    expect(r.status).toBe(200)
    expect(r.contentType).toMatch(/application\/json/)
    const body = JSON.parse(r.body)
    expect(body).toHaveProperty('data_dir')
    expect(body).not.toHaveProperty('conversation_count')
  })

  test('GET /api/config/stats returns AppConfigStats JSON with conversation_count', async ({ page, mockBackend }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/config/stats')
    expect(r.status).toBe(200)
    const body = JSON.parse(r.body)
    expect(body).toHaveProperty('data_dir')
    expect(body).toHaveProperty('conversation_count')
  })

  test('GET /api/orgs returns OrgsResponse', async ({ page, mockBackend }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/orgs')
    expect(r.status).toBe(200)
    const body = JSON.parse(r.body)
    expect(body).toHaveProperty('authenticated', true)
    expect(Array.isArray(body.orgs)).toBe(true)
  })

  test('GET /api/conversations (list) returns []', async ({ page, mockBackend }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/conversations')
    expect(r.status).toBe(200)
    expect(JSON.parse(r.body)).toEqual([])
  })

  test('GET /api/preferences returns empty envelope by default', async ({ page, mockBackend }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/preferences')
    expect(r.status).toBe(200)
    expect(r.contentType).toMatch(/application\/json/)
    expect(JSON.parse(r.body)).toEqual({ version: 1, data: {} })
  })

  test('PATCH /api/preferences echoes merged data and persists within the test', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})

    // Initial GET — empty.
    const g0 = await fetchOnPage(page, '/api/preferences')
    expect(JSON.parse(g0.body)).toEqual({ version: 1, data: {} })

    // PATCH — set theme.
    const p1 = await fetchOnPage(page, '/api/preferences', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: { theme: 'dark' } }),
    })
    expect(p1.status).toBe(200)
    expect(JSON.parse(p1.body)).toEqual({ version: 1, data: { theme: 'dark' } })

    // GET — reflects the patch.
    const g1 = await fetchOnPage(page, '/api/preferences')
    expect(JSON.parse(g1.body)).toEqual({ version: 1, data: { theme: 'dark' } })

    // Second PATCH — adds another key, prior key survives.
    const p2 = await fetchOnPage(page, '/api/preferences', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: { fontSize: 14 } }),
    })
    expect(JSON.parse(p2.body)).toEqual({
      version: 1,
      data: { theme: 'dark', fontSize: 14 },
    })
  })

  test('preferences seeding option pre-populates initial state', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ preferences: { sidebarWidth: 320 } })
    const r = await fetchOnPage(page, '/api/preferences')
    expect(JSON.parse(r.body)).toEqual({
      version: 1,
      data: { sidebarWidth: 320 },
    })
  })

  test('GET /api/search returns the empty SearchResponse envelope', async ({
    page,
    mockBackend,
  }) => {
    // 2026-05-16 (plan §B): the wire format is now an envelope, not a
    // bare list. The default mock returns an empty SearchResponse so
    // tests that don't override /api/search still hit the new shape.
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/search?q=anything')
    expect(r.status).toBe(200)
    expect(JSON.parse(r.body)).toEqual({
      results: [],
      total_messages_matched: 0,
      returned_messages: 0,
      truncated: false,
    })
  })

  test('GET /api/cc-image returns 404 by default', async ({ page, mockBackend, consoleAssertions }) => {
    // §5.15: the test deliberately fetches /api/cc-image with a path
    // that the mockBackend default 404s. The Chromium fetch fails at
    // the network layer and logs `Failed to load resource: ... 404`;
    // that noise is expected here.
    expectNetworkError(consoleAssertions, 404)
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/cc-image?path=/missing.png')
    expect(r.status).toBe(404)
  })

  test('GET /api/bookmarks returns the list envelope with empty bookmarks', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/bookmarks')
    expect(r.status).toBe(200)
    // listBookmarks() in src/lib/api.ts unwraps `body.bookmarks`,
    // so the default must return the envelope shape.
    expect(JSON.parse(r.body)).toEqual({ bookmarks: [] })
  })

  test('POST /api/bookmarks echoes a synthesized created bookmark', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/bookmarks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_uuid: 'c1', message_uuid: 'm1', note: 'n', snippet: 's' }),
    })
    expect(r.status).toBe(201)
    const body = JSON.parse(r.body)
    expect(body).toHaveProperty('id')
    expect(body).toHaveProperty('created_at')
    expect(body).toMatchObject({ conversation_uuid: 'c1', message_uuid: 'm1', note: 'n', snippet: 's' })
  })

  test('DELETE /api/bookmarks/<id> returns 204', async ({ page, mockBackend }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/bookmarks/abc-123', { method: 'DELETE' })
    expect(r.status).toBe(204)
  })

  test('GET /api/{org}/files/{uuid}/preview returns 404 by default', async ({
    page,
    mockBackend,
    consoleAssertions,
  }) => {
    expectNetworkError(consoleAssertions, 404)
    await mockBackend({})
    const r = await fetchOnPage(
      page,
      '/api/ae24ae66-4622-48e7-b4b3-1ab2c49f933d/files/file-1/preview',
    )
    expect(r.status).toBe(404)
  })

  test('GET /api/attachments/<conv>/<file>/<variant> returns 404 by default', async ({
    page,
    mockBackend,
    consoleAssertions,
  }) => {
    expectNetworkError(consoleAssertions, 404)
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/attachments/c1/f1/preview')
    expect(r.status).toBe(404)
  })

  test('GET /api/fetch/status returns a no-credentials envelope', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/fetch/status')
    expect(r.status).toBe(200)
    const body = JSON.parse(r.body)
    expect(body).toHaveProperty('has_credentials')
    expect(body).toHaveProperty('existing_count')
  })

  test('GET /api/fetch/start emits a minimal SSE start+complete stream', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/fetch/start')
    expect(r.status).toBe(200)
    expect(r.contentType).toMatch(/text\/event-stream/)
    // Should contain at least an event: start and event: complete frame.
    expect(r.body).toMatch(/event: start/)
    expect(r.body).toMatch(/event: complete/)
  })

  test('GET /api/fetch/refresh emits a minimal SSE start+complete stream', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/fetch/refresh')
    expect(r.status).toBe(200)
    expect(r.contentType).toMatch(/text\/event-stream/)
    expect(r.body).toMatch(/event: complete/)
  })

  test('POST /api/fetch/conversation/<uuid> returns success envelope', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/fetch/conversation/c1', { method: 'POST' })
    expect(r.status).toBe(200)
    const body = JSON.parse(r.body)
    expect(body).toMatchObject({ uuid: 'c1' })
    expect(body).toHaveProperty('status')
  })

  test('GET /api/conversations/<uuid>/export/markdown returns octet-stream', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/conversations/c1/export/markdown')
    expect(r.status).toBe(200)
    // Don't assert exact content-type since the app accepts any 200 body
    // for export streams; just confirm the route is intercepted.
    expect(r.contentType).not.toBeNull()
  })

  test('GET /api/export/all/markdown returns octet-stream', async ({ page, mockBackend }) => {
    await mockBackend({})
    const r = await fetchOnPage(page, '/api/export/all/markdown')
    expect(r.status).toBe(200)
    expect(r.contentType).not.toBeNull()
  })

  test('extraRoutes wins over defaults (LIFO override)', async ({ page, mockBackend }) => {
    await mockBackend({
      extraRoutes: async (p) => {
        await p.route('**/api/preferences', (route) => {
          route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ version: 1, data: { override: true } }),
          })
        })
      },
    })
    const r = await fetchOnPage(page, '/api/preferences')
    expect(JSON.parse(r.body)).toEqual({ version: 1, data: { override: true } })
  })
})
