import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'
import type { SearchResult } from '../src/lib/types'

/**
 * Original bug (2026-05-11): clicking a sidebar snippet on a tool-only
 * message left the conv pane focus ring on the wrong message. Sidebar
 * and conv pane DISAGREED. Root cause: `messageHasVisibleContent`
 * filters tool-only messages out of `useKeyboardNavigation.messages`,
 * so `findIndex(m => m.uuid === match.messageUuid)` returns -1, and
 * `setSelectedMessageIndex` is gated by that index.
 *
 * Architectural fix (the one we shipped): the SERVER honors the user's
 * `showToolCalls` preference. When `showToolCalls=false`, the search
 * endpoint excludes hits in tool_use / tool_result / thinking blocks —
 * so the sidebar can never show a snippet whose owning message is
 * hidden in the conv pane. Mismatch impossible by construction.
 *
 * This spec pins the new contract with TWO tests + a bidirectional
 * inversion test:
 *
 *  - **Test A** — original intent. With `showToolCalls=ON`, a search
 *    that hits a tool-only message produces a clickable sidebar snippet
 *    AND the conv pane focus follows. (Validates the mismatch is gone
 *    in the case where the user has explicitly asked to see tools.)
 *
 *  - **Test B** — the new behavior. With `showToolCalls=OFF` (default),
 *    a query that ONLY matches a tool-only message returns zero results
 *    (the backend mock honors `include_tool_calls=false`). Then flipping
 *    the Tools toggle ON triggers a re-fetch and the snippet appears.
 *    The inversion proves both directions of the predicate.
 */

const TM = '00000000-0000-0000-0000-00000000d404'

const summary = makeSummary({
  uuid: TM,
  name: 'Search focus mismatch fixture (tool-only message)',
  message_count: 4,
})

// Three messages. tm-tool is tool-only — content is a tool_use block,
// no text body — so it's hidden when showToolCalls is false (the
// default).
const messages = [
  makeMessage({
    uuid: 'tm-0',
    sender: 'human',
    text: 'first message, plain text — needle here',
    content: [{ type: 'text', text: 'first message, plain text — needle here' }],
  }),
  makeMessage({
    uuid: 'tm-1',
    sender: 'assistant',
    text: 'plain assistant reply, no match',
    content: [{ type: 'text', text: 'plain assistant reply, no match' }],
    parent_message_uuid: 'tm-0',
  }),
  // Tool-only message: no text body. Hidden by messageHasVisibleContent
  // when showToolCalls=false. The search server (mocked here) only
  // emits a match for this message when include_tool_calls=true.
  makeMessage({
    uuid: 'tm-tool',
    sender: 'assistant',
    text: '',
    content: [
      {
        type: 'tool_use',
        id: 'tu-1',
        name: 'Bash',
        input: { command: 'echo needle in tool call' },
      },
    ] as unknown as never,
    parent_message_uuid: 'tm-1',
  }),
  makeMessage({
    uuid: 'tm-3',
    sender: 'human',
    text: 'follow up text, no match',
    content: [{ type: 'text', text: 'follow up text, no match' }],
    parent_message_uuid: 'tm-tool',
  }),
]

const detail = makeDetail(summary, messages)

// Match payloads for the two server modes. The mock /api/search route
// below switches between them based on the `include_tool_calls` URL
// param.
// Backend ``_sort_results`` sorts ``matching_messages`` by per-message
// ``created_at`` matching ``sort_order`` (so desc → newest first). The
// mock must match real backend behavior: 2026-05-14 Bug B fix removed
// the frontend's redundant ``matches.sort()`` re-sort in
// ``SearchPanelContext.flatMatches``, so the order returned here is
// now the order rendered in the sidebar.
const fullResults: SearchResult[] = [{
  conversation_uuid: TM,
  conversation_name: summary.name,
  conversation_updated_at: summary.updated_at,
  conversation_created_at: summary.created_at,
  project_name: null,
  matching_messages: [
    {
      message_uuid: 'tm-tool',
      sender: 'assistant',
      snippet: 'echo needle in tool call',
      match_start: 5,
      match_end: 11,
      // Newest → slot 0 in default updated_at desc sort.
      created_at: '2026-05-09T11:00:00Z',
    },
    {
      message_uuid: 'tm-0',
      sender: 'human',
      snippet: 'first message, plain text — needle here',
      match_start: 28,
      match_end: 34,
      created_at: '2026-05-09T10:00:00Z',
    },
  ],
}]

// With include_tool_calls=false the server returns ONLY the visible-
// message match. The tool-only hit is dropped server-side.
const filteredResults: SearchResult[] = [{
  conversation_uuid: TM,
  conversation_name: summary.name,
  conversation_updated_at: summary.updated_at,
  conversation_created_at: summary.created_at,
  project_name: null,
  matching_messages: [
    {
      message_uuid: 'tm-0',
      sender: 'human',
      snippet: 'first message, plain text — needle here',
      match_start: 28,
      match_end: 34,
      created_at: '2026-05-09T10:00:00Z',
    },
  ],
}]

/**
 * Stand up a search mock that mimics the real backend's include_tool_calls
 * filter: when the URL has `include_tool_calls=false`, return the
 * filtered (text-only) payload; otherwise return the full payload.
 *
 * Also records every observed value of the include_tool_calls param so
 * tests can assert the toggle actually reached the network layer.
 */
async function mountSearchRouteAware(page: import('@playwright/test').Page) {
  const seen: Array<{ q: string; includeToolCalls: 'true' | 'false' }> = []
  await page.route('**/api/search**', (route) => {
    const url = new URL(route.request().url())
    const q = url.searchParams.get('q') ?? ''
    // Backend default is true (no param sent on the URL means true).
    const param = url.searchParams.get('include_tool_calls')
    const includeToolCalls: 'true' | 'false' = param === 'false' ? 'false' : 'true'
    seen.push({ q, includeToolCalls })
    const body = includeToolCalls === 'false' ? filteredResults : fullResults
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        results: body,
        total_messages_matched: body.length,
        returned_messages: body.length,
        truncated: false,
      }),
    })
  })
  return seen
}

/** Read which sidebar snippet card is marked active. */
async function getActiveSidebarMatchText(page: import('@playwright/test').Page): Promise<string | null> {
  return await page.evaluate(() => {
    const cards = Array.from(
      document.querySelectorAll('button[data-result-card]'),
    ) as HTMLElement[]
    const active = cards.find((c) => c.className.includes('ring-blue-500'))
    return active?.innerText ?? null
  })
}

/** Read which conversation-pane message has the blue keyboard-selection ring. */
async function getRingedMessageUuid(page: import('@playwright/test').Page): Promise<string | null> {
  return await page.evaluate(() => {
    const ringed = document.querySelector('.ring-blue-500')
    if (!ringed) return null
    const owner = ringed.closest('[data-message-uuid]')
    return owner?.getAttribute('data-message-uuid') ?? null
  })
}

test.describe('Search — include_tool_calls filter (architectural fix 2026-05-11)', () => {
  // ─────────────────────────────────────────────────────────────────────
  // Test A — original intent. With Tools visible, a tool-only match's
  // snippet card is active in the sidebar AND the conv pane focus ring
  // lands on the same message.
  // ─────────────────────────────────────────────────────────────────────
  test('Test A: showToolCalls=ON → sidebar match on a tool message; focus follows', async ({
    page,
    mockBackend,
  }) => {
    // Pre-set the preference so SettingsContext reads showToolCalls=true
    // on initial mount (no race with the toggle click).
    await mockBackend({
      conversations: [summary],
      details: { [TM]: detail },
      preferences: { showToolCalls: true },
    })
    // Note: showToolCalls is currently ephemeral (not persisted via
    // usePreferences) — it lives in React useState. So seeding via the
    // preferences fixture doesn't help. Click the toggle in-page after
    // mount instead.
    const seen = await mountSearchRouteAware(page)
    await page.setViewportSize({ width: 1024, height: 1200 })

    await withNetRetry(page, () => page.goto(`/conversations/${TM}`))
    await expect(page.locator('[data-message-uuid="tm-0"]')).toBeVisible()

    // Enable Tools — the checkbox toggles showToolCalls in SettingsContext,
    // which the SearchPanelProvider threads into useSearch's queryKey,
    // which re-fires the network request with include_tool_calls=true.
    // 2026-05-25: Tools control is now a <input type="checkbox">.
    await page.getByTestId('header-show-tools-checkbox').check()
    // tm-tool should now be rendered (no longer filtered out).
    await expect(page.locator('[data-message-uuid="tm-tool"]')).toBeVisible()

    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await searchInput.fill('needle')
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({
      timeout: 10000,
    })

    // Sidebar-active and conv-pane focus must point to the same message.
    // The tm-tool snippet auto-promotes to slot 0 (it's the newest).
    await expect
      .poll(
        async () => {
          const sidebar = await getActiveSidebarMatchText(page)
          const ringed = await getRingedMessageUuid(page)
          return { sidebar, ringed }
        },
        {
          timeout: 5000,
          message:
            'Test A: sidebar-active card and conv-pane focus ring must agree when Tools are visible',
        },
      )
      .toEqual({
        sidebar: expect.stringContaining('echo needle in tool call'),
        ringed: 'tm-tool',
      })

    // The network call MUST have used include_tool_calls=true at least once.
    const lastForNeedle = [...seen]
      .reverse()
      .find((c) => c.q.includes('needle'))
    expect(lastForNeedle?.includeToolCalls).toBe('true')
  })

  // ─────────────────────────────────────────────────────────────────────
  // Test B — since 2026-10-21. With Tools hidden (the default), the search
  // still finds the tool-only message, and the conversation pane shows
  // ONLY that tool call, so sidebar and pane agree. Until 2026-10-21 the
  // page sent include_tool_calls=false and the hit never appeared; the
  // user asked for every hit to be navigable, with only the matching tool
  // call revealed (UX.md §4).
  // ─────────────────────────────────────────────────────────────────────
  test('Test B: showToolCalls=OFF → the tool-only match appears and reveals only that tool call', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: [summary], details: { [TM]: detail } })
    const seen = await mountSearchRouteAware(page)
    await page.setViewportSize({ width: 1024, height: 1200 })

    await withNetRetry(page, () => page.goto(`/conversations/${TM}`))
    await expect(page.locator('[data-message-uuid="tm-0"]')).toBeVisible()
    // Sanity: showToolCalls defaults to false, so tm-tool is not rendered.
    await expect(page.locator('[data-message-uuid="tm-tool"]')).toBeHidden()

    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await searchInput.fill('needle')

    // Both hits appear, the tool-only one too.
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({
      timeout: 10000,
    })

    // The newest hit (tm-tool) auto-promotes: the pane shows that tool
    // call, and sidebar and pane agree.
    await expect
      .poll(
        async () => ({
          sidebar: await getActiveSidebarMatchText(page),
          ringed: await getRingedMessageUuid(page),
        }),
        { timeout: 5000, message: 'Test B: sidebar-active card and pane focus must agree' },
      )
      .toEqual({
        sidebar: expect.stringContaining('echo needle in tool call'),
        ringed: 'tm-tool',
      })
    const toolBubble = page.locator('[data-message-uuid="tm-tool"]')
    await expect(toolBubble.getByText('Tool: Bash')).toBeVisible()
    await expect(toolBubble.locator('mark', { hasText: 'needle' })).toBeVisible()
    // The Tools setting itself did not change.
    await expect(page.getByTestId('header-show-tools-checkbox')).not.toBeChecked()

    // No request left the tool content out.
    expect(seen.filter((c) => c.q.includes('needle')).map((c) => c.includeToolCalls)).not.toContain('false')
  })
})
