/**
 * N8-ui: the Codex entry in SOURCE_PRESETS (plan §4.1, "The presentation
 * map"; the filter label is "Codex").
 *
 * With the fake Codex descriptor the badge reads the registry labels. Without
 * it (a page that is newer or older than the backend), the preset's own
 * fallback labels must still say "Codex", never "Desktop".
 */
import { createElement, type ComponentType } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import {
  CODEX,
  clearLocalStorage,
  importModule,
  setCodexRegistry,
  setRegistryWithoutCodex,
} from './codexHelpers'

interface Preset {
  Icon: unknown
  label: string
  title: string
  groupBadge: boolean
  headerClasses: string
  rowIconClass: string
}

type BadgeModule = {
  SOURCE_PRESETS: Record<string, Preset | undefined>
  SourceBadge: ComponentType<{ source: string; variant: 'header' | 'row' }>
}

const loadBadge = () => importModule<BadgeModule>('@/components/conversation/SourceBadge')

beforeEach(clearLocalStorage)

describe('SOURCE_PRESETS has a Codex entry', () => {
  it('SOURCE_PRESETS.CODEX exists, with "Codex" labels and no group badge', async () => {
    setCodexRegistry()
    const { SOURCE_PRESETS } = await loadBadge()
    const preset = SOURCE_PRESETS.CODEX
    expect(preset).toBeDefined()
    expect(preset?.label).toBe('Codex')
    expect(preset?.title).toBe('Codex')
    expect(preset?.groupBadge).toBe(false)
  })

  it('the Codex icon and row color differ from every other preset', async () => {
    setCodexRegistry()
    const { SOURCE_PRESETS } = await loadBadge()
    const codex = SOURCE_PRESETS.CODEX
    expect(codex).toBeDefined()
    for (const [id, other] of Object.entries(SOURCE_PRESETS)) {
      if (id === 'CODEX' || !other) continue
      expect(codex?.Icon, `icon same as ${id}`).not.toBe(other.Icon)
      expect(codex?.rowIconClass, `row color same as ${id}`).not.toBe(other.rowIconClass)
    }
  })
})

describe('SourceBadge for a Codex conversation', () => {
  it('with the Codex descriptor: the header badge says short_label and the row title is label', async () => {
    setCodexRegistry()
    const { SourceBadge, SOURCE_PRESETS } = await loadBadge()
    expect(SOURCE_PRESETS.CODEX).toBeDefined()
    const header = render(createElement(SourceBadge, { source: 'CODEX', variant: 'header' }))
    expect(header.container.textContent).toBe(CODEX.short_label)
    header.unmount()
    const row = render(createElement(SourceBadge, { source: 'CODEX', variant: 'row' }))
    expect(row.container.querySelector('span')?.getAttribute('title')).toBe(CODEX.label)
  })

  it('without the Codex descriptor: the badge falls back to "Codex", not "Desktop"', async () => {
    setRegistryWithoutCodex()
    const { SourceBadge } = await loadBadge()
    const header = render(createElement(SourceBadge, { source: 'CODEX', variant: 'header' }))
    expect(header.container.textContent).toBe('Codex')
    header.unmount()
    const row = render(createElement(SourceBadge, { source: 'CODEX', variant: 'row' }))
    expect(row.container.querySelector('span')?.getAttribute('title')).toBe('Codex')
  })
})
