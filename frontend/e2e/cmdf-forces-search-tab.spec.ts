import { test, expect, withNetRetry, waitForKeyboardShortcuts } from './fixtures'

/**
 * V1 polish — Cmd+F must always switch the right-pane tab to "search",
 * NOT honor the persisted rightPaneTab preference.
 *
 * The bug: `rightPaneTab` is stored in server-side preferences (so a
 * user's last tab selection persists across reloads and tabs). If the
 * user ever clicked the Bookmarks tab, every subsequent Cmd+F would
 * open the panel showing Bookmarks — not what "find" muscle memory
 * expects.
 *
 * Fix: useKeyboardShortcuts.ts Cmd+F handler now calls
 * setRightPaneTab('search') before searchPanel.requestFocus().
 */

test('Cmd+F forces the right-pane tab to "search" regardless of persisted preference', async ({ page, mockBackend }) => {
  // Seed preferences with the panel already open AND rightPaneTab=bookmarks,
  // so on initial mount the user sees Bookmarks as the active tab. This
  // mimics a user who clicked Bookmarks at some point and closed/reopened
  // the app with that state persisted server-side.
  //
  // We can't open the panel via Cmd+K to set up this state, because
  // Cmd+K itself force-sets rightPaneTab='search' on open (the parallel
  // fix in `cmd-k-always-opens-search-tab.spec.ts`). Seeding `isOpen: true`
  // lets the panel hydrate directly from preferences with no shortcut
  // interaction.
  await mockBackend({
    preferences: { rightPaneTab: 'bookmarks', 'searchPanel.isOpen': true },
  })

  await withNetRetry(page, () => page.goto('/'))
  await page.locator('main').click()
  const aside = page.locator('aside[aria-label="Search panel"]')
  await expect(aside).toBeVisible()

  // Bookmarks tab should be active per the seeded preference.
  const bookmarksTab = page.getByRole('tab', { name: /Bookmarks/ })
  await expect(bookmarksTab).toHaveAttribute('aria-selected', 'true')

  // Now Cmd+F: the panel was already open on Bookmarks, but Cmd+F is
  // "find" muscle memory and MUST switch to Search.
  await page.keyboard.press('Meta+f')

  const searchTab = page.getByRole('tab', { name: /Search/ })
  await expect(searchTab).toHaveAttribute('aria-selected', 'true')
  await expect(bookmarksTab).toHaveAttribute('aria-selected', 'false')

  // The search input should be focused (Cmd+F's other contract).
  const searchInput = page.locator('input[placeholder="Search messages..."]')
  await expect(searchInput).toBeFocused()
})


test('Cmd+F from a closed panel opens AND switches to search tab', async ({ page, mockBackend }) => {
  // Start with rightPaneTab=bookmarks AND panel closed.
  await mockBackend({
    preferences: { rightPaneTab: 'bookmarks', 'searchPanel.isOpen': false },
  })

  await withNetRetry(page, () => page.goto('/'))
  // The keydown listener attaches after the first commit; wait for it (2026-10-26 flakes).
  await waitForKeyboardShortcuts(page, 'emacs')
  await page.locator('main').click()

  // Press Cmd+F. Even though rightPaneTab=bookmarks in prefs, the
  // panel must open with Search active.
  await page.keyboard.press('Meta+f')

  const aside = page.locator('aside[aria-label="Search panel"]')
  await expect(aside).toBeVisible()

  const searchTab = page.getByRole('tab', { name: /Search/ })
  await expect(searchTab).toHaveAttribute('aria-selected', 'true')

  const searchInput = page.locator('input[placeholder="Search messages..."]')
  await expect(searchInput).toBeFocused()
})
