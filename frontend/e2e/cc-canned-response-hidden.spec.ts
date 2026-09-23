import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'

/**
 * Case 3 — `/exit` + canned `"No response requested."` fold (V1 polish
 * 2026-05-12).
 *
 * Spec contract (PLANS/2026.05.13-slash-and-tools-display-spec.md, Case 3):
 *   The backend collapses each `/exit` triplet into a marker. The CC
 *   canned assistant reply `"No response requested."` that immediately
 *   follows a `/exit` marker is then ABSORBED into the marker by
 *   `backend/claude_code_reader.py::_fold_canned_assistant_responses_into_marker`.
 *   The marker carries `assistant_canned_response_consumed = True`; the
 *   canned-response Message is removed from the conversation entirely.
 *
 * What this spec pins (browser-visible):
 *   1. Given the POST-FOLD shape (1 marker, no canned-response row),
 *      the viewer renders only the marker bubble and ZERO assistant
 *      bubbles for the absent canned response. This is the "no phantom
 *      Claude bubble" invariant.
 *   2. Given a NEAR-MISS shape where the assistant message after the
 *      marker is a real, non-canned reply (e.g. "Acknowledged."), the
 *      assistant bubble DOES render. Pinning that the frontend does
 *      NOT introduce its own over-eager "drop short assistant reply
 *      after /exit" filter — if such a regression ever shipped, the
 *      bidirectional inverse here would catch it.
 *
 * Why pin Case 3 in Playwright (despite backend pytest covering the
 * fold logic itself):
 *   - The backend `_fold_canned_assistant_responses_into_marker` is
 *     well-pytested (`backend/tests/test_canned_response_fold_and_prelude.py`).
 *   - The user-visible promise ("no canned-response bubble appears
 *     after a /exit marker") sits ACROSS the API boundary. Pinning it
 *     in Playwright with the post-fold fixture guarantees the
 *     frontend's render contract for the post-fold shape — and the
 *     bidirectional inverse guards against a future frontend filter
 *     that mistakes a short assistant reply for a canned response.
 *
 * Settle pattern (per `feedback_playwright_settle_signals`):
 *   - Step 1: `getByTestId('message-stream')` visible.
 *   - Step 2: a known-rendered bubble visible (the marker for the
 *     positive case, the post-marker user msg for the inverse).
 *   - Step 3: assert on the absence of phantom assistant bubbles via
 *     `toHaveCount(0)` on selectors that would catch the regression.
 *
 * Bidirectional verification per TESTING.md §2:
 *   - "X NOT rendered when condition": no assistant bubble after marker
 *     in the post-fold fixture (Test A).
 *   - "X rendered without condition": assistant bubble DOES render
 *     when the assistant text is non-canned (Test B).
 */

const FOLD_UUID = '00000000-0000-0000-0000-0000000fcfd0'
const REAL_USER_TEXT = 'Now the real question begins.'

const summary = makeSummary({
  uuid: FOLD_UUID,
  name: '/exit canned fold',
  source: 'CLAUDE_CODE',
  message_count: 2,
  human_message_count: 2,
  has_branches: false,
  project_path: '/tmp/proj',
  project_name: 'proj',
})

// Post-fold shape: ONE marker row carrying `assistant_canned_response_consumed=true`,
// followed by the next real user message. The canned assistant reply
// has been removed by the backend. The marker is NOT a prelude (it is
// followed by a real user turn, but lives mid-conversation here so
// `is_prelude=false` and the SessionPreludeAffordance does not hide
// it). The frontend renders the marker via SlashCommandBadge + the
// "Session: /exit" body text (per Case 1 contract).
const postFoldMessages = [
  makeMessage({
    uuid: 'fold-marker',
    sender: 'human',
    text: 'Session: /exit',
    content: [{ type: 'text', text: 'Session: /exit' }],
    created_at: '2026-04-19T01:00:00Z',
    updated_at: '2026-04-19T01:00:00Z',
    parent_message_uuid: null,
    is_command_marker: true,
    is_prelude: false,
    assistant_canned_response_consumed: true,
    slash_command: '/exit',
  }),
  makeMessage({
    uuid: 'fold-real-user',
    sender: 'human',
    text: REAL_USER_TEXT,
    content: [{ type: 'text', text: REAL_USER_TEXT }],
    created_at: '2026-04-19T01:00:10Z',
    updated_at: '2026-04-19T01:00:10Z',
    parent_message_uuid: 'fold-marker',
  }),
]

const postFoldDetail = makeDetail(summary, postFoldMessages, {
  current_leaf_message_uuid: 'fold-real-user',
  file_path: '/tmp/proj/fake.jsonl',
  prelude_hidden_count: 0,
})

test.describe('CC Case 3 — canned response absorbed into /exit marker', () => {
  test('A: post-fold fixture renders the marker but NO phantom assistant bubble', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [FOLD_UUID]: postFoldDetail },
    })

    await withNetRetry(page, () => page.goto(`/conversations/${FOLD_UUID}`))

    // Settle: message stream + marker bubble rendered.
    await expect(page.getByTestId('message-stream')).toBeVisible()
    const marker = page.locator('[data-message-uuid="fold-marker"]')
    await expect(marker).toBeVisible()

    // Marker carries the badge per Case 1 contract.
    await expect(marker.getByTestId('slash-command-badge')).toHaveAttribute(
      'data-command',
      '/exit',
    )

    // The real follow-up user message renders too (settle for the
    // post-marker portion of the stream).
    await expect(
      page.locator('[data-message-uuid="fold-real-user"]'),
    ).toContainText(REAL_USER_TEXT)

    // INVARIANT: the conversation has ZERO assistant bubbles. The
    // post-fold shape contains only two human-role rows (marker +
    // real user); the canned-response assistant reply was absorbed
    // server-side and must NOT reappear via any frontend codepath.
    //
    // We scope on `data-message-uuid` (every bubble carries it) and
    // count by enumerating only the rows whose underlying message
    // would be assistant. A simpler proxy: the visible UI must not
    // contain the literal canned-response copy "No response
    // requested." anywhere.
    await expect(
      page.getByText('No response requested.', { exact: false }),
    ).toHaveCount(0)

    // Stronger: only TWO bubbles attach to the message-stream
    // (marker + real user), neither is assistant. Per the synthesized
    // fixture there's no assistant row, so this should be exactly 2.
    await expect(page.locator('[data-message-uuid]')).toHaveCount(2)
  })

  test('B: counter — a non-canned assistant reply after marker DOES render', async ({
    page,
    mockBackend,
  }) => {
    // Bidirectional inverse: prove the frontend does NOT have an
    // over-eager "drop short assistant reply right after a /exit
    // marker" filter. The backend's fold uses an EXACT string match
    // on "No response requested.". A near-miss like "Acknowledged."
    // MUST flow through to the viewer.

    const counterUuid = '00000000-0000-0000-0000-0000000fcfd1'
    const counterSummary = makeSummary({
      uuid: counterUuid,
      name: '/exit + non-canned',
      source: 'CLAUDE_CODE',
      message_count: 3,
      human_message_count: 1,
      project_path: '/tmp/proj',
      project_name: 'proj',
    })
    const counterMessages = [
      makeMessage({
        uuid: 'cf-marker',
        sender: 'human',
        text: 'Session: /exit',
        content: [{ type: 'text', text: 'Session: /exit' }],
        created_at: '2026-04-19T02:00:00Z',
        updated_at: '2026-04-19T02:00:00Z',
        parent_message_uuid: null,
        is_command_marker: true,
        is_prelude: false,
        // No assistant_canned_response_consumed flag here — the
        // assistant follow-up is a legitimate, non-canned reply that
        // the backend's fold did NOT absorb.
        slash_command: '/exit',
      }),
      makeMessage({
        uuid: 'cf-assistant',
        sender: 'assistant',
        // "Acknowledged." is a near-miss for the fold's exact-match
        // rule. Backend fold should NOT have absorbed this; the
        // frontend must render the bubble normally.
        text: 'Acknowledged.',
        content: [{ type: 'text', text: 'Acknowledged.' }],
        created_at: '2026-04-19T02:00:05Z',
        updated_at: '2026-04-19T02:00:05Z',
        parent_message_uuid: 'cf-marker',
      }),
      makeMessage({
        uuid: 'cf-real-user',
        sender: 'human',
        text: REAL_USER_TEXT,
        content: [{ type: 'text', text: REAL_USER_TEXT }],
        created_at: '2026-04-19T02:00:10Z',
        updated_at: '2026-04-19T02:00:10Z',
        parent_message_uuid: 'cf-assistant',
      }),
    ]
    const counterDetail = makeDetail(counterSummary, counterMessages, {
      current_leaf_message_uuid: 'cf-real-user',
      prelude_hidden_count: 0,
    })

    await mockBackend({
      conversations: [counterSummary],
      details: { [counterUuid]: counterDetail },
    })

    await withNetRetry(page, () => page.goto(`/conversations/${counterUuid}`))
    await expect(page.getByTestId('message-stream')).toBeVisible()

    // Settle: marker rendered.
    await expect(page.locator('[data-message-uuid="cf-marker"]')).toBeVisible()

    // POSITIVE: the non-canned assistant bubble renders with its body.
    // Pins "frontend doesn't second-guess the backend's exact-match
    // fold rule".
    const assistantBubble = page.locator('[data-message-uuid="cf-assistant"]')
    await expect(assistantBubble).toBeVisible()
    await expect(assistantBubble).toContainText('Acknowledged.')

    // POSITIVE: real-user follow-up also renders.
    await expect(
      page.locator('[data-message-uuid="cf-real-user"]'),
    ).toContainText(REAL_USER_TEXT)

    // STRONGER: there are exactly 3 bubbles (marker + assistant +
    // real-user). If a regression hid one, this catches it.
    await expect(page.locator('[data-message-uuid]')).toHaveCount(3)
  })
})
