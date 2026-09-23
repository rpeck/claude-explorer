import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures';

/**
 * Regression spec for the V1 polish bug (2026-05-12, council round 2):
 * a CC session that opened with one or more `/exit` slash commands
 * rendered a confusing "prelude" of `Session: /exit` markers (each one
 * had also absorbed CC's canned `"No response requested."` reply) BEFORE
 * the first real user turn. The reported symptom on session
 * `76fe578b-7872-4263-bc24-f911c7f2efcc` was: "I don't see the top of
 * the conversation." — the prelude was dominating scroll-to-top.
 *
 * Fix shape:
 *   - Backend `_flag_leading_prelude_markers` sets `is_prelude: true` on
 *     each leading marker and reports the count as `prelude_hidden_count`
 *     on the conversation detail response.
 *   - Frontend hides `is_prelude: true` messages by default and renders
 *     a `<SessionPreludeAffordance />` button above the stream offering
 *     "Session prelude: N earlier /exit runs (show)". Clicking the
 *     button toggles the markers back into view.
 *
 * Bidirectional verification (per TESTING.md):
 *   - NEW behavior assertion: first visible bubble is the real user
 *     message (NOT a marker); affordance is visible with count=2.
 *   - OLD-behavior catch: in the regression, two marker bubbles + two
 *     canned-response assistant bubbles would all be visible at the top
 *     and `[data-testid=session-prelude-affordance]` wouldn't exist.
 *   - Counter case: a CC conversation with `prelude_hidden_count=0`
 *     renders NO affordance at all.
 *
 * Settle pattern (per `feedback_playwright_settle_signals`): wait on the
 * deterministic `[data-testid="message-stream"]` + first
 * `[data-message-uuid]` to be attached before any assertion. After the
 * affordance click, wait for the previously-hidden marker UUID to be
 * attached before checking expand state.
 */

const UUID = '76fe578b-7872-4263-bc24-f911c7f2efcc';
const REAL_USER_TEXT = "I don't think we've pushed this repo yet. Check for me.";

const summary = makeSummary({
  uuid: UUID,
  name: 'Pushing this repo',
  source: 'CLAUDE_CODE',
  message_count: 4,
  human_message_count: 3,
  has_branches: false,
  project_path: '/tmp/proj',
  project_name: 'proj',
});

const messages = [
  // Two leading prelude markers — these are what the backend's
  // collapse + fold passes produce when the JSONL starts with two
  // /exit triplets each followed by a canned-response assistant.
  makeMessage({
    uuid: 'marker-1',
    sender: 'human',
    text: 'Session: /exit',
    content: [{ type: 'text', text: 'Session: /exit' }],
    created_at: '2026-04-19T01:31:14Z',
    updated_at: '2026-04-19T01:31:14Z',
    parent_message_uuid: null,
    is_command_marker: true,
    is_prelude: true,
    assistant_canned_response_consumed: true,
  }),
  makeMessage({
    uuid: 'marker-2',
    sender: 'human',
    text: 'Session: /exit',
    content: [{ type: 'text', text: 'Session: /exit' }],
    created_at: '2026-04-19T01:50:49Z',
    updated_at: '2026-04-19T01:50:49Z',
    parent_message_uuid: 'marker-1',
    is_command_marker: true,
    is_prelude: true,
    assistant_canned_response_consumed: true,
  }),
  // The first real user message — what scroll-to-top SHOULD land on.
  makeMessage({
    uuid: 'real-user-1',
    sender: 'human',
    text: REAL_USER_TEXT,
    content: [{ type: 'text', text: REAL_USER_TEXT }],
    created_at: '2026-04-19T01:53:49Z',
    updated_at: '2026-04-19T01:53:49Z',
    parent_message_uuid: 'marker-2',
  }),
  makeMessage({
    uuid: 'real-asst-1',
    sender: 'assistant',
    text: 'Let me check the git remote configuration.',
    content: [{ type: 'text', text: 'Let me check the git remote configuration.' }],
    created_at: '2026-04-19T01:53:54Z',
    updated_at: '2026-04-19T01:53:54Z',
    parent_message_uuid: 'real-user-1',
  }),
];

const detail = makeDetail(summary, messages, {
  current_leaf_message_uuid: 'real-asst-1',
  file_path: '/tmp/proj/fake.jsonl',
  prelude_hidden_count: 2,
});

test.describe('CC session-prelude affordance', () => {
  test('first visible bubble is the real user message, affordance shows count=2', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [UUID]: detail },
    });

    await withNetRetry(page, () => page.goto(`/conversations/${UUID}`));

    // Deterministic settle barrier (per feedback_playwright_settle_signals).
    await expect(page.getByTestId('message-stream')).toBeVisible();
    await expect(page.locator('[data-message-uuid]').first()).toBeVisible();

    // NEW behavior: the prelude markers are filtered out of the rendered
    // stream when collapsed (the default). Assert by-UUID: marker-1 and
    // marker-2 are NOT attached anywhere in the DOM.
    await expect(page.locator('[data-message-uuid="marker-1"]')).toHaveCount(0);
    await expect(page.locator('[data-message-uuid="marker-2"]')).toHaveCount(0);

    // NEW behavior: the first rendered bubble is the real user message.
    const firstBubble = page.locator('[data-message-uuid]').first();
    await expect(firstBubble).toHaveAttribute('data-message-uuid', 'real-user-1');
    // And it carries the real user text. We use partial-text match
    // (.toContainText) because MessageBubble adds avatar / timestamp
    // chrome around the text body.
    await expect(firstBubble).toContainText("pushed this repo");

    // NEW behavior: the affordance is rendered with count=2 and the
    // copy reads "Session prelude: 2 earlier /exit runs (show)".
    const affordance = page.getByTestId('session-prelude-affordance');
    await expect(affordance).toBeVisible();
    await expect(affordance).toHaveAttribute('data-prelude-count', '2');
    await expect(affordance).toHaveAttribute('data-expanded', 'false');
    await expect(affordance).toContainText('Session prelude: 2 earlier /exit runs (show)');
  });

  test('clicking the affordance reveals the hidden prelude markers', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [UUID]: detail },
    });

    await withNetRetry(page, () => page.goto(`/conversations/${UUID}`));
    await expect(page.getByTestId('message-stream')).toBeVisible();
    await expect(page.locator('[data-message-uuid="real-user-1"]')).toBeVisible();

    // OLD-state confirmation: markers are hidden before click.
    await expect(page.locator('[data-message-uuid="marker-1"]')).toHaveCount(0);
    await expect(page.locator('[data-message-uuid="marker-2"]')).toHaveCount(0);

    const affordance = page.getByTestId('session-prelude-affordance');
    await affordance.click();

    // Deterministic settle: wait for the previously-hidden marker UUIDs
    // to be ATTACHED to the DOM (per `feedback_playwright_settle_signals`,
    // never assume sync — wait on the deterministic post-click signal
    // that proves the filter has actually been re-evaluated).
    await expect(page.locator('[data-message-uuid="marker-1"]')).toBeVisible();
    await expect(page.locator('[data-message-uuid="marker-2"]')).toBeVisible();

    // The affordance now reports the expanded state.
    await expect(affordance).toHaveAttribute('data-expanded', 'true');
    await expect(affordance).toContainText('(hide)');

    // The real user message is still rendered after reveal.
    await expect(page.locator('[data-message-uuid="real-user-1"]')).toBeVisible();

    // Bounding-box ordering: marker-1 sits above marker-2, which sits
    // above the real user message. This proves the markers were
    // re-inserted at the TOP of the stream (correct chronology) and
    // didn't get appended to the bottom.
    const marker1Box = await page.locator('[data-message-uuid="marker-1"]').boundingBox();
    const marker2Box = await page.locator('[data-message-uuid="marker-2"]').boundingBox();
    const realBox = await page.locator('[data-message-uuid="real-user-1"]').boundingBox();
    expect(marker1Box).not.toBeNull();
    expect(marker2Box).not.toBeNull();
    expect(realBox).not.toBeNull();
    expect(marker1Box!.y).toBeLessThan(marker2Box!.y);
    expect(marker2Box!.y).toBeLessThan(realBox!.y);

    // Click again to collapse — markers disappear, copy flips back to "(show)".
    await affordance.click();
    await expect(page.locator('[data-message-uuid="marker-1"]')).toHaveCount(0);
    await expect(page.locator('[data-message-uuid="marker-2"]')).toHaveCount(0);
    await expect(affordance).toHaveAttribute('data-expanded', 'false');
    await expect(affordance).toContainText('(show)');
  });

  test('no affordance rendered when prelude_hidden_count is 0', async ({
    page,
    mockBackend,
  }) => {
    // Counter case: a normal CC session that did NOT open with /exit —
    // backend reports prelude_hidden_count=0 and there are no is_prelude
    // messages. The affordance must NOT render at all.
    const noPreludeUuid = '00000000-0000-0000-0000-ccccccccc000';
    const noPreludeSummary = makeSummary({
      uuid: noPreludeUuid,
      name: 'Normal CC session',
      source: 'CLAUDE_CODE',
      message_count: 2,
      human_message_count: 1,
      project_path: '/tmp/proj',
      project_name: 'proj',
    });
    const noPreludeMessages = [
      makeMessage({
        uuid: 'u1',
        sender: 'human',
        text: 'Hello.',
        content: [{ type: 'text', text: 'Hello.' }],
        created_at: '2026-04-19T01:00:00Z',
        updated_at: '2026-04-19T01:00:00Z',
        parent_message_uuid: null,
      }),
      makeMessage({
        uuid: 'a1',
        sender: 'assistant',
        text: 'Hi!',
        content: [{ type: 'text', text: 'Hi!' }],
        created_at: '2026-04-19T01:00:05Z',
        updated_at: '2026-04-19T01:00:05Z',
        parent_message_uuid: 'u1',
      }),
    ];
    const noPreludeDetail = makeDetail(noPreludeSummary, noPreludeMessages, {
      current_leaf_message_uuid: 'a1',
      prelude_hidden_count: 0,
    });

    await mockBackend({
      conversations: [noPreludeSummary],
      details: { [noPreludeUuid]: noPreludeDetail },
    });

    await withNetRetry(page, () => page.goto(`/conversations/${noPreludeUuid}`));
    await expect(page.getByTestId('message-stream')).toBeVisible();
    await expect(page.locator('[data-message-uuid="u1"]')).toBeVisible();

    // NEW behavior: affordance MUST NOT render when there's no prelude.
    await expect(page.getByTestId('session-prelude-affordance')).toHaveCount(0);
  });
});
