// The source registry. `sources.generated.json` comes from the backend
// registry (`scripts/generate_sources_json.py`); `scripts/check.py` fails
// when it is stale. Source rules read these flags, not source ids.
import generated from './sources.generated.json'
import type { ConversationSource, SourceFilter } from './types'

export interface SourceDescriptor {
  id: ConversationSource
  label: string
  short_label: string
  /** No branch tree; messages are in file order. */
  chronological: boolean
  /** Project grouping and the project and path filters apply. */
  has_projects: boolean
  /** The Archived toggle applies. */
  has_archive_state: boolean
  /** The workspace filter applies. */
  has_organizations: boolean
  /** The Re-download button applies. */
  refetchable: boolean
  messages_change_in_place: boolean
  /** The sender name of an assistant message, for example "Codex".
   *  Readers fall back to "Claude" for a source with no descriptor. */
  assistant_label: string
}

export type SourceFlag = {
  [K in keyof SourceDescriptor]-?: SourceDescriptor[K] extends boolean ? K : never
}[keyof SourceDescriptor]

/** Every source, in registry order. */
export const SOURCES = generated as readonly SourceDescriptor[]

export function sourceDescriptor(source: string | undefined): SourceDescriptor | undefined {
  return SOURCES.find((s) => s.id === source)
}

/**
 * True when `flag` holds for `source`. Under "all" (or no filter) it is
 * true when any source has the flag. An unknown source has no flags.
 */
export function sourceHas(source: SourceFilter | undefined, flag: SourceFlag): boolean {
  if (source === undefined || source === 'all') return SOURCES.some((s) => s[flag])
  return sourceDescriptor(source)?.[flag] ?? false
}
