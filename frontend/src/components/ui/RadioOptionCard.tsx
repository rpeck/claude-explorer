/**
 * RadioOptionCard — bordered, click-anywhere-on-the-card radio option.
 *
 * Collapses the ~10x-repeated "<label class='border... cursor-pointer'>
 * <RadioGroupItem .../><...content...></label>" pattern that lived in
 * SettingsPage (Theme x3, Keyboard Mode x2, Export Mode x3) and
 * MarkdownExportDialog (Export Mode x3). Each prior site carried its own
 * `oxlint-disable-next-line react-doctor/label-has-associated-control`
 * rationale comment — encapsulating the suppression once means a future
 * a11y refactor (e.g., swapping Radix for a custom radio) touches one
 * file instead of ten.
 *
 * Phase 1 a11y rationale (preserved verbatim from the call sites):
 * Radix RadioGroupItem renders as <button role="radio">, which IS
 * labelable via DOM containment. Oxlint can't see through the Radix
 * abstraction and flags the wrapping <label>; the pattern is fully
 * accessible (click-on-label-activates-control works via React's
 * synthetic event bubbling).
 *
 * Two layouts:
 *   - `inline`:  icon + single label on one row (Theme cards).
 *   - `stacked`: title row + description sub-row (Keyboard cards, all
 *     Export Mode cards).
 *
 * `active` controls the border swap (active = solid zinc-900/100, with
 * matching subtle bg; inactive = thin zinc-200/700 with a hover-bg
 * affordance). Pass `false` everywhere if the surrounding RadioGroup
 * delegates selection visuals to Radix's `data-state="checked"` instead.
 *
 * Extracted 2026-05-30 per PLANS/2026.05.30-STRICT-CODE-QUALITY-REVIEW.md
 * P1.3 (rubric F — canonical layer & reuse).
 */
import type { ReactNode } from 'react'
import { RadioGroupItem } from '@/components/ui/radio-group'

interface RadioOptionCardProps {
  value: string
  /** Unique DOM id for the RadioGroupItem (defaults to value if omitted). */
  id?: string
  title: ReactNode
  // ReactNode (not string) so descriptions can carry inline <code> or
  // other formatting — Export Mode descriptions in SettingsPage include
  // example file-path snippets, e.g.
  // ![alt](images/x.png) and ![[images/x.png]].
  description?: ReactNode
  icon?: ReactNode
  active: boolean
  layout: 'inline' | 'stacked'
  /** data-testid on the card. */
  testId?: string
}

const ACTIVE_BORDER =
  'border-zinc-900 bg-zinc-50 dark:border-zinc-100 dark:bg-zinc-900'
const INACTIVE_BORDER =
  'border-zinc-200 hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800'

export function RadioOptionCard({
  value,
  id,
  title,
  description,
  icon,
  active,
  layout,
  testId,
}: RadioOptionCardProps) {
  const borderClass = active ? ACTIVE_BORDER : INACTIVE_BORDER
  const itemId = id ?? value

  if (layout === 'inline') {
    return (
      // oxlint-disable-next-line react-doctor/label-has-associated-control -- Radix RadioGroupItem renders as <button role="radio">; oxlint can't see through the abstraction. Click-on-label works via DOM containment + React event bubbling.
      <label
        className={`flex cursor-pointer items-center gap-2 rounded-lg border p-3 transition-colors ${borderClass}`}
        data-testid={testId}
      >
        <RadioGroupItem value={value} id={itemId} />
        {icon}
        <span className="text-sm text-zinc-900 dark:text-zinc-100">{title}</span>
      </label>
    )
  }

  // stacked layout
  return (
    // oxlint-disable-next-line react-doctor/label-has-associated-control -- See inline-variant rationale above.
    <label
      className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${borderClass}`}
      data-testid={testId}
    >
      <RadioGroupItem value={value} id={itemId} className="mt-0.5" />
      <div className="flex flex-col">
        <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
          {title}
        </span>
        {description && (
          <span className="text-xs text-zinc-500 dark:text-zinc-400">
            {description}
          </span>
        )}
      </div>
    </label>
  )
}
