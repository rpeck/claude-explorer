import { test, expect, makeSummary, withNetRetry, type MockBackendOptions } from './fixtures'
import type { Locator, Page, Route } from '@playwright/test'
import type { ConversationDetail, ConversationSummary } from '../src/lib/types'
import { CM_CONV_UUID, CM_DETAIL, CM_SUMMARY } from '../src/test/fixtures/codeMode'
import {
  AGENT_ID, AGENT_KEY, AGENT_MSG_ID, CO, DIRECT, GONE_KEY, S1, SUBS, TREE, W11, W12, W15, WORKERS,
  activeGraph, hopsGraph, msgBetween, titleOf, treeDetails, treeSummaries, type PeerGraph,
} from './peerTreeFixture'

/**
 * L2 (issue #91, part 2): the Activity page and its Messages tab, on the
 * synthetic 60-session orchestrator tree (peerTreeFixture.ts). The contract
 * (orchestrator, 2026-11-27):
 *
 *   - /activity (activity-panel): tabs "Messages", "Code Mode", and a disabled
 *     "Subagents" ("Subagents: coming in #46"). Entry points: the sidebar link
 *     (sidebar-activity-link), the toolbar button (conversation-activity-button),
 *     and the "Messages with:" row link (peer-sessions-open-activity), which
 *     open /activity?center=<uuid>.
 *   - Scope (messages-scope): "All active sessions" (default; the only option
 *     without a center) and "Around this session" (hops 2). Hops
 *     (messages-hops): 1-5 and all. Toggle messages-subagents-toggle.
 *   - Views: Sequence (messages-sequence, messages-lane, messages-arrow), Graph
 *     (messages-graph, messages-graph-node, messages-graph-edge), List
 *     (messages-list, messages-list-row).
 *   - Lanes in depth-first order of the derived hierarchy (messages-derived-label);
 *     hop-2 sessions folded (messages-fold "+N sessions"); 12 lanes, then
 *     messages-other-lane "N other sessions" with messages-other-picker; lane
 *     menu messages-lane-menu (Pin, Hide, Collapse, Expand); messages-hidden-chip
 *     "Hidden: N".
 *   - A click opens the side that is not the center (no center: the receiver);
 *     a side not in the archive has no link and the title "not in the archive".
 *   - messages-filter: the tab's own named filter. messages-empty: "No active
 *     sessions.", "The active filter hides this session.", "No sessions match
 *     the filter."
 *   - Settings persist per center, and one set for no center.
 *   - Code Mode tab (code-mode-tab): code-mode-no-center without a center; one
 *     entry per run (code-mode-run-entry, added by the tester) with its
 *     sequence diagram; "No Code Mode runs in this session." otherwise.
 *   - session-state-chip on list rows and in the header.
 *   - The filter editor target select (filter-editor-target), and the sidebar
 *     filter on the L2 fields.
 *
 * The console gate of TESTING.md §5.15 runs for every test.
 */

interface Calls {
  active: URLSearchParams[]
  peers: { center: string; params: URLSearchParams }[]
}

const AGENT_DETAIL_ROW = `${S1.slice(0, 8)}-send-${AGENT_MSG_ID.slice(2)}`

function agentDetail(): ConversationDetail {
  const base = treeDetails()[S1]
  return { ...base, agent_id: AGENT_ID, agent_description: 'Help the sub' } as ConversationDetail
}

async function setup(
  mockBackend: (opts?: MockBackendOptions) => Promise<void>,
  opts: {
    active?: PeerGraph
    conversations?: ConversationSummary[]
    details?: Record<string, ConversationDetail>
    preferences?: Record<string, unknown>
    sharedPrefsState?: { data: Record<string, unknown> }
  } = {},
): Promise<Calls> {
  const calls: Calls = { active: [], peers: [] }
  await mockBackend({
    conversations: opts.conversations ?? [...treeSummaries(), CM_SUMMARY],
    details: { ...treeDetails(), [CM_CONV_UUID]: CM_DETAIL, ...(opts.details ?? {}) },
    preferences: opts.preferences,
    sharedPrefsState: opts.sharedPrefsState,
    extraRoutes: async (p: Page) => {
      await p.route('**/api/peers/active**', (route: Route) => {
        calls.active.push(new URL(route.request().url()).searchParams)
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(opts.active ?? activeGraph()) })
      })
      await p.route('**/api/conversations/*/peers**', (route: Route) => {
        const url = new URL(route.request().url())
        const center = url.pathname.split('/')[3]
        calls.peers.push({ center, params: url.searchParams })
        const raw = url.searchParams.get('hops') ?? '2'
        const hops = raw === 'all' ? 'all' : Number(raw)
        const g = hopsGraph(center, hops, url.searchParams.get('subagents') === 'true')
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(g) })
      })
      await p.route('**/api/conversations/*/subagents/*', (route: Route) =>
        route.fulfill({ contentType: 'application/json', body: JSON.stringify(agentDetail()) }),
      )
    },
  })
  return calls
}

const lastCall = (calls: Calls) => calls.peers[calls.peers.length - 1]

async function openActivity(page: Page, center?: string) {
  await withNetRetry(page, () => page.goto(center ? `/activity?center=${center}` : '/activity'))
  await expect(page.getByTestId('activity-panel')).toBeVisible()
}

const lanes = (page: Page) => page.getByTestId('messages-lane')
const lane = (page: Page, key: string) => page.locator(`[data-testid="messages-lane"][data-session-key="${key}"]`)
// The fold sits with the lane head: in it, or next to it.
const fold = (page: Page, key: string) => lane(page, key).locator('xpath=..').getByTestId('messages-fold')
// A title attribute, or an SVG <title> child.
const titleOf_ = (l: Locator) =>
  l.evaluate((e) => e.getAttribute('title') ?? e.querySelector('title')?.textContent ?? '')
const arrow = (page: Page, msgId: string) => page.locator(`[data-testid="messages-arrow"][data-msg-id="${msgId}"]`)

async function laneKeys(page: Page): Promise<string[]> {
  return lanes(page).evaluateAll((els) => els.map((e) => e.getAttribute('data-session-key') ?? ''))
}

async function isNativeSelect(control: Locator): Promise<boolean> {
  return control.evaluate((e) => e.tagName === 'SELECT')
}

async function optionLabels(page: Page, control: Locator): Promise<string[]> {
  if (await isNativeSelect(control)) return (await control.locator('option').allTextContents()).map((s) => s.trim())
  await control.click()
  const labels = (await page.getByRole('option').allTextContents()).map((s) => s.trim())
  await page.keyboard.press('Escape')
  return labels
}

async function choose(page: Page, control: Locator, label: string) {
  if (await isNativeSelect(control)) {
    await control.selectOption({ label })
    return
  }
  await control.click()
  await page.getByRole('option', { name: label, exact: true }).click()
}

async function selectedLabel(control: Locator): Promise<string> {
  if (await isNativeSelect(control)) {
    return control.evaluate((e) => (e as HTMLSelectElement).selectedOptions[0]?.textContent?.trim() ?? '')
  }
  return ((await control.textContent()) ?? '').trim()
}

async function laneAction(page: Page, key: string, action: string) {
  await lane(page, key).getByTestId('messages-lane-menu').click()
  await page.getByRole('menuitem', { name: action, exact: true }).or(page.getByRole('button', { name: action, exact: true })).first().click()
}

async function aroundThisSession(page: Page) {
  await choose(page, page.getByTestId('messages-scope'), 'Around this session')
}

// ---------------------------------------------------------------------------
// The page and its entry points
// ---------------------------------------------------------------------------

test.describe('Activity page (L2)', () => {
  test('the sidebar link opens the Activity page with its three tabs', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await withNetRetry(page, () => page.goto('/conversations'))
    await page.getByTestId('sidebar-activity-link').click()
    await expect(page).toHaveURL(/\/activity$/)
    const panel = page.getByTestId('activity-panel')
    await expect(panel.getByRole('tab', { name: 'Messages' })).toHaveAttribute('aria-selected', 'true')
    await expect(panel.getByRole('tab', { name: 'Code Mode' })).toBeVisible()
    const sub = panel.getByRole('tab', { name: /Subagents/ })
    await expect(sub).toBeDisabled()
    const label = `${(await sub.textContent()) ?? ''} ${(await sub.getAttribute('title')) ?? ''}`
    expect(label).toContain('Subagents: coming in #46')
  })

  test('the conversation toolbar button opens Activity centered on the session', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await withNetRetry(page, () => page.goto(`/conversations/${W11}`))
    await page.getByTestId('conversation-activity-button').click()
    await expect(page).toHaveURL(new RegExp(`/activity\\?center=${W11}$`))
    await expect(page.getByTestId('activity-panel')).toBeVisible()
  })

  test('the "Messages with:" row links to the Messages tab of the session', async ({ page, mockBackend }) => {
    const d = { ...treeDetails()[S1], peers: [{ conversation_uuid: CO, name: 'fixture-coordinator', title: titleOf(CO), link_state: 'linked' }] }
    await setup(mockBackend, { details: { [S1]: d as ConversationDetail } })
    await withNetRetry(page, () => page.goto(`/conversations/${S1}`))
    await page.locator('header').first().getByTestId('peer-sessions-open-activity').click()
    await expect(page).toHaveURL(new RegExp(`/activity\\?center=${S1}$`))
  })
})

// ---------------------------------------------------------------------------
// All active sessions (the default)
// ---------------------------------------------------------------------------

test.describe('Messages tab: all active sessions (L2)', () => {
  test('without a center the scope offers only "All active sessions"', async ({ page, mockBackend }) => {
    const calls = await setup(mockBackend)
    await openActivity(page)
    expect(await optionLabels(page, page.getByTestId('messages-scope'))).toEqual(['All active sessions'])
    await expect.poll(() => calls.active.length).toBeGreaterThan(0)
    expect(calls.peers).toEqual([])
  })

  test('the default Sequence shows 12 lanes in hierarchy order and an "N other sessions" lane', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await expect(page.getByTestId('messages-sequence')).toBeVisible()
    await expect(lanes(page)).toHaveCount(12)
    expect(await laneKeys(page)).toEqual([CO, S1, ...WORKERS[0]])
    await expect(page.getByTestId('messages-other-lane')).toContainText('48 other sessions')
    await expect(page.getByTestId('messages-derived-label')).toContainText('derived')
  })

  test('an arrow between two shown lanes is drawn, one per message', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await expect(arrow(page, msgBetween(CO, S1).msg_id)).toHaveCount(1)
    await expect(arrow(page, msgBetween(S1, W11).msg_id)).toHaveCount(1)
    await expect(arrow(page, msgBetween(W11, S1).msg_id)).toHaveCount(1)
  })

  test('without a center a click on an arrow opens the receiver at the message', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    const m = msgBetween(CO, S1)
    await arrow(page, m.msg_id).click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${S1}\\?m=${m.to_message_uuid}$`))
  })

  test('no active sessions gives the empty state', async ({ page, mockBackend }) => {
    await setup(mockBackend, { active: { ...activeGraph(), sessions: [], messages: [] } })
    await openActivity(page)
    await expect(page.getByTestId('messages-empty')).toHaveText('No active sessions.')
  })
})

// ---------------------------------------------------------------------------
// Around one session (k hops)
// ---------------------------------------------------------------------------

test.describe('Messages tab: around one session (L2)', () => {
  test('a center adds "Around this session", which asks for two hops', async ({ page, mockBackend }) => {
    const calls = await setup(mockBackend)
    await openActivity(page, CO)
    expect(await optionLabels(page, page.getByTestId('messages-scope'))).toEqual(['All active sessions', 'Around this session'])
    expect(await selectedLabel(page.getByTestId('messages-scope'))).toContain('All active sessions')
    await aroundThisSession(page)
    await expect.poll(() => calls.peers.length).toBeGreaterThan(0)
    const last = calls.peers[calls.peers.length - 1]
    expect(last.center).toBe(CO)
    expect(last.params.get('hops')).toBe('2')
    expect(last.params.get('subagents') ?? 'false').toBe('false')
  })

  test('hop-2 sessions are folded under the hop-1 session that reaches them', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, CO)
    await aroundThisSession(page)
    await expect(lanes(page)).toHaveCount(10)
    expect(await laneKeys(page)).toEqual([CO, ...SUBS, ...DIRECT])
    for (const sub of SUBS) await expect(fold(page, sub)).toHaveText('+10 sessions')
    await expect(page.getByTestId('messages-other-lane')).toHaveCount(0)
  })

  test('a click on a fold expands it, and the lane limit applies', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, CO)
    await aroundThisSession(page)
    await fold(page, S1).click()
    await expect(lanes(page)).toHaveCount(12)
    expect(await laneKeys(page)).toEqual([CO, S1, ...WORKERS[0]])
    await expect(page.getByTestId('messages-other-lane')).toContainText('8 other sessions')
  })

  test('the hops select offers 1 to 5 and all, and asks for the chosen value', async ({ page, mockBackend }) => {
    const calls = await setup(mockBackend)
    await openActivity(page, W11)
    await aroundThisSession(page)
    const hops = page.getByTestId('messages-hops')
    const labels = await optionLabels(page, hops)
    expect(labels.slice(0, 5)).toEqual(['1', '2', '3', '4', '5'])
    expect(labels[5]).toMatch(/^all$/i)
    await choose(page, hops, labels[5])
    await expect.poll(() => lastCall(calls)?.params.get('hops')).toBe('all')
    await choose(page, hops, '1')
    await expect.poll(() => lastCall(calls)?.params.get('hops')).toBe('1')
    expect(await laneKeys(page)).toEqual(expect.arrayContaining([W11, S1, GONE_KEY]))
    await expect(lanes(page)).toHaveCount(3)
  })

  test('with a center a click on an arrow opens the side that is not the center', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, S1)
    await aroundThisSession(page)
    const toCenter = msgBetween(CO, S1)
    await arrow(page, toCenter.msg_id).click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${CO}\\?m=${toCenter.from_message_uuid}$`))
  })

  test('a message from the center opens the receiver', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, S1)
    await aroundThisSession(page)
    const fromCenter = msgBetween(S1, W11)
    await arrow(page, fromCenter.msg_id).click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${W11}\\?m=${fromCenter.to_message_uuid}$`))
  })

  test('a peer that is not in the archive has no link', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, W11)
    await aroundThisSession(page)
    await choose(page, page.getByTestId('messages-hops'), '1')
    await expect(lane(page, GONE_KEY)).toBeVisible()
    const a = arrow(page, msgBetween(W11, GONE_KEY).msg_id)
    await expect.poll(() => titleOf_(a)).toMatch(/not in the archive/)
    await a.click()
    await expect(page).toHaveURL(new RegExp(`/activity\\?center=${W11}$`))
  })

  test('the subagent toggle asks for subagent edges and shows the agent lane', async ({ page, mockBackend }) => {
    const calls = await setup(mockBackend)
    await openActivity(page, W15)
    await aroundThisSession(page)
    await choose(page, page.getByTestId('messages-hops'), '1')
    await expect(lane(page, AGENT_KEY)).toHaveCount(0)
    await page.getByTestId('messages-subagents-toggle').click()
    await expect.poll(() => lastCall(calls)?.params.get('subagents')).toBe('true')
    await expect(lane(page, AGENT_KEY)).toBeVisible()
  })

  test('a click on an agent side opens the agent transcript', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, W15)
    await aroundThisSession(page)
    await page.getByTestId('messages-subagents-toggle').click()
    await arrow(page, AGENT_MSG_ID).click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${S1}/agents/${AGENT_ID}\\?m=${AGENT_DETAIL_ROW}$`))
  })
})

// ---------------------------------------------------------------------------
// Lane actions
// ---------------------------------------------------------------------------

test.describe('Messages tab: lane actions (L2)', () => {
  test('Pin moves a lane to the front', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await laneAction(page, W12, 'Pin')
    await expect.poll(() => laneKeys(page).then((k) => k[0])).toBe(W12)
    await expect(lanes(page)).toHaveCount(12)
  })

  test('the "other sessions" picker pins the picked session', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await expect(lane(page, SUBS[1])).toHaveCount(0)
    await page.getByTestId('messages-other-lane').click()
    await page.getByTestId('messages-other-picker').getByText(titleOf(SUBS[1]), { exact: true }).click()
    await expect(lane(page, SUBS[1])).toBeVisible()
    expect((await laneKeys(page))[0]).toBe(SUBS[1])
  })

  test('Hide removes a lane; the "Hidden: N" chip shows it again', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await laneAction(page, W11, 'Hide')
    await expect(lane(page, W11)).toHaveCount(0)
    await expect(page.getByTestId('messages-hidden-chip')).toHaveText('Hidden: 1')
    await page.getByTestId('messages-hidden-chip').click()
    await expect(lane(page, W11)).toBeVisible()
    await expect(page.getByTestId('messages-hidden-chip')).toHaveCount(0)
  })

  test('Collapse folds a subtree, and Expand opens it again', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await laneAction(page, S1, 'Collapse')
    await expect(fold(page, S1)).toHaveText('+10 sessions')
    await expect(lane(page, W11)).toHaveCount(0)
    await laneAction(page, S1, 'Expand')
    await expect(lane(page, W11)).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Views
// ---------------------------------------------------------------------------

test.describe('Messages tab: Graph and List views (L2)', () => {
  test('the Graph view shows the sessions and the message count of each edge', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await page.getByTestId('messages-view-graph').click()
    const g = page.getByTestId('messages-graph')
    await expect(g).toBeVisible()
    await expect(g.locator(`[data-testid="messages-graph-node"][data-session-key="${CO}"]`)).toHaveCount(1)
    await expect(g.locator(`[data-testid="messages-graph-node"][data-session-key="${S1}"]`)).toHaveCount(1)
    // The count is the edge's own text; its <title> names the session.
    // L2 UI round 3 (U5): one edge per pair, both counts ("1 ⇄ 1").
    await expect(g.locator('[data-testid="messages-graph-edge"] text').filter({ hasText: /^1 ⇄ 1$/ }).first()).toBeVisible()
  })

  test('the List view shows each message with its text; a click opens it', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await page.getByTestId('messages-view-list').click()
    const m = msgBetween(CO, S1)
    const row = page.locator(`[data-testid="messages-list-row"][data-msg-id="${m.msg_id}"]`)
    await expect(row).toContainText(m.text)
    await expect(row).toContainText(titleOf(CO))
    await expect(row).toContainText(titleOf(S1))
    await row.click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${S1}\\?m=${m.to_message_uuid}$`))
  })
})

// ---------------------------------------------------------------------------
// The tab's own filter
// ---------------------------------------------------------------------------

function filterPrefs(nodes: Record<string, unknown>[]): Record<string, unknown> {
  return {
    filters: {
      nodes: Object.fromEntries(nodes.map((n) => [n.id as string, n])),
      activeId: null, _migratedV1: true, _migratedV2: true, migrationBannerDismissed: true,
    },
  }
}

const atom = (id: string, name: string, target: string, patterns: string[], behavior = 'show-only') => ({
  id, name, enabled: true, type: 'atom', patterns, behavior, mode: 'glob', target,
})

const WORKER_TASK = atom('f-task', 'Task of worker 1-1', 'message_text', ['Task for fixture-worker-1-1.'])
const HIDE_W11 = atom('f-hide', 'Hide worker 1-1', 'title', ['fixture-worker-1-1'], 'hide')
const PAYMENTS = atom('f-pay', 'Payments project', 'project', ['payments'])

test.describe('Messages tab: filters (L2)', () => {
  test('the filter select lists "No filter" and the named filters', async ({ page, mockBackend }) => {
    await setup(mockBackend, { preferences: filterPrefs([WORKER_TASK, HIDE_W11, PAYMENTS]) })
    await openActivity(page)
    const labels = await optionLabels(page, page.getByTestId('messages-filter'))
    expect(labels[0]).toBe('No filter')
    expect(labels).toEqual(expect.arrayContaining(['Task of worker 1-1', 'Hide worker 1-1', 'Payments project']))
  })

  test('a message_text filter keeps the sessions that sent or got the message', async ({ page, mockBackend }) => {
    await setup(mockBackend, { preferences: filterPrefs([WORKER_TASK]) })
    await openActivity(page)
    await choose(page, page.getByTestId('messages-filter'), 'Task of worker 1-1')
    await expect(lanes(page)).toHaveCount(2)
    expect(new Set(await laneKeys(page))).toEqual(new Set([S1, W11]))
    await choose(page, page.getByTestId('messages-filter'), 'No filter')
    await expect(lanes(page)).toHaveCount(12)
  })

  test('the tab filter does not change the sidebar filter', async ({ page, mockBackend }) => {
    const shared: { data: Record<string, unknown> } = { data: { listRecentOnly: false, sidebarFiltersOpen: true, ...filterPrefs([WORKER_TASK]) } }
    await setup(mockBackend, { sharedPrefsState: shared })
    await openActivity(page)
    await choose(page, page.getByTestId('messages-filter'), 'Task of worker 1-1')
    await expect(lanes(page)).toHaveCount(2)
    // The tab saves its own settings; wait for that write, then check the sidebar's.
    await expect.poll(() => Object.keys(shared.data).length).toBeGreaterThan(3)
    expect((shared.data.filters as { activeId: string | null }).activeId).toBeNull()
  })

  test('a filter that hides the center says so', async ({ page, mockBackend }) => {
    await setup(mockBackend, { preferences: filterPrefs([HIDE_W11]) })
    await openActivity(page, W11)
    await aroundThisSession(page)
    await choose(page, page.getByTestId('messages-filter'), 'Hide worker 1-1')
    await expect(page.getByTestId('messages-empty')).toHaveText('The active filter hides this session.')
    await expect(lane(page, W11)).toHaveCount(0)
  })

  test('a filter that matches no session says so', async ({ page, mockBackend }) => {
    await setup(mockBackend, { preferences: filterPrefs([PAYMENTS]) })
    await openActivity(page)
    await choose(page, page.getByTestId('messages-filter'), 'Payments project')
    await expect(page.getByTestId('messages-empty')).toHaveText('No sessions match the filter.')
    await expect(lanes(page)).toHaveCount(0)
  })
})

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

test.describe('Messages tab: settings persist (L2)', () => {
  test('view, scope, and hops persist per center across a reload', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, W11)
    await aroundThisSession(page)
    await choose(page, page.getByTestId('messages-hops'), '3')
    await page.getByTestId('messages-view-graph').click()
    await expect(page.getByTestId('messages-graph')).toBeVisible()

    await page.reload()
    await expect(page.getByTestId('messages-graph')).toBeVisible()
    expect(await selectedLabel(page.getByTestId('messages-scope'))).toContain('Around this session')
    expect(await selectedLabel(page.getByTestId('messages-hops'))).toContain('3')

    // Another center has its own settings: the defaults.
    await openActivity(page, S1)
    await expect(page.getByTestId('messages-sequence')).toBeVisible()
    expect(await selectedLabel(page.getByTestId('messages-scope'))).toContain('All active sessions')
  })

  test('the no-center settings are their own set', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await laneAction(page, W11, 'Hide')
    await page.getByTestId('messages-view-list').click()
    await page.reload()
    await expect(page.getByTestId('messages-list')).toBeVisible()
    await page.getByTestId('messages-view-sequence').click()
    await expect(page.getByTestId('messages-hidden-chip')).toHaveText('Hidden: 1')

    await openActivity(page, CO)
    await expect(page.getByTestId('messages-sequence')).toBeVisible()
    await expect(page.getByTestId('messages-hidden-chip')).toHaveCount(0)
  })
})

// ---------------------------------------------------------------------------
// The Code Mode tab
// ---------------------------------------------------------------------------

test.describe('Activity: Code Mode tab (L2)', () => {
  test('without a center it asks for a session', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page)
    await page.getByTestId('activity-panel').getByRole('tab', { name: 'Code Mode' }).click()
    await expect(page.getByTestId('code-mode-no-center')).toHaveText('Pick a session to see its Code Mode runs.')
  })

  test('with a center it lists each run with its diagram; a click opens the run', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, CM_CONV_UUID)
    await page.getByTestId('activity-panel').getByRole('tab', { name: 'Code Mode' }).click()
    const tab = page.getByTestId('code-mode-tab')
    const entries = tab.getByTestId('code-mode-run-entry')
    await expect(entries).toHaveCount(1)
    await expect(entries.first().getByTestId('sequence-diagram')).toBeVisible()
    await entries.first().click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${CM_CONV_UUID}\\?m=cm-a1$`))
  })

  test('a center without runs says so', async ({ page, mockBackend }) => {
    await setup(mockBackend)
    await openActivity(page, CO)
    await page.getByTestId('activity-panel').getByRole('tab', { name: 'Code Mode' }).click()
    await expect(page.getByTestId('code-mode-tab')).toContainText('No Code Mode runs in this session.')
  })
})

// ---------------------------------------------------------------------------
// Session state chip, and the L2 targets in the sidebar and the filter editor
// ---------------------------------------------------------------------------

const DESKTOP = makeSummary({ uuid: 'dddd0000-0000-4000-8000-00000000d001', name: 'A Desktop chat', updated_at: new Date().toISOString() })

function listRow(page: Page, title: string): Locator {
  return page.getByTestId('conversation-list-flat').getByRole('button').filter({ has: page.getByText(title, { exact: true }) })
}

test.describe('Session state and L2 filter targets (L2)', () => {
  const STATES: Record<string, string> = { [CO]: 'open', [S1]: 'running', [W11]: 'ended', [W12]: 'ended_no_exit' }
  const LABELS: Record<string, string> = { [CO]: 'Open', [S1]: 'Running', [W11]: 'Ended', [W12]: 'Ended (no exit record)' }
  const few = () => treeSummaries(STATES).filter((s) => s.uuid in STATES)

  test('each list row shows its session state chip', async ({ page, mockBackend }) => {
    await setup(mockBackend, { conversations: few() })
    await withNetRetry(page, () => page.goto('/conversations'))
    for (const uuid of Object.keys(STATES)) {
      const chip = listRow(page, titleOf(uuid)).getByTestId('session-state-chip')
      if (STATES[uuid] === 'ended_no_exit') {
        // L2 UI round 3 (U6): the list may show a shorter label, with the full text as its tooltip.
        await expect(chip).toHaveText(/^Ended \(no exit( record)?\)$/)
        const full = `${(await chip.getAttribute('title')) ?? ''} ${(await chip.getAttribute('aria-label')) ?? ''} ${(await chip.textContent()) ?? ''}`
        expect(full).toContain(LABELS[uuid])
      } else {
        await expect(chip).toHaveText(LABELS[uuid])
      }
    }
  })

  test('the header shows the session state chip', async ({ page, mockBackend }) => {
    const d = { ...treeDetails()[W11], state: 'ended' } as ConversationDetail
    await setup(mockBackend, { conversations: few(), details: { [W11]: d } })
    await withNetRetry(page, () => page.goto(`/conversations/${W11}`))
    await expect(page.locator('header').first().getByTestId('session-state-chip')).toHaveText('Ended')
  })

  test('the sidebar filter applies a project atom to the list', async ({ page, mockBackend }) => {
    // A whole-value glob: it matches the project "orchestra" but no title.
    const prefs = filterPrefs([atom('p', 'Orchestra', 'project', ['orch*stra'])])
    ;(prefs.filters as { activeId: string }).activeId = 'p'
    await setup(mockBackend, { conversations: [...few(), DESKTOP], preferences: prefs })
    await withNetRetry(page, () => page.goto('/conversations'))
    await expect(listRow(page, titleOf(CO))).toBeVisible()
    await expect(page.getByText('A Desktop chat', { exact: true })).toHaveCount(0)
  })

  test('the sidebar filter applies a state atom to the list', async ({ page, mockBackend }) => {
    const prefs = filterPrefs([atom('s', 'Running', 'state', ['running'])])
    ;(prefs.filters as { activeId: string }).activeId = 's'
    await setup(mockBackend, { conversations: [...few(), DESKTOP], preferences: prefs })
    await withNetRetry(page, () => page.goto('/conversations'))
    await expect(listRow(page, titleOf(S1))).toBeVisible()
    for (const uuid of [CO, W11, W12]) await expect(page.getByText(titleOf(uuid), { exact: true })).toHaveCount(0)
  })

  test('the filter editor offers the L2 targets in order', async ({ page, mockBackend }) => {
    await setup(mockBackend, { conversations: few(), preferences: filterPrefs([]) })
    await withNetRetry(page, () => page.goto('/conversations'))
    await page.getByTestId('active-filter-select').click()
    await page.getByTestId('active-filter-manage').click()
    await page.getByTestId('manage-filters-new').click()
    const labels = await optionLabels(page, page.getByTestId('filter-editor-target'))
    const mine = ['Message text', 'Project', 'Source', 'Machine', 'Active within', 'State']
    expect(labels.filter((l) => mine.includes(l))).toEqual(mine)
  })

  const TARGETS: [string, string][] = [
    ['Message text', 'message_text'], ['Project', 'project'], ['Source', 'source'],
    ['Machine', 'machine'], ['Active within', 'active_within'], ['State', 'state'],
  ]
  for (const [label, target] of TARGETS) {
    test(`the filter editor saves an atom with target ${target}`, async ({ page, mockBackend }) => {
        const shared: { data: Record<string, unknown> } = { data: { listRecentOnly: false, sidebarFiltersOpen: true, ...filterPrefs([]) } }
      await setup(mockBackend, { conversations: few(), sharedPrefsState: shared })
      await withNetRetry(page, () => page.goto('/conversations'))
      await page.getByTestId('active-filter-select').click()
      await page.getByTestId('active-filter-manage').click()
      await page.getByTestId('manage-filters-new').click()
      await page.getByTestId('filter-editor-name').fill(`L2 ${target}`)
      await choose(page, page.getByTestId('filter-editor-target'), label)
      await page.getByTestId('filter-editor-patterns').fill(target === 'active_within' ? '1d' : 'x')
      await page.getByTestId('filter-editor-save').click()
      await expect.poll(() => {
        const nodes = (shared.data.filters as { nodes: Record<string, { name: string; target?: string }> }).nodes
        return Object.values(nodes).find((n) => n.name === `L2 ${target}`)?.target
      }).toBe(target)
    })
  }
})

// Keep the imports used when a block above changes.
void TREE
