import { useState, useEffect, useCallback, useRef, useEffectEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { WifiOff, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { queryKeys } from '@/lib/queryClient'

type ConnectionState = 'connected' | 'connecting' | 'disconnected'

export function ConnectionStatus() {
  const [state, setState] = useState<ConnectionState>('connecting')
  const [retryCount, setRetryCount] = useState(0)
  const [showDialog, setShowDialog] = useState(false)
  const [lastError, setLastError] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const maxRetries = 5
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Check connection by fetching config
  const checkConnection = useCallback(async (): Promise<boolean> => {
    try {
      const response = await fetch('/api/config', {
        method: 'GET',
        signal: AbortSignal.timeout(5000)
      })
      if (response.ok) {
        setState('connected')
        setRetryCount(0)
        setLastError(null)
        setShowDialog(false)
        return true
      }
      throw new Error(`Server returned ${response.status}`)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error'
      setLastError(message)
      return false
    }
  }, [])

  // Retry with exponential backoff
  const attemptConnection = useCallback(async (attempt: number) => {
    setState('connecting')
    // Only show retry count after first attempt has failed
    // (retryCount stays 0 during initial check)

    const connected = await checkConnection()
    if (connected) {
      setRetryCount(0)
      return
    }

    // Update retry count AFTER failure (so dialog shows only on retry)
    setRetryCount(attempt)

    if (attempt >= maxRetries) {
      setState('disconnected')
      setShowDialog(true)
      return
    }

    // Schedule next retry with exponential backoff. First retry waits
    // 4s (was 2s) so a healthy backend's `--reload` cold-start (5-10s)
    // has time to come up before the user sees a connection dialog.
    // Subsequent retries: 8s, 10s (cap), 10s, 10s — total 42s.
    const delay = Math.min(2000 * Math.pow(2, attempt), 10000)
    retryTimeoutRef.current = setTimeout(() => {
      // eslint-disable-next-line react-hooks/immutability -- Canonical recursive-retry idiom. The closure captures `attemptConnection` lexically; reference resolves at setTimeout fire time, not at useCallback definition time. Stale-closure risk is bounded: this useCallback depends only on `checkConnection` (which has [] deps), so attemptConnection's identity is stable across re-renders. The Phase-2 React 19 migration target is to refactor with useEffectEvent (already imported below for onQueryError) but doing it today risks regressing the retry timing pinned by the postmortem.
      attemptConnection(attempt + 1)
    }, delay)
  }, [checkConnection])

  // A DOM signal of the retry state, which the closed dialog does not show.
  // e2e tests wait for it (e.g. "the first attempt failed") instead of a
  // fixed delay (connection-status.spec.ts, 2026-10-24).
  useEffect(() => {
    const root = document.documentElement.dataset
    root.connectionState = state
    root.connectionRetries = String(retryCount)
    return () => {
      delete root.connectionState
      delete root.connectionRetries
    }
  }, [state, retryCount])

  // Initial connection check
  // oxlint-disable-next-line react-doctor/exhaustive-deps -- The cleanup intentionally reads `retryTimeoutRef.current` at unmount time (not at effect-run time). Capturing the ref value at effect-run would always be null (this effect runs on mount before any timer is scheduled). What we want on unmount is "clear whichever timer is currently pending," which is exactly the live ref read.
  useEffect(() => {
    attemptConnection(1)

    return () => {
      if (retryTimeoutRef.current) {
        clearTimeout(retryTimeoutRef.current)
      }
    }
  }, [attemptConnection])

  // Monitor query errors for connection issues.
  //
  // Phase 2 perf (React Doctor prefer-use-effect-event): previously
  // re-subscribed to the entire query cache on every `state` change
  // (3 transitions per disconnection cycle: connected -> connecting ->
  // disconnected -> connected). useEffectEvent lets the subscription
  // mount once and read the latest `state` + `attemptConnection`
  // without re-binding.
  const onQueryError = useEffectEvent((errorMessage: string) => {
    const isConnectionError =
      errorMessage.includes('fetch') ||
      errorMessage.includes('network') ||
      errorMessage.includes('ECONNREFUSED') ||
      errorMessage.includes('Failed to fetch')
    if (isConnectionError && state === 'connected') {
      attemptConnection(1)
    }
  })
  useEffect(() => {
    const unsubscribe = queryClient.getQueryCache().subscribe((event) => {
      if (event?.type === 'updated' && event.query.state.status === 'error') {
        const error = event.query.state.error
        const errorMessage = error instanceof Error ? error.message : String(error)
        onQueryError(errorMessage)
      }
    })
    return () => unsubscribe()
  }, [queryClient])

  const handleReconnect = async () => {
    setShowDialog(false)
    attemptConnection(1)
  }

  const handleDismiss = () => {
    setShowDialog(false)
  }

  const handleRetryNow = async () => {
    // Clear any pending retry
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current)
    }

    const connected = await checkConnection()
    if (connected) {
      // Refetch all queries
      await queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
    } else {
      // Continue retry loop from current count
      attemptConnection(retryCount + 1)
    }
  }

  // Show dialog only after the SECOND retry attempt (or terminal failure).
  // A one-off transient (Wi-Fi blip, single dropped request) shouldn't
  // flash the dialog — wait until we're truly struggling.
  const isDialogOpen = showDialog || (state === 'connecting' && retryCount >= 2)

  return (
    <Dialog open={isDialogOpen} onOpenChange={(open) => {
      if (!open && state === 'disconnected') {
        setShowDialog(false)
      }
    }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {state === 'connecting' ? (
              <>
                <RefreshCw className="h-5 w-5 animate-spin text-amber-500" />
                Connecting to Backend
              </>
            ) : (
              <>
                <WifiOff className="h-5 w-5 text-red-500" />
                Connection Failed
              </>
            )}
          </DialogTitle>
          <DialogDescription asChild>
            <div className="space-y-3">
              {state === 'connecting' ? (
                <>
                  <p>
                    Attempting to connect to the backend server...
                  </p>
                  <p className="text-sm font-medium">
                    Attempt {retryCount} of {maxRetries}
                  </p>
                </>
              ) : (
                <>
                  <p>
                    Unable to connect to the backend server after {maxRetries} attempts.
                  </p>
                  <p>Make sure the server is running:</p>
                  <pre className="rounded bg-zinc-100 p-2 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                    claude-explorer serve
                  </pre>
                </>
              )}
              {/* Only surface lastError when we've stopped trying. While
                  state === 'connecting', the spinner already conveys
                  "we're attempting" — the red "Last error: …" line on
                  top of an active retry contradicts the spinner and
                  reads as failure when it's actually just retrying. */}
              {lastError && state !== 'connecting' && (
                <p className="text-xs text-red-500">
                  Last error: {lastError}
                </p>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>
        <div className="flex justify-end gap-2 mt-4">
          {state === 'connecting' ? (
            <Button onClick={handleRetryNow}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Retry Now
            </Button>
          ) : (
            <>
              <Button variant="outline" onClick={handleDismiss}>
                Dismiss
              </Button>
              <Button onClick={handleReconnect}>
                <RefreshCw className="h-4 w-4 mr-2" />
                Try Again
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}