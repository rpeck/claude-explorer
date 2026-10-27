/**
 * V1 polish (2026-05-14, Bug A THIRD fix) — single-character tokens
 * must NOT spam the snippet with `<mark>` decorations.
 *
 * Live repro (Playwright MCP on http://localhost:5173, before fix):
 *   Query `comprehensive m` → first card has 8× `<mark>m</mark>` plus
 *   1× `<mark>comprehensive</mark>`. ZERO `<mark>medium</mark>` (the
 *   user's snippet didn't contain `medium`). The "every single letter
 *   m is wrapped" user-reported bug.
 *
 * The mid-typing path is the realistic failure mode: every multi-word
 * query passes through this state on its way to the full word. The
 * SearchPanel already gates entire-query length < 2 with "Type at
 * least 2 characters" (SearchPanel.tsx:147); this spec pins the
 * per-token analog.
 *
 * Seed-trap dimension: the backend's `match_start`/`match_end` is
 * blindly seeded into the highlight set (highlightRanges.ts:178). So
 * even after the helper drops the `m` token, a 1-char backend seed
 * would still emit `<mark>m</mark>` on its own. The fix MUST drop
 * len-1 seeds in token mode. To prove that, this spec keeps the mock
 * backend's match_start pointing at an `m`.
 *
 * Bidirectional regression guard: when the user finishes typing
 * `comprehensive medium`, both words highlight as expected.
 *
 * Settle signal per `feedback_playwright_settle_signals`: poll the
 * deterministic DOM signal (card count + mark inner-text set) rather
 * than `waitForTimeout`. The 200ms search debounce + React render is
 * absorbed by `expect.poll`.
 */
import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry, waitForKeyboardShortcuts } from './fixtures'
import type { SearchResult } from '../src/lib/types'
import type { Route } from './fixtures'

const HIT_UUID = '00000000-0000-0000-0000-0000000000c1'

const hitSummary = makeSummary({
  uuid: HIT_UUID,
  name: 'Claude Explorer functionality',
  message_count: 1,
  human_message_count: 1,
})

// Snippet body modeled after the real production message that caused
// the user-reported bug: many `m` letters, NO occurrence of `medium`.
const SNIPPET_TEXT =
  'this is a comprehensive description with modules, Component, ' +
  'matrix, README.md, and many Enhancement mentions'

const hitDetail = makeDetail(hitSummary, [
  makeMessage({
    uuid: 'c1-m1',
    sender: 'assistant',
    text: SNIPPET_TEXT,
    content: [{ type: 'text', text: SNIPPET_TEXT }],
  }),
])

const COMPREHENSIVE_START = SNIPPET_TEXT.indexOf('comprehensive')
const COMPREHENSIVE_END = COMPREHENSIVE_START + 'comprehensive'.length
// The FIRST `m` in the snippet — at `comprehensive` index 0 there's no
// `m`; later in `modules`/`Component`/etc. The bug requires the seed
// to point at a STANDALONE `m`-only range so the helper is tempted to
// emit `<mark>m</mark>`. We use the `m` of "modules" (or the next one).
const FIRST_M = SNIPPET_TEXT.indexOf('modules')

async function installSearchMock(page: Page) {
  await page.route('**/api/search**', (route: Route) => {
    const url = new URL(route.request().url())
    const q = url.searchParams.get('q') ?? ''
    let results: SearchResult[] = []
    // We mock the API for the THREE intermediate query states the user
    // passes through while typing `comprehensive medium`:
    //   1. `comprehensive` (single token, found in snippet)
    //   2. `comprehensive m` (the bug state — mid-typing)
    //   3. `comprehensive medium` (full query — but snippet doesn't
    //      contain `medium`, so AND-of-tokens makes this a non-match)
    //
    // For (2), the mock backend returns a 1-char match_start/match_end
    // pointing at an `m` — modeling the worst-case backend behavior
    // where the FTS5 path matched the token `m` literally.
    if (q === 'comprehensive') {
      results = [
        {
          conversation_uuid: HIT_UUID,
          conversation_name: hitSummary.name,
          conversation_updated_at: hitSummary.updated_at,
          conversation_created_at: hitSummary.created_at,
          project_name: null,
          matching_messages: [
            {
              message_uuid: 'c1-m1',
              sender: 'assistant',
              snippet: SNIPPET_TEXT,
              match_start: COMPREHENSIVE_START,
              match_end: COMPREHENSIVE_END,
              created_at: '2026-05-01T12:00:00Z',
            },
          ],
        },
      ]
    } else if (q === 'comprehensive m') {
      // CRITICAL: backend seed at a 1-char `m` range. The helper must
      // drop this seed AND filter the `m` token → no `<mark>m</mark>`.
      results = [
        {
          conversation_uuid: HIT_UUID,
          conversation_name: hitSummary.name,
          conversation_updated_at: hitSummary.updated_at,
          conversation_created_at: hitSummary.created_at,
          project_name: null,
          matching_messages: [
            {
              message_uuid: 'c1-m1',
              sender: 'assistant',
              snippet: SNIPPET_TEXT,
              match_start: FIRST_M,
              match_end: FIRST_M + 1,
              created_at: '2026-05-01T12:00:00Z',
            },
          ],
        },
      ]
    }
    // `comprehensive medium` → empty (the snippet body doesn't contain
    // `medium`, so AND-of-tokens makes this a non-match in the real
    // backend; we model that here so the spec doesn't depend on actual
    // backend AND-filter behavior).
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

test.describe('Search highlight — Bug A v3: no single-char `m` marks', () => {
  test('mid-typing `comprehensive m` shows ONLY comprehensive (no m-spam)', async ({
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

    // Type the full mid-typing query. Use fill (instant set) so the
    // resulting state is unambiguously `comprehensive m`.
    await input.fill('comprehensive m')

    // Settle on the card count first — that's the DOM signal proving
    // the debounced search + render cycle completed.
    const cards = page.locator('[data-result-card]')
    await expect.poll(async () => cards.count(), { timeout: 5000 }).toBe(1)

    const firstCard = cards.first()
    const marks = firstCard.locator('mark')

    // Settle on the inner-text set. This polls until the rendering
    // stabilizes (no in-flight React commit between mark count + inner
    // text reads).
    await expect.poll(async () => {
      const texts = await marks.evaluateAll((els) =>
        els.map((el) => (el.textContent ?? '').toLowerCase()),
      )
      return JSON.stringify(texts.sort())
    }, { timeout: 5000 }).toBe(JSON.stringify(['comprehensive']))

    // Strong assertion: every visible <mark> has length >= 2 (no 1-char
    // marks anywhere on the page).
    const allMarks = page.locator('mark')
    const allTexts = await allMarks.evaluateAll((els) =>
      els.map((el) => (el.textContent ?? '').toLowerCase()),
    )
    for (const t of allTexts) {
      expect(t.length).toBeGreaterThanOrEqual(2)
    }
    // None of the visible marks are a bare `m`.
    expect(allTexts).not.toContain('m')
  })

  // Bidirectional regression guard — single-token query still works.
  test('single-token `comprehensive` highlights comprehensive (regression guard)', async ({
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

    const marks = cards.first().locator('mark')
    await expect.poll(async () => marks.count(), { timeout: 5000 }).toBe(1)
    const text = (await marks.first().textContent())?.toLowerCase() ?? ''
    expect(text).toBe('comprehensive')
  })
})
