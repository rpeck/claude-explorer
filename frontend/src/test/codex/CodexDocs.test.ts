/**
 * N8-ui: UX.md describes the Codex header and the consent dialog (AGENTS.md,
 * "UX Rules": a UI change keeps UX.md accurate; plan §7 "Docs").
 * The checks look only for names, not for the wording.
 */
import { describe, expect, it } from 'vitest'
import ux from '../../../../UX.md?raw'

describe('UX.md covers N8-ui', () => {
  it('the cloud-sync section no longer says that the dialog is a later node', () => {
    expect(ux).not.toMatch(/next Codex node/)
    expect(ux).not.toMatch(/Until the dialog exists/)
  })

  it('the Codex header rows are documented', () => {
    for (const name of ['Thread ID', 'Codex name', 'Codex home']) expect(ux, name).toContain(name)
  })

  it('the consent dialog and its typed provider name step are documented', () => {
    expect(ux).toMatch(/consent dialog/i)
    expect(ux).toMatch(/type[sd]? the (provider|provider's) name|typed provider name/i)
  })

  it('the cloud-sync banner is documented', () => {
    expect(ux).toMatch(/cloud-sync (gate )?banner|CloudSyncGateBanner/i)
  })
})
