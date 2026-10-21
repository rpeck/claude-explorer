import { useState } from 'react'
import { Power } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { useFetchPipeline } from '@/contexts/FetchPipelineContext'
import { api } from '@/lib/api'
import { queryClient } from '@/lib/queryClient'
import { markServerStopped } from '@/lib/serverStopped'
import { ApiError } from '@/lib/types'

function reasonFor(error: unknown): string {
  if (error instanceof ApiError) {
    try {
      const detail = JSON.parse(error.message)?.detail
      if (typeof detail === 'string' && detail) return detail
    } catch {
      // not JSON; fall through to the raw text
    }
    return error.message || `HTTP ${error.status}`
  }
  return error instanceof Error ? error.message : String(error)
}

/**
 * Sidebar power button: stops the server after a confirmation.
 * Contract: UX.md → "Quit (power button)".
 */
export function QuitButton() {
  const [open, setOpen] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { isRunning: refreshRunning, closeStream } = useFetchPipeline()

  const onOpenChange = (next: boolean) => {
    if (stopping) return
    setOpen(next)
    if (!next) setError(null)
  }

  const quit = async () => {
    setStopping(true)
    setError(null)
    try {
      await api.shutdownServer()
    } catch (e) {
      setError(reasonFor(e))
      setStopping(false)
      return
    }
    // Close a running Refresh stream first: the server's graceful
    // shutdown waits for open connections.
    closeStream()
    void queryClient.cancelQueries()
    markServerStopped()
  }

  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8"
        onClick={() => setOpen(true)}
        title="Quit Claude Explorer"
        aria-label="Quit Claude Explorer"
      >
        <Power className="h-4 w-4" />
      </Button>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Quit Claude Explorer?</DialogTitle>
            <DialogDescription>
              This stops the Claude Explorer server. Open Claude Explorer again to restart it.
            </DialogDescription>
          </DialogHeader>
          {refreshRunning && (
            <p className="text-sm text-amber-700 dark:text-amber-400">
              A Refresh is running. Quitting interrupts it; the next Refresh picks up where it stopped.
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              Could not stop the server. {error}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={stopping}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={quit} disabled={stopping}>
              {stopping ? 'Quitting…' : 'Quit'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
