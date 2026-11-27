/**
 * The state of a session (issue #91, UX.md "Session state"): Running, Open,
 * Ended, or Ended (no exit record). Desktop conversations have no state.
 */
import type { SessionState } from '@/lib/types'

const STATE_LABELS: Record<SessionState, string> = {
  running: 'Running',
  open: 'Open',
  ended: 'Ended',
  ended_no_exit: 'Ended (no exit record)',
}

const TONE: Record<SessionState, string> = {
  running: 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300',
  open: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',
  ended: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
  ended_no_exit: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
}

export function SessionStateChip({ state }: { state?: SessionState | null }) {
  if (!state) return null
  return (
    <span
      data-testid="session-state-chip"
      data-state={state}
      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium leading-none ${TONE[state]}`}
    >
      {STATE_LABELS[state]}
    </span>
  )
}
