/**
 * EntrypointChip — the launcher of a session (issue #93, UX.md "Tickets").
 * The text is the label; the title is the raw value. Nothing without a value.
 */
import { Badge } from '@/components/ui/badge'
import { entrypointLabel } from './entrypoint'

export function EntrypointChip({ entrypoint, className }: { entrypoint?: string | null; className?: string }) {
  if (!entrypoint) return null
  return (
    <Badge variant="outline" className={className} data-testid="entrypoint-chip" title={entrypoint}>
      {entrypointLabel(entrypoint)}
    </Badge>
  )
}
