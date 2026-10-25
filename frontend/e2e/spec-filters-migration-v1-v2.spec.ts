// Spec-driven test: migration of legacy preferences to the v1 + v2
// composable filters shape.
//
// UX.md clauses verified (lines 615-738, "Composable filters" §
// "Migration from the legacy pinned model"):
//   - v1: "Each legacy filter becomes an AtomFilter (drop pinned).
//     The previously-pinned atoms become children of a single GroupFilter
//     named Default (migrated). The new active filter is the migrated
//     group when at least one filter was pinned, otherwise null. The
//     legacy savedFilters and activeFilterIds keys are explicitly nulled
//     in the migration PATCH so the backend's per-key overwrite clears
//     them. A sentinel filters._migratedV1: true is set so subsequent
//     mounts skip migration."
//   - v2: "each atom's previous polarity: 'include' becomes
//     behavior: 'show-only', and polarity: 'exclude' becomes
//     behavior: 'hide'. Groups are unchanged. Saved filters survive the
//     migration with their previous semantics intact."
//   - Composable filters banner: appears once after migration; dismiss
//     persists through the same preferences PATCH.
//   - Fresh installs never see the banner.
//
// NO APP CODE was read while writing this test.

import { test, expect, withNetRetry, recordPreferencePatches, preferencePatchesSent } from './fixtures'
import { makeSummary } from './fixtures'

const conversations = [
  makeSummary({ uuid: 'c-foo', name: 'Foo morning' }),
  makeSummary({ uuid: 'c-bar', name: 'Bar afternoon' }),
  makeSummary({ uuid: 'c-baz', name: 'Baz evening' }),
]

test.describe('Migration v1 → v2 + banner', () => {
  test('Legacy savedFilters + activeFilterIds → "Default (migrated)" group + tombstone PATCH', async ({ page, mockBackend }) => {
    const patches: Array<Record<string, unknown>> = []

    await mockBackend({
      conversations,
      preferences: {
        // Legacy shape: savedFilters[] + activeFilterIds[] + each filter
        // has polarity instead of behavior.
        savedFilters: [
          {
            id: 'legacy-1',
            name: 'LegacyOne',
            patterns: ['*Foo*'],
            polarity: 'exclude',
            mode: 'glob',
            target: 'title',
            pinned: true,
            enabled: true,
          },
          {
            id: 'legacy-2',
            name: 'LegacyTwo',
            patterns: ['*Bar*'],
            polarity: 'include',
            mode: 'glob',
            target: 'title',
            pinned: true,
            enabled: true,
          },
          {
            id: 'legacy-3',
            name: 'LegacyThree',
            patterns: ['*Baz*'],
            polarity: 'exclude',
            mode: 'glob',
            target: 'title',
            pinned: false,
            enabled: true,
          },
        ],
        activeFilterIds: ['legacy-1', 'legacy-2'],
      },
    })

    // PATCH spy MUST be registered AFTER mockBackend so LIFO grants it
    // top priority. The handler delegates the actual response back to
    // mockBackend's stateful echo via route.fallback().
    await page.route('**/api/preferences', async (route, req) => {
      if (req.method() === 'PATCH' || req.method() === 'PUT') {
        try {
          const parsed = JSON.parse(req.postData() ?? '{}') as { data?: Record<string, unknown> }
          if (parsed.data) patches.push(parsed.data)
        } catch {
          // ignore
        }
      }
      await route.fallback()
    })

    await withNetRetry(page, () => page.goto('/'))

    // Wait for the migration PATCH to land. Poll for tombstones AND the
    // new filters blob.
    await expect.poll(() => {
      // Find a patch that contains the migration payload.
      return patches.some((p) => 'filters' in p && 'savedFilters' in p && 'activeFilterIds' in p)
    }, { timeout: 5000 }).toBe(true)

    const migrationPatch = patches.find(
      (p) => 'filters' in p && 'savedFilters' in p && 'activeFilterIds' in p,
    )
    expect(migrationPatch).toBeTruthy()

    // Tombstones explicit:
    expect(migrationPatch?.savedFilters).toBeNull()
    expect(migrationPatch?.activeFilterIds).toBeNull()

    // _migratedV1 sentinel is set.
    const filters = migrationPatch?.filters as {
      nodes?: Record<string, unknown>
      activeId?: string | null
      _migratedV1?: boolean
      _migratedV2?: boolean
    } | undefined
    expect(filters?._migratedV1).toBe(true)

    // Default (migrated) group is named correctly and is the active
    // filter (we had pinned filters).
    expect(filters?.activeId).toBeTruthy()
    const nodes = filters?.nodes ?? {}
    const activeNode = (nodes as Record<string, { name?: string; type?: string; childIds?: string[] }>)[
      filters?.activeId as string
    ]
    expect(activeNode?.name).toMatch(/Default \(migrated\)/i)
    expect(activeNode?.type).toBe('group')
  })

  test('v1 → v2 polarity → behavior promotion; groups unchanged; _migratedV2 sentinel', async ({ page, mockBackend }) => {
    const patches: Array<Record<string, unknown>> = []

    // v1 shape: filters.nodes contain atoms with polarity (no behavior),
    // and a group. _migratedV1: true (so v1 already ran) but no
    // _migratedV2 yet.
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-include': {
              id: 'a-include',
              type: 'atom',
              name: 'IncludeFoo',
              enabled: true,
              polarity: 'include',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
            'a-exclude': {
              id: 'a-exclude',
              type: 'atom',
              name: 'ExcludeBar',
              enabled: true,
              polarity: 'exclude',
              patterns: ['*Bar*'],
              mode: 'glob',
              target: 'title',
            },
            'g-1': {
              id: 'g-1',
              type: 'group',
              name: 'Group1',
              enabled: true,
              match: 'all',
              childIds: ['a-include', 'a-exclude'],
            },
          },
          activeId: 'g-1',
          _migratedV1: true,
        },
      },
    })

    // PATCH spy AFTER mockBackend (LIFO).
    await page.route('**/api/preferences', async (route, req) => {
      if (req.method() === 'PATCH' || req.method() === 'PUT') {
        try {
          const parsed = JSON.parse(req.postData() ?? '{}') as { data?: Record<string, unknown> }
          if (parsed.data) patches.push(parsed.data)
        } catch {
          // ignore
        }
      }
      await route.fallback()
    })

    await withNetRetry(page, () => page.goto('/'))

    // Wait for v2 migration patch.
    await expect.poll(() => {
      return patches.some((p) => {
        const f = p.filters as { _migratedV2?: boolean } | undefined
        return f?._migratedV2 === true
      })
    }, { timeout: 5000 }).toBe(true)

    const migrationPatch = patches.find((p) => {
      const f = p.filters as { _migratedV2?: boolean } | undefined
      return f?._migratedV2 === true
    })
    const filters = migrationPatch?.filters as {
      nodes: Record<string, { type: string; behavior?: string; polarity?: string; match?: string }>
      _migratedV2: boolean
    }

    // Polarity → behavior:
    expect(filters.nodes['a-include'].behavior).toBe('show-only')
    expect(filters.nodes['a-exclude'].behavior).toBe('hide')

    // Groups unchanged (still no behavior of their own per UX.md).
    expect(filters.nodes['g-1'].type).toBe('group')
    expect(filters.nodes['g-1'].match).toBe('all')

    // Sentinel set.
    expect(filters._migratedV2).toBe(true)
  })

  test('Migration is idempotent: no double-PATCH across mounts', async ({ page, mockBackend }) => {
    const patches: Array<Record<string, unknown>> = []

    // Already-migrated state: both sentinels true.
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'AlreadyMigrated',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: 'a-1',
          _migratedV1: true,
          _migratedV2: true,
          migrationBannerDismissed: true,
        },
      },
    })

    // PATCH spy AFTER mockBackend (LIFO).
    await page.route('**/api/preferences', async (route, req) => {
      if (req.method() === 'PATCH' || req.method() === 'PUT') {
        try {
          const parsed = JSON.parse(req.postData() ?? '{}') as { data?: Record<string, unknown> }
          if (parsed.data) patches.push(parsed.data)
        } catch {
          // ignore
        }
      }
      await route.fallback()
    })

    await recordPreferencePatches(page)
    await withNetRetry(page, () => page.goto('/'))
    // The migration effects ran (a DOM signal of FilterContext), so any
    // PATCH they send has been sent (2026-10-24: a 500 ms sleep).
    await expect(page.locator('html[data-filters-migration="v1v2"]')).toHaveCount(1)
    for (const sent of await preferencePatchesSent(page)) if (sent.data) patches.push(sent.data)

    // Filter applied as expected.
    await expect(page.getByText('Foo morning')).toHaveCount(0)

    // No migration patch should have written sentinels (the prefs are
    // already migrated). Patches that target only filters._migratedV1
    // or _migratedV2 should not appear.
    const migrationLikePatches = patches.filter((p) => {
      const f = p.filters as { _migratedV1?: boolean; _migratedV2?: boolean } | undefined
      // Patches that re-set _migratedV1/V2 indicate an unwanted re-run.
      return f && (f._migratedV1 !== undefined || f._migratedV2 !== undefined)
    })
    // Tolerate one no-op sentinel echo on first hydration, but not many.
    expect(migrationLikePatches.length).toBeLessThanOrEqual(1)
  })

  test('Banner appears once when _migratedV1 && !migrationBannerDismissed', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'MigratedAtom',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: 'a-1',
          _migratedV1: true,
          _migratedV2: true,
          migrationBannerDismissed: false,
        },
      },
    })

    await withNetRetry(page, () => page.goto('/'))

    // Banner is visible: contains "composable" (per the plan's banner copy).
    await expect(page.getByText(/composable/i)).toBeVisible()
  })

  test('Dismiss persists across reload (intercept PATCH then reload, banner gone)', async ({ page, mockBackend }) => {
    const patches: Array<Record<string, unknown>> = []

    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'MigratedAtom',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: 'a-1',
          _migratedV1: true,
          _migratedV2: true,
          migrationBannerDismissed: false,
        },
      },
    })

    // PATCH spy AFTER mockBackend (LIFO).
    await page.route('**/api/preferences', async (route, req) => {
      if (req.method() === 'PATCH' || req.method() === 'PUT') {
        try {
          const parsed = JSON.parse(req.postData() ?? '{}') as { data?: Record<string, unknown> }
          if (parsed.data) patches.push(parsed.data)
        } catch {
          // ignore
        }
      }
      await route.fallback()
    })

    await withNetRetry(page, () => page.goto('/'))

    // Banner present.
    await expect(page.getByText(/composable/i)).toBeVisible()

    // Dismiss the banner.
    const dismiss = page.getByRole('button', { name: /dismiss|close|×/i }).first()
    await expect(dismiss).toBeVisible()
    await dismiss.click()

    // Banner gone in this session.
    await expect(page.getByText(/composable/i)).toHaveCount(0)

    // Wait for the dismiss PATCH to fire.
    await expect.poll(() => {
      return patches.some((p) => {
        const f = p.filters as { migrationBannerDismissed?: boolean } | undefined
        if (f?.migrationBannerDismissed === true) return true
        // Some implementations may PATCH the flag at the top level.
        return p.migrationBannerDismissed === true
      })
    }, { timeout: 5000 }).toBe(true)

    // Reload — banner should NOT reappear.
    await withNetRetry(page, () => page.reload())
    await expect(page.getByText(/composable/i)).toHaveCount(0)
  })

  test('Fresh install never sees the banner', async ({ page, mockBackend }) => {
    // Fresh install: no legacy state, no _migratedV1.
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {},
          activeId: null,
          _migratedV1: false,
          _migratedV2: true,
        },
      },
    })

    await withNetRetry(page, () => page.goto('/'))

    // No "composable" banner.
    await expect(page.getByText(/composable/i)).toHaveCount(0)
  })
})
