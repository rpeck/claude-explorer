/**
 * Issue #92: the machine that a session came from. UX.md → "Machines".
 * Shown only when the archive holds two or more machines.
 */
import { Laptop } from 'lucide-react'
import { useMachines } from '@/components/settings/machinesQuery'

interface MachineChipProps {
  machineId?: string | null
  label?: string | null
  labels?: string[]
}

export function MachineChip({ machineId, label: shown, labels }: MachineChipProps) {
  const { data } = useMachines()
  const label = shown || (machineId ? `Machine ${machineId.slice(0, 8)}` : null)
  if (!label || (data?.machines.length ?? 0) < 2) return null
  const all = labels && labels.length > 1 ? labels.join(', ') : label
  return (
    <span
      data-testid="machine-chip"
      className="inline-flex min-w-0 shrink items-center gap-1 text-xs text-zinc-500 dark:text-zinc-400"
      title={`Machine: ${all}`}
    >
      <Laptop className="h-3 w-3 shrink-0" aria-hidden="true" />
      <span className="truncate">{label}</span>
    </span>
  )
}
