import { test, expect, withNetRetry, type MockBackendOptions } from './fixtures'
import type { Page, Route } from '@playwright/test'
import {
  CO, MESSAGES, S1, SUBS, activeGraph, hopsGraph, msgBetween, treeDetails, treeSummaries,
  type GraphSession, type PeerGraph,
} from './peerTreeFixture'

/**
 * L2 UI round 4 (issue #91): the amendment of _orchestration/L2-UI.md, U7 and
 * U8, from the round 3 screenshots. Synthetic data only (peerTreeFixture.ts),
 * 1440 x 900.
 *
 *   - U7: the Graph scale after the first render and after "Fit" is at least
 *     GRAPH_MIN_SCALE (0.75) and at most 1, and the pane scrolls below it; at
 *     scale 1 node labels are >= 12 px and edge labels >= 11 px; zoom stops at
 *     0.25 and 2.
 *   - U8: a Sequence arrow label starts 8 px from the sender's lifeline, on the
 *     side of the arrow; a long label is cut with "…" to the space to the next
 *     lifeline (at least 160 px) and keeps the full text as its tooltip; in
 *     "All active sessions" every label of an arrow whose sender lane shows is
 *     inside the visible pane.
 *
 * The graph scale is measured as the rendered width of a node's <rect>
 * divided by its own width. The console gate of TESTING.md §5.15 runs for
 * every test.
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
      await p.route('**/api/conversations/*/peers**', (route: Route) => {
        const url = new URL(route.request().url())
        const raw = url.searchParams.get('hops') ?? '2'
        const g = hopsGraph(url.pathname.split('/')[3], raw === 'all' ? 'all' : Number(raw), url.searchParams.get('subagents') === 'true')
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(g) })
      })
    },
  })
}

async function openActivity(page: Page, center?: string) {
  await withNetRetry(page, () => page.goto(center ? `/activity?center=${center}` : '/activity'))
  await expect(page.getByTestId('activity-panel')).toBeVisible()
}

function sessionOf(key: string): GraphSession {
  const s = activeGraph().sessions.find((x) => x.key === key)
  if (!s) throw new Error(key)
  return { ...s }
}

// ---------------------------------------------------------------------------
// U7. Graph labels stay readable
// ---------------------------------------------------------------------------

async function openGraph(page: Page) {
  await openActivity(page)
  await page.getByTestId('messages-view-graph').click()
  await expect(page.getByTestId('messages-graph-node').first()).toBeVisible()
}

/** The rendered width of a node's rect over its own width. */
const scaleOf = (page: Page) =>
  page.getByTestId('messages-graph-node').first().locator('rect').first().evaluate((r) => {
    const rect = r as SVGRectElement
    return rect.getBoundingClientRect().width / rect.width.baseVal.value
  })

/** True when the graph pane (or a box inside it) scrolls in either direction. */
const paneScrolls = (page: Page) =>
  page.getByTestId('messages-graph').evaluate((root) => {
    const boxes = [root as HTMLElement, ...Array.from(root.querySelectorAll<HTMLElement>('*'))]
    return boxes.some((e) => {
      const st = getComputedStyle(e)
      return (e.scrollWidth > e.clientWidth + 1 && /(auto|scroll)/.test(st.overflowX))
        || (e.scrollHeight > e.clientHeight + 1 && /(auto|scroll)/.test(st.overflowY))
    })
  })

function pairGraph(): PeerGraph {
  const down = msgBetween(CO, S1)
  return { ...activeGraph(), sessions: [sessionOf(CO), sessionOf(S1)], messages: [down, msgBetween(S1, CO)] }
}

test.describe('U7: graph labels stay readable', () => {
  test('U7: the first render keeps the scale at 0.75 or more, and the pane scrolls', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openGraph(page)
    const s = await scaleOf(page)
    expect(s).toBeGreaterThanOrEqual(0.749)
    expect(s).toBeLessThanOrEqual(1.001)
    expect(await paneScrolls(page)).toBe(true)
  })

  test('U7: "Fit" keeps the scale at 0.75 or more', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openGraph(page)
    for (let i = 0; i < 3; i++) await page.getByRole('button', { name: 'Zoom out', exact: true }).click()
    await page.getByRole('button', { name: 'Fit', exact: true }).click()
    await expect.poll(() => scaleOf(page)).toBeGreaterThanOrEqual(0.749)
    expect(await scaleOf(page)).toBeLessThanOrEqual(1.001)
  })

  test('U7: at scale 1, node labels are 12 px or more and edge labels 11 px or more', async ({ page, mockBackend }) => {
    await setup(mockBackend, pairGraph())
    await openGraph(page)
    expect(Math.abs((await scaleOf(page)) - 1)).toBeLessThanOrEqual(0.01)
    const size = (sel: string) =>
      page.getByTestId('messages-graph').locator(sel).first().evaluate((t) => parseFloat(getComputedStyle(t).fontSize))
    expect(await size('[data-testid="messages-graph-node"] text')).toBeGreaterThanOrEqual(12)
    expect(await size('[data-testid="messages-graph-edge"] text')).toBeGreaterThanOrEqual(11)
  })

  test('U7: zoom stops at 0.25 and at 2', async ({ page, mockBackend }) => {
    await setup(mockBackend, pairGraph())
    await openGraph(page)
    for (let i = 0; i < 15; i++) await page.getByRole('button', { name: 'Zoom out', exact: true }).click()
    await expect.poll(() => scaleOf(page)).toBeGreaterThanOrEqual(0.245)
    expect(await scaleOf(page)).toBeLessThanOrEqual(0.255)
    for (let i = 0; i < 25; i++) await page.getByRole('button', { name: 'Zoom in', exact: true }).click()
    await expect.poll(() => scaleOf(page)).toBeLessThanOrEqual(2.01)
    expect(await scaleOf(page)).toBeGreaterThanOrEqual(1.99)
  })
})

// ---------------------------------------------------------------------------
// U8. Sequence labels near the sender
// ---------------------------------------------------------------------------

const FROM = new Map(MESSAGES.map((m) => [m.msg_id, m.from_key]))
const TO = new Map(MESSAGES.map((m) => [m.msg_id, m.to_key]))

/** The x of the lifeline of a lane: the middle of its head. */
async function lifeline(page: Page, key: string): Promise<number> {
  const b = (await page.locator(`[data-testid="messages-lane"][data-session-key="${key}"]`).boundingBox())!
  return b.x + b.width / 2
}

async function labelBox(page: Page, msgId: string) {
  return (await page.locator(`[data-testid="messages-arrow"][data-msg-id="${msgId}"] text`).first().boundingBox())!
}

/**
 * Where a label is placed: its text-anchor, and its distance from the arrow's
 * start (the sender's lifeline) in SVG units, on the side of the arrow. The
 * lifeline is also given in client px, to check it against the lane head.
 */
async function placement(page: Page, msgId: string) {
  return page.locator(`[data-testid="messages-arrow"][data-msg-id="${msgId}"]`).evaluate((g) => {
    const line = g.querySelector('line') as SVGLineElement
    const text = g.querySelector('text') as SVGTextElement
    const x1 = line.x1.baseVal.value
    const x2 = line.x2.baseVal.value
    const x = text.x.baseVal.length ? text.x.baseVal.getItem(0).value : 0
    const anchor = text.getAttribute('text-anchor') ?? getComputedStyle(text).textAnchor
    const ctm = (line.ownerSVGElement as SVGSVGElement).getScreenCTM() as DOMMatrix
    return { anchor, offset: x2 >= x1 ? x - x1 : x1 - x, lifelineClientX: ctm.a * x1 + ctm.e }
  })
}

const LONG_TEXT = 'Synthetic long message: ' + 'please read the whole schema migration plan and report back. '.repeat(4).trim()

test.describe('U8: sequence labels near the sender', () => {
  test('U8: a label starts 8 to 16 px from the sender lifeline, on the side of the arrow', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, CO)
    await page.getByTestId('messages-scope').selectOption({ label: 'Around this session' })
    const right = msgBetween(CO, S1) // the coordinator's lane is left of fixture-sub-1's
    const left = msgBetween(S1, CO)
    await expect(page.locator(`[data-testid="messages-arrow"][data-msg-id="${right.msg_id}"]`)).toBeVisible()
    const co = await lifeline(page, CO)
    const s1 = await lifeline(page, S1)
    expect(s1).toBeGreaterThan(co)
    // The placement, not the drawn glyphs (fonts start the first glyph a pixel off on
    // Linux): the <text> x and text-anchor against the arrow's start, the sender's
    // lifeline, in the same SVG coordinates.
    const r = await placement(page, right.msg_id)
    expect(Math.abs(r.lifelineClientX - co)).toBeLessThanOrEqual(2)
    expect(r.anchor).toBe('start')
    expect(r.offset).toBeGreaterThanOrEqual(8)
    expect(r.offset).toBeLessThanOrEqual(16)
    const l = await placement(page, left.msg_id)
    expect(Math.abs(l.lifelineClientX - s1)).toBeLessThanOrEqual(2)
    expect(l.anchor).toBe('end')
    expect(l.offset).toBeGreaterThanOrEqual(8)
    expect(l.offset).toBeLessThanOrEqual(16)
  })

  test('U8: a long label ends with "…", fits the space to the next lifeline, and keeps the full text', async ({ page, mockBackend }) => {
    const long = { ...msgBetween(CO, S1), text: LONG_TEXT }
    await setup(mockBackend, { ...activeGraph(), sessions: [sessionOf(CO), sessionOf(S1)], messages: [long] })
    await openActivity(page)
    const arrow = page.locator(`[data-testid="messages-arrow"][data-msg-id="${long.msg_id}"]`)
    await expect(arrow).toBeVisible()
    const shown = (await arrow.locator('text').first().textContent()) ?? ''
    expect(shown.endsWith('…')).toBe(true)
    const full = await arrow.evaluate((g) => `${g.getAttribute('aria-label') ?? ''} ${g.getAttribute('title') ?? ''} ${g.querySelector('title')?.textContent ?? ''}`)
    expect(full).toContain(LONG_TEXT)
    const gap = (await lifeline(page, S1)) - (await lifeline(page, CO))
    const box = await labelBox(page, long.msg_id)
    expect(box.width).toBeLessThanOrEqual(Math.max(160, gap) + 2)
  })

  test('U8: in "All active sessions", every label of an arrow from a shown lane is inside the visible pane', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await expect(page.getByTestId('messages-arrow').first()).toBeVisible()
    const pane = (await page.getByTestId('messages-sequence').boundingBox())!
    const left = Math.max(0, pane.x)
    const rightEdge = Math.min(1440, pane.x + pane.width)
    const shown = new Set(await page.getByTestId('messages-lane').evaluateAll((els) => els.map((e) => {
      const r = e.getBoundingClientRect()
      return r.right <= window.innerWidth && r.left >= 0 ? (e.getAttribute('data-session-key') ?? '') : ''
    })))
    const ids = await page.getByTestId('messages-arrow').evaluateAll((els) => els.map((e) => e.getAttribute('data-msg-id') ?? ''))
    const outside: string[] = []
    let checked = 0
    for (const id of ids) {
      const from = FROM.get(id)
      if (!from || !shown.has(from)) continue
      checked += 1
      const b = await labelBox(page, id)
      if (b.x < left - 0.5 || b.x + b.width > rightEdge + 0.5) outside.push(`${id} (${from} -> ${TO.get(id)})`)
    }
    expect(checked).toBeGreaterThan(10)
    expect(outside).toEqual([])
  })
})

// The sub-orchestrators beyond the first sit in the "N other sessions" lane in "All active sessions".
void SUBS
