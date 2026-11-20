/**
 * B52 (issue #52), part C: `modelDisplayName` and `replyLabel` in
 * `frontend/src/lib/modelName.ts`. The same rules as the Python
 * `backend/model_names.py`, so that one model has one name everywhere.
 */
import { describe, expect, it } from 'vitest'
import { modelDisplayName, replyLabel } from '@/lib/modelName'

const NAMES: Array<[string, string]> = [
  ['claude-opus-5-5', 'Opus 5.5'],
  ['claude-fable-5-1', 'Fable 5.1'],
  ['claude-sonnet-5', 'Sonnet 5'],
  ['claude-haiku-4-5-20251001', 'Haiku 4.5'],
  ['claude-opus-4-5-20251101', 'Opus 4.5'],
  ['claude-opus-4-7', 'Opus 4.7'],
  ['claude-opus-4-20250514', 'Opus 4'],
  ['claude-sonnet-4-6', 'Sonnet 4.6'],
  ['claude-sonnet-4-5-20250929', 'Sonnet 4.5'],
  // The older order: the version comes before the family.
  ['claude-3-5-sonnet-20241022', 'Sonnet 3.5'],
  ['claude-3-7-sonnet-20250219', 'Sonnet 3.7'],
  ['claude-3-opus-20240229', 'Opus 3'],
  ['claude-3-5-haiku-20241022', 'Haiku 3.5'],
  // Claude Code's own rows.
  ['<synthetic>', 'Claude Code'],
  // An id that does not match shows as it is.
  ['gpt-5.6-codex', 'gpt-5.6-codex'],
  ['some-unknown-model', 'some-unknown-model'],
]

describe('modelDisplayName', () => {
  for (const [id, name] of NAMES) {
    it(`${id} gives "${name}"`, () => {
      expect(modelDisplayName(id)).toBe(name)
    })
  }
})

const LABELS: Array<[string, string | null | undefined, string]> = [
  ['Claude', 'claude-opus-5-5', 'Claude Opus 5.5'],
  ['Claude', 'claude-fable-5-1', 'Claude Fable 5.1'],
  ['Claude', '<synthetic>', 'Claude Code'],
  ['Claude', null, 'Claude'],
  ['Claude', undefined, 'Claude'],
  ['Claude', '', 'Claude'],
  ['Codex', 'gpt-5.6-codex', 'Codex gpt-5.6-codex'],
  ['Codex', null, 'Codex'],
]

describe('replyLabel', () => {
  for (const [assistantLabel, model, label] of LABELS) {
    it(`(${assistantLabel}, ${String(model)}) gives "${label}"`, () => {
      expect(replyLabel(assistantLabel, model)).toBe(label)
    })
  }
})
