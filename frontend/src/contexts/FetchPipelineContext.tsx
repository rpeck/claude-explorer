import {
  createContext,
  use,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import { invalidateAfterFetch } from '@/lib/queryClient'
import { api } from '@/lib/api'
import { errorToast } from '@/lib/errorToast'
import { formatProgressText, type FetchProgress } from '@/components/fetch/FetchToast'

/**
 * Build-9 Bug 1: lift the Refresh-pipeline state out of the Sidebar's
 * private hook so the FetchDialog modal can render the SAME live state
 * the toast is driven by.
 *
 * Previously the modal read a one-shot snapshot of /fetch/status when it
 * was first opened, while the toast kept consuming SSE events from a
 * separate EventSource. Result: the modal showed stale "42 already
 * downloaded" text while the toast showed "Fetching 17/100…".
 *
 * This context owns:
 *   - The single in-flight EventSource (so we never run two in parallel).
 *   - The latest SSE FetchProgress event (`progress`).
 *   - A coarse pipeline state machine (idle | running | complete | error).
 *   - The Sonner loading-toast id, so both producers can update it.
 *
 * The Sidebar reads `startRefresh` + `isRunning`; the FetchDialog reads
 * `state` + `progress` + `errorMessage`.
 */

export type PipelineState = 'idle' | 'running' | 'complete' | 'error'

interface FetchPipelineContextValue {
  state: PipelineState
  progress: FetchProgress | null
  errorMessage: string | null
  isRunning: boolean
  startRefresh: (incremental?: boolean) => void
  /**
   * Reset the pipeline back to `idle`. Useful when the modal closes and we
   * want to re-show the static fetch-status snapshot on next open.
   */
  reset: () => void
  /**
   * Open the Details modal — the toast's "Details" action calls this so
   * the dialog open-state lives alongside the pipeline state and modal
   * consumers don't need a separate prop drill.
   */
  openDetails: () => void
  closeDetails: () => void
  detailsOpen: boolean
}

const FetchPipelineContext = createContext<FetchPipelineContextValue | null>(null)

export function FetchPipelineProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const sourceRef = useRef<EventSource | null>(null)
  const toastIdRef = useRef<number | string | null>(null)
  // Forward-reference for the retry toast: the showErrorByKind closure
  // calls startRefresh, which is the SAME function being declared via
  // useCallback. React 19 compiler flags this as a TDZ; the ref breaks
  // the cycle without changing the runtime call shape.
  const startRefreshRef = useRef<(incremental?: boolean) => void>(() => {})

  const [state, setState] = useState<PipelineState>('idle')
  const [progress, setProgress] = useState<FetchProgress | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [detailsOpen, setDetailsOpen] = useState(false)

  const openDetails = useCallback(() => setDetailsOpen(true), [])
  const closeDetails = useCallback(() => setDetailsOpen(false), [])

  const reset = useCallback(() => {
    setState('idle')
    setProgress(null)
    setErrorMessage(null)
  }, [])

  const startRefresh = useCallback(
    (incremental: boolean = true) => {
      // Defense-in-depth: if a pipeline is already running, ignore the click.
      if (sourceRef.current) {
        return
      }
      setState('running')
      setProgress(null)
      setErrorMessage(null)

      const toastId = toast.loading('Refreshing…', {
        duration: Infinity,
        action: {
          label: 'Details',
          onClick: () => openDetails(),
        },
      })
      toastIdRef.current = toastId

      const eventSource = api.startRefresh(incremental)
      sourceRef.current = eventSource

      let latestType: FetchProgress['type'] | null = null

      const close = () => {
        eventSource.close()
        sourceRef.current = null
      }

      const showErrorByKind = (
        message: string,
        kind?: FetchProgress['kind'],
        retryable?: boolean,
      ) => {
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
        latestType = data.type
        setProgress(data)

        switch (data.type) {
          case 'capture_start':
            toast.loading(
              data.message || 'Opening browser to log in to Claude…',
              {
                id: toastId,
                duration: Infinity,
                action: { label: 'Details', onClick: () => openDetails() },
              },
            )
            break
          case 'capture_waiting_login':
            toast.loading(data.message || 'Waiting for you to log in…', {
              id: toastId,
              duration: Infinity,
              action: { label: 'Details', onClick: () => openDetails() },
            })
            break
          case 'capture_done':
            toast.loading(data.message || 'Credentials captured. Fetching…', {
              id: toastId,
              duration: Infinity,
              action: { label: 'Details', onClick: () => openDetails() },
            })
            break
          case 'start':
          case 'progress': {
            const text = formatProgressText(data)
            toast.loading(text, {
              id: toastId,
              duration: Infinity,
              action: { label: 'Details', onClick: () => openDetails() },
            })
            break
          }
          case 'complete':
            toast.success(data.message || 'Refresh complete.', {
              id: toastId,
              duration: 5000,
            })
            void invalidateAfterFetch(queryClient)
            setState('complete')
            close()
            break
          case 'error':
            showErrorByKind(
              data.message || 'Refresh failed.',
              data.kind,
              data.retryable,
            )
            setErrorMessage(data.message || 'Refresh failed.')
            setState('error')
            close()
            break
        }
      }

      eventSource.onerror = () => {
        if (latestType === 'complete') {
          return
        }
        showErrorByKind('Connection lost during refresh.', 'TRANSIENT', true)
        setErrorMessage('Connection lost during refresh.')
        setState('error')
        close()
      }
    },
    [openDetails, queryClient],
  )

  useEffect(() => {
    startRefreshRef.current = startRefresh
  }, [startRefresh])

  const isRunning = state === 'running'

  // Project invariant #2 (CLAUDE.md "Performance Work"): every
  // `<Provider value={{...}}>` MUST be wrapped in `useMemo` with an
  // explicit deps list. Inline object literals rebuild value identity
  // every render and fire the entire subscriber graph.
  const value = useMemo(
    () => ({
      state,
      progress,
      errorMessage,
      isRunning,
      startRefresh,
      reset,
      openDetails,
      closeDetails,
      detailsOpen,
    }),
    [
      state,
      progress,
      errorMessage,
      isRunning,
      startRefresh,
      reset,
      openDetails,
      closeDetails,
      detailsOpen,
    ],
  )

  return (
    <FetchPipelineContext.Provider value={value}>
      {children}
    </FetchPipelineContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components -- safe: context Provider + hook co-located by convention. HMR fast refresh falls back to full reload for this file; no runtime impact.
export function useFetchPipeline(): FetchPipelineContextValue {
  // Phase 3: React 19 use() replaces useContext().
  const ctx = use(FetchPipelineContext)
  if (!ctx) {
    throw new Error('useFetchPipeline must be used within a FetchPipelineProvider')
  }
  return ctx
}
