import { test, expect, makeSummary, makeMessage, makeDetail, type Page, type Route, withNetRetry, waitForKeyboardShortcuts } from './fixtures'
import type { Message } from '../src/lib/types'

/**
 * Manual finding 2026-05-04: search focus model.
 *
 *   - Cmd+G / Cmd+Shift+G: MOVE keyboard focus to the matching bubble in
 *     the conversation pane, so the user can Cmd+C the message body.
 *     (Updated 2026-05-23: prior contract pinned focus to the input,
 *     which made Cmd+C silently fall to the browser default. The user-
 *     observable contract is now "Cmd+G + Cmd+C copies the matched
 *     message" — the bubble must own DOM focus for that to work.)
 *     An aria-live region in the panel header still announces
 *     "Match N of M" for screen-reader users.
 *   - Enter on the active result card: focus the corresponding message
 *     in the conversation pane; SearchPanel stays open.
 *   - Esc: close the SearchPanel and focus the message that
 *     activeMatchIndex pointed at (preserves "land near hit, then read
 *     around it" workflow).
 */

const C = '00000000-0000-0000-0000-000000fffff0'

const summary = makeSummary({
  uuid: C,
  source: 'CLAUDE_CODE',
  message_count: 3,
  project_path: '/work/projectX',
  project_name: 'projectX',
  name: 'Focus model fixture',
})

const m1 = makeMessage({
  uuid: 'msg-1',
  sender: 'human',
  text: 'first needle line',
  content: [{ type: 'text', text: 'first needle line' }],
} as Partial<Message> & { uuid: string })
const m2 = makeMessage({
  uuid: 'msg-2',
  sender: 'assistant',
  text: 'second needle line',
  content: [{ type: 'text', text: 'second needle line' }],
} as Partial<Message> & { uuid: string })
const m3 = makeMessage({
  uuid: 'msg-3',
  sender: 'human',
  text: 'third needle line',
  content: [{ type: 'text', text: 'third needle line' }],
} as Partial<Message> & { uuid: string })

const detail = makeDetail(summary, [m1, m2, m3])

async function mockSearchResults(page: Page) {
  await page.route('**/api/search**', (route: Route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        results: [
          {
            conversation_uuid: C,
            conversation_name: 'Focus model fixture',
            conversation_updated_at: summary.updated_at,
            conversation_created_at: summary.created_at,
            project_name: 'projectX',
            matching_messages: [
              {
                message_uuid: 'msg-1',
                sender: 'human',
                snippet: 'first needle line',
                match_start: 6,
                match_end: 12,
                created_at: m1.created_at,
              },
              {
                message_uuid: 'msg-2',
                sender: 'assistant',
                snippet: 'second needle line',
                match_start: 7,
                match_end: 13,
                created_at: m2.created_at,
              },
              {
                message_uuid: 'msg-3',
                sender: 'human',
                snippet: 'third needle line',
                match_start: 6,
                match_end: 12,
                created_at: m3.created_at,
              },
            ],
          },
        ],
        total_messages_matched: 3,
        returned_messages: 3,
        truncated: false,
      }),
    })
  })
}

async function openPanelAndType(page: Page, q: string) {
  const isMac = process.platform === 'darwin'
  // The keydown listener attaches in an effect after the first commit; a
  // Cmd/Ctrl+F before it is lost (2026-10-26 CI flake: the panel stayed
  // closed). The closed panel is still in the page, slid off-screen, so
  // toBeVisible() passed and input.click() waited for the off-screen
  // input until the 60 s test timeout. Wait for the listener, then for
  // the panel to report itself open.
  await waitForKeyboardShortcuts(page, 'emacs')
  await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
  await expect(page.locator('[aria-label="Search panel"]')).toHaveAttribute('aria-hidden', 'false')
  const input = page.getByPlaceholder('Search messages...')
  await expect(input).toBeVisible({ timeout: 3000 })
  await input.click()
  await input.fill(q)
  await expect(page.getByText(/of\s+3\s+matches/)).toBeVisible({ timeout: 5000 })
}

test.describe('Search focus model (manual finding 2026-05-04)', () => {
  test('Cmd+G moves focus to the matching bubble so Cmd+C copies the message', async ({ page, mockBackend, context }) => {
    // 2026-05-23 contract change: prior behavior pinned focus on the
    // input after Cmd+G. That pin ratified a bug — pressing Cmd+C
    // immediately after Cmd+G silently fell to the browser default
    // (copy nothing, because the input had no selection). The user-
    // observable contract is "Cmd+G then Cmd+C copies the matched
    // message". For that to work, the bubble must own DOM focus AFTER
    // Cmd+G. (Search input stays available — Cmd+F brings focus back.)
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockSearchResults(page)
    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    await openPanelAndType(page, 'needle')

    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeFocused()

    const isMac = process.platform === 'darwin'
    // Cmd+G advances activeMatchIndex from 0 → 1 (msg-2). The new
    // design's downstream effect calls navigateToMatch with focus:true
    // for source='user' navigations.
    await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')

    // The matching bubble must be the focused element.
    const bubble = page.locator('[data-message-uuid="msg-2"]')
    await expect(bubble).toBeFocused({ timeout: 2000 })

    // And Cmd+C must copy the bubble's content (proves the focus
    // transfer enabled the previously-broken copy path).
    await page.keyboard.press(isMac ? 'Meta+c' : 'Control+c')
    const clipboard = await page.evaluate(() => navigator.clipboard.readText())
    expect(clipboard).toContain('second needle line')
  })

  test('aria-live region announces "Match N of M"', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockSearchResults(page)
    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    await openPanelAndType(page, 'needle')

    const live = page.locator('[data-testid="search-match-aria-live"]')
    await expect(live).toHaveAttribute('aria-live', 'polite')

    // V1 polish: auto-focus promotes activeMatchIndex to 0 once results
    // land, so the live region already shows "Match 1 of 3" without any
    // Cmd+G press. Verify that, then advance to match 2 and 3.
    await expect(live).toContainText(/Match\s+1\s+of\s+3/i, { timeout: 3000 })

    const isMac = process.platform === 'darwin'
    await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')
    await expect(live).toContainText(/Match\s+2\s+of\s+3/i, { timeout: 3000 })

    await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')
    await expect(live).toContainText(/Match\s+3\s+of\s+3/i, { timeout: 3000 })
  })

  test('Enter on the active match focuses the message and keeps panel open', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockSearchResults(page)
    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    await openPanelAndType(page, 'needle')

    // V1 polish: auto-focus already promoted activeMatchIndex to 0 once
    // results landed, so there's already an active card — no Cmd+G
    // needed before Enter.

    // Enter on the input commits "open active match" → focuses the message.
    await page.keyboard.press('Enter')

    // The selected message bubble should now have actual DOM focus.
    const bubble = page.locator('[data-message-uuid="msg-1"]')
    await expect(bubble).toBeVisible()
    await expect(bubble).toBeFocused()

    // Panel stays open (aria-hidden=false on the aside).
    const aside = page.locator('aside[aria-label="Search panel"]')
    await expect(aside).toHaveAttribute('aria-hidden', 'false')
  })

  test('Cross-conversation Enter focuses the target message in the new conversation', async ({ page, mockBackend }) => {
    // Set up TWO conversations. Initially open conversation A. Search hits in B.
    // Soft concern from council on commit 113da97: cross-conversation case
    // had a race where requestAnimationFrame(() => el.focus()) fired before
    // the new conversation's bubbles were mounted, so .focus() was a no-op.
    const A = '00000000-0000-0000-0000-00000000aa01'
    const B = '00000000-0000-0000-0000-00000000bb02'

    const summaryA = makeSummary({ uuid: A, name: 'Conv A', source: 'CLAUDE_CODE', message_count: 1, project_path: '/x', project_name: 'x' })
    const summaryB = makeSummary({ uuid: B, name: 'Conv B', source: 'CLAUDE_CODE', message_count: 1, project_path: '/x', project_name: 'x' })

    const mA = makeMessage({ uuid: 'a-msg-1', sender: 'human', text: 'lorem', content: [{ type: 'text', text: 'lorem' }] } as Partial<Message> & { uuid: string })
    const mB = makeMessage({ uuid: 'b-msg-1', sender: 'assistant', text: 'needle in B', content: [{ type: 'text', text: 'needle in B' }] } as Partial<Message> & { uuid: string })

    const detailA = makeDetail(summaryA, [mA])
    const detailB = makeDetail(summaryB, [mB])
    await mockBackend({ conversations: [summaryA, summaryB], details: { [A]: detailA, [B]: detailB } })

    // Mock /api/search to return one match — in B.
    await page.route('**/api/search**', (route: Route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          results: [{
            conversation_uuid: B,
            conversation_name: 'Conv B',
            conversation_updated_at: summaryB.updated_at,
            conversation_created_at: summaryB.created_at,
            project_name: 'x',
            matching_messages: [{ message_uuid: 'b-msg-1', sender: 'assistant', snippet: 'needle in B', match_start: 0, match_end: 6, created_at: mB.created_at }],
          }],
          total_messages_matched: 1,
          returned_messages: 1,
          truncated: false,
        }),
      })
    })

    // F7 audit — assert BEFORE state right after panel opens, BEFORE
    // typing the query. Once results land, the V1 auto-focus effect
    // navigates cross-conv on its own and the URL changes; the BEFORE
    // state has to be captured before that happens, otherwise a
    // regression where navigation fires on input mount (not Enter)
    // would still pass.
    //
    // 1. URL is on conv A — auto-focus hasn't fired yet.
    // 2. B's bubble is NOT in the DOM yet (cross-conv navigation hasn't
    //    happened).
    // 3. The search input has focus (panel opened, ready to type).
    await withNetRetry(page, () => page.goto(`/conversations/${A}`))
    const isMac = process.platform === 'darwin'
    // Wait for the keydown listener before Cmd/Ctrl+F (see openPanelAndType).
    await waitForKeyboardShortcuts(page, 'emacs')
    await page.keyboard.press(isMac ? 'Meta+f' : 'Control+f')
    await expect(page.locator('[aria-label="Search panel"]')).toHaveAttribute('aria-hidden', 'false')
    const input = page.getByPlaceholder('Search messages...')
    await expect(input).toBeVisible({ timeout: 3000 })

    await expect(page).toHaveURL(new RegExp(`/conversations/${A}`))
    await expect(page.locator('[data-message-uuid="b-msg-1"]')).toHaveCount(0)
    await expect(input).toBeFocused()

    // Type query. Auto-focus then navigates cross-conv to B, mounts B's
    // bubble, and (via ConversationPage's ?highlight= handler) focuses
    // the target bubble after a 100ms timer. Wait for that full
    // settling so the Cmd+G+Enter below is exercising the openActiveMatch
    // re-navigate path, not racing the initial navigation.
    await input.fill('needle')
    await expect(page.getByText(/of\s+1\s+matches/)).toBeVisible({ timeout: 5000 })

    await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')
    await page.keyboard.press('Enter')

    // URL changed to B (auto-focus + Enter both target same conv now);
    // bubble in B is visible AND focused (openActiveMatch enforces focus
    // via rAF + .focus() — also reinforced by ConversationPage's
    // ?highlight= effect).
    await expect(page).toHaveURL(new RegExp(`/conversations/${B}`))
    const bubble = page.locator('[data-message-uuid="b-msg-1"]')
    await expect(bubble).toBeVisible({ timeout: 5000 })
    await expect(bubble).toBeFocused({ timeout: 5000 })
  })

  test('Esc closes the panel and keeps focus on the active-match message', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: [summary], details: { [C]: detail } })
    await mockSearchResults(page)
    await withNetRetry(page, () => page.goto(`/conversations/${C}`))
    await openPanelAndType(page, 'needle')

    const isMac = process.platform === 'darwin'
    // Step to second match.
    await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')
    await page.keyboard.press(isMac ? 'Meta+g' : 'Control+g')

    // Esc closes the panel immediately and focuses the active-match
    // message (manual finding 2026-05-04: keep current selection).
    await page.keyboard.press('Escape')

    // Panel slides off-screen; assert aria-hidden=true on the panel
    // root rather than visibility (translate-x-full leaves "visible").
    await expect.poll(async () => {
      return await page.evaluate(() => {
        const el = document.querySelector('[aria-label="Search panel"]')
        return el?.getAttribute('aria-hidden')
      })
    }, { timeout: 3000 }).toBe('true')

    // The active-match message (msg-2) should still be visible.
    const bubble = page.locator('[data-message-uuid="msg-2"]')
    await expect(bubble).toBeVisible()
  })
})
