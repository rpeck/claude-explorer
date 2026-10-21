// Shared pieces of the fetch and Refresh toasts. The live SSE consumers are
// contexts/FetchPipelineContext.tsx (Refresh) and fetch/FetchDialog.tsx
// (Fetch New / Full Refresh). Two older hooks here, useFetchToast and
// useRefreshPipeline, were removed on 2026-10-21: nothing used them.

import { api } from '@/lib/api'

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
  // Present on `type:"complete"` events: Cowork tasks archived, and tasks
  // whose download failed (backend/routers/fetch.py:_remote_fields).
  cowork_tasks_fetched?: number
  cowork_tasks_failed?: number
  // "cowork" on the progress frames of the remote Cowork step. Its counts
  // start again at 1 for each workspace, after the chat phase's totals.
  phase?: 'cowork'
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
 * (or "Fetching…") otherwise. A Cowork frame (`phase: "cowork"`) says
 * "Cowork task N/M" instead of "Fetching N/M": its counts start again at 1,
 * and the toast seemed to jump back from "Fetching 120/120" to "Fetching
 * 1/18" (PR #8 review, 2026-10-21). Exported for unit testing in isolation.
 */
export function formatProgressText(data: FetchProgress): string {
  const total = data.total ?? 0
  const current = data.current ?? 0
  const name = data.conversation_name?.trim()
  const label = data.phase === 'cowork' ? 'Cowork task' : 'Fetching'
  if (total > 0 && name) {
    return `${label} ${current}/${total}: ${truncateName(name)}`
  }
  if (total > 0) {
    return `${label} ${current}/${total}…`
  }
  return data.message || 'Fetching…'
}

/** Shown when the server refused a fetch because another one runs (409). */
export const FETCH_BUSY_MESSAGE =
  'Another fetch is already running. Wait for it to finish, then try again.'

/**
 * The text for a fetch stream that failed (`EventSource.onerror`).
 *
 * An EventSource cannot read the HTTP status of a refused stream, so a 409
 * ("a fetch is already in progress") looked like a lost connection. When
 * the stream never delivered an event, ask /fetch/status: if a fetch runs,
 * say so. Otherwise, or if the status call fails, return `fallback`.
 * Exported for unit tests.
 */
export async function streamFailureMessage(
  receivedEvents: boolean,
  fallback: string,
  getStatus: () => Promise<{ fetch_in_progress?: boolean }> = api.getFetchStatus,
): Promise<string> {
  if (receivedEvents) return fallback
  try {
    const status = await getStatus()
    return status.fetch_in_progress ? FETCH_BUSY_MESSAGE : fallback
  } catch {
    return fallback
  }
}

/**
 * Which toast a `complete` event gets. A Cowork task that failed to download
 * makes it a warning: a green "successfully" toast once hid such failures,
 * Refresh after Refresh (PR #6 review, 2026-10-19). Exported for unit tests.
 */
export function completeToastFor(
  data: FetchProgress,
  fallback: string = 'Fetch complete.',
): { kind: 'success' | 'warning'; message: string } {
  const message = data.message || fallback
  return (data.cowork_tasks_failed ?? 0) > 0
    ? { kind: 'warning', message }
    : { kind: 'success', message }
}
