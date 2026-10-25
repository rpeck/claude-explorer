import { useEffect, useState } from 'react'

/**
 * The current time in ms, updated every `intervalMs` while `enabled`.
 *
 * The recent-sessions filter of the Conversation List uses it, so that a
 * row that becomes too old leaves the list without a reload. When the
 * filter turns on, the time updates at once (a timeout of 0), not only at
 * the next interval: a value from before can be hours old.
 */
export function useNow(enabled: boolean, intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) return
    const tick = () => setNow(Date.now())
    const first = window.setTimeout(tick, 0)
    const id = window.setInterval(tick, intervalMs)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(id)
    }
  }, [enabled, intervalMs])
  return now
}
