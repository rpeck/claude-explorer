import { Power } from 'lucide-react'

/** Replaces the whole app after the power button stopped the server. */
export function ServerStoppedScreen() {
  return (
    <main className="flex h-screen flex-col items-center justify-center gap-3 bg-white px-4 text-center dark:bg-zinc-950">
      <Power className="h-8 w-8 text-zinc-400" aria-hidden="true" />
      <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
        Claude Explorer has stopped
      </h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">You can close this tab.</p>
      <p className="max-w-sm text-xs text-zinc-500">
        To use it again, open Claude Explorer, or run{' '}
        <code className="whitespace-nowrap rounded bg-zinc-100 px-1 dark:bg-zinc-800">claude-explorer serve</code>.
      </p>
    </main>
  )
}
