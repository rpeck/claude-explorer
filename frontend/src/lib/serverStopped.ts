/**
 * App-wide "the server has been stopped" flag (UX.md → "Quit (power button)").
 *
 * The power button lives deep in the sidebar, but stopping must replace the
 * whole app: unmounting everything is what stops the connection poller and
 * every query from firing at a server that is gone. A tiny external store
 * lets the App root read the flag without a provider.
 */

import { useSyncExternalStore } from 'react'

let stopped = false
const listeners = new Set<() => void>()

export function markServerStopped(): void {
  if (stopped) return
  stopped = true
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useServerStopped(): boolean {
  return useSyncExternalStore(subscribe, () => stopped)
}
