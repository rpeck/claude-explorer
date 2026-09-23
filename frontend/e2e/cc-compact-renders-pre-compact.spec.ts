import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures';

/**
 * Regression spec for the bug observed 2026-05-12: a CC session that
 * had `/compact` invoked once rendered the compact marker as the FIRST
 * visible item in the right panel — every pre-compact message was
 * silently dropped by the parent-chain leaf-walk inside
 * `backend/store.resolve_active_branch`.
 *
 * The fix is in the backend: for CC sessions, render `chat_messages`
 * in original chronological JSONL order and skip leaf-walking
 * entirely. This spec asserts the resulting UI shape:
 *
 *   - The first rendered bubble is the actual first user message
 *     (NOT the compact marker).
 *   - The compact marker is preserved at its original chronological
 *     index (between pre- and post-compact messages).
 *
 * Bidirectional verification (per TESTING.md):
 * - NEW behavior assertion: pre-compact message renders first.
 * - OLD-behavior catch: explicitly asserts the compact marker is NOT
 *   the first child of the message stream, which is exactly what the
 *   user reported. A regression to the old bug would put
 *   `[data-compact-marker]` at index 0 and fail this assertion.
 */

const UUID = '00000000-0000-0000-0000-cccccccc0001';

const summary = makeSummary({
  uuid: UUID,
  name: 'Medium post creation',
  source: 'CLAUDE_CODE',
  message_count: 5,
  human_message_count: 3,
  has_branches: false,
  project_path: '/tmp/proj',
  project_name: 'proj',
});

const messages = [
  makeMessage({
    uuid: 'pre-msg-1',
    sender: 'human',
    text: 'first pre-compact user message',
    content: [{ type: 'text', text: 'first pre-compact user message' }],
    created_at: '2026-04-01T10:00:00Z',
    updated_at: '2026-04-01T10:00:00Z',
    parent_message_uuid: null,
  }),
  makeMessage({
    uuid: 'pre-msg-2',
    sender: 'assistant',
    text: 'pre-compact assistant reply',
    content: [{ type: 'text', text: 'pre-compact assistant reply' }],
    created_at: '2026-04-01T10:00:10Z',
    updated_at: '2026-04-01T10:00:10Z',
    parent_message_uuid: 'pre-msg-1',
  }),
  makeMessage({
    uuid: 'compact-summary',
    sender: 'human',
    text: 'This session is being continued from a previous conversation.',
    content: [{ type: 'text', text: 'This session is being continued from a previous conversation.' }],
    created_at: '2026-04-01T11:00:00Z',
    updated_at: '2026-04-01T11:00:00Z',
    parent_message_uuid: 'pre-msg-2',
  }),
  makeMessage({
    uuid: 'post-msg-1',
    sender: 'human',
    text: 'first post-compact user message',
    content: [{ type: 'text', text: 'first post-compact user message' }],
    created_at: '2026-04-01T12:00:00Z',
    updated_at: '2026-04-01T12:00:00Z',
    parent_message_uuid: 'compact-summary',
  }),
  makeMessage({
    uuid: 'post-msg-2',
    sender: 'assistant',
    text: 'post-compact assistant reply',
    content: [{ type: 'text', text: 'post-compact assistant reply' }],
    created_at: '2026-04-01T12:00:10Z',
    updated_at: '2026-04-01T12:00:10Z',
    parent_message_uuid: 'post-msg-1',
  }),
];

const detail = makeDetail(summary, messages, {
  current_leaf_message_uuid: 'post-msg-2',
  file_path: '/tmp/proj/fake.jsonl',
  compact_markers: [
    {
      message_uuid: 'compact-summary',
      summary_text: 'This session is being continued from a previous conversation.',
      timestamp: '2026-04-01T11:00:00Z',
      kind: 'manual',
      user_prompt: '',
    },
  ],
});

test.describe('CC compact-aware rendering', () => {
  test('first rendered bubble is the original first message, not the compact marker', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [UUID]: detail },
    });

    await withNetRetry(page, () => page.goto(`/conversations/${UUID}`));

    // Deterministic settle barrier: wait until the message stream has
    // hydrated (per feedback_playwright_settle_signals — never assume
    // sync, wait on the DOM signal that proves render finished).
    await expect(page.getByTestId('message-stream')).toBeVisible();
    await expect(page.locator('[data-message-uuid]').first()).toBeVisible();

    // 2026-05-23: rewritten to be implementation-agnostic for the
    // virtualization landing. CompactMarker carries the SAME
    // `data-message-uuid` attribute as a regular MessageBubble (see
    // CompactMarker.tsx:61), so we can compare by the message-uuid
    // attribute alone — the test no longer needs to walk a specific
    // wrapper-div depth that ChangedShape under virtualization. The
    // load-bearing assertion (the user-observable contract): the
    // FIRST message-uuid-carrying element in document order is
    // pre-msg-1, NOT compact-summary.
    const firstMessageUuid = await page
      .locator('[data-testid="message-stream"] [data-message-uuid]')
      .first()
      .getAttribute('data-message-uuid');
    expect(
      firstMessageUuid,
      'First rendered bubble must be pre-msg-1, NOT the compact-summary. ' +
        'The user-reported bug had the compact marker as the head item.',
    ).toBe('pre-msg-1');

    // NEW behavior: the compact marker IS rendered, just inline at
    // its original chronological position (between pre and post
    // messages, not at the head).
    const compactMarker = page.locator('[data-compact-marker]');
    await expect(compactMarker).toHaveCount(1);
    await expect(compactMarker).toHaveAttribute(
      'data-compact-marker',
      'compact-summary'
    );

    // Sanity: both pre-compact AND post-compact messages render.
    // A regression that "fixes" the head ordering but still drops
    // pre-compact rows would fail this.
    await expect(page.locator('[data-message-uuid="pre-msg-1"]')).toHaveCount(1);
    await expect(page.locator('[data-message-uuid="pre-msg-2"]')).toHaveCount(1);
    await expect(page.locator('[data-message-uuid="post-msg-1"]')).toHaveCount(1);
    await expect(page.locator('[data-message-uuid="post-msg-2"]')).toHaveCount(1);
  });

  test('compact marker appears between pre- and post-compact messages, not at the top', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [UUID]: detail },
    });

    await withNetRetry(page, () => page.goto(`/conversations/${UUID}`));
    await expect(page.locator('[data-message-uuid="pre-msg-1"]')).toBeVisible();

    // Bounding-box ordering check: compact marker's y-position lies
    // strictly between pre-msg-2 and post-msg-1. This is the strongest
    // "preserved at original chronological position" signal we can
    // make from outside the React tree.
    const preBottom = await page
      .locator('[data-message-uuid="pre-msg-2"]')
      .boundingBox();
    const compactBox = await page
      .locator('[data-compact-marker]')
      .boundingBox();
    const postTop = await page
      .locator('[data-message-uuid="post-msg-1"]')
      .boundingBox();

    expect(preBottom).not.toBeNull();
    expect(compactBox).not.toBeNull();
    expect(postTop).not.toBeNull();

    // pre-msg-2 sits above the compact marker, which sits above post-msg-1.
    expect(preBottom!.y).toBeLessThan(compactBox!.y);
    expect(compactBox!.y).toBeLessThan(postTop!.y);
  });

  test('sidebar title is the friendly name, not the truncated first message', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [UUID]: detail },
    });

    await withNetRetry(page, () => page.goto('/'));

    // NEW behavior: friendly title from `name` field surfaces in the
    // sidebar conversation list.
    await expect(page.getByText('Medium post creation').first()).toBeVisible();

    // OLD-behavior catch: a regression that fell back to the truncated
    // first user message would surface "first pre-compact user
    // message…" in the sidebar instead.
    await expect(page.getByText(/first pre-compact user message/)).toHaveCount(0);
  });
});
