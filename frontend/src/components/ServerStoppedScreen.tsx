import { useEffect, useRef } from 'react'
import { Power } from 'lucide-react'

/**
 * Replaces the whole app after the server was stopped (power button, here or
 * in another tab). Focus moves to the heading and the instruction sits in a
 * status region, so a screen reader announces the change.
 */
export function ServerStoppedScreen() {
  const headingRef = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    headingRef.current?.focus()
  }, [])

  return (
    <main className="flex h-screen flex-col items-center justify-center gap-3 bg-white px-4 text-center dark:bg-zinc-950">
      <Power className="h-8 w-8 text-zinc-400" aria-hidden="true" />
      <h1
        ref={headingRef}
        tabIndex={-1}
        className="text-xl font-semibold text-zinc-900 outline-none dark:text-zinc-100"
      >
        Claude Explorer has stopped
      </h1>
      <p role="status" aria-live="polite" className="text-sm text-zinc-600 dark:text-zinc-400">
        You can close this tab.
      </p>
      <p className="max-w-sm text-xs text-zinc-500">
        To use it again, open Claude Explorer, or run{' '}
        <code className="whitespace-nowrap rounded bg-zinc-100 px-1 dark:bg-zinc-800">claude-explorer serve</code>.
      </p>
    </main>
  )
}
