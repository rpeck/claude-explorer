import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const REPO_ROOT = path.resolve(__dirname, '..')

/**
 * Playwright E2E test configuration.
 *
 * Runs TWO web servers in parallel: a real FastAPI backend pinned at the
 * committed e2e fixtures, and the Vite dev server. Playwright waits on
 * both `url`s before starting tests, so there is no startup race.
 *
 * Why a real backend (rearchitected 2026-06-04, see PLANS/2026.06.04-
 * e2e-console-gate-failures-and-fix-plan.md Option C):
 *
 *   - The previous Vite-only setup proxied any un-mocked `/api/*` to
 *     a dead `:8765`, returning 500. The §5.15 console-error fixture
 *     captured those 500s and failed every test that touched a new
 *     startup endpoint, even when the spec's own assertions passed.
 *   - The real backend serves config / orgs / conversations / search
 *     against the deterministic fixtures in `backend/tests/fixtures/`,
 *     so the catch-all 500 only fires for endpoints that genuinely
 *     haven't been wired (a real bug, not test infra noise).
 *
 * The shared `mockBackend()` fixture in `e2e/fixtures.ts` is still
 * available for specs that want deterministic mutative state
 * (preferences, bookmarks, watcher health). Without these in-memory
 * mocks, parallel workers would race over the real backend's mutable
 * state (preferences.json under data_dir.parent, bookmarks.json under
 * ~/.claude-explorer/) and dirty the fixtures tree.
 *
 * Background tasks that would otherwise write into `data_dir` (or its
 * parent) — search index, summary cache, file cache warmers, CC
 * watcher — are disabled via env vars so the `git diff --quiet
 * backend/tests/fixtures` freshness check in CI stays green.
 */

// Dedicated ports. Locally, reuseExistingServer reuses any server on these
// ports, and 8765 and 5173 are the ports of the installed app and of
// `npm run dev`. On 2026-10-21 the tests reused the installed app, with the
// developer's real archive, so the suite tested an older release.
const E2E_API_PORT = 18765
const E2E_WEB_PORT = 15173

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 1,
  workers: process.env.CI ? 2 : 4,
  timeout: 60_000,
  reporter: 'html',
  use: {
    baseURL: `http://localhost:${E2E_WEB_PORT}`,
    trace: 'on-first-retry',
    // A stuck action or navigation fails in 10 s / 30 s with its call log,
    // instead of using up the 60 s test timeout with no call log
    // (2026-10-26: a click on the off-screen, closed Search Pane input).
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    // Uncomment to test in other browsers
    // {
    //   name: 'firefox',
    //   use: { ...devices['Desktop Firefox'] },
    // },
    // {
    //   name: 'webkit',
    //   use: { ...devices['Desktop Safari'] },
    // },
    // Mobile viewport
    // {
    //   name: 'mobile-chrome',
    //   use: { ...devices['Pixel 5'] },
    // },
  ],

  webServer: [
    {
      command: `uv run uvicorn backend.main:app --port ${E2E_API_PORT}`,
      cwd: REPO_ROOT,
      url: `http://localhost:${E2E_API_PORT}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: 'pipe',
      stderr: 'pipe',
      env: {
        // Point the backend at the committed fixtures. The data_dir
        // already has the `by-org/.migrated_v2` sentinel so the
        // lifespan migration short-circuits; the explicit SKIP_MIGRATION
        // is defense in depth.
        CLAUDE_EXPLORER_DATA_DIR: path.join(REPO_ROOT, 'backend/tests/fixtures/desktop'),
        CLAUDE_DIR: path.join(REPO_ROOT, 'backend/tests/fixtures/claude'),
        CLAUDE_DESKTOP_APP_DIR: path.join(REPO_ROOT, 'backend/tests/fixtures/cowork'),

        // Disable every background task that would dirty the fixtures
        // tree. Without these the search index sqlite, summary cache
        // sqlite, and file-cache warmers all write files next to
        // `backend/tests/fixtures/desktop/`, failing the CI freshness
        // check.
        CLAUDE_EXPLORER_SKIP_MIGRATION: '1',
        CLAUDE_EXPLORER_DISABLE_CC_WATCHER: '1',
        CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX: '1',
        CLAUDE_EXPLORER_DISABLE_CC_WARM: '1',
        CLAUDE_EXPLORER_DISABLE_FTS5_WARM: '1',
        CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM: '1',
        CLAUDE_EXPLORER_DISABLE_FILECACHE_WARM: '1',
        // /api/account would otherwise ask claude.ai who the developer's
        // real ~/.claude-explorer credentials belong to.
        CLAUDE_EXPLORER_DISABLE_ACCOUNT_LOOKUP: '1',

        // macOS dev only: WeasyPrint links against system libs from
        // Homebrew. Harmless on Linux CI (the env var is ignored).
        // The backend doesn't import WeasyPrint at module top, but
        // leaving the path set keeps PDF-export-touching specs
        // deterministic across host environments.
        DYLD_FALLBACK_LIBRARY_PATH: '/opt/homebrew/lib',
      },
    },
    {
      command: `npm run dev -- --port ${E2E_WEB_PORT} --strictPort`,
      url: `http://localhost:${E2E_WEB_PORT}`,
      // vite.config.ts proxies /api to this backend.
      env: { CLAUDE_EXPLORER_API_URL: `http://localhost:${E2E_API_PORT}` },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
})
