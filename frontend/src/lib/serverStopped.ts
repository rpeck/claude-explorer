/**
 * App-wide "the server has been stopped" flag (UX.md → "Quit (power button)").
 *
 * The power button lives deep in the sidebar, but stopping must replace the
 * whole app: unmounting everything is what stops the connection poller and
 * every query from firing at a server that is gone. A tiny external store
 * lets the App root read the flag without a provider.
 *
 * The flag is shared with the other open tabs through a BroadcastChannel.
 * Without it, a second tab saw only a server that went away, and showed the
 * "Connecting to Backend" / "Connection Failed" dialog instead of the
 * stopped screen.
 */

import { useSyncExternalStore } from 'react'

const CHANNEL_NAME = 'claude-explorer-server'
const STOPPED_MESSAGE = { type: 'server-stopped' } as const

let stopped = false
const listeners = new Set<() => void>()
let channel: BroadcastChannel | null = null

function setStopped(): void {
  if (stopped) return
  stopped = true
  for (const listener of listeners) listener()
}

function isStoppedMessage(data: unknown): boolean {
  return (
    typeof data === 'object' &&
    data !== null &&
    (data as { type?: unknown }).type === STOPPED_MESSAGE.type
  )
}

/** The cross-tab channel, opened on first use. Null where unsupported. */
function getChannel(): BroadcastChannel | null {
  if (channel) return channel
  if (typeof BroadcastChannel === 'undefined') return null
  try {
    channel = new BroadcastChannel(CHANNEL_NAME)
  } catch {
    return null
  }
  channel.onmessage = (event: MessageEvent) => {
    if (isStoppedMessage(event.data)) setStopped()
  }
  return channel
}

/** Mark the server stopped here, and tell every other open tab. */
export function markServerStopped(): void {
  if (stopped) return
  setStopped()
  try {
    getChannel()?.postMessage(STOPPED_MESSAGE)
  } catch {
    // A closed or unsupported channel only costs the other tabs the
    // friendly screen; this tab is already stopped.
  }
}

export function isServerStopped(): boolean {
  return stopped
}

function subscribe(listener: () => void): () => void {
  // Listen for other tabs from the moment the app root subscribes.
  getChannel()
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useServerStopped(): boolean {
  return useSyncExternalStore(subscribe, isServerStopped)
}
