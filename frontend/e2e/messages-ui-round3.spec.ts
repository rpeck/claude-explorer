import { test, expect, makeSummary, withNetRetry, type MockBackendOptions } from './fixtures'
import type { Locator, Page, Route } from '@playwright/test'
import type { ConversationSummary } from '../src/lib/types'
import {
  CO, S1, SUBS, WORKERS, activeGraph, hopsGraph, msgBetween, treeDetails, treeSummaries, titleOf,
  type GraphMessage, type GraphSession, type PeerGraph,
} from './peerTreeFixture'

/**
 * L2 UI round 3 (issue #91): the contract _orchestration/L2-UI.md, U1 to U6,
 * from the review of the fixture screenshots. Synthetic data only (the
 * 60-session tree of peerTreeFixture.ts), 1440 x 900.
 *
 *   - U1: the filter editor texts name what the target matches.
 *   - U2: sibling lanes by first message time; sessions with no message last,
 *     newest first; pinned lanes first, in pin order.
 *   - U3: lane heads show the name (a 20-character name in full).
 *   - U4: the lane heads stay at the top while the body scrolls.
 *   - U5: one Graph edge per pair; fit to the pane; zoom and pan; folded groups.
 *   - U6: the list row and the sidebar footer fit at the 320 px sidebar.
 *
 * The console gate of TESTING.md §5.15 runs for every test.
 */

test.use({ viewport: { width: 1440, height: 900 } })

async function setup(
  mockBackend: (opts?: MockBackendOptions) => Promise<void>,
  opts: { active?: PeerGraph; conversations?: ConversationSummary[]; preferences?: Record<string, unknown> } = {},
) {
  await mockBackend({
    conversations: opts.conversations ?? treeSummaries(),
    details: treeDetails(),
    preferences: opts.preferences,
    extraRoutes: async (p: Page) => {
      await p.route('**/api/peers/active**', (route: Route) =>
        route.fulfill({ contentType: 'application/json', body: JSON.stringify(opts.active ?? activeGraph()) }),
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

const lanes = (page: Page) => page.getByTestId('messages-lane')
const lane = (page: Page, key: string) => page.locator(`[data-testid="messages-lane"][data-session-key="${key}"]`)
const laneKeys = (page: Page) => lanes(page).evaluateAll((els) => els.map((e) => e.getAttribute('data-session-key') ?? ''))

async function laneAction(page: Page, key: string, action: string) {
  await lane(page, key).getByTestId('messages-lane-menu').click()
  await page.getByRole('menuitem', { name: action, exact: true }).click()
}

/** A copy of a message with a new id and a later time. */
function again(m: GraphMessage, n: number): GraphMessage {
  const t = new Date(Date.parse(m.timestamp ?? '') + 3_600_000 + n * 1000).toISOString()
  return { ...m, msg_id: `${m.msg_id}-again-${n}`, timestamp: t, to_message_uuid: `${m.to_message_uuid}-again-${n}` }
}

function sessionOf(key: string): GraphSession {
  const s = activeGraph().sessions.find((x) => x.key === key)
  if (!s) throw new Error(key)
  return { ...s }
}

// ---------------------------------------------------------------------------
// U1. Filter editor texts follow the target
// ---------------------------------------------------------------------------

async function openEditor(page: Page) {
  await withNetRetry(page, () => page.goto('/conversations'))
  // The e2e preferences open the sidebar filter panel (sidebarFiltersOpen).
  await page.getByTestId('active-filter-select').click()
  await page.getByTestId('active-filter-manage').click()
  await page.getByTestId('manage-filters-new').click()
}

async function draft(page: Page, target: string, pattern = 'x') {
  await page.getByTestId('filter-editor-name').fill(`U1 ${target}`)
  await page.getByTestId('filter-editor-target').selectOption({ label: target })
  await page.getByTestId('filter-editor-behavior-show-only').click()
  await page.getByTestId('filter-editor-mode-glob').click()
  await page.getByTestId('filter-editor-patterns').fill(pattern)
}

const EMPTY_FILTERS = { filters: { nodes: {}, activeId: null, _migratedV1: true, _migratedV2: true, migrationBannerDismissed: true } }

test.describe('U1: filter editor texts follow the target', () => {
  test('U1: the dialog subtitle names no target', async ({ page, mockBackend }) => {
    await setup(mockBackend, { preferences: EMPTY_FILTERS })
    await openEditor(page)
    await expect(page.getByRole('dialog')).toContainText('Create, edit, and combine saved filters for the conversation list.')
  })

  test('U1: the Message text summary says "message text"', async ({ page, mockBackend }) => {
    await setup(mockBackend, { preferences: EMPTY_FILTERS })
    await openEditor(page)
    await draft(page, 'Message text', 'fixture-worker-1-')
    await expect(page.getByTestId('filter-editor-summary')).toHaveText(
      'Shows only conversations whose message text matches any of: fixture-worker-1-.',
    )
  })

  test('U1: the glob help names the target', async ({ page, mockBackend }) => {
    await setup(mockBackend, { preferences: EMPTY_FILTERS })
    await openEditor(page)
    await draft(page, 'Message text')
    const help = page.getByTestId('filter-editor-pattern-help')
    await expect(help).toContainText('the whole text')
    await expect(help).toContainText('anywhere in the text')
    await expect(help).toContainText('Patterns match the message text, case-insensitively.')
    await expect(help).not.toContainText('title')
  })

  const NOUNS: [string, string][] = [
    ['Message text', 'message text'], ['Project', 'project folders'], ['Source', 'sources'],
    ['Machine', 'machines'], ['Active within', 'activity in the last'], ['State', 'states'],
  ]
  for (const [target, noun] of NOUNS) {
    // Active within has its own sentence: "... with activity in the last <value>." (orchestrator).
    test(`U1: the ${target} summary and help name "${noun}", not "title"`, async ({ page, mockBackend }) => {
      await setup(mockBackend, { preferences: EMPTY_FILTERS })
      await openEditor(page)
      await draft(page, target, target === 'Active within' ? '1d' : 'x')
      await expect(page.getByTestId('filter-editor-summary')).toContainText(noun)
      await expect(page.getByTestId('filter-editor-summary')).not.toContainText('title')
      await expect(page.getByTestId('filter-editor-pattern-help')).not.toContainText('title')
    })
  }

  test('U1: the Title summary still says "titles"', async ({ page, mockBackend }) => {
    await setup(mockBackend, { preferences: EMPTY_FILTERS })
    await openEditor(page)
    await draft(page, 'Title', 'digest')
    await expect(page.getByTestId('filter-editor-summary')).toContainText('titles')
  })
})

// ---------------------------------------------------------------------------
// U2. Sibling lanes in time order
// ---------------------------------------------------------------------------

test.describe('U2: sibling lanes in time order', () => {
  test('U2: siblings follow their first message, not the API order', async ({ page, mockBackend }) => {
    // The API lists the newest session first; worker 1-5 got the newest message.
    const g = activeGraph()
    await setup(mockBackend, { active: { ...g, sessions: [...g.sessions].reverse() } })
    await openActivity(page)
    await expect(lanes(page)).toHaveCount(12)
    expect(await laneKeys(page)).toEqual([CO, S1, ...WORKERS[0]])
  })

  test('U2: sessions with no message come last, newest first', async ({ page, mockBackend }) => {
    const [w11, w12, w13] = WORKERS[0]
    const older = { ...sessionOf(w12), key: 'n1000000-0000-4000-8000-000000000001', uuid: 'n1000000-0000-4000-8000-000000000001',
      title: 'No messages, older', name: 'fixture-quiet-older', derived_parent_key: null,
      updated_at: new Date(Date.now() - 5 * 3_600_000).toISOString() }
    const newer = { ...older, key: 'n2000000-0000-4000-8000-000000000002', uuid: 'n2000000-0000-4000-8000-000000000002',
      title: 'No messages, newer', name: 'fixture-quiet-newer', updated_at: new Date(Date.now() - 60_000).toISOString() }
    const msgs = [msgBetween(CO, S1), msgBetween(S1, w11), msgBetween(S1, w12), msgBetween(S1, w13)]
    const active: PeerGraph = {
      ...activeGraph(),
      sessions: [older, sessionOf(w13), sessionOf(S1), newer, sessionOf(w11), sessionOf(CO), sessionOf(w12)],
      messages: [...msgs, again(msgBetween(S1, w13), 1)],
    }
    await setup(mockBackend, { active })
    await openActivity(page)
    await expect(lanes(page)).toHaveCount(7)
    expect(await laneKeys(page)).toEqual([CO, S1, w11, w12, w13, newer.key, older.key])
  })

  test('U2: pinned lanes come first, in pin order', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    const [w11, , w13] = WORKERS[0]
    await laneAction(page, w13, 'Pin')
    await laneAction(page, w11, 'Pin')
    await expect.poll(async () => (await laneKeys(page)).slice(0, 2)).toEqual([w13, w11])
  })
})

// ---------------------------------------------------------------------------
// U3. Lane heads show the name
// ---------------------------------------------------------------------------

const LONG = 'fixture-coordinator-with-a-very-long-name-for-one-lane-head'

function namedGraph(title: string): PeerGraph {
  const g = activeGraph()
  return { ...g, sessions: g.sessions.map((s) => (s.key === CO ? { ...s, title } : s)) }
}

test.describe('U3: lane heads show the name', () => {
  test('U3: a 20-character name shows in full at 1440 px', async ({ page, mockBackend }) => {
    await setup(mockBackend, { active: namedGraph('fixture-coordinator') })
    await openActivity(page)
    const name = lane(page, CO).getByText('fixture-coordinator', { exact: true }).first()
    await expect(name).toBeVisible()
    expect(await name.evaluate((e) => e.scrollWidth <= e.clientWidth)).toBe(true)
  })

  test('U3: a cut name keeps the full name as tooltip and text', async ({ page, mockBackend }) => {
    await setup(mockBackend, { active: namedGraph(LONG) })
    await openActivity(page)
    await expect(lane(page, CO).locator(`[title="${LONG}"]`).first()).toBeAttached()
    await expect(lane(page, CO)).toContainText(LONG)
  })

  test('U3: the lane still shows its state', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    const l = lane(page, CO)
    await expect(l).toBeVisible()
    const asText = await l.getByText('Open', { exact: true }).count()
    const asLabel = await l.locator('[aria-label="Open"], [title="Open"]').count()
    expect(asText + asLabel).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// U4. The Sequence view scrolls, and no label is lost
// ---------------------------------------------------------------------------

test.describe('U4: the Sequence view scrolls', () => {
  test('U4: the lanes area scrolls sideways', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    const seq = page.getByTestId('messages-sequence')
    await expect(seq).toBeVisible()
    const moved = await seq.evaluate((root) => {
      let e: HTMLElement | null = root as HTMLElement
      while (e && !(e.scrollWidth > e.clientWidth && /(auto|scroll)/.test(getComputedStyle(e).overflowX))) e = e.parentElement
      if (!e) return false
      e.scrollLeft = 400
      return e.scrollLeft > 0
    })
    expect(moved).toBe(true)
  })

  test('U4: the lane heads stay at the top while the body scrolls down', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    const head = lanes(page).first()
    await expect(head).toBeVisible()
    const before = (await head.boundingBox())!.y
    const scrolled = await page.getByTestId('messages-sequence').evaluate((root) => {
      // The nearest box that scrolls down: the view itself or a parent.
      let e: HTMLElement | null = root as HTMLElement
      while (e && !(e.scrollHeight > e.clientHeight && /(auto|scroll)/.test(getComputedStyle(e).overflowY))) e = e.parentElement
      const box = e ?? (document.scrollingElement as HTMLElement)
      box.scrollTop = 600
      return box.scrollTop
    })
    expect(scrolled).toBeGreaterThan(0)
    await expect.poll(async () => Math.abs((await head.boundingBox())!.y - before)).toBeLessThanOrEqual(2)
    await expect(head).toBeInViewport()
  })

  test('U4: every arrow label lies inside the SVG', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await expect(page.getByTestId('messages-arrow').first()).toBeAttached()
    const outside = await page.getByTestId('messages-sequence').evaluate((root) => {
      const bad: string[] = []
      for (const g of root.querySelectorAll('[data-testid="messages-arrow"]')) {
        const svg = g.closest('svg') as SVGSVGElement
        const t = g.querySelector('text') as SVGTextElement | null
        if (!t) continue
        const b = t.getBBox()
        if (b.x < 0 || b.x + b.width > svg.width.baseVal.value + 0.5) bad.push(g.getAttribute('data-msg-id') ?? '?')
      }
      return bad
    })
    expect(outside).toEqual([])
  })

  test('U4: an arrow to the "N other sessions" lane is drawn', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await expect(page.getByTestId('messages-other-lane')).toBeVisible()
    const m = msgBetween(CO, SUBS[1]) // fixture-sub-2 is in the other lane
    await expect(page.locator(`[data-testid="messages-arrow"][data-msg-id="${m.msg_id}"]`)).toHaveCount(1)
  })
})

// ---------------------------------------------------------------------------
// U5. The Graph view
// ---------------------------------------------------------------------------

function pairGraph(): PeerGraph {
  const [s2] = [SUBS[1]]
  const down = msgBetween(CO, S1)
  return {
    ...activeGraph(),
    sessions: [sessionOf(CO), sessionOf(S1), sessionOf(s2)],
    messages: [down, again(down, 1), again(down, 2), msgBetween(S1, CO), msgBetween(CO, s2)],
  }
}

async function openGraph(page: Page, center?: string) {
  await openActivity(page, center)
  await page.getByTestId('messages-view-graph').click()
  await expect(page.getByTestId('messages-graph')).toBeVisible()
}

const edgeLabels = (page: Page) =>
  page.getByTestId('messages-graph').locator('[data-testid="messages-graph-edge"] text').allTextContents()

test.describe('U5: the Graph view', () => {
  test('U5: one edge per pair, with both counts when both directions exist', async ({ page, mockBackend }) => {
    await setup(mockBackend, { active: pairGraph() })
    await openGraph(page)
    await expect(page.getByTestId('messages-graph-edge')).toHaveCount(2)
    expect((await edgeLabels(page)).map((s) => s.trim())).toContain('3 ⇄ 1')
  })

  test('U5: a one-way pair shows one count with an arrow', async ({ page, mockBackend }) => {
    await setup(mockBackend, { active: pairGraph() })
    await openGraph(page)
    const labels = (await edgeLabels(page)).map((s) => s.trim())
    const oneWay = labels.filter((s) => !s.includes('⇄'))
    expect(oneWay).toHaveLength(1)
    expect(oneWay[0]).toMatch(/1/)
    expect(oneWay[0]).toMatch(/[→←↑↓]/)
  })

  test('U5: the graph fits the pane width on first render', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openGraph(page)
    const pane = page.getByTestId('messages-graph')
    const svgBox = (await pane.locator('svg').first().boundingBox())!
    const paneBox = (await pane.boundingBox())!
    // Round 4 (U7): below GRAPH_MIN_SCALE the graph stays at 0.75 and the pane scrolls instead.
    const scrolls = await pane.evaluate((root) =>
      [root as HTMLElement, ...Array.from(root.querySelectorAll<HTMLElement>('*'))].some(
        (e) => e.scrollWidth > e.clientWidth + 1 && /(auto|scroll)/.test(getComputedStyle(e).overflowX),
      ))
    expect(svgBox.width <= paneBox.width + 1 || scrolls).toBe(true)
  })

  test('U5: "+", "−", and "Fit" zoom the graph', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openGraph(page)
    const node = page.locator(`[data-testid="messages-graph-node"][data-session-key="${CO}"]`)
    const w0 = (await node.boundingBox())!.width
    // Visible text "+", "−", "Fit"; accessible names "Zoom in", "Zoom out", "Fit" (orchestrator).
    await page.getByRole('button', { name: 'Zoom in', exact: true }).click()
    await expect.poll(async () => (await node.boundingBox())!.width).toBeGreaterThan(w0 + 1)
    await page.getByRole('button', { name: 'Zoom out', exact: true }).click()
    await page.getByRole('button', { name: 'Zoom out', exact: true }).click()
    await expect.poll(async () => (await node.boundingBox())!.width).toBeLessThan(w0 - 1)
    await page.getByRole('button', { name: 'Fit', exact: true }).click()
    await expect.poll(async () => Math.abs((await node.boundingBox())!.width - w0)).toBeLessThanOrEqual(1)
  })

  test('U5: a drag pans the graph', async ({ page, mockBackend }) => {
    // Round 4 (U7): a drag scrolls the pane, so it needs a graph wider than the
    // pane: the 60-session tree, which stays at GRAPH_MIN_SCALE.
    await setup(mockBackend)
    await openGraph(page)
    const node = page.locator(`[data-testid="messages-graph-node"][data-session-key="${CO}"]`)
    const before = (await node.boundingBox())!
    const pane = (await page.getByTestId('messages-graph').boundingBox())!
    // Drag to the left from empty space near the bottom middle of the pane.
    await page.mouse.move(pane.x + pane.width / 2, pane.y + pane.height - 20)
    await page.mouse.down()
    await page.mouse.move(pane.x + pane.width / 2 - 150, pane.y + pane.height - 20, { steps: 6 })
    await page.mouse.up()
    await expect.poll(async () => Math.round(before.x - (await node.boundingBox())!.x)).toBeGreaterThan(50)
  })

  test('U5: a folded group is one node, and a click expands it', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, CO)
    await page.getByTestId('messages-scope').selectOption({ label: 'Around this session' })
    await page.getByTestId('messages-view-graph').click()
    const nodes = page.getByTestId('messages-graph-node')
    const folds = nodes.filter({ hasText: '+10 sessions' })
    await expect(folds).toHaveCount(5)
    // The first fold is fixture-sub-1's; the lane limit still applies, as in the Sequence view.
    // (The other subs then move into the "N other sessions" node, with their folds.)
    await folds.first().click()
    await expect(page.locator(`[data-testid="messages-graph-node"][data-session-key="${WORKERS[0][0]}"]`)).toHaveCount(1)
  })
})

// ---------------------------------------------------------------------------
// U6. The list row and the sidebar footer
// ---------------------------------------------------------------------------

test.describe('U6: list row and sidebar footer at 320 px', () => {
  test('U6: the "Ended (no exit record)" row keeps model, date, and count on one line', async ({ page, mockBackend }) => {
    const old = new Date(Date.now() - 10 * 24 * 3_600_000).toISOString()
    const rows = [makeSummary({
      uuid: WORKERS[0][1], name: titleOf(WORKERS[0][1]), source: 'CLAUDE_CODE', project_path: '/home/fixture/orchestra',
      project_name: 'orchestra', model: 'claude-sonnet-5-5', message_count: 2, updated_at: old, created_at: old,
      ...({ state: 'ended_no_exit' } as Partial<ConversationSummary>),
    })]
    await setup(mockBackend, { conversations: rows })
    await withNetRetry(page, () => page.goto('/conversations'))
    const row = page.getByTestId('conversation-list-flat').getByRole('button').filter({ hasText: titleOf(WORKERS[0][1]) })
    await expect(row.getByTestId('session-state-chip')).toBeVisible()
    const boxes = await row.getByTestId('list-row-model').evaluate((badge) => {
      const parts = [badge, badge.nextElementSibling, badge.nextElementSibling?.nextElementSibling] as HTMLElement[]
      return parts.map((e) => { const r = e.getBoundingClientRect(); return { top: r.top, height: r.height, text: e.textContent } })
    })
    expect(boxes[2].text).toMatch(/msgs$/)
    for (const b of boxes) expect(b.height, b.text ?? '').toBeLessThanOrEqual(20)
    expect(Math.abs(boxes[1].top - boxes[2].top)).toBeLessThanOrEqual(3)
    expect(Math.abs(boxes[0].top + boxes[0].height / 2 - (boxes[2].top + boxes[2].height / 2))).toBeLessThanOrEqual(3)
  })

  test('U6: the power button keeps 8 px from the sidebar border', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await withNetRetry(page, () => page.goto('/conversations'))
    const aside = (await page.locator('aside').first().boundingBox())!
    const quit = (await page.getByRole('button', { name: 'Quit Claude Explorer' }).boundingBox())!
    expect(aside.width).toBeLessThanOrEqual(321)
    expect(quit.x + quit.width).toBeLessThanOrEqual(aside.x + aside.width - 8)
  })
})

// Keep the type-only helpers referenced.
export type { Locator }
