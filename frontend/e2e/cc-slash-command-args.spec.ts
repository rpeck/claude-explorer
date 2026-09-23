import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures';

/**
 * Regression spec for the V1 polish round 3 bug (2026-05-12):
 *
 * User opened session `76fe578b-7872-4263-bc24-f911c7f2efcc` whose first
 * turn was `/coding Double-check your plan with the LLM council.`. The
 * conversation rendered an EMPTY `Session: /coding` marker because the
 * triplet collapser dropped the `<command-args>` payload. The user's
 * actual prompt text vanished — silent data loss for ALL argful slash
 * commands (/coding, /plan, /metaprompt, /tax-prep, etc.).
 *
 * Fix shape:
 *   - Backend `_collapse_local_command_triplets` now extracts the args
 *     body AND the command name, surfaces the args as the marker's
 *     `text`, and adds a separate `slash_command` field for the badge.
 *   - Frontend `MessageBubble` renders `<SlashCommandBadge command="/coding" />`
 *     above the markdown body whenever `message.slash_command` is set.
 *
 * Bidirectional verification (per TESTING.md):
 *   - NEW behavior assertion: argful marker bubble shows the args body
 *     AND a slash-command badge with `data-command="/coding"`.
 *   - OLD-behavior catch: argless markers (legacy /exit) STILL show the
 *     badge — argless and argful share the badge UI (uniform UX).
 *   - Counter case: Desktop conversations (no slash_command on any
 *     message) render NO badge anywhere in the stream.
 *
 * Settle pattern (per `feedback_playwright_settle_signals`): wait on
 * `[data-testid="message-stream"]` + first `[data-message-uuid]` to be
 * attached before any assertion. Then assert on the badge's
 * `[data-testid="slash-command-badge"]` + `[data-command="..."]` rather
 * than parsing the visible copy.
 */

const ARGFUL_UUID = '76fe578b-7872-4263-bc24-f911c7f2efcc';
const ARGS_TEXT = "Double-check your plan with the LLM council.";

const argfulSummary = makeSummary({
  uuid: ARGFUL_UUID,
  name: 'Pushing this repo',
  source: 'CLAUDE_CODE',
  message_count: 2,
  human_message_count: 1,
  has_branches: false,
  project_path: '/tmp/proj',
  project_name: 'proj',
});

// Argful marker: backend collapses the /coding triplet into a single
// row whose `text` is the args body and whose `slash_command` is "/coding".
// `is_command_marker: true` is kept so downstream styling logic still
// recognizes the row. `is_prelude` is NOT set (argful markers must stay
// visible — they carry the user's real prompt).
const argfulMessages = [
  makeMessage({
    uuid: 'marker-coding',
    sender: 'human',
    text: ARGS_TEXT,
    content: [{ type: 'text', text: ARGS_TEXT }],
    created_at: '2026-04-19T01:31:14Z',
    updated_at: '2026-04-19T01:31:14Z',
    parent_message_uuid: null,
    is_command_marker: true,
    is_prelude: false,
    slash_command: '/coding',
  }),
  makeMessage({
    uuid: 'asst-1',
    sender: 'assistant',
    text: "Let me run the council on your plan.",
    content: [{ type: 'text', text: "Let me run the council on your plan." }],
    created_at: '2026-04-19T01:31:20Z',
    updated_at: '2026-04-19T01:31:20Z',
    parent_message_uuid: 'marker-coding',
  }),
];

const argfulDetail = makeDetail(argfulSummary, argfulMessages, {
  current_leaf_message_uuid: 'asst-1',
  file_path: '/tmp/proj/fake.jsonl',
  // CRUCIAL: argful leading marker is NOT prelude — count is 0 so the
  // SessionPreludeAffordance doesn't render and the marker stays visible.
  prelude_hidden_count: 0,
});

test.describe('CC slash-command badge — argful marker', () => {
  test('argful /coding marker shows badge AND args body text', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [argfulSummary],
      details: { [ARGFUL_UUID]: argfulDetail },
    });

    await withNetRetry(page, () => page.goto(`/conversations/${ARGFUL_UUID}`));

    // Deterministic settle.
    await expect(page.getByTestId('message-stream')).toBeVisible();
    await expect(page.locator('[data-message-uuid="marker-coding"]')).toBeVisible();

    // NEW: the badge is rendered with the correct command name.
    const markerBubble = page.locator('[data-message-uuid="marker-coding"]');
    const badge = markerBubble.getByTestId('slash-command-badge');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveAttribute('data-command', '/coding');
    // The visible copy contains the command name (the badge is the
    // chrome; we assert via the data-attribute as the source of truth).
    await expect(badge).toContainText('/coding');

    // NEW: the bubble body carries the user's real prompt text — the
    // entire point of the args-preservation fix. The original bug was
    // that this text was DROPPED.
    await expect(markerBubble).toContainText(ARGS_TEXT);

    // SessionPreludeAffordance MUST NOT render — argful markers must
    // never be hidden behind the affordance.
    await expect(page.getByTestId('session-prelude-affordance')).toHaveCount(0);
  });

  test('argful marker bubble does NOT render an empty body', async ({
    page,
    mockBackend,
  }) => {
    // OLD-state catch: if the args-preservation logic ever regresses,
    // the marker text would fall back to "Session: /coding" and the
    // args body would be missing. Assert the body is NOT the legacy
    // session-label placeholder.
    await mockBackend({
      conversations: [argfulSummary],
      details: { [ARGFUL_UUID]: argfulDetail },
    });

    await withNetRetry(page, () => page.goto(`/conversations/${ARGFUL_UUID}`));
    await expect(page.getByTestId('message-stream')).toBeVisible();
    const markerBubble = page.locator('[data-message-uuid="marker-coding"]');
    await expect(markerBubble).toBeVisible();

    // The bubble must NOT contain the legacy fallback label when args
    // were supplied — that would mean args were dropped.
    const bodyText = await markerBubble.innerText();
    expect(bodyText).not.toMatch(/^Session: \/coding\s*$/m);
    expect(bodyText).toContain(ARGS_TEXT);
  });
});

// -- Argless marker case ----------------------------------------------------

const ARGLESS_UUID = '11111111-1111-1111-1111-111111111111';
const arglessSummary = makeSummary({
  uuid: ARGLESS_UUID,
  name: 'Exit-prefixed session',
  source: 'CLAUDE_CODE',
  message_count: 2,
  human_message_count: 1,
  has_branches: false,
  project_path: '/tmp/proj',
  project_name: 'proj',
});

// Argless marker: legacy "Session: /exit" text + slash_command="/exit".
// is_prelude=false because this fixture has only ONE marker which we
// want to render directly (we tested prelude-hide behavior in the
// `cc-session-prelude-affordance.spec.ts` spec; this spec focuses on
// the badge contract).
const arglessMessages = [
  makeMessage({
    uuid: 'marker-exit',
    sender: 'human',
    text: 'Session: /exit',
    content: [{ type: 'text', text: 'Session: /exit' }],
    created_at: '2026-04-19T01:00:00Z',
    updated_at: '2026-04-19T01:00:00Z',
    parent_message_uuid: null,
    is_command_marker: true,
    is_prelude: false,
    slash_command: '/exit',
  }),
  makeMessage({
    uuid: 'real-user',
    sender: 'human',
    text: 'Real follow-up after the exit.',
    content: [{ type: 'text', text: 'Real follow-up after the exit.' }],
    created_at: '2026-04-19T01:01:00Z',
    updated_at: '2026-04-19T01:01:00Z',
    parent_message_uuid: 'marker-exit',
  }),
];

const arglessDetail = makeDetail(arglessSummary, arglessMessages, {
  current_leaf_message_uuid: 'real-user',
  prelude_hidden_count: 0,
});

test.describe('CC slash-command badge — argless marker', () => {
  test('argless /exit marker still shows the badge (uniform UX)', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [arglessSummary],
      details: { [ARGLESS_UUID]: arglessDetail },
    });

    await withNetRetry(page, () => page.goto(`/conversations/${ARGLESS_UUID}`));
    await expect(page.getByTestId('message-stream')).toBeVisible();
    await expect(page.locator('[data-message-uuid="marker-exit"]')).toBeVisible();

    // NEW: argless markers ALSO carry the badge. The original V1 polish
    // round 2 marker had no badge concept; round 3 adds it uniformly so
    // every marker has the same UI affordance, only the body text differs.
    const markerBubble = page.locator('[data-message-uuid="marker-exit"]');
    const badge = markerBubble.getByTestId('slash-command-badge');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveAttribute('data-command', '/exit');

    // The body is the legacy "Session: /exit" label (the badge gives
    // the user a second, clearer signal of which slash command
    // produced this row).
    await expect(markerBubble).toContainText('Session: /exit');

    // The real user follow-up still renders, unaffected.
    await expect(page.locator('[data-message-uuid="real-user"]')).toContainText(
      'Real follow-up after the exit.',
    );
  });
});

// -- Desktop / non-CC counter-case -----------------------------------------

const DESKTOP_UUID = '22222222-2222-2222-2222-222222222222';
const desktopSummary = makeSummary({
  uuid: DESKTOP_UUID,
  name: 'Plain Desktop chat',
  source: 'CLAUDE_AI',
  message_count: 2,
  human_message_count: 1,
});

const desktopMessages = [
  makeMessage({
    uuid: 'd-u1',
    sender: 'human',
    text: 'What is a slash command in Claude Code?',
    content: [{ type: 'text', text: 'What is a slash command in Claude Code?' }],
    created_at: '2026-04-19T01:00:00Z',
    updated_at: '2026-04-19T01:00:00Z',
    parent_message_uuid: null,
    // No slash_command field — Desktop messages never have one.
  }),
  makeMessage({
    uuid: 'd-a1',
    sender: 'assistant',
    text: 'A slash command in CC is...',
    content: [{ type: 'text', text: 'A slash command in CC is...' }],
    created_at: '2026-04-19T01:00:10Z',
    updated_at: '2026-04-19T01:00:10Z',
    parent_message_uuid: 'd-u1',
  }),
];

const desktopDetail = makeDetail(desktopSummary, desktopMessages, {
  current_leaf_message_uuid: 'd-a1',
});

test.describe('CC slash-command badge — Desktop counter-case', () => {
  test('Desktop conversation renders NO slash-command badge anywhere', async ({
    page,
    mockBackend,
  }) => {
    // A Desktop conversation never has `slash_command` set on any
    // message. The badge MUST NOT render anywhere — if a render guard
    // ever regresses (e.g. `message.slash_command !== undefined` instead
    // of `if (message.slash_command)`), this test catches it because
    // missing-field reads as undefined which is falsy.
    await mockBackend({
      conversations: [desktopSummary],
      details: { [DESKTOP_UUID]: desktopDetail },
    });

    await withNetRetry(page, () => page.goto(`/conversations/${DESKTOP_UUID}`));
    await expect(page.getByTestId('message-stream')).toBeVisible();
    await expect(page.locator('[data-message-uuid="d-u1"]')).toBeVisible();

    // NEGATIVE: zero badges across the entire stream.
    await expect(page.getByTestId('slash-command-badge')).toHaveCount(0);

    // Sanity: the actual content rendered correctly.
    await expect(page.locator('[data-message-uuid="d-u1"]')).toContainText(
      'What is a slash command in Claude Code?',
    );
  });
});
