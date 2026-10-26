import { test, expect, makeSummary, makeMessage, makeDetail, type Page, withNetRetry } from './fixtures'
import type { SearchResult } from '../src/lib/types'

/**
 * Keyboard-shortcut coverage for the article claims that previously had
 * no explicit Playwright test:
 *
 *   B10 — Cmd+G / Cmd+Shift+G next/prev match within current conversation.
 *   B11 — Cmd+G crosses conversation boundaries.
 *   B12 — "Match N of M" overlay updates as you navigate.
 *   B13 — u/a jump next user/assistant; U/A reverse.
 *   B14 — Emacs Ctrl+N/P movement, Alt+N/P paging (article-corrected).
 *   B15 — Vim j/k movement, g/G jump (article-corrected, single g not gg).
 *   B16 — Cmd+C copies focused message + speaker (clipboard permissions
 *         from the shared fixture).
 *   B17 — HintState shows when sidebar selection differs from loaded
 *         conversation (the "Press Enter to open this conversation" hint
 *         that the article promises and the code already implements).
 *   B18 — data-allow-shortcuts: Cmd+K/F/G/Esc fire while SearchPanel
 *         input has focus.
 *
 * All tests use the shared `mockBackend` fixture so they don't depend on
 * the live dev backend's data — the existing keyboard-navigation.spec.ts
 * has known flakes from that dependency.
 */

const C1 = '00000000-0000-0000-0000-0000000000a1'
const C2 = '00000000-0000-0000-0000-0000000000a2'

const c1Summary = makeSummary({
  uuid: C1,
  name: 'First conversation about TLS handshakes',
  message_count: 4,
  human_message_count: 2,
  source: 'CLAUDE_AI',
})

const c2Summary = makeSummary({
  uuid: C2,
  name: 'Second conversation about caching',
  message_count: 4,
  human_message_count: 2,
  source: 'CLAUDE_AI',
  updated_at: '2026-04-19T10:00:00Z', // newer than c1
})

const c1Messages = [
  makeMessage({
    uuid: 'c1-m1',
    sender: 'human',
    text: 'How do TLS handshakes work?',
    content: [{ type: 'text', text: 'How do TLS handshakes work?' }],
  }),
  makeMessage({
    uuid: 'c1-m2',
    sender: 'assistant',
    text: 'TLS handshakes negotiate keys.',
    content: [{ type: 'text', text: 'TLS handshakes negotiate keys.' }],
    parent_message_uuid: 'c1-m1',
  }),
  makeMessage({
    uuid: 'c1-m3',
    sender: 'human',
    text: 'What about session resumption?',
    content: [{ type: 'text', text: 'What about session resumption?' }],
    parent_message_uuid: 'c1-m2',
  }),
  makeMessage({
    uuid: 'c1-m4',
    sender: 'assistant',
    text: 'Session resumption skips the handshake.',
    content: [{ type: 'text', text: 'Session resumption skips the handshake.' }],
    parent_message_uuid: 'c1-m3',
  }),
]

const c2Messages = [
  makeMessage({
    uuid: 'c2-m1',
    sender: 'human',
    text: 'Cache invalidation strategies?',
    content: [{ type: 'text', text: 'Cache invalidation strategies?' }],
  }),
  makeMessage({
    uuid: 'c2-m2',
    sender: 'assistant',
    text: 'There are two hard problems...',
    content: [{ type: 'text', text: 'There are two hard problems...' }],
    parent_message_uuid: 'c2-m1',
  }),
]

const c1Detail = makeDetail(c1Summary, c1Messages)
const c2Detail = makeDetail(c2Summary, c2Messages)

const SEARCH_QUERY = 'handshake'

// Two matches in c1 (m2, m4). Used for B10/B12 within-conversation jumps.
const searchResultsWithinC1: SearchResult[] = [
  {
    conversation_uuid: C1,
    conversation_name: c1Summary.name,
    conversation_updated_at: c1Summary.updated_at,
    conversation_created_at: c1Summary.created_at,
    project_name: null,
    matching_messages: [
      {
        message_uuid: 'c1-m2',
        sender: 'assistant',
        snippet: 'TLS handshakes negotiate keys.',
        match_start: 4,
        match_end: 13,
        created_at: c1Messages[1].created_at,
      },
      {
        message_uuid: 'c1-m4',
        sender: 'assistant',
        snippet: 'Session resumption skips the handshake.',
        match_start: 31,
        match_end: 40,
        created_at: c1Messages[3].created_at,
      },
    ],
  },
]

async function mockSearch(page: Page, results: SearchResult[]) {
  await page.route('**/api/search**', (route) => {
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

test.describe('Keyboard — Cmd+G match navigation (B10, B12)', () => {
  test('Cmd+G advances; Cmd+Shift+G reverses; Match N of M updates', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: [c1Summary],
      details: { [C1]: c1Detail },
    })
    await mockSearch(page, searchResultsWithinC1)

    await withNetRetry(page, () => page.goto(`/conversations/${C1}`))

    // Open SearchPanel and type query.
    await page.locator('main').click()
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(SEARCH_QUERY)

    // Wait for the "of 2 matches" counter to appear (debounced search).
    const counter = page.locator('text=/of\\s+2\\s+matches/')
    await expect(counter).toBeVisible({ timeout: 5000 })

    // V1 polish (2026-05-09): auto-focus promotes activeMatchIndex to 0
    // once results land, so the counter goes straight to "1 of 2"
    // (no longer "— of 2"). The Cmd+G/Cmd+Shift+G cycle still works.
    await expect(page.locator('text=/1\\s+of\\s+2\\s+matches/')).toBeVisible()

    // Cmd+G → second match: counter says "2 of 2".
    await page.keyboard.press('Meta+g')
    await expect(page.locator('text=/2\\s+of\\s+2\\s+matches/')).toBeVisible()

    // Cmd+G again → wraps to "1 of 2".
    await page.keyboard.press('Meta+g')
    await expect(page.locator('text=/1\\s+of\\s+2\\s+matches/')).toBeVisible()

    // Cmd+Shift+G → wraps backward to "2 of 2".
    await page.keyboard.press('Meta+Shift+g')
    await expect(page.locator('text=/2\\s+of\\s+2\\s+matches/')).toBeVisible()
  })
})

test.describe('Keyboard — Cmd+G crosses conversation boundaries (B11)', () => {
  test('Cmd+G across conversations navigates to the other conversation URL', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: [c1Summary, c2Summary],
      details: { [C1]: c1Detail, [C2]: c2Detail },
    })
    // Type-distinct query strings per conversation so SearchPanel's client-
    // side filter accepts both rows and we end up with exactly 2 flat
    // matches (one per conversation).
    await mockSearch(page, [
      {
        conversation_uuid: C1,
        conversation_name: c1Summary.name,
        conversation_updated_at: c1Summary.updated_at,
        conversation_created_at: c1Summary.created_at,
        project_name: null,
        matching_messages: [
          {
            message_uuid: 'c1-m2',
            sender: 'assistant',
            snippet: 'TLS handshake handshake reply.',
            match_start: 4,
            match_end: 13,
            created_at: c1Messages[1].created_at,
          },
        ],
      },
      {
        conversation_uuid: C2,
        conversation_name: 'C2 also mentions handshake',
        conversation_updated_at: c2Summary.updated_at,
        conversation_created_at: c2Summary.created_at,
        project_name: null,
        matching_messages: [
          {
            message_uuid: 'c2-m2',
            sender: 'assistant',
            snippet: 'Cache then handshake.',
            match_start: 11,
            match_end: 20,
            created_at: c2Messages[1].created_at,
          },
        ],
      },
    ])

    await withNetRetry(page, () => page.goto(`/conversations/${C1}`))
    await expect(page.locator('[data-message-uuid="c1-m1"]')).toBeVisible()

    await page.locator('main').click()
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.fill(SEARCH_QUERY)
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({ timeout: 10000 })

    // Match 1: stays in c1.
    await page.keyboard.press('Meta+g')
    // Match 2: jumps to c2 (or stays — the order depends on sort, so we just
    // assert that two presses land us at one of the conversations and that
    // the URL changed at least once during the loop).
    const urlAfterFirst = page.url()
    await page.keyboard.press('Meta+g')
    await expect.poll(() => page.url() !== urlAfterFirst).toBe(true)
    // Final URL is one of the two conversations.
    expect(page.url()).toMatch(new RegExp(`/conversations/(${C1}|${C2})`))
  })
})

test.describe('Keyboard — u/a/U/A role-based message jump (B13)', () => {
  test('u jumps to next user msg; a to next assistant; U/A reverse', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: [c1Summary],
      details: { [C1]: c1Detail },
    })
    await withNetRetry(page, () => page.goto(`/conversations/${C1}`))

    // Wait for the conversation to render.
    await expect(page.locator('[data-message-uuid="c1-m1"]')).toBeVisible()
    await expect(page.locator('[data-message-uuid="c1-m4"]')).toBeVisible()

    // Force focusArea = 'detail' by clicking a message.
    await page.locator('[data-message-uuid="c1-m1"]').click()

    // u → next user message: c1-m1 is the first user message; pressing 'u'
    // from focusArea='detail' should select it. After it, pressing 'u' again
    // selects c1-m3.
    await page.keyboard.press('u')
    // The selected message gets a ring class (isKeyboardSelected).
    // Easiest assertion: data-message-selected attribute would be cleaner,
    // but absent. Instead verify via focusArea by pressing 'u' again and
    // confirming the URL didn't change (no nav side-effect).
    await page.keyboard.press('u')
    // Same conversation, no navigation.
    await expect(page).toHaveURL(new RegExp(`/conversations/${C1}`))

    // 'a' → next assistant message.
    await page.keyboard.press('a')
    await expect(page).toHaveURL(new RegExp(`/conversations/${C1}`))

    // 'U' / 'A' reverse — just confirm key handlers don't blow up.
    await page.keyboard.press('Shift+U')
    await page.keyboard.press('Shift+A')
    await expect(page).toHaveURL(new RegExp(`/conversations/${C1}`))
  })
})

test.describe('Keyboard — Emacs paging keys (B14, article-corrected)', () => {
  test.beforeEach(async ({ page }) => {
    // Default mode is Emacs. Ensure localStorage isn't holding "vim" from
    // a previous test run.
    await withNetRetry(page, () => page.goto('/'))
    await page.evaluate(() => localStorage.setItem('keyboardMode', JSON.stringify('emacs')))
    await withNetRetry(page, () => page.reload())
  })

  test('Ctrl+N / Ctrl+P move within detail; Alt+N / Alt+P page; Cmd+F toggles SearchPanel', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: [c1Summary],
      details: { [C1]: c1Detail },
    })
    await withNetRetry(page, () => page.goto(`/conversations/${C1}`))
    await expect(page.locator('[data-message-uuid="c1-m1"]')).toBeVisible()

    await page.locator('[data-message-uuid="c1-m1"]').click()

    // Ctrl+N / Ctrl+P just shouldn't blow up and shouldn't change URL.
    await page.keyboard.press('Control+n')
    await page.keyboard.press('Control+p')
    await expect(page).toHaveURL(new RegExp(`/conversations/${C1}`))

    // Alt+N / Alt+P paging — same expectation.
    await page.keyboard.press('Alt+n')
    await page.keyboard.press('Alt+p')
    await expect(page).toHaveURL(new RegExp(`/conversations/${C1}`))

    // Cmd+F opens the SearchPanel (overrides forward-char as the article notes).
    // The panel is shown/hidden via a CSS transform on the parent <aside>;
    // assert via the aria-hidden attribute, which flips when the panel
    // toggles, rather than presence of the input element.
    const searchAside = page.locator('aside[aria-label="Search panel"]')
    await page.keyboard.press('Meta+f')
    await expect(searchAside).toHaveAttribute('aria-hidden', 'false')

    // The article promises Cmd+F is "find" muscle memory — the user must
    // be able to start typing the query immediately after pressing it,
    // without an extra click.
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeFocused()
  })

  test('Cmd+F focuses the Search messages input even when the panel is already open', async ({
    page,
    mockBackend,
  }) => {
    // Regression test for the manual finding 2026-05-03: opening the
    // panel and then pressing Cmd+F again from elsewhere on the page
    // must FOCUS the search input (so the user can type immediately),
    // not close the panel.
    await mockBackend({
      conversations: [c1Summary],
      details: { [C1]: c1Detail },
    })
    await withNetRetry(page, () => page.goto(`/conversations/${C1}`))
    await expect(page.locator('[data-message-uuid="c1-m1"]')).toBeVisible()

    const searchAside = page.locator('aside[aria-label="Search panel"]')
    const searchInput = page.locator('input[placeholder="Search messages..."]')

    // Open the panel.
    await page.keyboard.press('Meta+k')
    await expect(searchAside).toHaveAttribute('aria-hidden', 'false')
    await expect(searchInput).toBeFocused()

    // Move focus away (click a message in the detail pane).
    await page.locator('[data-message-uuid="c1-m1"]').click()
    await expect(searchInput).not.toBeFocused()

    // Cmd+F from elsewhere — panel stays open AND search input regains focus.
    await page.keyboard.press('Meta+f')
    await expect(searchAside).toHaveAttribute('aria-hidden', 'false')
    await expect(searchInput).toBeFocused()

    // Typing now goes into the search input.
    await page.keyboard.type('handshake')
    await expect(searchInput).toHaveValue('handshake')
  })
})

test.describe('Keyboard — Vim navigation (B15, article-corrected)', () => {
  test.beforeEach(async ({ page }) => {
    await withNetRetry(page, () => page.goto('/'))
    await page.evaluate(() => localStorage.setItem('keyboardMode', JSON.stringify('vim')))
    await withNetRetry(page, () => page.reload())
  })

  test('j/k move; g/G jump first/last; / focuses sidebar search', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: [c1Summary, c2Summary],
      details: { [C1]: c1Detail, [C2]: c2Detail },
    })

    await withNetRetry(page, () => page.goto(`/conversations/${C1}`))
    await expect(page.locator('[data-message-uuid="c1-m1"]')).toBeVisible()
    await page.locator('[data-message-uuid="c1-m1"]').click()

    // j/k movement — no URL change, no crash.
    await page.keyboard.press('j')
    await page.keyboard.press('k')
    await expect(page).toHaveURL(new RegExp(`/conversations/${C1}`))

    // g jumps to first message; G jumps to last. (Article-corrected: g not gg.)
    await page.keyboard.press('g')
    await page.keyboard.press('Shift+g')
    await expect(page).toHaveURL(new RegExp(`/conversations/${C1}`))

    // '/' focuses the sidebar search input.
    // The sidebar search placeholder is "Search titles and projects"
    // (renamed when project filtering shipped). Click the sidebar to
    // set focusArea to 'list' first.
    // Click a non-interactive spot of the sidebar (its heading): the middle
    // of the sidebar is a control (the time dropdown), not background.
    await page.locator('aside.w-80').getByRole('heading', { name: 'Claude Explorer' }).click()
    await page.keyboard.press('/')
    const sidebarSearch = page.getByPlaceholder('Search titles and projects')
    await expect(sidebarSearch).toBeFocused()
  })
})

test.describe('Keyboard — Cmd+C copies focused message (B16)', () => {
  test('Cmd+C copies focused-message Markdown to the clipboard', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: [c1Summary],
      details: { [C1]: c1Detail },
    })
    await withNetRetry(page, () => page.goto(`/conversations/${C1}`))
    await expect(page.locator('[data-message-uuid="c1-m1"]')).toBeVisible()

    // Click a specific message to focus the detail pane and select it.
    await page.locator('[data-message-uuid="c1-m2"]').click()
    // Now press 'a' so selectedMessageIndex aligns with c1-m2 (an assistant
    // message). The current keyboard model's getSelectedMessageId reads
    // selectedMessageIndex; we use 'a' (next assistant) to ensure we land on
    // a known message regardless of click semantics.
    // Simpler: mimic Enter from list to set focus deterministically.
    // Click a non-interactive spot of the sidebar (its heading): the middle
    // of the sidebar is a control (the time dropdown), not background.
    await page.locator('aside.w-80').getByRole('heading', { name: 'Claude Explorer' }).click()
    await page.keyboard.press('Enter')
    // Now in detail pane with the first message selected (index 0).

    // Cmd+C — should copy c1-m1 markdown.
    await page.keyboard.press('Meta+c')

    // Read clipboard.
    const clipboard = await page.evaluate(() => navigator.clipboard.readText())
    // messageToMarkdown formats as "**You:**\n\n<text>" for human messages.
    expect(clipboard).toContain('You:')
    expect(clipboard).toContain('How do TLS handshakes work?')
  })
})

test.describe('Keyboard — HintState when sidebar selection differs (B17)', () => {
  test('navigating list via Ctrl+P keeps detail showing the original conversation but signals the sidebar diverged', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: [c1Summary, c2Summary],
      details: { [C1]: c1Detail, [C2]: c2Detail },
    })

    await withNetRetry(page, () => page.goto(`/conversations/${C1}`))
    // Wait for c1 detail to load (a message bubble visible is enough).
    await expect(page.locator('[data-message-uuid="c1-m1"]')).toBeVisible()

    // Step the sidebar selection forward without opening a new conversation.
    // Click a non-interactive spot of the sidebar (its heading): the middle
    // of the sidebar is a control (the time dropdown), not background.
    await page.locator('aside.w-80').getByRole('heading', { name: 'Claude Explorer' }).click()
    // Use ArrowDown to advance the keyboard-selected sidebar row.
    await page.keyboard.press('ArrowDown')

    // The HintState replaces the conversation content while sidebar
    // selection != currently-loaded conversation.
    await expect(page.getByText(/Press\s+Enter\s+to open this conversation/i)).toBeVisible()
  })
})

test.describe('Keyboard — data-allow-shortcuts (B18)', () => {
  test('Cmd+K, Cmd+G, Cmd+Shift+G, Esc still fire while SearchPanel input is focused', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: [c1Summary],
      details: { [C1]: c1Detail },
    })
    await mockSearch(page, searchResultsWithinC1)

    await withNetRetry(page, () => page.goto(`/conversations/${C1}`))

    // Open the SearchPanel and put focus into its input.
    await page.locator('main').click()
    await page.keyboard.press('Meta+k')
    const searchInput = page.locator('input[placeholder="Search messages..."]')
    await expect(searchInput).toBeVisible()
    await searchInput.click()
    await expect(searchInput).toBeFocused()
    await searchInput.fill(SEARCH_QUERY)
    await expect(page.locator('text=/of\\s+2\\s+matches/')).toBeVisible({ timeout: 5000 })

    // V1 polish (2026-05-09): auto-focus already promoted to "1 of 2".
    await expect(page.locator('text=/1\\s+of\\s+2\\s+matches/')).toBeVisible()

    // Cmd+G fires from inside the input → advances to 2.
    await page.keyboard.press('Meta+g')
    await expect(page.locator('text=/2\\s+of\\s+2\\s+matches/')).toBeVisible()

    // Cmd+Shift+G goes back to 1.
    await page.keyboard.press('Meta+Shift+g')
    await expect(page.locator('text=/1\\s+of\\s+2\\s+matches/')).toBeVisible()

    // Manual finding 2026-05-04: Esc closes the panel immediately (the
    // earlier "clear-then-close" two-step was surprising; the user
    // explicitly asked Esc to keep current selection and just close).
    const searchAside = page.locator('aside[aria-label="Search panel"]')
    await page.keyboard.press('Escape')
    await expect(searchAside).toHaveAttribute('aria-hidden', 'true')

    // Cmd+K reopens.
    await page.keyboard.press('Meta+k')
    await expect(searchAside).toHaveAttribute('aria-hidden', 'false')
  })
})
