import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'
import type { Message, ContentBlock } from '../src/lib/types'

/**
 * Case 6 — Thinking blocks (V1 polish 2026-05-13).
 *
 * Spec invariants pinned by THIS spec (browser-visible):
 *
 *   1. **Viewer drop.** A Claude Code assistant message containing a
 *      `thinking` content block MUST render its sibling `text` block
 *      normally but MUST NOT surface the thinking text anywhere in the
 *      bubble. The frontend `ContentBlockRenderer` (MessageBubble.tsx
 *      ~L499-534) has no `case 'thinking':` arm; thinking falls through
 *      to `default: return null`. This spec pins that behavior so a
 *      future refactor (e.g. adding `case 'thinking':` without a render
 *      guard) can't silently leak thinking content into V1.
 *
 *   2. **Clipboard drop ("one truth, three surfaces").** The bubble's
 *      Copy-as-Markdown button (MessageBubble handleCopyMessage) calls
 *      `messageToMarkdown(message, showToolCalls)` (lib/utils.ts).
 *      `contentBlockToMarkdown` likewise has no `case 'thinking':` and
 *      falls through to `default: return ''`. Asserting the resulting
 *      clipboard text excludes the thinking needle pins that the
 *      viewer's drop also propagates to the export/copy surface — which
 *      is the spec's promise.
 *
 * Settle pattern (per `feedback_playwright_settle_signals`):
 *   - Step 1: `getByTestId('message-stream')` visible — page has hydrated.
 *   - Step 2: the target bubble (`[data-message-uuid=...]`) visible.
 *   - Step 3: a POSITIVE assertion on the sibling text content
 *     (`toContainText('Visible body needle')`) — proves the renderer
 *     walked the entire `content` array. If thinking was going to leak,
 *     it would have leaked by the time the sibling text rendered.
 *   - Step 4: SAFE NEGATIVE assertion that the thinking needle is
 *     absent. By construction (steps 1-3 satisfied), this is no longer
 *     a race.
 *
 * Bidirectional verification (per TESTING.md §2):
 *   - "X renders WITH condition": regular text DOES render → asserted
 *     in test A via `toContainText`.
 *   - "X does NOT render WITHOUT condition": thinking DOES NOT render
 *     → asserted in test A via `not.toContainText`.
 *   - Counter-case: a Desktop message WITHOUT any thinking block STILL
 *     renders normally (sanity that the negative isn't a false positive
 *     because the renderer crashed). Asserted in test C.
 *   - Clipboard inverse: a message with NO thinking block produces
 *     clipboard text that CONTAINS the body — proves the assertion
 *     "clipboard excludes thinking" isn't a false positive because
 *     clipboard read is somehow broken. Asserted via the test B's
 *     positive assertion that the visible body IS in clipboard.
 *
 * Why no search-side spec here:
 *   The V1 polish 2026-05-13 (just shipped) made `backend/search.py`
 *   stop indexing `thinking` blocks at all in FTS5 (see comments at
 *   search.py L66-74, L146-155). The frontend has no thinking-related
 *   search logic. Adding a Playwright spec that mocks /api/search to
 *   return 0 hits for a thinking-only term would be testing the mock,
 *   not the app. If FTS5 ever re-enables thinking indexing (i.e., the
 *   "Show thinking" affordance ships), revisit this — add a combined
 *   search-ghost spec at that time.
 */

const C = '00000000-0000-0000-0000-00000000th01'

// A NON-MEANINGFUL word is used as the thinking-needle so a false-positive
// substring match in chrome (timestamp, role label, etc.) is impossible.
// Long enough that incidental render of any UI glyph can't hit it.
const THINKING_NEEDLE = 'XQTHINKINGNEEDLEXQ_secret_internal_reasoning'
const VISIBLE_BODY = 'Visible assistant reply body — XVISIBLEXBODYX'

// Content blocks include a 'thinking' type. The frontend `ContentBlock`
// union (frontend/src/lib/types.ts L60) does NOT include 'thinking' as a
// member — that's intentional: V1 viewer has no renderer for it, so the
// type narrows to the four it CAN render. We cast in tests via
// `as unknown as ContentBlock` to mimic what the backend would actually
// send. Other specs (e.g. search-match-focus-mismatch.spec.ts) use the
// same `as unknown as never` pattern for tool_use blocks that aren't in
// the narrowed type either; we stay consistent with that convention.
const blocks: ContentBlock[] = [
  { type: 'text', text: VISIBLE_BODY },
  // The 'thinking' branch is what we're proving is silently dropped.
  {
    type: 'thinking',
    text: THINKING_NEEDLE,
  } as unknown as ContentBlock,
]

const summary = makeSummary({
  uuid: C,
  name: 'Thinking block fixture',
  source: 'CLAUDE_CODE',
  message_count: 2,
  human_message_count: 1,
  project_path: '/tmp/proj',
  project_name: 'proj',
})

const messages = [
  makeMessage({
    uuid: 'th-user',
    sender: 'human',
    text: 'A regular prompt with no thinking.',
    content: [{ type: 'text', text: 'A regular prompt with no thinking.' }],
    created_at: '2026-05-13T10:00:00Z',
    updated_at: '2026-05-13T10:00:00Z',
    parent_message_uuid: null,
  }),
  makeMessage({
    uuid: 'th-assistant',
    sender: 'assistant',
    // message.text is the JOIN of visible content for legacy renderers;
    // we deliberately exclude the thinking needle from `text` because
    // the backend extracts thinking into a separate `thinking_content`
    // field (per spec Case 6 invariant 1: "thinking_content text MUST
    // NOT appear as a substring of content on the same message"). If a
    // future regression concatenates thinking into the body string,
    // both the viewer and clipboard tests below would catch it.
    text: VISIBLE_BODY,
    content: blocks,
    created_at: '2026-05-13T10:00:05Z',
    updated_at: '2026-05-13T10:00:05Z',
    parent_message_uuid: 'th-user',
  } as Partial<Message> & { uuid: string }),
]

const detail = makeDetail(summary, messages, {
  current_leaf_message_uuid: 'th-assistant',
  file_path: '/tmp/proj/fake.jsonl',
})

test.describe('CC Case 6 — thinking blocks silently dropped in viewer', () => {
  test('A: thinking content is NOT rendered; visible body IS rendered', async ({
    page,
    mockBackend,
  }) => {
    await mockBackend({
      conversations: [summary],
      details: { [C]: detail },
    })

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))

    // Step 1: page-level settle.
    await expect(page.getByTestId('message-stream')).toBeVisible()
    // Step 2: assistant bubble attached.
    const bubble = page.locator('[data-message-uuid="th-assistant"]')
    await expect(bubble).toBeVisible()
    // Step 3: POSITIVE settle — the visible body IS rendered. This
    // proves `ContentBlockRenderer` walked the content array and the
    // text-block case fired. If the renderer crashed on the thinking
    // block, this assertion would fail loud.
    await expect(bubble).toContainText(VISIBLE_BODY)

    // Step 4: SAFE NEGATIVE — the thinking needle is absent anywhere
    // in the bubble. Pinning the `default: return null` branch.
    await expect(bubble).not.toContainText(THINKING_NEEDLE)

    // Stronger guarantee: the needle is also absent from the ENTIRE
    // page (not just the bubble). Cheap sanity for "no other widget
    // accidentally surfaces it" (e.g. a debug pane, a future
    // 'thinking sidebar' that someone forgets to gate).
    await expect(page.getByText(THINKING_NEEDLE)).toHaveCount(0)
  })

  test('B: clipboard copy excludes thinking content, includes visible body', async ({
    page,
    mockBackend,
  }) => {
    // "One truth, three surfaces": the spec promises the viewer's drop
    // of thinking ALSO propagates to copy-as-markdown / export. The
    // backend Markdown export pipeline has its own pytest coverage;
    // this spec pins the browser's `navigator.clipboard.writeText`
    // produced by `messageToMarkdown` (lib/utils.ts).

    await mockBackend({
      conversations: [summary],
      details: { [C]: detail },
    })

    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    await expect(page.getByTestId('message-stream')).toBeVisible()

    const bubble = page.locator('[data-message-uuid="th-assistant"]')
    await expect(bubble).toBeVisible()
    // Settle: visible body rendered → safe to interact.
    await expect(bubble).toContainText(VISIBLE_BODY)

    // The Copy button is in the hover-revealed action cluster (the
    // wrapper has `opacity-0 group-hover:opacity-100`). Hovering
    // brings it into the interactable layer for Playwright. Use the
    // `title` attribute — that's the stable accessible label set in
    // MessageBubble (L174 "Copy message as Markdown").
    await bubble.hover()
    const copyBtn = bubble.locator('button[title="Copy message as Markdown"]')
    await expect(copyBtn).toBeVisible()

    await copyBtn.click()

    // Deterministic post-click settle: MessageBubble sets `copied=true`
    // for 2s after a successful write, which swaps the <Copy/> icon for
    // a <Check/> with class `text-green-500`. Asserting the green-tinted
    // descendant proves the write resolved and React re-rendered. After
    // this point, the clipboard read is race-free.
    await expect(copyBtn.locator('.text-green-500')).toBeVisible()

    const clipboardText = await page.evaluate(() =>
      navigator.clipboard.readText(),
    )

    // POSITIVE: clipboard contains the visible body — proves clipboard
    // read works and is not just empty (would otherwise pass the
    // negative trivially).
    expect(clipboardText).toContain(VISIBLE_BODY)

    // NEGATIVE: clipboard does NOT contain the thinking needle. Pins
    // `contentBlockToMarkdown`'s default-return-empty branch for the
    // thinking case (lib/utils.ts ~L114).
    expect(clipboardText).not.toContain(THINKING_NEEDLE)
  })

  test('C: counter-case — a message with NO thinking block renders normally', async ({
    page,
    mockBackend,
  }) => {
    // Counter case (bidirectional inversion): if the renderer were
    // accidentally dropping ALL assistant content (e.g. a regression
    // that broke the text-block case), test A would still pass for the
    // wrong reason (bubble empty == thinking needle absent). This
    // counter-case fixture has no thinking block and asserts the body
    // STILL renders, so a global render breakage would surface here.

    const noThinkUuid = '00000000-0000-0000-0000-00000000th02'
    const noThinkSummary = makeSummary({
      uuid: noThinkUuid,
      name: 'No thinking',
      source: 'CLAUDE_CODE',
      message_count: 1,
      human_message_count: 0,
      project_path: '/tmp/proj',
      project_name: 'proj',
    })
    const noThinkMessages = [
      makeMessage({
        uuid: 'nt-a',
        sender: 'assistant',
        text: VISIBLE_BODY,
        content: [{ type: 'text', text: VISIBLE_BODY }],
      }),
    ]
    const noThinkDetail = makeDetail(noThinkSummary, noThinkMessages, {
      current_leaf_message_uuid: 'nt-a',
    })

    await mockBackend({
      conversations: [noThinkSummary],
      details: { [noThinkUuid]: noThinkDetail },
    })

    await withNetRetry(page, () => page.goto(`/conversations/${noThinkUuid}`))
    await expect(page.getByTestId('message-stream')).toBeVisible()
    const bubble = page.locator('[data-message-uuid="nt-a"]')
    await expect(bubble).toBeVisible()
    await expect(bubble).toContainText(VISIBLE_BODY)
  })
})
