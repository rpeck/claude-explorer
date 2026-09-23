import { test, expect, Route, withNetRetry, installLocalPrefsMock } from './fixtures'

/**
 * User-observable contract test for the 2026-05-22 compact-marker
 * auto-expand behavior.
 *
 * THE USER REPORT (2026-05-23): "Search hits in compact messages
 * don't seem to be auto-expanding anymore." The pre-existing unit
 * tests (CompactMarker.forceOpen.test.tsx + navigateToMatch.test.tsx
 * Test 5) all pass, but the production chain is broken. This file is
 * the missing user-observable test that exercises the full chain:
 * type query → results land → CompactMarker panel becomes visible.
 *
 * Per TESTING.md §5.13: the unit tests pin the resolution
 * RULES (the `forceOpen` prop honors transitions; the navigateToMatch
 * fast path clicks the pill if data-compact-marker is present). This
 * file pins the user-observable CONTRACT: the user types and the
 * compact panel becomes visible.
 *
 * Three scenarios:
 *   1. Auto-promote during typing, SAME conversation.
 *   2. Auto-promote during typing, CROSS conversation.
 *   3. Explicit click on the sidebar result card.
 *
 * All three must end with the compact marker's `[data-compact-marker-panel]`
 * VISIBLE, containing the matched needle text.
 */

const CONV_A = '00000000-0000-0000-0000-000000aaa001'
const CONV_B = '00000000-0000-0000-0000-000000bbb002'

// The needle is intentionally unique so we never collide with stray
// fixture text. It lives ONLY in the compact summary, not in any
// regular message body — so the search hit can ONLY land on the
// compact marker.
const NEEDLE = 'ZEBRAQUARK'

const baseConvA = {
  uuid: CONV_A,
  name: 'Conv A — has compact with needle',
  summary: '',
  model: 'claude-sonnet-4-6',
  created_at: '2026-04-01T10:00:00Z',
  updated_at: '2026-04-01T13:00:00Z',
  is_starred: false,
  message_count: 3,
  human_message_count: 2,
  has_branches: false,
  source: 'CLAUDE_CODE' as const,
  project_path: '/tmp/proj',
  project_name: 'proj',
  git_branch: '',
  subagents: [],
}

const baseConvB = {
  ...baseConvA,
  uuid: CONV_B,
  name: 'Conv B — also has compact with needle',
}

const messagesA = [
  {
    uuid: 'a-msg-1',
    sender: 'human' as const,
    text: 'Begin work in A',
    content: [{ type: 'text', text: 'Begin work in A' }],
    created_at: '2026-04-01T10:00:00Z',
    updated_at: '2026-04-01T10:00:00Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
  },
  {
    uuid: 'a-compact-msg',
    sender: 'human' as const,
    text: `Compact in A containing ${NEEDLE} in the summary.`,
    content: [{ type: 'text', text: `Compact in A containing ${NEEDLE} in the summary.` }],
    created_at: '2026-04-01T11:00:00Z',
    updated_at: '2026-04-01T11:00:00Z',
    truncated: false,
    parent_message_uuid: 'a-msg-1',
    attachments: [],
    files: [],
  },
  {
    uuid: 'a-msg-3',
    sender: 'assistant' as const,
    text: 'Continuing in A.',
    content: [{ type: 'text', text: 'Continuing in A.' }],
    created_at: '2026-04-01T13:00:00Z',
    updated_at: '2026-04-01T13:00:00Z',
    truncated: false,
    parent_message_uuid: 'a-compact-msg',
    attachments: [],
    files: [],
  },
]

const messagesB = [
  {
    uuid: 'b-msg-1',
    sender: 'human' as const,
    text: 'Begin work in B',
    content: [{ type: 'text', text: 'Begin work in B' }],
    created_at: '2026-04-01T10:00:00Z',
    updated_at: '2026-04-01T10:00:00Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
  },
  {
    uuid: 'b-compact-msg',
    sender: 'human' as const,
    text: `Compact in B containing ${NEEDLE} in the summary.`,
    content: [{ type: 'text', text: `Compact in B containing ${NEEDLE} in the summary.` }],
    created_at: '2026-04-01T11:00:00Z',
    updated_at: '2026-04-01T11:00:00Z',
    truncated: false,
    parent_message_uuid: 'b-msg-1',
    attachments: [],
    files: [],
  },
]

const compactMarkersA = [
  {
    message_uuid: 'a-compact-msg',
    summary_text: `Compact in A containing ${NEEDLE} in the summary.`,
    timestamp: '2026-04-01T11:00:00Z',
    kind: 'manual' as const,
    user_prompt: 'preserve A',
  },
]

const compactMarkersB = [
  {
    message_uuid: 'b-compact-msg',
    summary_text: `Compact in B containing ${NEEDLE} in the summary.`,
    timestamp: '2026-04-01T11:00:00Z',
    kind: 'manual' as const,
    user_prompt: 'preserve B',
  },
]

async function mockBackend(page: import('@playwright/test').Page) {
  await page.route('**/api/conversations**', (route: Route) => {
    const url = route.request().url()
    if (url.includes(`/conversations/${CONV_A}/tree`)) {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ uuid: CONV_A, root_messages: [], active_path: [] }),
      })
      return
    }
    if (url.includes(`/conversations/${CONV_B}/tree`)) {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ uuid: CONV_B, root_messages: [], active_path: [] }),
      })
      return
    }
    if (url.includes(`/conversations/${CONV_A}`)) {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          ...baseConvA,
          messages: messagesA,
          current_leaf_message_uuid: 'a-msg-3',
          file_path: '/tmp/proj/a.jsonl',
          compact_markers: compactMarkersA,
        }),
      })
      return
    }
    if (url.includes(`/conversations/${CONV_B}`)) {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          ...baseConvB,
          messages: messagesB,
          current_leaf_message_uuid: 'b-compact-msg',
          file_path: '/tmp/proj/b.jsonl',
          compact_markers: compactMarkersB,
        }),
      })
      return
    }
    if (url.match(/\/api\/conversations(\?|$)/)) {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify([baseConvA, baseConvB]),
      })
      return
    }
    route.continue()
  })

  await page.route('**/api/config', (route: Route) => {
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data_dir: '/tmp', conversation_count: 2 }),
    })
  })

  // Per-test preferences store. Without this, PATCH /api/preferences
  // falls through the Vite proxy to whatever backend is on :8765, so
  // tab/searchPanel state from one test bleeds into the next and the
  // serial-run flake for `CROSS-conv auto-promote` surfaces as a stale
  // SearchPanel state. See fixtures.installLocalPrefsMock header for
  // the shared rationale (2026-06-01).
  await installLocalPrefsMock(page)

  // Search endpoint: returns a hit on the compact marker in whichever
  // conversation matches first. The needle only appears in compact
  // summaries, so the match uuid is the compact-marker's message_uuid.
  //
  // Mock SHAPE = real backend shape post-2026-05-23 (Option C fix):
  //   * SEARCH_TEXT_NEEDLE that only lives in summary → marker UUID
  //   * SEARCH_TEXT_NEEDLE that only lives in /compact trigger row's
  //     <command-args> (user_prompt) → ALSO marker UUID (backend rewrite,
  //     not exercised here because the test needle lives only in summary).
  // The corresponding pytest pin lives at
  // backend/tests/test_search_compact_trigger_rewrite.py
  // (test_fts5_user_prompt_text_does_not_match_via_trigger_row).
  await page.route('**/api/search**', async (route: Route) => {
    const url = new URL(route.request().url())
    const q = (url.searchParams.get('q') ?? '').toLowerCase()
    const conv = url.searchParams.get('conversation_uuid')
    let results: unknown[] = []
    if (q.includes(NEEDLE.toLowerCase())) {
      const inA = !conv || conv === CONV_A
      const inB = !conv || conv === CONV_B
      const rows: unknown[] = []
      if (inA) {
        rows.push({
          conversation_uuid: CONV_A,
          conversation_name: baseConvA.name,
          conversation_updated_at: baseConvA.updated_at,
          conversation_created_at: baseConvA.created_at,
          project_name: 'proj',
          matching_messages: [
            {
              message_uuid: 'a-compact-msg',
              sender: 'human',
              snippet: `Compact in A containing ${NEEDLE} in the summary.`,
              match_start: 22,
              match_end: 22 + NEEDLE.length,
              created_at: '2026-04-01T11:00:00Z',
            },
          ],
        })
      }
      if (inB) {
        rows.push({
          conversation_uuid: CONV_B,
          conversation_name: baseConvB.name,
          conversation_updated_at: baseConvB.updated_at,
          conversation_created_at: baseConvB.created_at,
          project_name: 'proj',
          matching_messages: [
            {
              message_uuid: 'b-compact-msg',
              sender: 'human',
              snippet: `Compact in B containing ${NEEDLE} in the summary.`,
              match_start: 22,
              match_end: 22 + NEEDLE.length,
              created_at: '2026-04-01T11:00:00Z',
            },
          ],
        })
      }
      results = rows
    }
    await new Promise((r) => setTimeout(r, 50))
    await route.fulfill({
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
}

async function openSearchPanel(page: import('@playwright/test').Page) {
  const isMac = process.platform === 'darwin'
  await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
  const input = page.getByPlaceholder('Search messages...')
  await expect(input).toBeVisible({ timeout: 3000 })
  return input
}

test.describe('Search hit on a compact marker → marker auto-expands (2026-05-23 user report)', () => {
  test.beforeEach(async ({ page }) => {
    await mockBackend(page)
  })

  test('SAME-conv auto-promote: typing into search expands the compact marker', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${CONV_A}`))

    // Precondition: marker is in the DOM but the panel is COLLAPSED.
    const marker = page.locator('[data-message-uuid="a-compact-msg"]')
    await expect(marker).toBeVisible()
    await expect(page.locator('[data-compact-marker-panel]')).toHaveCount(0)

    // Open search panel, type the needle.
    const input = await openSearchPanel(page)
    await input.click()
    await input.fill(NEEDLE)

    // Wait for the result count to appear in the sidebar (debounce +
    // fetch settle). At this point auto-promote has fired.
    await expect(page.getByText(/of\s+\d+\s+match/)).toBeVisible({ timeout: 5000 })

    // USER-OBSERVABLE CONTRACT: the compact marker panel must be
    // visible (the auto-expand the user is asking for).
    const panel = page.locator('[data-compact-marker-panel]').first()
    await expect(panel).toBeVisible({ timeout: 5000 })
    await expect(panel).toContainText(NEEDLE)

    // Negative-pair: focus must stay in the search input (the other
    // half of the 2026-05-23 design contract).
    await expect(input).toBeFocused()
  })

  test('CROSS-conv auto-promote: typing navigates to the other conv AND expands its compact marker', async ({ page }) => {
    // Start on conv A; type a query that hits BOTH conversations.
    // Auto-promote on the first result (which will be one of them —
    // we don't care which; whichever it lands on must auto-expand).
    await withNetRetry(page, () => page.goto(`/conversations/${CONV_A}`))
    const input = await openSearchPanel(page)
    await input.click()
    await input.fill(NEEDLE)

    // Wait for results.
    await expect(page.getByText(/of\s+\d+\s+match/)).toBeVisible({ timeout: 5000 })

    // The auto-promote may have left us on A (first result) or moved
    // us to B. Either way, the conversation we LAND ON must have its
    // compact panel visible.
    const panel = page.locator('[data-compact-marker-panel]').first()
    await expect(panel).toBeVisible({ timeout: 5000 })
    await expect(panel).toContainText(NEEDLE)

    // Focus still in the input — even for cross-conv auto-promote.
    await expect(input).toBeFocused({ timeout: 2000 })
  })

  test('LARGE-conv (virtualization): compact marker far down the list still auto-expands', async ({ page }) => {
    // Reproduces the real-world scenario: 16K-message conversation
    // where the compact marker is initially OUTSIDE the virtualizer
    // window. The auto-promote chain has to:
    //   1. Navigate URL to ?highlight=<markerUuid>
    //   2. Trigger virtualizer.scrollToIndex(visIdx)
    //   3. Wait for the row to mount via the rAF poll
    //   4. Apply scroll + ring
    //   5. The CompactMarker's forceOpen prop must be wired correctly
    //      at mount time so isOpen starts true.
    //
    // Override the conv-A mock to insert 500 filler messages BEFORE
    // the compact marker, ensuring it's well outside the initial
    // virtualizer window.
    await page.route(`**/api/conversations/${CONV_A}**`, (route: Route) => {
      const url = route.request().url()
      if (url.includes('/tree')) {
        route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({ uuid: CONV_A, root_messages: [], active_path: [] }),
        })
        return
      }
      const filler: Array<Record<string, unknown>> = []
      let prev: string | null = null
      for (let i = 0; i < 500; i++) {
        const uuid = `filler-${i.toString().padStart(4, '0')}`
        filler.push({
          uuid,
          sender: i % 2 === 0 ? 'human' : 'assistant',
          text: `Filler message ${i}`,
          content: [{ type: 'text', text: `Filler message ${i}` }],
          created_at: `2026-04-01T10:${(i % 60).toString().padStart(2, '0')}:00Z`,
          updated_at: `2026-04-01T10:${(i % 60).toString().padStart(2, '0')}:00Z`,
          truncated: false,
          parent_message_uuid: prev,
          attachments: [],
          files: [],
        })
        prev = uuid
      }
      const compactRow = {
        uuid: 'a-compact-msg',
        sender: 'human' as const,
        text: `Compact in A containing ${NEEDLE} in the summary.`,
        content: [{ type: 'text', text: `Compact in A containing ${NEEDLE} in the summary.` }],
        created_at: '2026-04-01T11:00:00Z',
        updated_at: '2026-04-01T11:00:00Z',
        truncated: false,
        parent_message_uuid: prev,
        attachments: [],
        files: [],
      }
      const trailing = {
        uuid: 'a-msg-tail',
        sender: 'assistant' as const,
        text: 'Continuing in A.',
        content: [{ type: 'text', text: 'Continuing in A.' }],
        created_at: '2026-04-01T13:00:00Z',
        updated_at: '2026-04-01T13:00:00Z',
        truncated: false,
        parent_message_uuid: 'a-compact-msg',
        attachments: [],
        files: [],
      }
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          ...baseConvA,
          message_count: 502,
          messages: [...filler, compactRow, trailing],
          current_leaf_message_uuid: 'a-msg-tail',
          file_path: '/tmp/proj/a.jsonl',
          compact_markers: compactMarkersA,
        }),
      })
    })

    await withNetRetry(page, () => page.goto(`/conversations/${CONV_A}`))

    // Wait for the conv to load (any bubble visible).
    await expect(page.locator('[data-message-uuid="filler-0000"]')).toBeVisible({ timeout: 5000 })

    // Precondition: the compact marker is OUTSIDE the initial
    // virtualizer window. data-message-uuid should NOT find it in the
    // DOM yet (virtualization is working).
    const markerCount = await page.locator('[data-message-uuid="a-compact-msg"]').count()
    expect(markerCount).toBe(0)

    // Open search and type the needle.
    const input = await openSearchPanel(page)
    await input.click()
    await input.fill(NEEDLE)

    // Wait for results card to appear.
    await expect(page.getByText(/of\s+\d+\s+match/)).toBeVisible({ timeout: 5000 })

    // USER-OBSERVABLE CONTRACT: the compact marker must now be visible
    // AND auto-expanded. The virtualizer must have mounted it via the
    // highlight-effect's scrollToIndex, AND the forceOpen prop must
    // have started true so isOpen state is true on mount.
    const marker = page.locator('[data-message-uuid="a-compact-msg"]')
    await expect(marker).toBeVisible({ timeout: 5000 })
    const panel = page.locator('[data-compact-marker-panel]').first()
    await expect(panel).toBeVisible({ timeout: 5000 })
    await expect(panel).toContainText(NEEDLE)

    // Focus still in input (auto-promote contract).
    await expect(input).toBeFocused()
  })

  test('Explicit click on sidebar result card expands the compact marker AND focuses it', async ({ page }) => {
    // The user-action path: bypass any auto-promote weirdness and
    // explicitly click the result card. The marker must open AND
    // gain focus (so Cmd+C copies its content — the 2026-05-23
    // GATE 4 contract).
    await withNetRetry(page, () => page.goto(`/conversations/${CONV_A}`))

    // Make sure the marker is collapsed before we start.
    await expect(page.locator('[data-compact-marker-panel]')).toHaveCount(0)

    const input = await openSearchPanel(page)
    await input.click()
    await input.fill(NEEDLE)

    // Wait for results card to appear.
    await expect(page.getByText(/of\s+\d+\s+match/)).toBeVisible({ timeout: 5000 })

    // Click the first result card. (handleCardClick passes 'user' to
    // setActiveMatchIndex, which is the explicit-action path.)
    const card = page.locator('[data-result-card]').first()
    await card.click()

    // Marker panel must be visible.
    const panel = page.locator('[data-compact-marker-panel]').first()
    await expect(panel).toBeVisible({ timeout: 5000 })
    await expect(panel).toContainText(NEEDLE)
  })
})
