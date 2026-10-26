import { test, expect, Route, withNetRetry, E2E_DEFAULT_PREFS } from './fixtures';

/**
 * Persistent rich title-based sidebar filters (Build-5).
 *
 * Mocks the backend so the test can assert filter math deterministically.
 */

const conversations = [
  { uuid: 'a-1', name: 'MCP server bootstrap', model: 'claude', source: 'CLAUDE_CODE', is_starred: false, message_count: 4, human_message_count: 2, has_branches: false, summary: '', created_at: '2026-04-01T10:00:00Z', updated_at: '2026-04-01T10:00:00Z', project_path: '/p/explorer', project_name: 'explorer', git_branch: 'main', subagents: [] },
  { uuid: 'b-2', name: 'React refactor', model: 'claude', source: 'CLAUDE_CODE', is_starred: false, message_count: 4, human_message_count: 2, has_branches: false, summary: '', created_at: '2026-04-02T10:00:00Z', updated_at: '2026-04-02T10:00:00Z', project_path: '/p/other', project_name: 'other', git_branch: 'main', subagents: [] },
  { uuid: 'c-3', name: 'MCP test plan', model: 'claude', source: 'CLAUDE_CODE', is_starred: false, message_count: 4, human_message_count: 2, has_branches: false, summary: '', created_at: '2026-04-03T10:00:00Z', updated_at: '2026-04-03T10:00:00Z', project_path: '/p/explorer', project_name: 'explorer', git_branch: 'main', subagents: [] },
];

// CF1: composable-filter graph helper. Seeds the new prefs blob shape so
// these tests bypass the legacy migration path entirely.
function seedFiltersBlob(filters: Record<string, unknown>) {
  return { filters };
}

async function seedPrefs(page: import('@playwright/test').Page, prefs: Record<string, unknown>) {
  // E2E_DEFAULT_PREFS: the recent-sessions filter is off (fixture dates are in the past).
  const data: Record<string, unknown> = { ...E2E_DEFAULT_PREFS, ...prefs };
  await page.route('**/api/preferences', (route: Route) => {
    const req = route.request();
    if (req.method() === 'PATCH') {
      try {
        const body = JSON.parse(req.postData() ?? '{}') as { data?: Record<string, unknown> };
        Object.assign(data, body.data ?? {});
      } catch {
        /* ignore */
      }
      route.fulfill({ contentType: 'application/json', body: JSON.stringify({ version: 1, data }) });
      return;
    }
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ version: 1, data }) });
  });
}

test.describe('Sidebar filters (CF1)', () => {
  test.beforeEach(async ({ context }) => {
    await context.clearCookies();
  });

  test('Manage filters button opens the modal', async ({ page }) => {
    // mockBackend with empty prefs.
    await page.route('**/api/conversations**', (route: Route) => {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(conversations) });
    });
    await page.route('**/api/config', (route) => {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data_dir: '/tmp', conversation_count: conversations.length }),
      });
    });
    await seedPrefs(page, {});

    await withNetRetry(page, () => page.goto('/'));
    await page.evaluate(() => { localStorage.clear(); });
    // CFR1: "Manage filters…" lives in the active-filter picker dropdown.
    await page.getByTestId('active-filter-select').click();
    await page.getByTestId('active-filter-manage').click();
    await expect(page.getByRole('dialog', { name: /manage filters/i })).toBeVisible();
  });

  test('creating an include-glob filter via the modal narrows the list when picked active', async ({ page }) => {
    await page.route('**/api/conversations**', (route: Route) => {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(conversations) });
    });
    await page.route('**/api/config', (route) => {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data_dir: '/tmp', conversation_count: conversations.length }),
      });
    });
    await seedPrefs(page, { filters: { nodes: {}, activeId: null, _migratedV1: true, _migratedV2: true } });

    await withNetRetry(page, () => page.goto('/'));
    await page.evaluate(() => { localStorage.clear(); });

    await page.getByTestId('active-filter-select').click();
    await page.getByTestId('active-filter-manage').click();
    // CFR1: two-pane editor — click "+ New filter" then fill the editor by testid.
    await page.getByTestId('manage-filters-new').click();

    await page.getByTestId('filter-editor-name').fill('MCP work');
    await page.getByTestId('filter-editor-behavior-show-only').click();
    await page.getByTestId('filter-editor-mode-glob').click();
    await page.getByTestId('filter-editor-patterns').fill('*mcp*');

    await page.getByTestId('filter-editor-save').click();
    // Close the modal explicitly (CF2 keeps the modal open after save so the
    // user can continue editing other filters).
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: /manage filters/i })).toHaveCount(0);

    // The new filter appears as an option in the active picker.
    const picker = page.getByTestId('active-filter-select');
    await picker.click();
    await page.getByRole('option', { name: 'MCP work' }).click();

    // Sidebar list should show only MCP* conversations.
    await expect(page.getByText('MCP server bootstrap')).toBeVisible();
    await expect(page.getByText('MCP test plan')).toBeVisible();
    await expect(page.getByText('React refactor')).toHaveCount(0);
  });

  test('hide-behavior filter (graph schema) hides matching conversations', async ({ page }) => {
    await page.route('**/api/conversations**', (route: Route) => {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(conversations) });
    });
    await page.route('**/api/config', (route) => {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data_dir: '/tmp', conversation_count: conversations.length }),
      });
    });
    await seedPrefs(page, seedFiltersBlob({
      nodes: {
        'f-2': {
          type: 'atom', id: 'f-2', name: 'Hide tests', enabled: true,
          patterns: ['*test*'], behavior: 'hide', mode: 'glob', target: 'title',
        },
      },
      activeId: 'f-2',
      _migratedV1: true,
      _migratedV2: true,
    }));

    await withNetRetry(page, () => page.goto('/'));
    await expect(page.getByText('MCP server bootstrap')).toBeVisible();
    await expect(page.getByText('React refactor')).toBeVisible();
    await expect(page.getByText('MCP test plan')).toHaveCount(0);
  });

  test('empty-state banner appears when active filter hides everything', async ({ page }) => {
    await page.route('**/api/conversations**', (route: Route) => {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(conversations) });
    });
    await page.route('**/api/config', (route) => {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data_dir: '/tmp', conversation_count: conversations.length }),
      });
    });
    await seedPrefs(page, seedFiltersBlob({
      nodes: {
        'f-3': {
          type: 'atom', id: 'f-3', name: 'Impossible', enabled: true,
          patterns: ['*xxxxxxxxxxx*'], behavior: 'show-only', mode: 'glob', target: 'title',
        },
      },
      activeId: 'f-3',
      _migratedV1: true,
      _migratedV2: true,
    }));

    await withNetRetry(page, () => page.goto('/'));
    await expect(page.getByText(/hidden by/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /clear active filter/i })).toBeVisible();
  });
});

/**
 * P1.2 — sidebar title-search scope.
 *
 * The sidebar's "Search titles..." input must filter on `name` OR
 * `project_path` only. It MUST NOT match the `summary` field. User report:
 * typing "polish" matched conversations whose title did not contain
 * "polish" because `summary` was also being scanned.
 *
 * Two tests pin the contract from both sides:
 *   (a) summary-only match -> excluded
 *   (b) project_path-only match -> included
 */
test.describe('Sidebar title search scope (P1.2)', () => {
  const summaryOnlyMatch = {
    uuid: 'sum-1',
    name: 'Quarterly review draft',
    model: 'claude',
    source: 'CLAUDE_AI',
    is_starred: false,
    message_count: 4,
    human_message_count: 2,
    has_branches: false,
    summary: 'Notes about polish on the deck',
    created_at: '2026-04-01T10:00:00Z',
    updated_at: '2026-04-01T10:00:00Z',
    project_path: '/p/reviews',
    project_name: 'reviews',
    git_branch: 'main',
    organization_id: null,
    organization_name: null,
    subagents: [],
  };

  const projectPathMatch = {
    uuid: 'proj-1',
    name: 'Build pipeline rework',
    model: 'claude',
    source: 'CLAUDE_CODE',
    is_starred: false,
    message_count: 4,
    human_message_count: 2,
    has_branches: false,
    summary: 'unrelated content',
    created_at: '2026-04-02T10:00:00Z',
    updated_at: '2026-04-02T10:00:00Z',
    project_path: '/Users/me/Source/polish-app',
    project_name: 'polish-app',
    git_branch: 'main',
    organization_id: null,
    organization_name: null,
    subagents: [],
  };

  async function mockOnly(page: import('@playwright/test').Page, rows: unknown[]) {
    await page.route('**/api/conversations**', (route: Route) => {
      const url = new URL(route.request().url());
      // Skip detail and tree URLs.
      if (/\/api\/conversations\/[^/?]+(\/tree)?($|\?)/.test(url.pathname)) {
        route.fulfill({ contentType: 'application/json', body: '{}' });
        return;
      }
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(rows) });
    });
    await page.route('**/api/config', (route) => {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data_dir: '/tmp', conversation_count: rows.length }),
      });
    });
    await page.route('**/api/orgs', (route) => {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ authenticated: true, orgs: [] }),
      });
    });
    // P3f: isolate per-test preferences (FilterContext now reads/writes server prefs).
    await seedPrefs(page, {});
  }

  test('summary-only match is excluded', async ({ page }) => {
    await mockOnly(page, [summaryOnlyMatch]);
    await withNetRetry(page, () => page.goto('/'));
    // Row visible before typing (sanity).
    await expect(page.getByText(summaryOnlyMatch.name)).toBeVisible();

    const searchInput = page.getByTestId('sidebar-title-search');
    await searchInput.fill('polish');

    // Title and project_path do NOT contain "polish"; only summary does.
    // The row must be hidden.
    await expect(page.getByText(summaryOnlyMatch.name)).toHaveCount(0);
  });

  test('project_path-only match is included', async ({ page }) => {
    await mockOnly(page, [projectPathMatch]);
    await withNetRetry(page, () => page.goto('/'));
    await expect(page.getByText(projectPathMatch.name)).toBeVisible();

    const searchInput = page.getByTestId('sidebar-title-search');
    await searchInput.fill('polish');

    // project_path contains "polish" -> row stays visible.
    await expect(page.getByText(projectPathMatch.name)).toBeVisible();
  });

  test('sidebar title-search input has verbatim placeholder copy', async ({ page }) => {
    // G7 — spec calls for the verbatim placeholder "Search titles and
    // projects" (renamed from the legacy "Search conversations..." when
    // the scope was widened to include project_path). toHaveAttribute
    // asserts the literal value, not just that A placeholder exists.
    await mockOnly(page, [summaryOnlyMatch]);
    await withNetRetry(page, () => page.goto('/'));

    const searchInput = page.getByTestId('sidebar-title-search');
    await expect(searchInput).toBeVisible();
    await expect(searchInput).toHaveAttribute('placeholder', 'Search titles and projects');

    // Bidirectional check: the placeholder must NOT be empty or the
    // legacy "Search conversations..." copy (the rename is load-bearing
    // for the user understanding that project_path is also queried).
    const ph = await searchInput.getAttribute('placeholder');
    expect(ph).toBeTruthy();
    expect(ph).not.toBe('Search conversations...');
    expect(ph).not.toBe('');
  });
});
