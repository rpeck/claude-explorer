import { test as base, expect, type Page, type Request, type Route } from '@playwright/test'
import type {
  AppConfig,
  AppConfigStats,
  ConversationDetail,
  ConversationSummary,
  ConversationTree,
  Message,
  MessageNode,
  OrgsResponse,
} from '../src/lib/types'

/**
 * Shared Playwright test fixtures for the article ↔ test coverage suite.
 *
 * Why this exists:
 *   - Clipboard tests (Cmd+C copy paths) need explicit browser-context
 *     permissions or they fail headless. Grant once for every spec.
 *   - Most specs ad-hoc reimplement `mockBackend()`; mock drift across
 *     files is a real risk. Centralize the backend mock here.
 *   - Mock payloads should match backend Pydantic shapes. We import the
 *     real TS types from `src/lib/types.ts` so any backend schema change
 *     that lands in TS surfaces as a type error in fixtures, not a flaky
 *     test in CI.
 *
 * Usage:
 *   import { test, expect } from './fixtures'
 *   test('something', async ({ page, mockBackend }) => {
 *     await mockBackend({ conversations: [...], detail: {...} })
 *     await page.goto('/')
 *   })
 */

export interface MockBackendOptions {
  /** Defaults: data_dir='/tmp', conversation_count = conversations.length */
  config?: Partial<AppConfig>
  /** Sidebar list payload. Default: []. */
  conversations?: ConversationSummary[]
  /** Detail responses keyed by uuid. Tree payload synthesized if missing. */
  details?: Record<string, ConversationDetail>
  /** Tree responses keyed by uuid. Default: synthesize linear from messages. */
  trees?: Record<string, ConversationTree>
  /** /api/orgs response. Default: authenticated single primary org. */
  orgs?: OrgsResponse
  /**
   * Initial server-side preferences blob (the `data` field of the
   * `/api/preferences` envelope). Tests that need pre-populated prefs
   * pass them here; subsequent PATCH/PUT requests in the same test
   * mutate this state in-memory.
   */
  preferences?: Record<string, unknown>
  /**
   * G1 audit — pass a caller-owned prefs state map so multiple Pages
   * (e.g. two browser contexts in a single test) can share the same
   * server-side prefs blob. Behavior:
   *   - When provided, mockBackend uses this object's `data` map for
   *     GET / merges PATCH bodies into it / overwrites it on PUT.
   *   - The `preferences` option is ignored in this mode (caller seeds
   *     the shared state directly).
   *   - When NOT provided, behavior is unchanged: each call gets its
   *     own private state. Existing tests remain isolated by default.
   *
   * Used by `preferences-cross-context.spec.ts` to prove cross-context
   * persistence without spinning up a real uvicorn process.
   */
  sharedPrefsState?: { data: Record<string, unknown> }
  /** Optional extra route handlers (priority over the defaults). */
  extraRoutes?: (page: Page) => Promise<void>
}

export type MockBackendFn = (opts?: MockBackendOptions) => Promise<void>

/**
 * Console / page-error capture, attached automatically to every spec via the
 * `consoleAssertions` auto-fixture below. Tests can opt-in to inspect or
 * extend the allowlist by depending on `consoleAssertions` directly.
 */
export interface ConsoleCapture {
  errors: string[]
  warnings: string[]
  /** Live-allowlist hook: each entry is a regex tested against the message
   *  text. Caller-added entries apply only within the test that pushes them.
   *  Pre-populated with the project-wide noise allowlist defined inside the
   *  fixture (HMR connect handshakes, React DevTools install hint, etc.). */
  allowlist: RegExp[]
}

interface Fixtures {
  mockBackend: MockBackendFn
  consoleAssertions: ConsoleCapture
}

/**
 * Project-wide console-noise allowlist. Each pattern needs a comment naming
 * its source and reason for tolerance. Adding to this list is a code-review
 * checkpoint per TESTING.md §5.15.
 *
 * Tests can extend per-test by pushing into `consoleAssertions.allowlist`
 * inside the test body (the auto-fixture is invoked AFTER the test, so
 * mid-test additions take effect for that test only).
 */
const PROJECT_CONSOLE_ALLOWLIST: RegExp[] = [
  // Vite HMR client handshake — fires on every page load in dev.
  /\[vite\] (connecting\.{3}|connected\.)/,
  // React DevTools install hint — environment-level info message.
  /Download the React DevTools/,
  // MSW unhandled-request warnings only fire in vitest, not Playwright;
  // listed defensively in case a spec ever spins up the worker.
  /\[MSW\]/,
  // Tailscale / macOS routing-table flip mid-navigation. The OS swaps
  // routes when Tailscale re-associates or WiFi roams; any in-flight
  // subresource load dies with `net::ERR_NETWORK_CHANGED` even though
  // localhost is up. `withNetRetry` catches this at the action layer
  // and (since 2026-06-04) at the subresource layer too — it retries
  // until the routing tick passes. But Chromium has already written
  // the `Failed to load resource:` line to the console for each failed
  // subresource on every failed attempt, and that text persists past
  // the retry. The pattern is anchored on the specific OS-routing-tick
  // error class so §5.15 still catches every other Failed-to-load-
  // resource shape (404, 500, 503, etc.). CI Linux runners don't see
  // this class at all.
  /Failed to load resource:.*net::ERR_NETWORK_CHANGED/,
  // Our own mockBackend leakage guard writes `[mockBackend] Unmocked
  // API call leaked through:` — that IS the failure mode (route not
  // mocked); leave it as an error so it fails the test, NOT allowlisted.
]

/**
 * Per-test helper for error-path tests that deliberately mock a network
 * failure (404 / 401 / 504 / connection-refused) and then assert the app's
 * error UI. Chromium logs `Failed to load resource: ...` (or
 * `net::ERR_CONNECTION_REFUSED`) at the network layer regardless of how the
 * app handles the rejection — for `<img>` 404s especially, the app cannot
 * suppress it. §5.15 still fires on every OTHER console error or warning.
 *
 * Usage:
 *
 *   test('shows fallback on 404', async ({ page, mockBackend, consoleAssertions }) => {
 *     expectNetworkError(consoleAssertions, 404)        // image / fetch 404s
 *     // OR
 *     expectNetworkError(consoleAssertions, 'connectionrefused')
 *     // ...
 *   })
 *
 * Keep the regex tight: pass a specific status code (or the symbolic
 * `'connectionrefused'`) — never a bare `/Failed to load resource/`, which
 * would blind the guardrail to genuinely-swallowed errors.
 */
export function expectNetworkError(
  consoleAssertions: ConsoleCapture,
  kind: 404 | 401 | 403 | 504 | 'connectionrefused',
): void {
  if (kind === 'connectionrefused') {
    consoleAssertions.allowlist.push(/net::ERR_CONNECTION_REFUSED/)
    // The Failed-to-load-resource line precedes the ERR_CONNECTION_REFUSED
    // detail in some Chromium versions; allow both shapes.
    consoleAssertions.allowlist.push(/Failed to load resource:.*ERR_CONNECTION_REFUSED/)
    return
  }
  // Numeric statuses log as `Failed to load resource: the server responded
  // with a status of <code> (<reason>)`. Anchor on the status so a 404
  // allowlist does not also catch a 504 leak.
  const code = String(kind)
  consoleAssertions.allowlist.push(
    new RegExp(`Failed to load resource:.*status of ${code}\\b`),
  )
}

/**
 * Per-test in-memory `/api/preferences` mock for specs that define their
 * OWN local `mockBackend(page)` (i.e. NOT using the shared `mockBackend`
 * fixture from this file). Without this, GET/PATCH/PUT `/api/preferences`
 * falls through Vite's proxy to whatever backend happens to be running
 * on `:8765`, so prefs (`rightPaneTab`, `searchPanel.isOpen`,
 * `showCompactions`, …) persist across browser contexts and bleed
 * between tests.
 *
 * The 2026-06-01 recovery surfaced this as the root cause of every
 * remaining post-Tailscale-fix intermittent (bookmarks, compact-markers,
 * cowork-multi-org, force-refetch, per-bubble-tools, redownload-
 * conversation, url-navigation, search-compact-auto-expand, connection-
 * status). Each spec had its own local-prefs mock copy-pasted ~18 lines
 * verbatim; this helper consolidates them.
 *
 * Behavior:
 *   - GET  → returns `{ data: prefs.data }` (current in-memory blob).
 *   - PATCH → merges the request body's `data` field (or the whole body
 *     as a fallback) into the existing prefs.
 *   - PUT  → overwrites the prefs blob with the request body's `data`
 *     field (or the whole body as a fallback).
 *   - other → 405 Method Not Allowed.
 *
 * Usage in a local mockBackend(page) helper:
 *
 *   await installLocalPrefsMock(page)                            // empty start
 *   await installLocalPrefsMock(page, { rightPaneTab: 'search' }) // seeded
 *
 * Specs that use the shared `mockBackend` fixture from this file do NOT
 * need this — the fixture already wires its own prefs mock with the
 * same shape (and accepts `preferences` / `sharedPrefsState` options).
 */
export async function installLocalPrefsMock(
  page: Page,
  initial: Record<string, unknown> = {},
): Promise<void> {
  const prefs: { data: Record<string, unknown> } = { data: { ...initial } }
  await page.route('**/api/preferences', async (route: Route) => {
    const req = route.request()
    const method = req.method()
    if (method === 'GET') {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: prefs.data }),
      })
      return
    }
    if (method === 'PATCH' || method === 'PUT') {
      const body = (req.postDataJSON() ?? {}) as Record<string, unknown>
      const patch = (body.data ?? body) as Record<string, unknown>
      prefs.data = method === 'PUT' ? patch : { ...prefs.data, ...patch }
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: prefs.data }),
      })
      return
    }
    route.fulfill({ status: 405, body: 'Method Not Allowed' })
  })
}

const PRIMARY_ORG_ID = 'ae24ae66-4622-48e7-b4b3-1ab2c49f933d'

const DEFAULT_ORGS: OrgsResponse = {
  authenticated: true,
  orgs: [{ org_id: PRIMARY_ORG_ID, name: 'Personal', is_primary: true }],
}

/**
 * Build a ConversationSummary fixture. All fields default to safe sentinels;
 * pass overrides for whatever your test cares about.
 */
export function makeSummary(overrides: Partial<ConversationSummary> & { uuid: string }): ConversationSummary {
  return {
    name: 'Untitled',
    summary: '',
    model: 'claude-sonnet-4-6',
    created_at: '2026-04-01T10:00:00Z',
    updated_at: '2026-04-01T10:00:00Z',
    is_starred: false,
    message_count: 0,
    human_message_count: 0,
    has_branches: false,
    source: 'CLAUDE_AI',
    project_path: null,
    project_name: null,
    git_branch: null,
    organization_id: PRIMARY_ORG_ID,
    organization_name: 'Personal',
    subagents: [],
    ...overrides,
  }
}

export function makeMessage(overrides: Partial<Message> & { uuid: string }): Message {
  return {
    sender: 'human',
    text: '',
    content: [],
    created_at: '2026-04-01T10:00:00Z',
    updated_at: '2026-04-01T10:00:00Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
    ...overrides,
  }
}

export function makeDetail(
  summary: ConversationSummary,
  messages: Message[],
  overrides: Partial<ConversationDetail> = {},
): ConversationDetail {
  return {
    ...summary,
    messages,
    current_leaf_message_uuid: messages.length > 0 ? messages[messages.length - 1].uuid : '',
    file_path: null,
    compact_markers: [],
    ...overrides,
  }
}

/**
 * Wrap an array of SearchResult in the SearchResponse envelope shape
 * that `/api/search` now returns (plan §B). Per-spec route overrides
 * that used to pass `body: JSON.stringify([...])` should now pass
 * `body: searchEnvelopeJson([...])` so the response shape matches
 * what the frontend parses.
 *
 * The envelope's totals default to the array length with
 * `truncated: false`. Tests that care about the truncation footer
 * should pass `total` / `returned` / `truncated` overrides.
 */
export function searchEnvelope(
  results: unknown[],
  opts: { total?: number; returned?: number; truncated?: boolean } = {},
): {
  results: unknown[]
  total_messages_matched: number
  returned_messages: number
  truncated: boolean
} {
  const returned = opts.returned ?? results.length
  const total = opts.total ?? returned
  return {
    results,
    total_messages_matched: total,
    returned_messages: returned,
    truncated: opts.truncated ?? returned < total,
  }
}

export function searchEnvelopeJson(
  results: unknown[],
  opts: { total?: number; returned?: number; truncated?: boolean } = {},
): string {
  return JSON.stringify(searchEnvelope(results, opts))
}

function synthesizeTree(detail: ConversationDetail): ConversationTree {
  // Linear chain: each message has at most one child.
  let chain: MessageNode[] = []
  for (let i = detail.messages.length - 1; i >= 0; i--) {
    chain = [{ message: detail.messages[i], children: chain }]
  }
  return {
    uuid: detail.uuid,
    root_messages: chain,
    active_path: detail.messages.map((m) => m.uuid),
  }
}

/**
 * Wrap any Playwright navigation action (`page.reload`, `page.goto`,
 * etc.) so it tolerates Tailscale-induced `net::ERR_NETWORK_CHANGED`
 * from Chromium. macOS flips the routing table when Tailscale
 * re-associates, WiFi roams, or a VPN reconnects; a navigation that
 * happens to land in that millisecond window dies with the network-
 * changed error even though the localhost dev server is still up.
 * Retrying within a few hundred milliseconds always wins.
 *
 * Two failure surfaces, both handled:
 *
 *  1. The action itself rejects with the error in its message
 *     (`page.reload()` / `page.goto()` fails because the main document
 *     request died). Caught in the catch block.
 *
 *  2. The action resolves (main document loaded fine), but one or more
 *     subresources — JS chunks, CSS, API calls fired during React
 *     mount — failed mid-navigation with the same error class. The
 *     half-loaded page never reaches a usable DOM state, so downstream
 *     assertions time out. Detected via `page.on('requestfailed')`
 *     filtered to ERR_NETWORK_CHANGED; treated as a transient retry
 *     trigger. This case shipped as the 2026-06-04 fix for
 *     `settings.spec.ts` flakes (PLANS/2026.06.04-e2e-console-gate-
 *     failures-and-fix-plan.md).
 *
 * Catches that specific error class only — any other failure (real
 * test bug, server down, navigation timeout, mocked 404) propagates
 * unchanged. The §5.15 console-error guard also allowlists the
 * `Failed to load resource: net::ERR_NETWORK_CHANGED` line that
 * Chromium emits per failed subresource, so the post-retry console
 * stays clean.
 *
 * Use in place of `page.reload()` / `page.goto(...)` for tests that
 * exercise navigation early in setup (before the page has settled),
 * or anywhere a Tailscale-style routing tick has been observed to
 * intercept a navigation. CI without Tailscale will never trip the
 * retry path (subresource detection is a no-op when no failures
 * fire).
 *
 * Usage:
 *   await withNetRetry(page, () => page.reload())
 *   await withNetRetry(page, () => page.goto('/conversations'))
 */
export async function withNetRetry<T>(
  page: Page,
  action: () => Promise<T>,
  maxAttempts = 4,
): Promise<T> {
  let lastError: unknown
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const subresourceFailures: string[] = []
    const onRequestFailed = (req: Request) => {
      const failure = req.failure()
      if (failure?.errorText.includes('net::ERR_NETWORK_CHANGED')) {
        subresourceFailures.push(req.url())
      }
    }
    page.on('requestfailed', onRequestFailed)
    try {
      const result = await action()
      if (subresourceFailures.length === 0) {
        return result
      }
      // Action resolved (main doc loaded) but one or more subresource
      // loads died mid-navigation. The page is half-loaded; retry the
      // whole action so React can mount against a stable routing table.
      lastError = new Error(
        `net::ERR_NETWORK_CHANGED on ${subresourceFailures.length} ` +
          `subresource(s) during navigation (attempt ${attempt + 1}/${maxAttempts}). ` +
          `First failure: ${subresourceFailures[0]}`,
      )
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      if (!message.includes('net::ERR_NETWORK_CHANGED')) {
        throw err
      }
      lastError = err
    } finally {
      page.off('requestfailed', onRequestFailed)
    }
    // Tiny backoff so the next OS routing tick lands first.
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw lastError
}

export const test = base.extend<Fixtures>({
  /**
   * Grant clipboard permissions for every spec so Cmd+C tests pass headless.
   * Override per-test by re-granting different permissions if needed.
   */
  context: async ({ context }, use) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    // eslint-disable-next-line react-hooks/rules-of-hooks -- safe: Playwright fixture API `use(value)`, not React.use(). The eslint plugin pattern-matches on the bare name.
    await use(context)
  },

  mockBackend: async ({ page }, use) => {
    const fn: MockBackendFn = async (opts = {}) => {
      const conversations = opts.conversations ?? []
      const details = opts.details ?? {}
      const trees = opts.trees ?? {}
      const orgs = opts.orgs ?? DEFAULT_ORGS
      const config: AppConfig = {
        data_dir: '/tmp',
        ...(opts.config ?? {}),
      }
      const configStats: AppConfigStats = {
        ...config,
        conversation_count: conversations.length,
      }

      // Per-test mutable preferences state. Deep-copy seed so callers
      // can safely reuse the same object across tests.
      //
      // G1 audit: when `sharedPrefsState` is supplied, the caller owns
      // the map and we wire route handlers to mutate it directly — that
      // lets two different page contexts in the same test see each
      // other's PATCHes. Default path (no sharedPrefsState) is unchanged
      // and remains test-isolated.
      const prefsState: { data: Record<string, unknown> } =
        opts.sharedPrefsState ?? {
          data: JSON.parse(JSON.stringify(opts.preferences ?? {})),
        }

      // -----------------------------------------------------------------
      // Catch-all leakage guard (registered FIRST so LIFO order runs it
      // LAST). Any /api/* request that isn't matched by a more specific
      // default below — or by an extraRoutes/per-test override — falls
      // through to here, gets a noisy 500, and surfaces as a test
      // failure instead of silently leaking to whatever backend is
      // running on :8765. This is the load-bearing safety net for the
      // whole mock-data-conversion plan.
      // -----------------------------------------------------------------
      await page.route('**/api/**', (route: Route) => {
        const req = route.request()
        console.error(
          `[mockBackend] Unmocked API call leaked through: ${req.method()} ${req.url()}`,
        )
        route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({
            error: 'Unmocked API route hit in test',
            method: req.method(),
            url: req.url(),
          }),
        })
      })

      // -----------------------------------------------------------------
      // Watcher health (RootLayout's WatcherMissingBanner consumes this
      // on every page mount). The endpoint shipped after the e2e
      // leakage-guard and the console-error assertion fixtures, so
      // pre-fixture-update specs hit the catch-all 500 and the auto-
      // assertion fired on every test. Default to ``installed: true``
      // so the banner renders nothing — matches the production
      // experience for users who have run ``install-watcher``. Specs
      // that need to assert the banner can override via extraRoutes.
      // -----------------------------------------------------------------
      await page.route('**/api/health/watcher', (route: Route) => {
        route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({
            installed: true,
            platform: 'darwin',
            install_command: 'uv run claude-explorer install-watcher',
            docs_url: '',
          }),
        })
      })

      // -----------------------------------------------------------------
      // Config
      // -----------------------------------------------------------------
      await page.route('**/api/config', (route: Route) => {
        route.fulfill({ contentType: 'application/json', body: JSON.stringify(config) })
      })

      await page.route('**/api/config/stats', (route: Route) => {
        route.fulfill({ contentType: 'application/json', body: JSON.stringify(configStats) })
      })

      // -----------------------------------------------------------------
      // Orgs
      // -----------------------------------------------------------------
      await page.route('**/api/orgs', (route: Route) => {
        route.fulfill({ contentType: 'application/json', body: JSON.stringify(orgs) })
      })

      // -----------------------------------------------------------------
      // Preferences (GET / PATCH / PUT) — stateful echo.
      //
      // The Settings + filter migrations all dual-write here, so a real
      // PATCH must merge into in-memory state and a follow-up GET must
      // reflect prior PATCHes. This is the highest-leakage route post-P3
      // and the single biggest reason this M1 fixture extension exists.
      // -----------------------------------------------------------------
      await page.route('**/api/preferences', (route: Route) => {
        const req = route.request()
        const method = req.method()
        if (method === 'PATCH' || method === 'PUT') {
          let body: { data?: Record<string, unknown> } = {}
          try {
            body = JSON.parse(req.postData() ?? '{}') as { data?: Record<string, unknown> }
          } catch {
            body = {}
          }
          const incoming = body.data ?? {}
          if (method === 'PUT') {
            prefsState.data = { ...incoming }
          } else {
            Object.assign(prefsState.data, incoming)
          }
        }
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ version: 1, data: prefsState.data }),
        })
      })

      // -----------------------------------------------------------------
      // Search — empty SearchResponse envelope by default. Per-test
      // specs that exercise search override via extraRoutes or
      // page.route(). 2026-05-16 (plan §B): the wire format changed
      // from `list[SearchResult]` to a wrapped envelope with
      // truncation disclosure (backend.models.SearchResponse).
      // -----------------------------------------------------------------
      await page.route('**/api/search**', (route: Route) => {
        route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({
            results: [],
            total_messages_matched: 0,
            returned_messages: 0,
            truncated: false,
          }),
        })
      })

      // -----------------------------------------------------------------
      // Claude Code image cache — 404 by default. Specs that need a real
      // image (or a 200 retry path) install their own page.route().
      // -----------------------------------------------------------------
      await page.route('**/api/cc-image**', (route: Route) => {
        route.fulfill({
          status: 404,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'no cache' }),
        })
      })

      // -----------------------------------------------------------------
      // Bookmarks — list + CRUD echo.
      // -----------------------------------------------------------------
      await page.route('**/api/bookmarks', (route: Route) => {
        const req = route.request()
        if (req.method() === 'POST') {
          let body: Record<string, unknown> = {}
          try {
            body = JSON.parse(req.postData() ?? '{}') as Record<string, unknown>
          } catch {
            body = {}
          }
          const created = {
            id: `bk-${Math.random().toString(36).slice(2, 10)}`,
            created_at: new Date().toISOString(),
            ...body,
          }
          route.fulfill({
            status: 201,
            contentType: 'application/json',
            body: JSON.stringify(created),
          })
          return
        }
        // GET (list) → unwrapped envelope shape: src/lib/api.ts:listBookmarks
        // reads `body.bookmarks`, so the envelope must be present.
        route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({ bookmarks: [] }),
        })
      })

      await page.route('**/api/bookmarks/*', (route: Route) => {
        const req = route.request()
        const url = req.url()
        const id = url.split('/').pop()?.split('?')[0] ?? ''
        if (req.method() === 'DELETE') {
          route.fulfill({ status: 204, body: '' })
          return
        }
        if (req.method() === 'PATCH') {
          let body: Record<string, unknown> = {}
          try {
            body = JSON.parse(req.postData() ?? '{}') as Record<string, unknown>
          } catch {
            body = {}
          }
          route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              id,
              conversation_uuid: '',
              message_uuid: '',
              created_at: new Date().toISOString(),
              ...body,
            }),
          })
          return
        }
        route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
      })

      // -----------------------------------------------------------------
      // Desktop file proxy: /api/{org_id}/files/{file_uuid}/{thumbnail|preview}
      // The single-segment `*` glob safely matches UUID org ids.
      // -----------------------------------------------------------------
      await page.route('**/api/*/files/**', (route: Route) => {
        route.fulfill({
          status: 404,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'not cached' }),
        })
      })

      // -----------------------------------------------------------------
      // Post-P4c local attachments cache: /api/attachments/{conv}/{file}/{variant}
      // -----------------------------------------------------------------
      await page.route('**/api/attachments/**', (route: Route) => {
        route.fulfill({
          status: 404,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'not cached' }),
        })
      })

      // -----------------------------------------------------------------
      // Fetch pipeline routes
      // -----------------------------------------------------------------
      await page.route('**/api/fetch/status', (route: Route) => {
        route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({
            has_credentials: false,
            credentials_path: '/tmp/credentials.json',
            output_dir: '/tmp/conversations',
            existing_count: 0,
            credentials_age_days: null,
          }),
        })
      })

      // SSE streams — emit a minimal start+complete frame and signal a
      // huge `retry:` interval so the browser's native EventSource does
      // NOT auto-reconnect after the body closes (default 3s reconnect
      // would loop forever).
      const sseBody =
        'retry: 999999\n' +
        'event: start\ndata: {}\n\n' +
        'event: complete\ndata: {}\n\n'

      const fulfillSse = (route: Route) => {
        route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          headers: { 'cache-control': 'no-cache' },
          body: sseBody,
        })
      }
      await page.route('**/api/fetch/start**', fulfillSse)
      await page.route('**/api/fetch/refresh**', fulfillSse)

      await page.route('**/api/fetch/conversation/*', (route: Route) => {
        const url = route.request().url()
        const uuid = url.split('/').pop()?.split('?')[0] ?? ''
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ uuid, status: 'fetched', name: 'Mock' }),
        })
      })

      // -----------------------------------------------------------------
      // Conversations list/detail/tree (existing behavior)
      // -----------------------------------------------------------------
      await page.route('**/api/conversations**', (route: Route) => {
        const url = route.request().url()
        // Detail tree: /api/conversations/<uuid>/tree
        const treeMatch = url.match(/\/api\/conversations\/([^/?]+)\/tree/)
        if (treeMatch) {
          const uuid = treeMatch[1]
          const tree = trees[uuid] ?? (details[uuid] ? synthesizeTree(details[uuid]) : { uuid, root_messages: [], active_path: [] })
          route.fulfill({ contentType: 'application/json', body: JSON.stringify(tree) })
          return
        }
        // Detail: /api/conversations/<uuid>
        const detailMatch = url.match(/\/api\/conversations\/([^/?]+)(?:\?|$)/)
        if (detailMatch) {
          const uuid = detailMatch[1]
          const detail = details[uuid]
          if (detail) {
            route.fulfill({ contentType: 'application/json', body: JSON.stringify(detail) })
          } else {
            route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
          }
          return
        }
        // List: /api/conversations(?...)
        route.fulfill({ contentType: 'application/json', body: JSON.stringify(conversations) })
      })

      // -----------------------------------------------------------------
      // Export endpoints — empty octet-stream by default. Registered
      // AFTER the conversations handler so LIFO favors the export
      // matcher for /api/conversations/<uuid>/export/* paths.
      // -----------------------------------------------------------------
      const fulfillEmptyOctet = (route: Route) => {
        route.fulfill({
          status: 200,
          contentType: 'application/octet-stream',
          body: '',
        })
      }
      await page.route('**/api/conversations/*/export/**', fulfillEmptyOctet)
      await page.route('**/api/export/**', fulfillEmptyOctet)

      // Per-test overrides go LAST so LIFO grants them top priority.
      if (opts.extraRoutes) {
        await opts.extraRoutes(page)
      }
    }
    // eslint-disable-next-line react-hooks/rules-of-hooks -- safe: Playwright fixture API `use(value)`, not React.use(). The eslint plugin pattern-matches on the bare name.
    await use(fn)
  },

  /**
   * AUTO-FIXTURE (runs for every test): capture browser console errors
   * and warnings + uncaught page errors throughout the test, then assert
   * empty at teardown (modulo PROJECT_CONSOLE_ALLOWLIST + any per-test
   * additions to `consoleAssertions.allowlist`).
   *
   * Codified in TESTING.md §5.15. Caught by the 2026-05-24 settings
   * flash-and-disappear regression — that bug shipped past my e2e because
   * I asserted DOM state but never console state. The user found it on
   * first manual test.
   *
   * Failure modes this catches:
   *   - Uncaught promise rejections (e.g. setIncludeCompactInExports
   *     racing with a destroyed component)
   *   - React lifecycle warnings (missing key, missing aria-describedby
   *     on Dialog, useEffect dep drift)
   *   - Network errors that the UI silently swallows (e.g. failed
   *     prefs PATCH that the user doesn't see but the dev tools do)
   *
   * Per-test opt-out: a test that legitimately needs a noisy console can
   * push a regex into `consoleAssertions.allowlist`:
   *
   *     test('legacy noisy thing', async ({ page, consoleAssertions }) => {
   *       consoleAssertions.allowlist.push(/known third-party warning/)
   *       // ... rest of test
   *     })
   *
   * The push is scoped to that test (allowlist array is fresh per test).
   */
  consoleAssertions: [
    async ({ page }, use) => {
      const capture: ConsoleCapture = {
        errors: [],
        warnings: [],
        allowlist: [...PROJECT_CONSOLE_ALLOWLIST],
      }
      const isAllowed = (text: string) =>
        capture.allowlist.some((rx) => rx.test(text))
      page.on('pageerror', (e: Error) => {
        const msg = `pageerror: ${e.message}`
        if (!isAllowed(msg)) capture.errors.push(msg)
      })
      page.on('console', (m) => {
        const text = m.text()
        if (isAllowed(text)) return
        const type = m.type()
        if (type === 'error') capture.errors.push(text)
        else if (type === 'warning') capture.warnings.push(text)
      })
      await use(capture)
      // Assert AFTER the test body completes. Both arrays empty → test
      // truly passed. Either populated → test was misleading-green per
      // §5.15.
      if (capture.errors.length > 0) {
        throw new Error(
          `Console errors during test (see TESTING.md §5.15):\n  ` +
            capture.errors.join('\n  '),
        )
      }
      if (capture.warnings.length > 0) {
        throw new Error(
          `Console warnings during test (see TESTING.md §5.15):\n  ` +
            capture.warnings.join('\n  '),
        )
      }
    },
    { auto: true },
  ],
})

export { expect }
export type { Page, Route } from '@playwright/test'
export const PRIMARY_ORG = PRIMARY_ORG_ID
