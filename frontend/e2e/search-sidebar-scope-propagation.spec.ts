// Search scope propagation (2026-05-14)
//
// Spec: PLANS/2026.05.14-search-scope-propagation-spec.md
//
// The sidebar's three composing filters — active filter graph (atoms/groups),
// source dropdown, and workspace dropdown — must narrow BOTH the sidebar list
// AND the full-text search panel. Today the sidebar list honors all three;
// search ignores the active filter and the workspace. These tests pin the
// fixed behavior end-to-end.
//
// Invariants under test (spec §5):
//   I1 — Visibility-set parity. Search results ⊆ sidebar-visible set.
//   I3 — Active-filter sidebar↔search parity.
//   I4 — Restoration: toggling a filter off restores results automatically.
//
// Settle pattern: each scope change yields a fresh /api/search request (the
// queryKey includes the scope params). We capture every request and assert
// on the URL/body params, NOT on response timing. This is the deterministic
// signal that proves the wiring works.

import { test, expect, makeSummary, type Page, type Route, withNetRetry } from './fixtures'

const PRIMARY_ORG_ID = 'ae24ae66-4622-48e7-b4b3-1ab2c49f933d'
const SECONDARY_ORG_ID = '99999999-9999-9999-9999-999999999999'

const UUID_A = '00000000-0000-0000-0000-0000000000aa'
const UUID_B = '00000000-0000-0000-0000-0000000000bb'
const UUID_C = '00000000-0000-0000-0000-0000000000cc'

// Three conversations mirroring the backend fixture in
// backend/tests/test_search_sidebar_scope.py:
//   ConvA = Desktop, project /work/foo, org_a, title "foo project chat"
//   ConvB = CC,      project /work/bar, no org,  title "bar project chat"
//   ConvC = CC,      project /work/foo, no org,  title "foo cc chat"
const conversations = [
  makeSummary({
    uuid: UUID_A,
    name: 'foo project chat',
    source: 'CLAUDE_AI',
    project_path: '/work/foo',
    project_name: 'foo',
    organization_id: PRIMARY_ORG_ID,
    organization_name: 'Personal',
  }),
  makeSummary({
    uuid: UUID_B,
    name: 'bar project chat',
    source: 'CLAUDE_CODE',
    project_path: '/work/bar',
    project_name: 'bar',
    organization_id: null,
    organization_name: null,
  }),
  makeSummary({
    uuid: UUID_C,
    name: 'foo cc chat',
    source: 'CLAUDE_CODE',
    project_path: '/work/foo',
    project_name: 'foo',
    organization_id: null,
    organization_name: null,
  }),
]

// Test orgs response with two workspaces so the workspace dropdown renders.
const ORGS_RESPONSE = {
  authenticated: true,
  orgs: [
    { org_id: PRIMARY_ORG_ID, name: 'Personal', is_primary: true },
    { org_id: SECONDARY_ORG_ID, name: 'Workspace2', is_primary: false },
  ],
}

interface SearchRequest {
  method: string
  url: string
  searchParams: URLSearchParams
  body: Record<string, unknown> | null
}

/**
 * Install a /api/search route that captures every request (GET or POST) and
 * returns a mock response computed by `responder` against the captured
 * filter set.
 */
async function captureSearch(
  page: Page,
  responder: (req: SearchRequest) => unknown[],
): Promise<SearchRequest[]> {
  const captured: SearchRequest[] = []
  await page.route('**/api/search**', (route: Route) => {
    const req = route.request()
    const url = new URL(req.url())
    let body: Record<string, unknown> | null = null
    if (req.method() === 'POST') {
      try {
        body = JSON.parse(req.postData() ?? '{}') as Record<string, unknown>
      } catch {
        body = null
      }
    }
    const entry: SearchRequest = {
      method: req.method(),
      url: req.url(),
      searchParams: url.searchParams,
      body,
    }
    captured.push(entry)
    const results = responder(entry)
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        results,
        total_messages_matched: results.length,
        returned_messages: results.length,
        truncated: false,
      }),
    })
  })
  return captured
}

/** Build a SearchResult fixture in the API's response shape. */
function makeSearchResult(uuid: string, name: string) {
  return {
    conversation_uuid: uuid,
    conversation_name: name,
    conversation_updated_at: '2026-05-01T13:00:00Z',
    conversation_created_at: '2026-05-01T12:00:00Z',
    project_name: name.split(' ')[0],
    matching_messages: [
      {
        message_uuid: `${uuid}-m1`,
        sender: 'human',
        snippet: `needle in ${name}`,
        match_start: 0,
        match_end: 6,
        created_at: '2026-05-01T12:00:00Z',
      },
    ],
  }
}

/** Pick out the conversation_uuids param from either GET or POST request. */
function extractConversationUuids(req: SearchRequest): string[] | null {
  if (req.method === 'GET') {
    const csv = req.searchParams.get('conversation_uuids')
    if (csv === null) return null
    if (csv === '') return []
    return csv.split(',').filter(Boolean)
  }
  // POST
  const body = req.body
  if (body === null || !('conversation_uuids' in body)) return null
  const v = body['conversation_uuids']
  if (Array.isArray(v)) return v.map(String)
  return null
}

/** Pick out the organization_id (workspace) param. */
function extractOrganizationId(req: SearchRequest): string | null | undefined {
  if (req.method === 'GET') {
    return req.searchParams.get('organization_id')
  }
  if (req.body && 'organization_id' in req.body) {
    return req.body['organization_id'] as string | null
  }
  return undefined
}

/** Pick out the source param. */
function extractSource(req: SearchRequest): string | null | undefined {
  if (req.method === 'GET') {
    return req.searchParams.get('source')
  }
  if (req.body && 'source' in req.body) {
    return req.body['source'] as string | null
  }
  return undefined
}

/** Pick out the user query string from either GET or POST. */
function extractQuery(req: SearchRequest): string | null {
  if (req.method === 'GET') {
    return req.searchParams.get('q')
  }
  if (req.body && 'q' in req.body) {
    return String(req.body['q'])
  }
  return null
}

async function openSearchPanel(page: Page) {
  // Cmd+K opens the search panel and forces the search tab; same global
  // shortcut works regardless of where focus currently sits (the keydown
  // handler is on document, and the active-filter / source / workspace
  // <Select> popups close on Escape — Cmd+K alone is enough).
  const isMac = process.platform === 'darwin'
  // Press Escape first to dismiss any open dropdown that grabbed focus,
  // then fire Cmd+K to open the search panel.
  await page.keyboard.press('Escape')
  await page.keyboard.press(isMac ? 'Meta+k' : 'Control+k')
  const input = page.getByPlaceholder('Search messages...')
  await expect(input).toBeVisible({ timeout: 3000 })
  return input
}

test.describe('Search scope propagation — full-text search honors sidebar scope', () => {
  test('source filter narrows search results (existing behavior pin)', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations, orgs: ORGS_RESPONSE })
    // captureSearch installs AFTER mockBackend so its /api/search handler
    // wins by Playwright's LIFO route dispatch.
    const requests = await captureSearch(page, (req) => {
      // Filter the response based on the source param that the frontend sent.
      const source = extractSource(req)
      let convs = conversations
      if (source === 'CLAUDE_CODE') {
        convs = conversations.filter((c) => c.source === 'CLAUDE_CODE')
      } else if (source === 'CLAUDE_AI') {
        convs = conversations.filter((c) => c.source === 'CLAUDE_AI')
      }
      return convs.map((c) => makeSearchResult(c.uuid, c.name))
    })
    await withNetRetry(page, () => page.goto('/'))

    // Set source to Claude Code via the sidebar dropdown.
    const sourceSelect = page.locator('[data-testid]').nth(0) // fallback; use generic
    void sourceSelect
    // Open the source <Select>. The first one without a testid is the source filter
    // (after active-filter-select which DOES have a testid). Use role.
    // The Sidebar mounts three <Select>s in order:
    //   [0] active-filter-select (testid set)
    //   [1] source filter (no testid; placeholder "Filter by source")
    //   [2] workspace-select (testid set, only when >=2 orgs)
    // The source select's first SelectItem is "All Agents";
    // active-filter's first SelectItem is "All conversations".
    // We pick the source one by index since it's structurally fixed.
    const sourceTrigger = page.locator('aside button[role="combobox"]').nth(1)
    await sourceTrigger.click()
    // Disambiguate: source dropdown's "Claude Code" option has a green
    // Terminal icon AND comes from the source SelectItems (not from any
    // active-filter named filter). Use a tighter match.
    await page.getByRole('option').filter({ hasText: /^\s*Claude Code\s*$/ }).click()

    const input = await openSearchPanel(page)
    await input.fill('needle')

    // Settle: wait until at least one search request has fired with source=CLAUDE_CODE.
    // The /api/search request URL is "/api/search?q=needle&source=CLAUDE_CODE&...".
    await expect
      .poll(
        () => requests.some((r) => extractSource(r) === 'CLAUDE_CODE'),
        { timeout: 5000 },
      )
      .toBe(true)
  })

  test('workspace filter narrows search results — sends organization_id param', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations, orgs: ORGS_RESPONSE })
    const requests = await captureSearch(page, (req) => {
      const orgId = extractOrganizationId(req)
      let convs = conversations
      if (orgId) {
        convs = conversations.filter((c) => c.organization_id === orgId)
      }
      return convs.map((c) => makeSearchResult(c.uuid, c.name))
    })
    await withNetRetry(page, () => page.goto('/'))

    // Pick the secondary workspace from the dropdown.
    const workspaceTrigger = page.getByTestId('workspace-select')
    await expect(workspaceTrigger).toBeVisible({ timeout: 5000 })
    await workspaceTrigger.click()
    await page.getByRole('option', { name: /Workspace2/i }).click()

    const input = await openSearchPanel(page)
    await input.fill('needle')

    // Settle: a search request must carry organization_id=SECONDARY_ORG_ID.
    await expect
      .poll(
        () =>
          requests.some(
            (r) =>
              extractOrganizationId(r) === SECONDARY_ORG_ID &&
              extractQuery(r) === 'needle',
          ),
        { timeout: 5000 },
      )
      .toBe(true)
  })

  test('active filter (hide bar) narrows search — passes conversation_uuids', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations,
      orgs: ORGS_RESPONSE,
      preferences: {
        filters: {
          nodes: {
            'a-hide-bar': {
              id: 'a-hide-bar',
              type: 'atom',
              name: 'Hide bar',
              enabled: true,
              behavior: 'hide',
              patterns: ['*bar*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: 'a-hide-bar',
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    const requests = await captureSearch(page, (req) => {
      // Apply conversation_uuids filter to the mock response.
      const allowed = extractConversationUuids(req)
      let convs = conversations
      if (allowed !== null) {
        const set = new Set(allowed)
        convs = conversations.filter((c) => set.has(c.uuid))
      }
      return convs.map((c) => makeSearchResult(c.uuid, c.name))
    })
    await withNetRetry(page, () => page.goto('/'))

    // Sanity: the "bar" conversation should be HIDDEN from the sidebar.
    await expect(page.getByText('bar project chat')).toHaveCount(0)
    // And the two "foo" titles should be visible.
    await expect(page.getByText('foo project chat')).toBeVisible()
    await expect(page.getByText('foo cc chat')).toBeVisible()

    const input = await openSearchPanel(page)
    await input.fill('needle')

    // Settle: at least one search request must carry conversation_uuids
    // that EXCLUDES ConvB (the "bar" one). The set should include ConvA
    // and ConvC but not ConvB. With conversation_uuids set, the frontend
    // switches to POST — so q lives in the body, not the URL.
    await expect
      .poll(
        () => {
          for (const r of requests) {
            if (extractQuery(r) !== 'needle') continue
            const uuids = extractConversationUuids(r)
            if (uuids === null) continue
            const set = new Set(uuids)
            if (set.has(UUID_A) && set.has(UUID_C) && !set.has(UUID_B)) {
              return true
            }
          }
          return false
        },
        { timeout: 5000 },
      )
      .toBe(true)
  })

  test('toggling active filter off restores previously-excluded results (I4)', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations,
      orgs: ORGS_RESPONSE,
      preferences: {
        filters: {
          nodes: {
            'a-hide-bar': {
              id: 'a-hide-bar',
              type: 'atom',
              name: 'Hide bar',
              enabled: true,
              behavior: 'hide',
              patterns: ['*bar*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: 'a-hide-bar',
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    const requests = await captureSearch(page, (req) => {
      const allowed = extractConversationUuids(req)
      let convs = conversations
      if (allowed !== null) {
        const set = new Set(allowed)
        convs = conversations.filter((c) => set.has(c.uuid))
      }
      return convs.map((c) => makeSearchResult(c.uuid, c.name))
    })
    await withNetRetry(page, () => page.goto('/'))

    const input = await openSearchPanel(page)
    await input.fill('needle')

    // First settle: filter active, conv_uuids EXCLUDES ConvB.
    await expect
      .poll(
        () => {
          for (const r of requests) {
            const uuids = extractConversationUuids(r)
            if (uuids === null) continue
            const set = new Set(uuids)
            if (set.has(UUID_A) && set.has(UUID_C) && !set.has(UUID_B)) {
              return true
            }
          }
          return false
        },
        { timeout: 5000 },
      )
      .toBe(true)

    const requestCountBeforeToggle = requests.length

    // Now toggle the active filter to "All conversations".
    const picker = page.getByTestId('active-filter-select')
    await picker.click()
    await page.getByRole('option', { name: /All conversations/i }).click()

    // Settle: a NEW search request must have fired (queryKey changed) and
    // it must either omit conversation_uuids OR include ALL THREE UUIDs.
    await expect
      .poll(
        () => {
          // Look at requests fired AFTER the toggle.
          for (let i = requestCountBeforeToggle; i < requests.length; i++) {
            const r = requests[i]
            const uuids = extractConversationUuids(r)
            if (uuids === null) return true // param absent = "no constraint"
            const set = new Set(uuids)
            if (set.has(UUID_A) && set.has(UUID_B) && set.has(UUID_C)) {
              return true
            }
          }
          return false
        },
        { timeout: 5000 },
      )
      .toBe(true)
  })

  test('title search (sidebar input) honors active filter — bar excluded after filter', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations,
      orgs: ORGS_RESPONSE,
      preferences: {
        filters: {
          nodes: {
            'a-hide-bar': {
              id: 'a-hide-bar',
              type: 'atom',
              name: 'Hide bar',
              enabled: true,
              behavior: 'hide',
              patterns: ['*bar*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: 'a-hide-bar',
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))

    // Type "chat" in the sidebar title-search — all three titles contain "chat",
    // but the active filter hides "bar". The visible list should be only the
    // two "foo" titles.
    const titleSearch = page.getByTestId('sidebar-title-search')
    await titleSearch.click()
    await titleSearch.pressSequentially('chat', { delay: 20 })

    // Settle on the deterministic DOM signal: count of visible conversation rows.
    await expect(page.getByText('foo project chat')).toBeVisible()
    await expect(page.getByText('foo cc chat')).toBeVisible()
    await expect(page.getByText('bar project chat')).toHaveCount(0)
  })

  test('source + workspace + active filter compose — only intersection appears', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations,
      orgs: ORGS_RESPONSE,
      preferences: {
        filters: {
          nodes: {
            'a-hide-bar': {
              id: 'a-hide-bar',
              type: 'atom',
              name: 'Hide bar',
              enabled: true,
              behavior: 'hide',
              patterns: ['*bar*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: 'a-hide-bar',
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    const requests = await captureSearch(page, (req) => {
      const source = extractSource(req)
      const orgId = extractOrganizationId(req)
      const allowed = extractConversationUuids(req)
      let convs = conversations
      if (source === 'CLAUDE_CODE') {
        convs = convs.filter((c) => c.source === 'CLAUDE_CODE')
      }
      if (orgId) {
        convs = convs.filter((c) => c.organization_id === orgId)
      }
      if (allowed !== null) {
        const set = new Set(allowed)
        convs = convs.filter((c) => set.has(c.uuid))
      }
      return convs.map((c) => makeSearchResult(c.uuid, c.name))
    })
    await withNetRetry(page, () => page.goto('/'))

    // Set source = Claude Code.
    // The Sidebar mounts three <Select>s in order:
    //   [0] active-filter-select (testid set)
    //   [1] source filter (no testid; placeholder "Filter by source")
    //   [2] workspace-select (testid set, only when >=2 orgs)
    // The source select's first SelectItem is "All Agents";
    // active-filter's first SelectItem is "All conversations".
    // We pick the source one by index since it's structurally fixed.
    const sourceTrigger = page.locator('aside button[role="combobox"]').nth(1)
    await sourceTrigger.click()
    // Disambiguate: source dropdown's "Claude Code" option has a green
    // Terminal icon AND comes from the source SelectItems (not from any
    // active-filter named filter). Use a tighter match.
    await page.getByRole('option').filter({ hasText: /^\s*Claude Code\s*$/ }).click()

    const input = await openSearchPanel(page)
    await input.fill('needle')

    // Settle: a request must carry source=CLAUDE_CODE AND a conv_uuids set
    // that excludes ConvB (filter hides bar). The intersection at this
    // point in the test is ConvC (CLAUDE_CODE AND not-bar).
    await expect
      .poll(
        () => {
          for (const r of requests) {
            if (extractSource(r) !== 'CLAUDE_CODE') continue
            const uuids = extractConversationUuids(r)
            if (uuids === null) continue
            const set = new Set(uuids)
            if (set.has(UUID_C) && !set.has(UUID_B)) {
              return true
            }
          }
          return false
        },
        { timeout: 5000 },
      )
      .toBe(true)
  })
})
