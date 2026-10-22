import { test, expect, Route, withNetRetry, installLocalPrefsMock } from './fixtures'

/**
 * A search hit inside a tool call or a tool result is navigable
 * (user report, 2026-10-21).
 *
 * "Show tool calls" is off by default. A message that holds only tool
 * blocks then rendered nothing, so a click on its search hit did nothing:
 * the pane had no element with that message's uuid. On one real archive,
 * 4,101 of about 6,000 hits were in such messages. Claude Code stores
 * tool results as user rows, so both senders were affected.
 *
 * Contract: a click on such a hit shows ONLY the tool block that holds
 * the match, expanded, with the match highlighted. The "Show tool calls"
 * setting does not change, and the other tool blocks stay hidden.
 */

const CONV = '00000000-0000-0000-0000-00000000c0de'
const RESULT_NEEDLE = 'KUMQUATFAIL'
const CALL_NEEDLE = 'PLUMBOBQUERY'
const PICK_NEEDLE = 'WOMBATTEXT'
const MIXED_NEEDLE = 'GIRAFFEFLAG'

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
  msg('m1', 'human', [{ type: 'text', text: 'Run the tests' }], 'Run the tests'),
  msg('m2', 'assistant', [
    { type: 'text', text: 'I will run them.' },
    { type: 'tool_use', id: 't1', name: 'Bash', input: { command: `pytest -q --${MIXED_NEEDLE}` } },
  ], 'I will run them.'),
  // A tool result: Claude Code stores it as a user row, with no text.
  msg('m3', 'human', [
    {
      type: 'tool_result',
      tool_use_id: 't1',
      content: [{ type: 'text', text: `1 failed: ${RESULT_NEEDLE} in test_fruit.py` }],
    },
  ]),
  // A tool call with no text.
  msg('m4', 'assistant', [
    { type: 'tool_use', id: 't2', name: 'Grep', input: { pattern: CALL_NEEDLE } },
  ]),
  // Two tool calls; only the second one holds the match.
  msg('m5', 'assistant', [
    { type: 'tool_use', id: 't3', name: 'Read', input: { file_path: 'src/zoo.py' } },
    { type: 'tool_use', id: 't4', name: 'Edit', input: { old_string: PICK_NEEDLE, new_string: 'x' } },
  ]),
  msg('m6', 'assistant', [{ type: 'text', text: 'Done.' }], 'Done.'),
]

const conv = {
  uuid: CONV,
  name: 'Session with tool calls',
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

const HITS: Record<string, { uuid: string; sender: string; snippet: string }> = {
  [RESULT_NEEDLE.toLowerCase()]: { uuid: 'm3', sender: 'human', snippet: `1 failed: ${RESULT_NEEDLE} in test_fruit.py` },
  [CALL_NEEDLE.toLowerCase()]: { uuid: 'm4', sender: 'assistant', snippet: `{"pattern": "${CALL_NEEDLE}"}` },
  [PICK_NEEDLE.toLowerCase()]: { uuid: 'm5', sender: 'assistant', snippet: `{"old_string": "${PICK_NEEDLE}"}` },
  [MIXED_NEEDLE.toLowerCase()]: { uuid: 'm2', sender: 'assistant', snippet: `pytest -q --${MIXED_NEEDLE}` },
}

async function mockBackend(page: import('@playwright/test').Page) {
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
    const q = (new URL(route.request().url()).searchParams.get('q') ?? '').toLowerCase()
    const hit = HITS[q]
    const results = hit
      ? [
          {
            conversation_uuid: CONV,
            conversation_name: conv.name,
            conversation_updated_at: conv.updated_at,
            conversation_created_at: conv.created_at,
            project_name: 'proj',
            matching_messages: [
              {
                message_uuid: hit.uuid,
                sender: hit.sender,
                snippet: hit.snippet,
                match_start: 0,
                match_end: 0,
                created_at: '2026-10-01T10:00:00Z',
              },
            ],
          },
        ]
      : []
    await route.fulfill({
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

async function searchAndClick(page: import('@playwright/test').Page, needle: string) {
  await withNetRetry(page, () => page.goto(`/conversations/${CONV}`))
  await expect(page.locator('[data-message-uuid="m6"]')).toBeVisible()
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+f' : 'Control+f')
  const input = page.getByPlaceholder('Search messages...')
  await expect(input).toBeVisible()
  await input.fill(needle)
  await expect(page.getByText(/of\s+\d+\s+match/)).toBeVisible({ timeout: 5000 })
  await page.locator('[data-result-card]').first().click()
  return input
}

test.describe('A search hit inside a tool call is navigable (2026-10-21)', () => {
  test.beforeEach(async ({ page }) => {
    await mockBackend(page)
  })

  test('precondition: with tool calls hidden, tool-only messages render nothing', async ({ page }) => {
    await withNetRetry(page, () => page.goto(`/conversations/${CONV}`))
    await expect(page.locator('[data-message-uuid="m6"]')).toBeVisible()
    await expect(page.locator('[data-message-uuid="m3"]')).toHaveCount(0)
    await expect(page.locator('[data-message-uuid="m4"]')).toHaveCount(0)
    await expect(page.getByText('Tool: Bash')).toHaveCount(0)
  })

  test('a hit in a tool result (a user row) shows that result, highlighted', async ({ page }) => {
    await searchAndClick(page, RESULT_NEEDLE)

    const bubble = page.locator('[data-message-uuid="m3"]')
    await expect(bubble).toBeVisible({ timeout: 5000 })
    await expect(bubble.locator('mark', { hasText: RESULT_NEEDLE })).toBeVisible()
    // Only this block: the tool call in m2 stays hidden.
    await expect(page.getByText('Tool: Bash')).toHaveCount(0)
  })

  test('a hit in a tool call (an assistant row) shows that call, highlighted', async ({ page }) => {
    await searchAndClick(page, CALL_NEEDLE)

    const bubble = page.locator('[data-message-uuid="m4"]')
    await expect(bubble).toBeVisible({ timeout: 5000 })
    await expect(bubble.getByText('Tool: Grep')).toBeVisible()
    await expect(bubble.locator('mark', { hasText: CALL_NEEDLE })).toBeVisible()
  })

  test('only the tool call that holds the match is shown', async ({ page }) => {
    await searchAndClick(page, PICK_NEEDLE)

    const bubble = page.locator('[data-message-uuid="m5"]')
    await expect(bubble).toBeVisible({ timeout: 5000 })
    await expect(bubble.getByText('Tool: Edit')).toBeVisible()
    await expect(bubble.locator('mark', { hasText: PICK_NEEDLE })).toBeVisible()
    await expect(bubble.getByText('Tool: Read')).toHaveCount(0)
  })

  test('the Show tool calls setting does not change, and clearing the search hides the block again', async ({ page }) => {
    const input = await searchAndClick(page, RESULT_NEEDLE)
    await expect(page.locator('[data-message-uuid="m3"]')).toBeVisible({ timeout: 5000 })
    await expect(page.getByTestId('header-show-tools-checkbox')).not.toBeChecked()

    await input.fill('')
    await expect(page.locator('[data-message-uuid="m3"]')).toHaveCount(0)
  })

  test('a hit in the tool call of a message that also has text shows that call', async ({ page }) => {
    // m2 always renders ("I will run them."), but its tool call was hidden,
    // so the click scrolled to the message and the match stayed invisible.
    await searchAndClick(page, MIXED_NEEDLE)

    const bubble = page.locator('[data-message-uuid="m2"]')
    await expect(bubble.getByText('Tool: Bash')).toBeVisible({ timeout: 5000 })
    await expect(bubble.locator('mark', { hasText: MIXED_NEEDLE })).toBeVisible()
  })

  test('with Show Tools on, the tool block that holds the match is expanded', async ({ page }) => {
    // Tool blocks start collapsed, so the match was not visible either.
    await withNetRetry(page, () => page.goto(`/conversations/${CONV}`))
    await page.getByTestId('header-show-tools-checkbox').check()
    await expect(page.getByText('Tool: Read')).toBeVisible()
    await expect(page.getByText('src/zoo.py')).toHaveCount(0) // collapsed

    await page.keyboard.press(process.platform === 'darwin' ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await input.fill(PICK_NEEDLE)
    await expect(page.getByText(/of\s+\d+\s+match/)).toBeVisible({ timeout: 5000 })
    await page.locator('[data-result-card]').first().click()

    const bubble = page.locator('[data-message-uuid="m5"]')
    await expect(bubble.locator('mark', { hasText: PICK_NEEDLE })).toBeVisible({ timeout: 5000 })
    // Only the matching block expands; the Read call stays collapsed.
    await expect(bubble.getByText('src/zoo.py')).toHaveCount(0)
  })
})
