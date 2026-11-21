import { test, expect, Route, withNetRetry, installLocalPrefsMock } from './fixtures'

/**
 * S2 (issue #84): the Search Pane looks inside tool calls and tool output
 * only while Show Tools is on (UX.md §4).
 *
 * - Show Tools off: a word that is only in a tool block is not a hit. The
 *   empty result says "Search limited by hidden tool calls." and offers
 *   "Search tool calls too", which turns Show Tools on.
 * - Turning Show Tools on or off runs the search again.
 * - Show Tools off: no hit reveals a tool block, also when a tool block of
 *   the hit message holds the word.
 * - Show Tools on: the tool block that holds the match expands; the others
 *   stay collapsed.
 *
 * The search mock honors `include_tool_calls` like the real backend: a hit
 * marked `tool` is returned only when the request does not send
 * `include_tool_calls=false`.
 */

const CONV = '00000000-0000-0000-0000-0000000084aa'
const CALL_NEEDLE = 'PLUMBOBSCOPE'
const OUTPUT_NEEDLE = 'KUMQUATSCOPE'
const BOTH_NEEDLE = 'FLAMINGOSCOPE'
const SPLIT_NEEDLE = 'OTTERSCOPE'
const PICK_NEEDLE = 'WOMBATSCOPE'

function msg(uuid: string, sender: 'human' | 'assistant', content: unknown[], text = '') {
  return {
    uuid,
    sender,
    text,
    content,
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
  }
}

const messages = [
  msg('m1', 'human', [{ type: 'text', text: `Run the tests, ${SPLIT_NEEDLE} first` }], `Run the tests, ${SPLIT_NEEDLE} first`),
  // Text and a tool call both hold BOTH_NEEDLE.
  msg('m2', 'assistant', [
    { type: 'text', text: `I will check ${BOTH_NEEDLE}.` },
    { type: 'tool_use', id: 't1', name: 'Bash', input: { command: `grep ${BOTH_NEEDLE} src` } },
  ], `I will check ${BOTH_NEEDLE}.`),
  // A tool result (a user row in Claude Code).
  msg('m3', 'human', [
    {
      type: 'tool_result',
      tool_use_id: 't1',
      content: [{ type: 'text', text: `src/a.py: ${OUTPUT_NEEDLE} ${SPLIT_NEEDLE}` }],
    },
  ]),
  // A tool call with no text.
  msg('m4', 'assistant', [{ type: 'tool_use', id: 't2', name: 'Grep', input: { pattern: CALL_NEEDLE } }]),
  // Two tool calls; only the second one holds the match.
  msg('m5', 'assistant', [
    { type: 'tool_use', id: 't3', name: 'Read', input: { file_path: 'src/zoo.py' } },
    { type: 'tool_use', id: 't4', name: 'Edit', input: { old_string: PICK_NEEDLE, new_string: 'x' } },
  ]),
  msg('m6', 'assistant', [{ type: 'text', text: 'Done.' }], 'Done.'),
]

const conv = {
  uuid: CONV,
  name: 'Session for the tool scope',
  summary: '',
  model: 'claude-sonnet-4-6',
  created_at: '2026-10-01T10:00:00Z',
  updated_at: '2026-10-01T11:00:00Z',
  is_starred: false,
  message_count: messages.length,
  human_message_count: 1,
  has_branches: false,
  source: 'CLAUDE_CODE' as const,
  project_path: '/tmp/proj',
  project_name: 'proj',
  git_branch: '',
  subagents: [],
}

type Hit = { uuid: string; sender: string; snippet: string; where: 'text' | 'tool' }

const HITS: Record<string, Hit[]> = {
  [CALL_NEEDLE.toLowerCase()]: [{ uuid: 'm4', sender: 'assistant', snippet: `{"pattern": "${CALL_NEEDLE}"}`, where: 'tool' }],
  [OUTPUT_NEEDLE.toLowerCase()]: [{ uuid: 'm3', sender: 'human', snippet: `src/a.py: ${OUTPUT_NEEDLE}`, where: 'tool' }],
  // The text of m2 matches, so m2 is a hit with tools on and off.
  [BOTH_NEEDLE.toLowerCase()]: [{ uuid: 'm2', sender: 'assistant', snippet: `I will check ${BOTH_NEEDLE}.`, where: 'text' }],
  [SPLIT_NEEDLE.toLowerCase()]: [
    { uuid: 'm1', sender: 'human', snippet: `Run the tests, ${SPLIT_NEEDLE} first`, where: 'text' },
    { uuid: 'm3', sender: 'human', snippet: `src/a.py: ${OUTPUT_NEEDLE} ${SPLIT_NEEDLE}`, where: 'tool' },
  ],
  [PICK_NEEDLE.toLowerCase()]: [{ uuid: 'm5', sender: 'assistant', snippet: `{"old_string": "${PICK_NEEDLE}"}`, where: 'tool' }],
}

async function mockBackend(page: import('@playwright/test').Page) {
  const seen: Array<{ q: string; includeToolCalls: boolean }> = []
  await page.route('**/api/conversations**', (route: Route) => {
    const url = route.request().url()
    if (url.includes(`/conversations/${CONV}/tree`)) {
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ uuid: CONV, root_messages: [], active_path: [] }),
      })
    }
    if (url.includes(`/conversations/${CONV}`)) {
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          ...conv,
          messages,
          current_leaf_message_uuid: 'm6',
          file_path: '/tmp/proj/s.jsonl',
          compact_markers: [],
        }),
      })
    }
    if (url.match(/\/api\/conversations(\?|$)/)) {
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify([conv]) })
    }
    return route.continue()
  })
  await page.route('**/api/config', (route: Route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data_dir: '/tmp', config_corrupt_reason: null }),
    }),
  )
  await installLocalPrefsMock(page)
  await page.route('**/api/search**', async (route: Route) => {
    const params = new URL(route.request().url()).searchParams
    const q = (params.get('q') ?? '').toLowerCase()
    // No parameter means the backend default, true.
    const includeToolCalls = params.get('include_tool_calls') !== 'false'
    seen.push({ q, includeToolCalls })
    const hits = (HITS[q] ?? []).filter((h) => includeToolCalls || h.where === 'text')
    const results = hits.length
      ? [
          {
            conversation_uuid: CONV,
            conversation_name: conv.name,
            conversation_updated_at: conv.updated_at,
            conversation_created_at: conv.created_at,
            project_name: 'proj',
            matching_messages: hits.map((h) => ({
              message_uuid: h.uuid,
              sender: h.sender,
              snippet: h.snippet,
              match_start: 0,
              match_end: 0,
              created_at: '2026-10-01T10:00:00Z',
            })),
          },
        ]
      : []
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        results,
        total_messages_matched: hits.length,
        returned_messages: hits.length,
        truncated: false,
      }),
    })
  })
  return seen
}

async function openAndSearch(page: import('@playwright/test').Page, needle: string, { toolsOn = false } = {}) {
  await withNetRetry(page, () => page.goto(`/conversations/${CONV}`))
  await expect(page.locator('[data-message-uuid="m6"]')).toBeVisible()
  if (toolsOn) {
    await page.getByTestId('header-show-tools-checkbox').check()
    await expect(page.getByText('Tool: Read')).toBeVisible()
  }
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+f' : 'Control+f')
  const input = page.getByPlaceholder('Search messages...')
  await expect(input).toBeVisible()
  await input.fill(needle)
  return input
}

const emptyLine = (page: import('@playwright/test').Page) => page.getByTestId('search-empty-filters')

test.describe('S2: search looks inside tool content only with Show Tools on (#84)', () => {
  test('Show Tools off: a word only in a tool call gives no hit and names hidden tool calls', async ({ page }) => {
    const seen = await mockBackend(page)
    await openAndSearch(page, CALL_NEEDLE)

    await expect(page.getByText(/No matches for/)).toBeVisible({ timeout: 5000 })
    await expect(emptyLine(page)).toHaveText('Search limited by hidden tool calls.')
    await expect(page.getByTestId('search-empty-show-tools')).toHaveText('Search tool calls too')
    await expect(page.locator('[data-result-card]')).toHaveCount(0)
    const sent = seen.filter((c) => c.q === CALL_NEEDLE.toLowerCase())
    expect(sent.length).toBeGreaterThan(0)
    expect(sent.map((c) => c.includeToolCalls)).not.toContain(true)
  })

  test('Show Tools off: a word only in a tool output gives no hit', async ({ page }) => {
    await mockBackend(page)
    await openAndSearch(page, OUTPUT_NEEDLE)

    await expect(page.getByText(/No matches for/)).toBeVisible({ timeout: 5000 })
    await expect(emptyLine(page)).toHaveText('Search limited by hidden tool calls.')
    await expect(page.locator('[data-message-uuid="m3"]')).toHaveCount(0)
  })

  test('Search tool calls too turns Show Tools on and the tool hit appears', async ({ page }) => {
    await mockBackend(page)
    const input = await openAndSearch(page, OUTPUT_NEEDLE)
    await expect(page.getByTestId('search-empty-show-tools')).toBeVisible({ timeout: 5000 })

    await page.getByTestId('search-empty-show-tools').click()
    await expect(page.getByTestId('header-show-tools-checkbox')).toBeChecked()
    await expect(page.getByText(/of\s+1\s+match/)).toBeVisible({ timeout: 5000 })
    await expect(emptyLine(page)).toHaveCount(0)
    await expect(input).toBeFocused()

    await page.locator('[data-result-card]').first().click()
    const bubble = page.locator('[data-message-uuid="m3"]')
    await expect(bubble.locator('mark', { hasText: OUTPUT_NEEDLE })).toBeVisible({ timeout: 5000 })
  })

  test('turning Show Tools on in the header runs the search again and the count grows', async ({ page }) => {
    const seen = await mockBackend(page)
    await openAndSearch(page, SPLIT_NEEDLE)
    await expect(page.getByText(/of\s+1\s+match/)).toBeVisible({ timeout: 5000 })

    const before = seen.length
    await page.getByTestId('header-show-tools-checkbox').check()
    await expect(page.getByText(/of\s+2\s+matches/)).toBeVisible({ timeout: 5000 })
    const after = seen.slice(before).filter((c) => c.q === SPLIT_NEEDLE.toLowerCase())
    expect(after.length).toBeGreaterThan(0)
    expect(after.every((c) => c.includeToolCalls)).toBe(true)

    await page.getByTestId('header-show-tools-checkbox').uncheck()
    await expect(page.getByText(/of\s+1\s+match/)).toBeVisible({ timeout: 5000 })
  })

  test('Show Tools off: a hit on a message whose tool block also holds the word reveals no tool block', async ({ page }) => {
    await mockBackend(page)
    await openAndSearch(page, BOTH_NEEDLE)
    await expect(page.getByText(/of\s+1\s+match/)).toBeVisible({ timeout: 5000 })
    await page.locator('[data-result-card]').first().click()

    const bubble = page.locator('[data-message-uuid="m2"]')
    await expect(bubble.locator('mark', { hasText: BOTH_NEEDLE })).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('Tool: Bash')).toHaveCount(0)
    await expect(page.getByText(`grep ${BOTH_NEEDLE} src`)).toHaveCount(0)
    await expect(page.getByTestId('header-show-tools-checkbox')).not.toBeChecked()
  })

  test('Show Tools on: the tool block that holds the match expands and the others stay collapsed', async ({ page }) => {
    await mockBackend(page)
    await openAndSearch(page, PICK_NEEDLE, { toolsOn: true })
    await expect(page.getByText('src/zoo.py')).toHaveCount(0) // collapsed
    await expect(page.getByText(/of\s+1\s+match/)).toBeVisible({ timeout: 5000 })
    await page.locator('[data-result-card]').first().click()

    const bubble = page.locator('[data-message-uuid="m5"]')
    await expect(bubble.locator('mark', { hasText: PICK_NEEDLE })).toBeVisible({ timeout: 5000 })
    await expect(bubble.getByText('src/zoo.py')).toHaveCount(0)
  })
})
