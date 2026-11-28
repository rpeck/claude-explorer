import { test, expect, withNetRetry, type MockBackendOptions } from './fixtures'
import type { Page, Route } from '@playwright/test'
import { S1, WORKERS, activeGraph, msgBetween, treeDetails, treeSummaries, type PeerGraph } from './peerTreeFixture'

/**
 * L2 UI round 5 (issue #91): U9 of _orchestration/L2-UI.md. The cut of a
 * Sequence arrow label uses the measured width of the text, so a label that
 * fits its space (U8: the space to the next lifeline, at least 160 px) shows
 * in full, and a longer one ends with "…" and fits. Synthetic data only
 * (peerTreeFixture.ts), 1440 x 900.
 *
 * The console gate of TESTING.md §5.15 runs for every test.
 */

test.use({ viewport: { width: 1440, height: 900 } })

async function setup(mockBackend: (opts?: MockBackendOptions) => Promise<void>, active?: PeerGraph) {
  await mockBackend({
    conversations: treeSummaries(),
    details: treeDetails(),
    extraRoutes: async (p: Page) => {
      await p.route('**/api/peers/active**', (route: Route) =>
        route.fulfill({ contentType: 'application/json', body: JSON.stringify(active ?? activeGraph()) }),
      )
    },
  })
}

async function openActive(page: Page) {
  await withNetRetry(page, () => page.goto('/activity'))
  await expect(page.getByTestId('messages-sequence')).toBeVisible()
}

const arrow = (page: Page, id: string) => page.locator(`[data-testid="messages-arrow"][data-msg-id="${id}"]`)

async function lifeline(page: Page, key: string): Promise<number> {
  const b = (await page.locator(`[data-testid="messages-lane"][data-session-key="${key}"]`).boundingBox())!
  return b.x + b.width / 2
}

test.describe('U9: a label that fits is not cut', () => {
  test('U9: "Task for fixture-worker-1-1." shows in full on the 60-session tree', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActive(page)
    const w11 = WORKERS[0][0]
    const task = msgBetween(S1, w11)
    const done = msgBetween(w11, S1)
    await expect(arrow(page, task.msg_id)).toBeVisible()
    await expect(arrow(page, done.msg_id).locator('text').first()).toHaveText(done.text)
    await expect(arrow(page, task.msg_id).locator('text').first()).toHaveText(task.text)
    const space = Math.max(160, (await lifeline(page, w11)) - (await lifeline(page, S1)))
    const box = (await arrow(page, task.msg_id).locator('text').first().boundingBox())!
    expect(box.width).toBeLessThanOrEqual(space + 2)
  })

  test('U9: a label longer than its space ends with "…" and fits', async ({ page, mockBackend }) => {
    const w11 = WORKERS[0][0]
    const longer = { ...msgBetween(S1, w11), text: 'Task for fixture-worker-1-1, with a few more synthetic words.' }
    const g = activeGraph()
    await setup(mockBackend, { ...g, messages: g.messages.map((m) => (m.msg_id === longer.msg_id ? longer : m)) })
    await openActive(page)
    const label = arrow(page, longer.msg_id).locator('text').first()
    await expect(label).toBeVisible()
    expect(((await label.textContent()) ?? '').endsWith('…')).toBe(true)
    const space = Math.max(160, (await lifeline(page, w11)) - (await lifeline(page, S1)))
    expect((await label.boundingBox())!.width).toBeLessThanOrEqual(space + 2)
  })
})
