/**
 * SourceBadge — single render site for the conversation-source indicator.
 *
 * Collapses the three-way ternary that previously lived in both
 * `ConversationPage.tsx` (under the conversation title) and
 * `ConversationList.tsx` (in the sidebar list row footer). Two variants
 * cover the existing surfaces:
 *
 *   - `header`: full <Badge> with icon + label text ("Code" / "Cowork" /
 *     "Desktop"). Used at ConversationPage.tsx:1125-1140.
 *   - `row`:    bare <span title="..."><Icon /></span>. Used at
 *     ConversationList.tsx:813-820. The sidebar filter dropdown
 *     (Sidebar.tsx) uses a parallel <SelectItem> with a different a11y
 *     shape; it renders `SourceIcon` next to the registry label.
 *
 * The source → icon + color map lives ONCE in `SOURCE_PRESETS` below.
 * The source's rules come from the registry (`@/lib/sources`); a new
 * source needs a preset here and nothing else in the frontend.
 *
 * Extracted 2026-05-30 per PLANS/2026.05.30-STRICT-CODE-QUALITY-REVIEW.md
 * P1.2 (rubric F — canonical layer & reuse).
 */
import { Terminal, Sparkles, MessageSquare, type LucideIcon } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type { ConversationSource } from '@/lib/types'
import { sourceDescriptor } from '@/lib/sources'

interface SourcePreset {
  Icon: LucideIcon
  // The registry's short_label and label win; these are the fallback for
  // a source that the registry does not know.
  label: string         // header-variant text
  title: string         // row-variant title attribute (hover tooltip)
  // A list group of only this source shows the source badge instead of
  // the folder icon (the workspace folders of Desktop chats).
  groupBadge: boolean
  // Tailwind class fragments split per variant. We keep header's
  // bg/text/dark- triple separate from row's flat text-* color because
  // the visual languages diverge.
  headerClasses: string
  rowIconClass: string
}

// "Desktop" (CLAUDE_AI) doubles as the fallback for unknown sources —
// the runtime should never hit an unknown source under normal operation,
// but a backend that adds a new enum value before the frontend ships an
// update should degrade gracefully rather than render nothing.
const DESKTOP_PRESET: SourcePreset = {
  Icon: MessageSquare,
  label: 'Desktop',
  title: 'Claude Desktop',
  headerClasses:
    'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300',
  rowIconClass: 'text-blue-500',
  groupBadge: true,
}

/* eslint-disable react-refresh/only-export-components -- safe: a constant map read by ConversationList and tests; HMR falls back to a full reload. */
// react-doctor-disable-next-line react-doctor/only-export-components -- safe: see the eslint comment above.
export const SOURCE_PRESETS: Record<ConversationSource, SourcePreset> = {
  CLAUDE_CODE: {
    Icon: Terminal,
    label: 'Code',
    title: 'Claude Code',
    headerClasses:
      'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300',
    rowIconClass: 'text-green-500',
    groupBadge: false,
  },
  CLAUDE_COWORK: {
    Icon: Sparkles,
    label: 'Cowork',
    title: 'Claude Cowork',
    headerClasses:
      'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300',
    rowIconClass: 'text-purple-500',
    groupBadge: false,
  },
  CLAUDE_AI: DESKTOP_PRESET,
}
/* eslint-enable react-refresh/only-export-components */

interface SourceBadgeProps {
  source: ConversationSource
  variant: 'header' | 'row'
}

function presetOf(source: ConversationSource): SourcePreset {
  return SOURCE_PRESETS[source] ?? DESKTOP_PRESET
}

/** The bare colored icon of a source (the sidebar source filter). */
export function SourceIcon({ source }: { source: ConversationSource }) {
  const { Icon, rowIconClass } = presetOf(source)
  return <Icon className={`h-3 w-3 ${rowIconClass}`} />
}

export function SourceBadge({ source, variant }: SourceBadgeProps) {
  const preset = presetOf(source)
  const descriptor = sourceDescriptor(source)
  const { Icon } = preset

  if (variant === 'header') {
    return (
      <Badge
        variant="secondary"
        className={`flex items-center gap-1 ${preset.headerClasses}`}
      >
        <Icon className="h-3 w-3" />
        {descriptor?.short_label ?? preset.label}
      </Badge>
    )
  }

  // row variant
  return (
    <span title={descriptor?.label ?? preset.title}>
      <SourceIcon source={source} />
    </span>
  )
}
