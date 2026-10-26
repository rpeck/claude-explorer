/**
 * CF1 — FilterContext composable-graph migration end-to-end.
 *
 * After this commit:
 *   - The legacy keys `savedFilters` + `activeFilterIds` are migrated
 *     once into the new graph blob `filters: { nodes, activeId, _migratedV1 }`.
 *   - The migration PATCH explicitly nulls `savedFilters` /
 *     `activeFilterIds` (the backend's per-key-overwrite semantics
 *     require explicit null to clear).
 *   - The sentinel `_migratedV1: true` prevents re-migration on reload.
 */

import { test, expect, withNetRetry, recordPreferencePatches, preferencePatchesSent, E2E_DEFAULT_PREFS } from './fixtures'
import type { Route } from './fixtures'

interface PrefsState {
  data: Record<string, unknown>
}

interface PatchLog {
  bodies: Array<Record<string, unknown>>
}

async function installPrefsRoute(
  page: import('@playwright/test').Page,
  initial: Record<string, unknown> = {},
): Promise<{ state: PrefsState; patches: PatchLog }> {
  // E2E_DEFAULT_PREFS: the recent-sessions filter is off (fixture dates are in the past).
  const state: PrefsState = { data: { ...E2E_DEFAULT_PREFS, ...initial } }
  const patches: PatchLog = { bodies: [] }

  await page.route('**/api/preferences', (route: Route) => {
    const req = route.request()
    if (req.method() === 'PATCH') {
      let body: { data?: Record<string, unknown> } = {}
      try {
        body = JSON.parse(req.postData() ?? '{}')
      } catch {
        body = {}
      }
      const patchData = body.data ?? {}
      patches.bodies.push(patchData)
      // Mirror the backend's per-key overwrite (including null = clear).
      Object.assign(state.data, patchData)
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ version: 1, data: state.data }),
      })
      return
    }
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ version: 1, data: state.data }),
    })
  })

  return { state, patches }
}

test.describe('FilterContext composable-graph migration (CF1)', () => {
  test('legacy savedFilters with a pinned filter migrates into a default-migrated group, with legacy keys nulled in the PATCH', async ({ page, mockBackend }) => {
    await mockBackend({})
    const { state, patches } = await installPrefsRoute(page, {
      savedFilters: [
        {
          id: 'p1', name: 'Scan Gmail',
          patterns: ['Scan Gmail*'], polarity: 'exclude', mode: 'glob',
          target: 'title', pinned: true,
        },
        {
          id: 'p2', name: 'Other',
          patterns: ['*other*'], polarity: 'include', mode: 'glob',
          target: 'title', pinned: false,
        },
      ],
      activeFilterIds: [],
    })

    await withNetRetry(page, () => page.goto('/'))

    // Wait until the migration PATCH has landed.
    await expect.poll(() => patches.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0)

    const migrationPatch = patches.bodies.find(
      (b) => 'filters' in b && b.savedFilters === null && b.activeFilterIds === null,
    )
    expect(migrationPatch).toBeDefined()

    interface MigratedFilters {
      nodes: Record<string, { type: string; childIds?: string[] } & Record<string, unknown>>
      activeId: string | null
      _migratedV1: boolean
      _migratedV2?: boolean
    }
    const filtersBlob = (migrationPatch as { filters: MigratedFilters }).filters

    // Both atoms migrated; pinned key gone; group references the pinned id.
    // CFR1: legacy migration emits v2-shape atoms directly (behavior, not
    // polarity). The mapping is include → show-only, exclude → hide.
    expect(filtersBlob.nodes['p1']).toMatchObject({ type: 'atom', name: 'Scan Gmail', behavior: 'hide' })
    expect(filtersBlob.nodes['p2']).toMatchObject({ type: 'atom', name: 'Other', behavior: 'show-only' })
    const grp = filtersBlob.nodes['default-migrated'] as { type: string; childIds: string[] }
    expect(grp.type).toBe('group')
    expect(grp.childIds).toEqual(['p1'])
    expect(filtersBlob.activeId).toBe('default-migrated')
    expect(filtersBlob._migratedV1).toBe(true)
    // CFR1: v0→v1 migration jumps straight to v2 (no intermediate v1
    // polarity stage), so the _migratedV2 sentinel is set in the same
    // PATCH.
    expect(filtersBlob._migratedV2).toBe(true)

    // After the migration applies, the server state has the legacy keys nulled.
    expect(state.data.savedFilters).toBeNull()
    expect(state.data.activeFilterIds).toBeNull()
    expect(state.data.filters).toBeDefined()
  })

  test('reload after migration does NOT re-PATCH', async ({ page, mockBackend }) => {
    await mockBackend({})
    const { patches } = await installPrefsRoute(page, {
      savedFilters: [
        { id: 'p1', name: 'P1', patterns: ['*p1*'], polarity: 'include', mode: 'glob', target: 'title', pinned: true },
      ],
      activeFilterIds: [],
    })

    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))
    await expect.poll(() => patches.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0)
    const countAfterFirstLoad = patches.bodies.length

    await withNetRetry(page, () => page.reload())
    // The migration effects ran for the reloaded state (a DOM signal of
    // FilterContext), so any PATCH they send has been sent (2026-10-24:
    // a 500 ms sleep "to settle").
    await expect(page.locator('html[data-filters-migration="v1v2"]')).toHaveCount(1)

    // No new PATCHes should have fired: legacy keys are nulled and
    // _migratedV1 sentinel is true on the server.
    expect(await preferencePatchesSent(page)).toEqual([])
    expect(patches.bodies.length).toBe(countAfterFirstLoad)
  })

  test('new-shape passthrough: prefs already contain v2 `filters`, no migration writes', async ({ page, mockBackend }) => {
    await mockBackend({})
    const { patches } = await installPrefsRoute(page, {
      filters: {
        nodes: {
          a: {
            // CFR1: v2 atoms carry `behavior`, not `polarity`.
            type: 'atom', id: 'a', name: 'Already migrated',
            enabled: true, patterns: ['*foo*'], behavior: 'show-only',
            mode: 'glob', target: 'title',
          },
        },
        activeId: 'a',
        _migratedV1: true,
        _migratedV2: true,
      },
    })

    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))
    // The migration effects ran for the server's state (a DOM signal of
    // FilterContext; 2026-10-24: a 500 ms sleep).
    await expect(page.locator('html[data-filters-migration="v1v2"]')).toHaveCount(1)
    // The picker shows the pre-existing filter as active (load smoke).
    await expect(page.getByTestId('active-filter-select')).toContainText('Already migrated')
    // No migration PATCH fired.
    expect(await preferencePatchesSent(page)).toEqual([])
    expect(patches.bodies).toEqual([])
  })
})
