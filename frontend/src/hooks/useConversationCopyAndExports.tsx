/**
 * useConversationCopyAndExports — clipboard-side-effect cohesion home.
 *
 * Owns the `copiedAll` feedback flag of the toolbar's Copy-as-Markdown
 * button. It flips true on a resolved `navigator.clipboard.writeText`
 * and back to false 2 s later via `useUnmountSafeTimer`. The timer
 * resets if the user re-clicks before expiry (the next schedule
 * supersedes the pending one).
 *
 * The header's copy buttons (session name, UUID, file path, project
 * path, git branch, sandbox path) do NOT live here: each is a
 * CopyValueButton that owns its own state (2026-10-28,
 * PLANS/2026.10.28-header-copy-buttons.md).
 *
 * Also owns the entire PDF-export pipeline: the `isExportingPdf`
 * state (drives the toolbar button's `disabled`), the synchronous
 * `isExportingPdfRef` re-entry guard for rapid double-clicks before
 * React commits, the `exportPdfAbortRef` AbortController + its
 * unmount cleanup effect, the toast spinner with elapsed-seconds
 * counter, the 504 timeout copy, and the AbortError branch suppression.
 *
 * Returns the names the toolbar expects (`copiedAll`, `handleCopyAll`,
 * `handleExportPdf`, `isExportingPdf`).
 *
 * Extracted from ConversationPage.tsx (2026-05-31, Commit 4 of
 * PLANS/2026.05.31-conversationpage-decomposition.md). Behavior-preserving.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { errorToast } from '@/lib/errorToast'
import { conversationToMarkdown, downloadBlob, sanitizeFilename } from '@/lib/utils'
import { api } from '@/lib/api'
import { useUnmountSafeTimer } from '@/hooks/useUnmountSafeTimer'
import type { ConversationDetail } from '@/lib/types'

interface UseConversationCopyAndExportsArgs {
  /** May be null/undefined during initial load — handlers early-return. */
  conversation: ConversationDetail | null | undefined
  showToolCalls: boolean
  includeCompactInExports: boolean
}

interface UseConversationCopyAndExportsResult {
  copiedAll: boolean
  handleCopyAll: () => Promise<void>
  handleExportPdf: () => Promise<void>
  isExportingPdf: boolean
}

export function useConversationCopyAndExports({
  conversation,
  showToolCalls,
  includeCompactInExports,
}: UseConversationCopyAndExportsArgs): UseConversationCopyAndExportsResult {
  const [copiedAll, setCopiedAll] = useState(false)

  // S5 T2d (2026-05-20): unmount-safe scheduling for the 2 s copy-feedback
  // flag clear. Bare setTimeout left orphan timers when the user clicked
  // Copy then navigated away before the 2 s elapsed; React 18 silently
  // no-op'd the setState, but the warning surfaced in dev and React 19's
  // stricter semantics would surface it harder.
  const scheduleCopiedAllClear = useUnmountSafeTimer()

  // Task A5 — PDF export spinner toast state.
  // `isExportingPdf` drives the `disabled` attribute on the button (needs
  // to trigger re-render). `isExportingPdfRef` is a synchronous re-entry
  // guard against rapid double-clicks before React commits the state.
  // `exportPdfAbortRef` lets us cancel the in-flight fetch on unmount —
  // otherwise the browser holds the connection slot and the backend
  // continues spending CPU on WeasyPrint for up to 30s after the user
  // navigates away. See PLANS/2026.05.18-perf-polish.md task A5.
  const [isExportingPdf, setIsExportingPdf] = useState(false)
  const isExportingPdfRef = useRef(false)
  const exportPdfAbortRef = useRef<AbortController | null>(null)

  // oxlint-disable-next-line react-doctor/exhaustive-deps -- Cleanup deliberately reads `exportPdfAbortRef.current` at unmount time. Capturing at effect-run would snapshot `null` (this effect runs on mount before any PDF export starts). The intent on unmount is "abort whichever export is currently in flight," which requires the live ref read.
  useEffect(() => {
    return () => {
      exportPdfAbortRef.current?.abort()
    }
  }, [])

  const handleCopyAll = useCallback(async () => {
    if (!conversation) return
    const markdown = conversationToMarkdown(
      conversation.name,
      conversation.messages,
      showToolCalls,
    )
    // LOW-1 (council follow-up): see MessageBubble.handleCopyMessage.
    // Flip the success affordance only on a resolved promise.
    try {
      await navigator.clipboard.writeText(markdown)
      setCopiedAll(true)
      scheduleCopiedAllClear(() => setCopiedAll(false), 2000)
    } catch {
      errorToast('Failed to copy conversation to clipboard.')
    }
  }, [conversation, showToolCalls, scheduleCopiedAllClear])

  const handleExportPdf = useCallback(async () => {
    if (!conversation) return
    // Task A5 — spinner toast UX during PDF export.
    //
    // Why all the moving parts:
    //   * `isExportingPdfRef` is a synchronous re-entry guard. The
    //     button is `disabled` on `isExportingPdf` state, but rapid
    //     double-clicks can fire before React commits the state.
    //   * `toastId` from `toast.loading()` is sonner's auto-generated
    //     unique id — passing it back into subsequent `toast.loading()`
    //     calls replaces the toast in place, and avoids collisions if
    //     the user has two browser tabs of the same conversation open.
    //   * The JSX body wraps the elapsed-seconds counter in
    //     `aria-hidden="true"` so screen readers only announce
    //     "Generating PDF…" once, not every tick.
    //   * `lastSec` throttles `toast.loading()` to once per visible
    //     change; the 250 ms interval ticks faster only to catch the
    //     second boundary promptly when the user clicks mid-second.
    //   * `AbortController` cancels the in-flight fetch on unmount.
    if (isExportingPdfRef.current) return
    isExportingPdfRef.current = true
    setIsExportingPdf(true)

    const controller = new AbortController()
    exportPdfAbortRef.current = controller

    const toastId = toast.loading(
      <span>
        Generating PDF… <span aria-hidden="true">0s</span>
      </span>,
      { duration: Infinity },
    )

    const startedAt = Date.now()
    let lastSec = 0
    const interval = window.setInterval(() => {
      const sec = Math.floor((Date.now() - startedAt) / 1000)
      if (sec === lastSec) return
      lastSec = sec
      toast.loading(
        <span>
          Generating PDF… <span aria-hidden="true">{sec}s</span>
        </span>,
        { id: toastId, duration: Infinity },
      )
    }, 250)

    try {
      const response = await api.exportPdf(
        conversation.uuid,
        showToolCalls,
        controller.signal,
        includeCompactInExports,
      )
      clearInterval(interval)
      if (!response.ok) {
        toast.dismiss(toastId)
        if (response.status === 504) {
          // Backend wraps WeasyPrint in `asyncio.to_thread(...)` with a
          // 30-second timeout (commit 0be9395) and returns 504 on
          // overrun. Surface a user-readable workaround (Markdown
          // export still works for huge conversations).
          errorToast(
            'PDF generation timed out (>30s). The conversation may be too large to render. Try exporting Markdown instead.',
          )
        } else {
          errorToast(`PDF export failed (${response.status}).`)
        }
        return
      }
      const blob = await response.blob()
      toast.dismiss(toastId)
      downloadBlob(blob, `${sanitizeFilename(conversation.name)}.pdf`)
    } catch (err) {
      clearInterval(interval)
      toast.dismiss(toastId)
      // AbortError surfaces here when the component unmounts (cleanup
      // effect calls controller.abort()). That's intentional — no toast.
      if (err instanceof DOMException && err.name === 'AbortError') {
        return
      }
      errorToast('PDF export failed: network error.')
    } finally {
      clearInterval(interval)
      isExportingPdfRef.current = false
      setIsExportingPdf(false)
      if (exportPdfAbortRef.current === controller) {
        exportPdfAbortRef.current = null
      }
    }
  }, [conversation, showToolCalls, includeCompactInExports])

  return {
    copiedAll,
    handleCopyAll,
    handleExportPdf,
    isExportingPdf,
  }
}
