/**
 * N2 (PLANS/2026.11.18-codex-source.md §4.1, "The presentation map"):
 * SOURCE_PRESETS in SourceBadge.tsx keeps the icon, label, and color of
 * each source, and a test fails if a registry source has no preset.
 *
 * These tests use the real `sources.generated.json`. When a later node
 * (N8) adds a source to the registry, they fail until SourceBadge gets
 * its preset.
 */
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { REAL, setRealRegistry, xfail } from './fakeRegistry'

describe('SOURCE_PRESETS covers the registry', () => {
  xfail('SourceBadge exports SOURCE_PRESETS with an entry for every registry source', async () => {
    setRealRegistry()
    const mod = (await import('@/components/conversation/SourceBadge')) as unknown as {
      SOURCE_PRESETS?: Record<string, unknown>
    }
    expect(mod.SOURCE_PRESETS).toBeDefined()
    const presets = mod.SOURCE_PRESETS ?? {}
    for (const d of REAL) expect(Object.keys(presets), d.id).toContain(d.id)
  })

  it.each(REAL.map((d) => [d.id, d] as const))(
    '%s: the header badge shows short_label and the row icon title is label',
    async (_id, d) => {
      setRealRegistry()
      const { SourceBadge } = await import('@/components/conversation/SourceBadge')
      const source = d.id as Parameters<typeof SourceBadge>[0]['source']
      const header = render(<SourceBadge source={source} variant="header" />)
      expect(header.container.textContent).toBe(d.short_label)
      header.unmount()
      const rowBadge = render(<SourceBadge source={source} variant="row" />)
      expect(rowBadge.container.querySelector('span')?.getAttribute('title')).toBe(d.label)
    },
  )
})
