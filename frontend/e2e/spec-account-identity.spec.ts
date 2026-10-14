import { test, expect, withNetRetry } from './fixtures'
import type { AccountResponse } from '../src/lib/types'

/**
 * Spec-driven tests for UX.md → "Signed-in account (sidebar footer)".
 *
 * Written from UX.md, e2e/fixtures.ts and src/lib/types.ts only
 * (TESTING.md §1, spec-driven test files).
 */

function account(overrides: Partial<AccountResponse> = {}): AccountResponse {
  return {
    authenticated: true,
    account_uuid: '00000000-0000-4000-8000-0000000000a1',
    email: 'ada@example.com',
    full_name: 'Ada Lovelace @ Engines.co',
    display_name: 'Ada Lovelace @ Engines.co',
    org_name: 'Analytical Engines',
    error: null,
    ...overrides,
  }
}

const region = (page: import('@playwright/test').Page) =>
  page.getByRole('region', { name: 'Signed-in account' })

test.describe('Signed-in account (sidebar footer)', () => {
  test('shows initials, display name, and "email · workspace"', async ({ page, mockBackend }) => {
    await mockBackend({ account: account() })
    await withNetRetry(page, () => page.goto('/'))

    const r = region(page)
    await expect(r).toBeVisible()
    await expect(r.getByText('AE', { exact: true })).toBeVisible()
    await expect(r.getByText('Ada Lovelace @ Engines.co', { exact: true })).toBeVisible()
    await expect(r.getByText('ada@example.com · Analytical Engines', { exact: true })).toBeVisible()

    // It sits in the sidebar, below the conversation list.
    const sidebar = page.locator('aside').first()
    await expect(sidebar.getByRole('region', { name: 'Signed-in account' })).toBeVisible()
    const settings = sidebar.getByRole('link', { name: /settings/i })
    const rBox = await r.boundingBox()
    const sBox = await settings.boundingBox()
    expect(rBox && sBox && rBox.y < sBox.y).toBeTruthy()
  })

  test('falls back to full name, then email, for the first line and initials', async ({ page, mockBackend }) => {
    await mockBackend({
      account: account({ display_name: null, full_name: 'Grace', email: 'grace@example.com', org_name: null }),
    })
    await withNetRetry(page, () => page.goto('/'))
    const r = region(page)
    await expect(r.getByText('Grace', { exact: true })).toBeVisible()
    await expect(r.getByText('G', { exact: true })).toBeVisible()
    // Missing workspace: no dangling separator.
    await expect(r.getByText('grace@example.com', { exact: true })).toBeVisible()
    await expect(r).not.toContainText('·')
  })

  test('with no name, the email is the first line and its first letter the avatar', async ({ page, mockBackend }) => {
    await mockBackend({
      account: account({ display_name: null, full_name: null, email: 'zed@example.com', org_name: 'Acme' }),
    })
    await withNetRetry(page, () => page.goto('/'))
    const r = region(page)
    await expect(r.getByText('Z', { exact: true })).toBeVisible()
    await expect(r.getByText('zed@example.com', { exact: true })).toBeVisible()
  })

  test('long text truncates inside the sidebar and keeps the full text in a tooltip', async ({ page, mockBackend }) => {
    const longName = 'Bartholomew Maximilian Featherstonehaugh-Cholmondeley the Third'
    const longEmail = 'bartholomew.maximilian.featherstonehaugh-cholmondeley@example.com'
    await mockBackend({
      account: account({ display_name: longName, full_name: longName, email: longEmail, org_name: 'A Very Long Workspace Name Indeed' }),
    })
    await withNetRetry(page, () => page.goto('/'))
    const r = region(page)
    const nameEl = r.getByText(longName, { exact: true })
    await expect(nameEl).toHaveAttribute('title', longName)
    await expect(r.getByText(`${longEmail} · A Very Long Workspace Name Indeed`, { exact: true }))
      .toHaveAttribute('title', `${longEmail} · A Very Long Workspace Name Indeed`)

    // Truncated, not overflowing: the region stays within the sidebar and
    // the name element is narrower than its content.
    const sidebarBox = await page.locator('aside').first().boundingBox()
    const regionBox = await r.boundingBox()
    expect(sidebarBox && regionBox).toBeTruthy()
    expect(regionBox!.x + regionBox!.width).toBeLessThanOrEqual(sidebarBox!.x + sidebarBox!.width + 1)
    const overflow = await nameEl.evaluate((el) => el.scrollWidth > el.clientWidth)
    expect(overflow).toBe(true)
    // Initials: first letters of the first and last words.
    await expect(r.getByText('BT', { exact: true })).toBeVisible()
  })

  test('no credentials: "Not signed in" and a Refresh hint', async ({ page, mockBackend }) => {
    await mockBackend({
      account: {
        authenticated: false,
        account_uuid: null,
        email: null,
        full_name: null,
        display_name: null,
        org_name: null,
        error: null,
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const r = region(page)
    await expect(r.getByText('Not signed in', { exact: true })).toBeVisible()
    await expect(r.getByText('Click Refresh to sign in', { exact: true })).toBeVisible()
  })

  test('failed lookup: workspace name and the error message', async ({ page, mockBackend }) => {
    await mockBackend({
      account: account({
        account_uuid: null,
        email: null,
        full_name: null,
        display_name: null,
        error: 'Session expired. Click Refresh to sign in again.',
      }),
    })
    await withNetRetry(page, () => page.goto('/'))
    const r = region(page)
    await expect(r.getByText('Analytical Engines', { exact: true })).toBeVisible()
    await expect(r.getByText('Session expired. Click Refresh to sign in again.', { exact: true })).toBeVisible()
  })

  test('failed lookup without a workspace: "Signed in"', async ({ page, mockBackend }) => {
    await mockBackend({
      account: account({
        account_uuid: null,
        email: null,
        full_name: null,
        display_name: null,
        org_name: null,
        error: 'Could not reach claude.ai to look up the signed-in account.',
      }),
    })
    await withNetRetry(page, () => page.goto('/'))
    const r = region(page)
    await expect(r.getByText('Signed in', { exact: true })).toBeVisible()
  })

  test('is looked up again when a Refresh pipeline completes', async ({ page, mockBackend }) => {
    // The account "changes" once the Refresh stream has been served, as it
    // would after a recapture with different credentials.
    let refreshed = false
    let lookupsAfterRefresh = 0
    await mockBackend({
      extraRoutes: async (p) => {
        await p.route('**/api/account', (route) => {
          if (refreshed) lookupsAfterRefresh += 1
          const body = refreshed
            ? account({ display_name: 'Home Account', full_name: 'Home Account', email: 'me@example.org', org_name: 'Personal' })
            : account()
          route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
        })
        await p.route('**/api/fetch/refresh*', (route) => {
          refreshed = true
          route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body:
              'data: {"type":"start","message":"Fetching conversation list..."}\n\n' +
              'data: {"type":"complete","message":"Fetched 0 conversations successfully.","current":0,"total":0}\n\n',
          })
        })
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const r = region(page)
    await expect(r.getByText('Ada Lovelace @ Engines.co', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: /refresh conversation list/i }).click()
    await expect(r.getByText('Home Account', { exact: true })).toBeVisible()
    await expect(r.getByText('me@example.org · Personal', { exact: true })).toBeVisible()
    expect(lookupsAfterRefresh).toBeGreaterThan(0)
  })
})
