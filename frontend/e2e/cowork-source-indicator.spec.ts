import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'

/**
 * F12 (2026-05-29) — Cowork source indicator.
 *
 * Pins the user-observable contract that a `CLAUDE_COWORK` conversation
 * is labeled "Cowork" — not "Desktop" — in both source-indicator UI
 * sites. Pre-fix code used a binary `CLAUDE_CODE ? : Desktop` ternary
 * at both sites; Cowork rows fell through to the blue Desktop arm,
 * which the source dropdown the user just picked "Claude Cowork" from
 * directly contradicts.
 *
 * Two assertions per acceptance criterion §9 in the plan:
 *   - Sidebar row icon: title="Claude Cowork" present, "Claude Desktop"
 *     title absent.
 *   - Conversation header badge: text "Cowork" present, text "Desktop"
 *     absent within the header region.
 *
 * Auto console-error assertion (per TESTING.md §5.15 and the
 * project-wide [[feedback_e2e_console_assertions]] rule) fires
 * automatically via the `consoleAssertions` auto-fixture — no extra
 * assertion call needed in the test body, just don't allowlist any new
 * patterns and the test will fail on unexpected console noise.
 */

const COWORK_UUID = 'eedd1111-2222-3333-4444-555566660001'

const coworkSummary = makeSummary({
  uuid: COWORK_UUID,
  name: 'Cowork Source Indicator Pin',
  source: 'CLAUDE_COWORK',
})

const userMsg = makeMessage({
  uuid: 'm1',
  sender: 'human',
  text: 'hello',
  content: [{ type: 'text', text: 'hello' }],
})

const coworkDetail = makeDetail(coworkSummary, [userMsg], {
  error: null,
})

test.describe('Cowork F12 — source indicator', () => {
  test('sidebar row renders the Claude Cowork title (not Claude Desktop)', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [coworkSummary],
      details: { [COWORK_UUID]: coworkDetail },
    })

    await withNetRetry(page, () => page.goto('/'))

    // The conversation name renders → list mounted.
    await expect(
      page.getByText('Cowork Source Indicator Pin'),
    ).toBeVisible()

    // Bidirectional: Cowork title present, Desktop title absent.
    await expect(page.locator('[title="Claude Cowork"]')).toHaveCount(1)
    await expect(page.locator('[title="Claude Desktop"]')).toHaveCount(0)
  })

  test('open conversation header renders the purple "Cowork" badge (not "Desktop")', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [coworkSummary],
      details: { [COWORK_UUID]: coworkDetail },
    })

    await withNetRetry(page, () => page.goto(`/conversations/${COWORK_UUID}`))
    await expect(page.getByTestId('message-stream')).toBeVisible()

    // The conversation-header region is the row directly under the
    // title. Scope by the conversation name to keep this assertion
    // off the sidebar (which also contains the row icon, with a
    // title="Claude Cowork" tooltip but no visible text "Cowork").
    const headerRegion = page.locator('header, [role="banner"], div').filter({
      hasText: 'Cowork Source Indicator Pin',
    }).first()

    // Bidirectional pin on the header badge text.
    await expect(headerRegion.getByText('Cowork', { exact: true })).toBeVisible()
    await expect(headerRegion.getByText('Desktop', { exact: true })).toHaveCount(0)
  })
})
