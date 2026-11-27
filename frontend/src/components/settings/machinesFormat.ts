/**
 * Issue #92: the text forms of the Machines settings (UX.md → "Machines").
 */
import type { AllowRule, ExportMode, MachineSource } from '@/lib/types'

export const SOURCES: MachineSource[] = ['claude-code', 'cowork', 'codex']

export const MODES: { value: ExportMode; label: string }[] = [
  { value: 'off', label: 'Off (nothing leaves this machine)' },
  { value: 'export', label: 'Export' },
  { value: 'receive-only', label: 'Receive only' },
]

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = n / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(1)} ${units[unit]}`
}

/** One allow rule per line: "<source> <project glob>", "<source>", or "<glob>"; "*" = any source. */
export function parseAllow(text: string): AllowRule[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [first, ...rest] = line.split(/\s+/)
      if (first === '*' || SOURCES.includes(first as MachineSource)) {
        return { source: first === '*' ? null : (first as MachineSource), project: rest.join(' ') || null }
      }
      return { source: null, project: line }
    })
}

export function formatAllow(rules: AllowRule[]): string {
  return rules.map((r) => `${r.source ?? '*'}${r.project ? ` ${r.project}` : ''}`).join('\n')
}
