import { test, expect, makeSummary, withNetRetry } from './fixtures'

/**
 * Cowork sidebar source filter (Phase 5).
 *
 * Pins the user-observable contract of the new "Claude Cowork" source
 * filter:
 *   1. The Source filter dropdown lists "Claude Cowork" as a third
 *      option alongside Claude Desktop and Claude Code.
 *   2. Selecting "Claude Cowork" filters the sidebar to ONLY
 *      CLAUDE_COWORK sessions — Desktop + CC sessions disappear.
 *   3. Selecting "Claude Cowork" hides the group-by-project toggle
 *      (Cowork project_path is a VM sandbox path like
 *      /sessions/<vm>; grouping by it is meaningless).
 *
 * Settle pattern (per feedback_playwright_settle_signals):
 *   - sidebar list visible -> click filter -> assert new filter list
 *     content -> assert toggle absence as a NEGATIVE check.
 *
 * Bidirectional verification per TESTING.md §2:
 *   - Toggle HIDDEN under CLAUDE_COWORK filter; SHOWN under
 *     'all'/'CLAUDE_CODE' (pinned by an inverse assertion).
 */

const COWORK_A_UUID = 'aaaa1111-2222-3333-4444-555566660001'
const COWORK_B_UUID = 'aaaa1111-2222-3333-4444-555566660002'
const DESKTOP_UUID = 'dddd1111-2222-3333-4444-555566660001'
const CC_UUID = 'cccc1111-2222-3333-4444-555566660001'

const coworkA = makeSummary({
  uuid: COWORK_A_UUID,
  name: 'Cowork Session Alpha',
  source: 'CLAUDE_COWORK',
  project_path: '/sessions/sandbox-alpha',
})

const coworkB = makeSummary({
  uuid: COWORK_B_UUID,
  name: 'Cowork Session Beta',
  source: 'CLAUDE_COWORK',
  project_path: '/sessions/sandbox-beta',
})

const desktop = makeSummary({
  uuid: DESKTOP_UUID,
  name: 'Desktop Conversation',
  source: 'CLAUDE_AI',
})

const cc = makeSummary({
  uuid: CC_UUID,
  name: 'CC Session',
  source: 'CLAUDE_CODE',
  project_path: '/home/user/project',
  project_name: 'project',
})

test.describe('Cowork sidebar source filter', () => {
  test('source dropdown lists Claude Cowork and selecting it filters sidebar', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [coworkA, coworkB, desktop, cc],
    })

    // Override the /api/conversations list route so it actually
    // honors the ?source=CLAUDE_COWORK query param (the default
    // mockBackend returns everything regardless of filter).
    // Registered AFTER mockBackend so LIFO order wins.
    await page.route('**/api/conversations**', (route) => {
      const url = new URL(route.request().url())
      // Bypass for detail / tree URLs — let the default mock handle them.
      if (url.pathname.match(/\/api\/conversations\/[^/]+(\/|$)/)) {
        return route.fallback()
      }
      const src = url.searchParams.get('source') ?? 'all'
      const all = [coworkA, coworkB, desktop, cc]
      const filtered = src === 'all' ? all : all.filter((c) => c.source === src)
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify(filtered),
      })
    })

    await withNetRetry(page, () => page.goto('/'))

    // Settle: sidebar populated with all 4 sessions.
    await expect(page.getByText('Cowork Session Alpha')).toBeVisible()
    await expect(page.getByText('Desktop Conversation')).toBeVisible()
    await expect(page.getByText('CC Session')).toBeVisible()

    // Open the source select trigger (data-testid pins it; other
    // sidebar selects — sort, workspace, active-filter — would also
    // match a bare combobox role lookup).
    await page.getByTestId('source-filter-select').click()

    // The "Claude Cowork" option must exist in the listbox.
    const coworkOption = page.getByRole('option', { name: /Claude Cowork/i })
    await expect(coworkOption).toBeVisible()

    await coworkOption.click()

    // After filter: only Cowork sessions remain.
    await expect(page.getByText('Cowork Session Alpha')).toBeVisible()
    await expect(page.getByText('Cowork Session Beta')).toBeVisible()
    await expect(page.getByText('Desktop Conversation')).toBeHidden()
    await expect(page.getByText('CC Session')).toBeHidden()
  })

  test('group-by-project toggle is hidden when filter is Claude Cowork', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({ conversations: [coworkA, coworkB, cc] })
    await withNetRetry(page, () => page.goto('/'))
    await expect(page.getByText('Cowork Session Alpha')).toBeVisible()

    // Baseline: under default 'all' filter (CC visible), toggle exists.
    await expect(
      page.getByRole('checkbox', { name: /Group sessions by project/i }),
    ).toBeVisible()

    // Switch to Claude Cowork.
    await page.getByTestId('source-filter-select').click()
    await page.getByRole('option', { name: /Claude Cowork/i }).click()

    // Inverse: toggle is hidden — Cowork's sandbox paths don't group meaningfully.
    await expect(
      page.getByRole('checkbox', { name: /Group sessions by project/i }),
    ).toBeHidden()
  })
})
