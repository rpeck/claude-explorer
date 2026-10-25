import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry, recordHistory, historyRecord, nextFrames } from './fixtures'
import type { Message } from '../src/lib/types'

/**
 * Regression: navigating /conversations/<uuid> → /settings flashes the
 * Settings page on then bounces the user back to the conversation page
 * (2026-05-24, screen-recording attached to PR).
 *
 * TWO BUGS, both fixed in the same commit (one didn't fully explain the
 * recording on its own; together they do):
 *
 *   1. **flushSync warnings (cosmetic but noisy).** TanStack Virtual's
 *      default `useFlushSync: true` fires `flushSync()` from its
 *      onChange callback during render. React 19 promoted the
 *      "flushSync was called from inside a lifecycle method" warning
 *      from a console.error to (in some paths) a render-time throw.
 *      Fix: `useFlushSync: false` on both useVirtualizer call sites
 *      (the documented TanStack React 19 migration).
 *
 *   2. **Auto-promote yanks user off /settings (the real flash bug).**
 *      The SearchPanel auto-promote effect calls navigateToMatch when
 *      results land, regardless of the current route. If the user
 *      types a query and navigates to /settings before the debounce
 *      settles, the fetch lands while they're on /settings and the
 *      navigation yanks them back to /conversations/<uuid>?highlight=
 *      <msg>. Visible in the screen recording: URL bar goes from
 *      /conversations/X → /settings → /conversations/X?highlight=Y.
 *      Fix: SearchPanel.tsx gates auto-promote (source='auto')
 *      navigation on `location.pathname.startsWith('/conversations/')`.
 *      USER-initiated nav (Cmd+G, Enter, card click) always wins —
 *      gate applies only to source='auto'.
 *
 * Why this test wasn't caught earlier (TESTING.md §5.15): until
 * 2026-05-24 my Playwright specs asserted only on DOM state. The new
 * hardwired auto-fixture (`fixtures.ts:consoleAssertions`) catches
 * `pageerror` events globally — this test pins the user-observable
 * contract on top: "clicking Settings from a conversation page LANDS
 * on Settings and stays there, even when a search is in flight."
 */

const CONV = '00000000-0000-0000-0000-000000aaa001'

const summary = makeSummary({
  uuid: CONV,
  source: 'CLAUDE_CODE',
  message_count: 2,
  name: 'Conv with bubbles',
})

const msgs: Message[] = [
  makeMessage({
    uuid: 'm1',
    sender: 'human',
    text: 'first',
    content: [{ type: 'text', text: 'first' }],
  }),
  makeMessage({
    uuid: 'm2',
    sender: 'assistant',
    text: 'second',
    content: [{ type: 'text', text: 'second' }],
  }),
]

const detail = makeDetail(summary, msgs)


/** No URL in the record after `from` left the route that `stay` matches. */
function expectStayed(record: string[], from: RegExp, stay: RegExp) {
  const i = record.findIndex((u) => from.test(u))
  expect(i, `never reached ${from}: ${JSON.stringify(record)}`).toBeGreaterThan(-1)
  const bounced = record.slice(i).filter((u) => !stay.test(u))
  expect(bounced, `bounced away: ${JSON.stringify(record)}`).toEqual([])
}

test.describe('Navigating to Settings does not flash-and-disappear (2026-05-24)', () => {
  test('clicking Settings from a conversation page lands on /settings and stays', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [CONV]: detail },
    })
    await recordHistory(page)
    await withNetRetry(page, () => page.goto(`/conversations/${CONV}`))

    // Wait for the virtualizer to mount at least one bubble (proves the
    // virtualizer is initialized and ResizeObserver is attached — the
    // precondition for the flushSync error to fire on unmount).
    await expect(page.locator('[data-message-uuid="m1"]')).toBeVisible()

    // Click the Settings link in the sidebar.
    await page.getByRole('link', { name: /settings/i }).click()

    // After click: URL must be /settings AND must STAY at /settings.
    await expect(page).toHaveURL(/\/settings$/)

    // Settings page renders (heading is visible).
    await expect(
      page.getByRole('heading', { name: /settings/i, level: 1 }),
    ).toBeVisible()

    // Pin a Settings element to confirm full mount — not just a
    // transiently-visible header. The Export section's data attribute
    // is stable across refactors of the underlying radio markup.
    // History notes:
    //   - `settings-include-compact-content` was removed in the
    //     2026-05-24 unified-toggle refactor (the conversation header's
    //     "Show Compactions" checkbox now drives both viewer + export).
    //   - `settings-markdown-bundle-images` was removed in the
    //     2026-05-29 markdown-export-mode unification (a single
    //     `markdownExportMode` key now drives both the Settings section
    //     and the Markdown dialog).
    await expect(
      page.locator('[data-section="markdown-export"]'),
    ).toBeVisible()

    // Negative pair: the URL must NOT have bounced back to a
    // conversation route. This is the user-observable contract that
    // the flushSync rollback used to violate.
    // A bounce would come from the unmount or the first ResizeObserver
    // callbacks, which run before the next frames (2026-10-24: a 500 ms
    // sleep, then one look at the URL).
    await nextFrames(page)
    expectStayed(await historyRecord(page), /\/settings$/, /\/settings$/)
    await expect(page).toHaveURL(/\/settings$/)
    await expect(page).not.toHaveURL(/\/conversations\//)
  })

  test('navigating back from /settings to /conversations/<uuid> does not crash either', async ({
    page,
    mockBackend,
  }) => {
    // Bidirectional pair: the bug surfaced on the conv→settings
    // unmount, but the same flushSync trap could fire on the reverse
    // direction (settings→conv mounts a fresh virtualizer). Pin both.
    await mockBackend({
      conversations: [summary],
      details: { [CONV]: detail },
    })
    await recordHistory(page)
    await withNetRetry(page, () => page.goto('/settings'))
    await expect(
      page.getByRole('heading', { name: /settings/i, level: 1 }),
    ).toBeVisible()

    // Navigate directly to the conversation. This mounts a fresh
    // virtualizer; if flushSync fires during the mount-time
    // ResizeObserver callback under React 19, the URL would bounce
    // back to /settings via the same error-rollback mechanism.
    await withNetRetry(page, () => page.goto(`/conversations/${CONV}`))
    await expect(page.locator('[data-message-uuid="m1"]')).toBeVisible()

    await nextFrames(page) // the mount-time ResizeObserver callbacks ran
    expectStayed(await historyRecord(page), new RegExp(`/conversations/${CONV}`), new RegExp(`/conversations/${CONV}`))
    await expect(page).toHaveURL(new RegExp(`/conversations/${CONV}`))
  })

  test('search-result auto-promote landing while on /settings does NOT yank user back to a conversation', async ({
    page,
    mockBackend,
  }) => {
    // Reproduces the actual screen-recording flow:
    //   1. User is on /conversations/X with the search panel open.
    //   2. User types a query.
    //   3. User clicks Settings BEFORE the 200ms debounce settles.
    //   4. The fetch lands while the user is on /settings.
    //   5. (Pre-fix) auto-promote calls navigateToMatch → URL changes
    //      to /conversations/Y?highlight=Z → Settings page disappears.
    //   6. (Post-fix) auto-promote skips navigation when location is
    //      NOT /conversations/* → URL stays at /settings.
    //
    // Mock the search endpoint with an artificial delay so we can land
    // the fetch DELIBERATELY after the route change.
    let releaseSearch!: () => void
    const searchReleased = new Promise<void>((r) => (releaseSearch = r))
    await recordHistory(page)
    await mockBackend({
      conversations: [summary],
      details: { [CONV]: detail },
    })
    // AFTER mockBackend: Playwright runs the route registered last, and
    // before 2026-10-24 mockBackend's empty search answered every call,
    // so the held 1-match answer never reached the app and the test
    // checked nothing.
    await page.route('**/api/search**', async (route) => {
      const url = new URL(route.request().url())
      const q = (url.searchParams.get('q') ?? '').toLowerCase()
      const results = q.includes('needle')
        ? [
            {
              conversation_uuid: CONV,
              conversation_name: summary.name,
              conversation_updated_at: summary.updated_at,
              conversation_created_at: summary.created_at,
              project_name: summary.project_name,
              matching_messages: [
                {
                  message_uuid: 'm1',
                  sender: 'human',
                  snippet: 'first needle hit',
                  match_start: 6,
                  match_end: 12,
                  created_at: msgs[0].created_at,
                },
              ],
            },
          ]
        : []
      // Held until the test is on /settings (2026-10-24: an 800 ms timer).
      await searchReleased
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
    await withNetRetry(page, () => page.goto(`/conversations/${CONV}`))
    await expect(page.locator('[data-message-uuid="m1"]')).toBeVisible()

    // Open search panel, type query, IMMEDIATELY navigate to Settings
    // before the 800ms search delay elapses.
    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible({ timeout: 3000 })
    await input.fill('needle')

    // Race condition: click Settings before search lands.
    await page.getByRole('link', { name: /settings/i }).click()

    // We must be on /settings now.
    await expect(page).toHaveURL(/\/settings$/)

    // Now the search result lands, and the app has processed it two
    // frames later. Without the fix, auto-promote would navigate back to
    // /conversations/...?highlight= in the effect after that render.
    const answered = page.waitForResponse((r) => r.url().includes('/api/search'))
    releaseSearch()
    await answered
    await nextFrames(page)
    expectStayed(await historyRecord(page), /\/settings$/, /\/settings$/)

    // USER-OBSERVABLE CONTRACT: URL must still be /settings, the page
    // must still be the Settings page (heading visible).
    await expect(page).toHaveURL(/\/settings$/)
    await expect(
      page.getByRole('heading', { name: /settings/i, level: 1 }),
    ).toBeVisible()
    await expect(page).not.toHaveURL(/highlight=/)
  })
})
