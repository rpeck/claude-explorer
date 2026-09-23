import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import { invalidateAfterFetch } from '@/lib/queryClient'
import { api } from '@/lib/api'
import { errorToast } from '@/lib/errorToast'

// Bug B/C: SSE error events now carry a `kind` field (AUTH | TRANSIENT |
// TERMINAL) and a `retryable` boolean. The frontend uses these to decide
// whether to show a Retry button vs. a sticky Details toast.
type ErrorKind = 'AUTH' | 'TRANSIENT' | 'TERMINAL'

export interface FetchProgress {
  type:
    | 'start'
    | 'progress'
    | 'complete'
    | 'error'
    // Build-9: capture phase events for the combined pipeline.
    | 'capture_start'
    | 'capture_waiting_login'
    | 'capture_done'
  message: string
  current?: number
  total?: number
  conversation_name?: string
  // Bug B: present on `type:"error"` events.
  kind?: ErrorKind
  retryable?: boolean
}

// Build-9 Bug 2: maximum visible chars for the per-conversation name suffix
// in the toast. Sonner's body text wraps but a long name pushes the action
// button off screen on a typical 1280px viewport; 40 chars is the sweet spot.
const TOAST_NAME_MAX_CHARS = 40

function truncateName(name: string, max: number = TOAST_NAME_MAX_CHARS): string {
  if (name.length <= max) return name
  // Reserve one char for the ellipsis so total length stays at `max`.
  return name.slice(0, Math.max(0, max - 1)) + '…'
}

/**
 * Build the loading-toast text for a `start`/`progress` SSE event.
 *
 * Shape: "Fetching N/M: <truncated conversation_name>" when both are present;
 * "Fetching N/M…" when only counts are available; the raw message string
 * (or "Fetching…") otherwise. Exported for unit testing in isolation.
 */
export function formatProgressText(data: FetchProgress): string {
  const total = data.total ?? 0
  const current = data.current ?? 0
  const name = data.conversation_name?.trim()
  if (total > 0 && name) {
    return `Fetching ${current}/${total}: ${truncateName(name)}`
  }
  if (total > 0) {
    return `Fetching ${current}/${total}…`
  }
  return data.message || 'Fetching…'
}

interface UseFetchToastOptions {
  onOpenDetails: () => void
}

export function useFetchToast({ onOpenDetails }: UseFetchToastOptions) {
  const queryClient = useQueryClient()
  const sourceRef = useRef<EventSource | null>(null)
  // Forward-reference to startFetch so the toast's Retry action can call it.
  const startRef = useRef<(incremental?: boolean) => void>(() => {})

  const startFetch = useCallback(
    (incremental: boolean = true) => {
      if (sourceRef.current) {
        sourceRef.current.close()
        sourceRef.current = null
      }

      const toastId = toast.loading('Fetching…', {
        duration: Infinity,
        action: {
          label: 'Details',
          onClick: () => onOpenDetails(),
        },
      })

      const eventSource = api.startFetch(incremental)
      sourceRef.current = eventSource

      let latestProgress: FetchProgress | null = null

      eventSource.onmessage = (event) => {
        let data: FetchProgress
        try {
          data = JSON.parse(event.data)
        } catch {
          return
        }
        latestProgress = data

        if (data.type === 'complete') {
          toast.success(data.message || 'Fetch complete.', {
            id: toastId,
            duration: 5000,
          })
          void invalidateAfterFetch(queryClient)
          eventSource.close()
          sourceRef.current = null
        } else if (data.type === 'error') {
          // Bug C: classify by SSE kind. TRANSIENT gets a Retry button +
          // 8s minimum; AUTH/TERMINAL/unknown stays sticky.
          const isTransient =
            data.kind === 'TRANSIENT' || data.retryable === true
          errorToast(data.message || 'Fetch failed.', {
            id: toastId,
            sticky: !isTransient,
            retry: isTransient ? () => startRef.current(incremental) : undefined,
            details: isTransient ? undefined : () => onOpenDetails(),
          })
          eventSource.close()
          sourceRef.current = null
        } else if (data.type === 'progress' || data.type === 'start') {
          const text = formatProgressText(data)
          toast.loading(text, {
            id: toastId,
            duration: Infinity,
            action: {
              label: 'Details',
              onClick: () => onOpenDetails(),
            },
          })
        }
      }

      eventSource.onerror = () => {
        if (latestProgress?.type === 'complete') {
          return
        }
        // Connection drops are transient by nature — give the user a Retry
        // button rather than a sticky doom toast.
        errorToast('Connection lost during fetch.', {
          id: toastId,
          retry: () => startRef.current(incremental),
        })
        eventSource.close()
        sourceRef.current = null
      }
    },
    [onOpenDetails, queryClient],
  )

  // React 19 / React-Compiler: do NOT assign to a ref during render.
  // The retry-button closure inside startFetch reads `startRef.current`
  // when the user clicks (after render commits), so an effect is the
  // correct write site.
  useEffect(() => {
    startRef.current = startFetch
  }, [startFetch])

  return { startFetch }
}


// ---------------------------------------------------------------------------
// Build-9: One-button Refresh — capture + fetch pipeline.
// ---------------------------------------------------------------------------

interface UseRefreshPipelineOptions {
  onOpenDetails: () => void
}

export function useRefreshPipeline({ onOpenDetails }: UseRefreshPipelineOptions) {
  const queryClient = useQueryClient()
  const sourceRef = useRef<EventSource | null>(null)
  const [isRunning, setIsRunning] = useState(false)
  // Forward-reference for the toast's Retry action: the closure inside
  // startRefresh references startRefresh itself for retry; via ref we
  // avoid both the TDZ ("Cannot access variable before declared") that
  // the React 19 compiler flags AND we don't capture a stale callback.
  const startRefreshRef = useRef<(incremental?: boolean) => void>(() => {})

  const startRefresh = useCallback(
    (incremental: boolean = true) => {
      // Defense-in-depth: if a pipeline is already running, ignore the click.
      if (sourceRef.current) {
        return
      }
      setIsRunning(true)

      const toastId = toast.loading('Refreshing…', {
        duration: Infinity,
        action: {
          label: 'Details',
          onClick: () => onOpenDetails(),
        },
      })

      const eventSource = api.startRefresh(incremental)
      sourceRef.current = eventSource

      let latestProgress: FetchProgress | null = null

      const close = () => {
        eventSource.close()
        sourceRef.current = null
        setIsRunning(false)
      }

      // Bug C: classify error events by `kind`.
      //   TRANSIENT -> 8s minimum, Retry button.
      //   AUTH/TERMINAL/unknown -> sticky, Retry button (so the user is
      //   never trapped without a way to recover from a real-world failure).
      const showErrorByKind = (message: string, kind?: ErrorKind, retryable?: boolean) => {
        const isTransient = kind === 'TRANSIENT' || retryable === true
        errorToast(message, {
          id: toastId,
          sticky: !isTransient,
          retry: () => {
            toast.dismiss(toastId)
            startRefreshRef.current(incremental)
          },
        })
      }

      eventSource.onmessage = (event) => {
        let data: FetchProgress
        try {
          data = JSON.parse(event.data)
        } catch {
          return
        }
        latestProgress = data

        switch (data.type) {
          case 'capture_start':
            toast.loading(
              data.message || 'Opening browser to log in to Claude…',
              {
                id: toastId,
                duration: Infinity,
                action: {
                  label: 'Details',
                  onClick: () => onOpenDetails(),
                },
              },
            )
            break
          case 'capture_waiting_login':
            toast.loading(
              data.message || 'Waiting for you to log in…',
              {
                id: toastId,
                duration: Infinity,
                action: {
                  label: 'Details',
                  onClick: () => onOpenDetails(),
                },
              },
            )
            break
          case 'capture_done':
            toast.loading(data.message || 'Credentials captured. Fetching…', {
              id: toastId,
              duration: Infinity,
              action: {
                label: 'Details',
                onClick: () => onOpenDetails(),
              },
            })
            break
          case 'start':
          case 'progress': {
            // Build-9 Bug 2: per-conversation feedback. Without the
            // conversation_name suffix, the user stares at a static
            // "Fetching N/M…" with no sense of progress for long fetches.
            const text = formatProgressText(data)
            toast.loading(text, {
              id: toastId,
              duration: Infinity,
              action: {
                label: 'Details',
                onClick: () => onOpenDetails(),
              },
            })
            break
          }
          case 'complete':
            toast.success(data.message || 'Refresh complete.', {
              id: toastId,
              duration: 5000,
            })
            void invalidateAfterFetch(queryClient)
            close()
            break
          case 'error':
            showErrorByKind(
              data.message || 'Refresh failed.',
              data.kind,
              data.retryable,
            )
            close()
            break
        }
      }

      eventSource.onerror = () => {
        if (latestProgress?.type === 'complete') {
          return
        }
        // Connection drop -> treat as transient (give user a Retry button).
        showErrorByKind('Connection lost during refresh.', 'TRANSIENT', true)
        close()
      }
    },
    [onOpenDetails, queryClient],
  )

  useEffect(() => {
    startRefreshRef.current = startRefresh
  }, [startRefresh])

  return { startRefresh, isRunning }
}
