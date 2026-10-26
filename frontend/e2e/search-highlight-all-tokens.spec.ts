/**
 * V1 polish (2026-05-14, Bug 1, second fix) — multi-word search must
 * highlight ALL tokens in the snippet, not just the first.
 *
 * Pins the user-visible contract that the previous "fix" missed: when
 * the user types `comprehensive medium` (two unquoted words), the
 * rendered snippet must wrap BOTH `comprehensive` AND `medium` in
 * `<mark>` tags. The backend currently returns a single
 * `match_start`/`match_end` pair (it locates only the FIRST token's
 * first occurrence); the frontend must therefore re-scan the snippet
 * client-side and apply highlight ranges for every token in the query.
 *
 * The bidirectional guard tests pin:
 *   - single-token query produces exactly one <mark>
 *   - 3-token query where none of the snippet tokens match produces 0
 *     results (we never render a card without highlights to look at)
 *
 * Settle signal per feedback_playwright_settle_signals: poll on the
 * deterministic DOM signal (result-card count + mark count) instead of
 * any bare `waitForTimeout`.
 */
import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry, waitForKeyboardShortcuts } from './fixtures'
import type { SearchResult } from '../src/lib/types'
import type { Route } from './fixtures'

const HIT_UUID = '00000000-0000-0000-0000-0000000000b1'
const NOHIT_UUID = '00000000-0000-0000-0000-0000000000b2'

const hitSummary = makeSummary({
  uuid: HIT_UUID,
  name: 'Claude Explorer functionality',
  message_count: 1,
  human_message_count: 1,
})

const hitDetail = makeDetail(hitSummary, [
  makeMessage({
    uuid: 'hit-m1',
    sender: 'assistant',
    // Snippet body contains BOTH `comprehensive` and `medium`. The
    // backend reports `match_start`/`match_end` for `comprehensive`
    // only (the first token's first occurrence); the frontend must
    // highlight `medium` too.
    text: 'this is a comprehensive medium-form article about the explorer',
    content: [
      {
        type: 'text',
        text: 'this is a comprehensive medium-form article about the explorer',
      },
    ],
  }),
])

const SNIPPET_TEXT =
  'this is a comprehensive medium-form article about the explorer'
// 0123456789012345678901234567890
//           1111111111222222222233
// 'comprehensive' starts at index 10, ends at 23.
const COMPREHENSIVE_START = SNIPPET_TEXT.indexOf('comprehensive')
const COMPREHENSIVE_END = COMPREHENSIVE_START + 'comprehensive'.length

async function installSearchMock(page: Page) {
  await page.route('**/api/search**', (route: Route) => {
    const url = new URL(route.request().url())
    const q = url.searchParams.get('q') ?? ''
    let results: SearchResult[] = []
    if (q === 'comprehensive medium' || q === 'comprehensive') {
      results = [
        {
          conversation_uuid: HIT_UUID,
          conversation_name: hitSummary.name,
          conversation_updated_at: hitSummary.updated_at,
          conversation_created_at: hitSummary.created_at,
          project_name: null,
          matching_messages: [
            {
              message_uuid: 'hit-m1',
              sender: 'assistant',
              snippet: SNIPPET_TEXT,
              // Backend always reports a SINGLE highlight range,
              // pointing at the first token's first occurrence.
              // Frontend is responsible for the rest.
              match_start: COMPREHENSIVE_START,
              match_end: COMPREHENSIVE_END,
              created_at: '2026-05-01T12:00:00Z',
            },
          ],
        },
      ]
    } else if (q === 'tensorflow kubernetes rustlang') {
      results = []
    }
    route.fulfill({
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

test.describe('Search highlight rendering (all tokens, 2026-05-14)', () => {
  test('two-word query highlights BOTH tokens in the snippet', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [hitSummary],
      details: { [HIT_UUID]: hitDetail },
      extraRoutes: async (p) => {
        await installSearchMock(p)
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press('Meta+k')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible()
    await input.fill('comprehensive medium')

    // Settle: card count stabilizes at 1.
    const cards = page.locator('[data-result-card]')
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(1)

    const firstCard = cards.first()

    // Bug: previously only `comprehensive` was wrapped in <mark>.
    // We need BOTH `comprehensive` AND `medium` highlighted.
    const marks = firstCard.locator('mark')
    await expect
      .poll(async () => marks.count(), { timeout: 5000 })
      .toBeGreaterThanOrEqual(2)

    const markTexts = await marks.evaluateAll((els) =>
      els.map((el) => (el.textContent ?? '').toLowerCase()),
    )
    expect(markTexts).toEqual(
      expect.arrayContaining(['comprehensive', 'medium']),
    )
  })

  test('single-token query produces exactly one <mark>', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [hitSummary],
      details: { [HIT_UUID]: hitDetail },
      extraRoutes: async (p) => {
        await installSearchMock(p)
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press('Meta+k')
    const input = page.getByPlaceholder('Search messages...')
    await input.fill('comprehensive')

    const cards = page.locator('[data-result-card]')
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(1)

    const firstCard = cards.first()
    const marks = firstCard.locator('mark')
    // Single-token regression guard. Exactly one <mark>, wrapping
    // `comprehensive`.
    await expect.poll(async () => marks.count(), { timeout: 5000 }).toBe(1)
    const markText = (await marks.first().textContent())?.toLowerCase() ?? ''
    expect(markText).toBe('comprehensive')
  })

  test('three-token query with no overlap returns zero results', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [
        makeSummary({
          uuid: NOHIT_UUID,
          name: 'Unrelated',
          message_count: 1,
          human_message_count: 1,
        }),
      ],
      details: {
        [NOHIT_UUID]: makeDetail(
          makeSummary({
            uuid: NOHIT_UUID,
            name: 'Unrelated',
            message_count: 1,
            human_message_count: 1,
          }),
          [
            makeMessage({
              uuid: 'nh-m1',
              sender: 'human',
              text: 'lorem ipsum dolor sit amet',
              content: [{ type: 'text', text: 'lorem ipsum dolor sit amet' }],
            }),
          ],
        ),
      },
      extraRoutes: async (p) => {
        await installSearchMock(p)
      },
    })

    await withNetRetry(page, () => page.goto('/'))
    // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press('Meta+k')
    const input = page.getByPlaceholder('Search messages...')
    await input.fill('tensorflow kubernetes rustlang')

    // "No matches for ..." copy is the deterministic empty-state signal.
    await expect(
      page.getByText(/No matches for/i),
    ).toBeVisible({ timeout: 5000 })

    const cards = page.locator('[data-result-card]')
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(0)
  })
})
