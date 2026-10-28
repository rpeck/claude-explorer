/**
 * useConversationCopyAndExports — unit contract.
 *
 * Three behavior groups:
 *
 *   1. Copy flag: handleCopyAll writes to navigator.clipboard, sets
 *      copiedAll true, and schedules a 2 s reset. Bidirectional check: a
 *      second call before expiry RE-schedules (the older timer is
 *      cancelled, not stacked). The header's UUID and file-path copy
 *      buttons moved to CopyValueButton (2026-10-28), which has its own
 *      tests.
 *
 *   2. Clipboard failure: navigator.clipboard.writeText rejection fires
 *      errorToast and does NOT set copiedAll.
 *
 *   3. PDF export: handleExportPdf flips isExportingPdf true, calls
 *      api.exportPdf with the correct args, downloads the blob on
 *      response.ok, errorToasts on 504/non-ok, AbortError silent.
 *      Re-entry guard: a second handleExportPdf call while the first
 *      is in-flight is a no-op.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useConversationCopyAndExports } from '../../hooks/useConversationCopyAndExports'
import type { ConversationDetail } from '../../lib/types'

// ---- Module mocks --------------------------------------------------------

vi.mock('@/lib/api', () => ({
  api: {
    exportPdf: vi.fn(),
  },
}))

vi.mock('@/lib/errorToast', () => ({
  errorToast: vi.fn(),
}))

vi.mock('sonner', () => ({
  toast: {
    loading: vi.fn(() => 'toast-id-1'),
    dismiss: vi.fn(),
    success: vi.fn(),
  },
}))

vi.mock('@/lib/utils', async () => {
  const actual = await vi.importActual<typeof import('@/lib/utils')>('@/lib/utils')
  return {
    ...actual,
    downloadBlob: vi.fn(),
  }
})

import { api } from '@/lib/api'
import { errorToast } from '@/lib/errorToast'
import { downloadBlob } from '@/lib/utils'

// ---- Test helpers --------------------------------------------------------

const makeConversation = (): ConversationDetail =>
  ({
    uuid: 'conv-uuid-1',
    name: 'Test conversation',
    source: 'CLAUDE_AI',
    model: 'claude-opus',
    created_at: '2026-05-30T12:00:00Z',
    updated_at: '2026-05-30T12:00:00Z',
    message_count: 2,
    has_branches: false,
    messages: [
      { uuid: 'm-1', sender: 'human', text: 'hi', created_at: '2026-05-30T12:00:00Z' },
      { uuid: 'm-2', sender: 'assistant', text: 'hello', created_at: '2026-05-30T12:00:01Z' },
    ],
    compact_markers: [],
    prelude_hidden_count: 0,
    file_path: '/Users/test/conv-uuid-1.json',
    project_path: null,
    git_branch: null,
    sandbox_path: null,
    error: null,
  }) as unknown as ConversationDetail

const installClipboardMock = () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  })
  return writeText
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
})

afterEach(() => {
  vi.useRealTimers()
})

// ---- Copy flags ----------------------------------------------------------

describe('useConversationCopyAndExports — copy flags', () => {
  it('handleCopyAll writes the markdown and flips copiedAll true → false after 2s', async () => {
    const writeText = installClipboardMock()
    const conversation = makeConversation()
    const { result } = renderHook(() =>
      useConversationCopyAndExports({
        conversation,
        showToolCalls: false,
        includeCompactInExports: true,
      }),
    )

    expect(result.current.copiedAll).toBe(false)
    await act(async () => {
      await result.current.handleCopyAll()
    })
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(typeof writeText.mock.calls[0][0]).toBe('string')
    expect(result.current.copiedAll).toBe(true)

    act(() => {
      vi.advanceTimersByTime(2001)
    })
    expect(result.current.copiedAll).toBe(false)
  })

  it('second handleCopyAll before timer expiry re-schedules (does not stack)', async () => {
    installClipboardMock()
    const conversation = makeConversation()
    const { result } = renderHook(() =>
      useConversationCopyAndExports({
        conversation,
        showToolCalls: false,
        includeCompactInExports: true,
      }),
    )

    // 1st click at t=0 — arms a timer to fire at t=2000.
    await act(async () => {
      await result.current.handleCopyAll()
    })
    expect(result.current.copiedAll).toBe(true)

    // Advance to t=1000 (halfway through the first timer).
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(result.current.copiedAll).toBe(true)

    // 2nd click — cancels the first timer (which would have fired at t=2000)
    // and arms a new one for t=1000+2000=3000.
    await act(async () => {
      await result.current.handleCopyAll()
    })
    expect(result.current.copiedAll).toBe(true)

    // Advance to t=2050 (past the original timer's would-be firing time).
    // If the first timer were still armed, copiedAll would flip false here.
    act(() => {
      vi.advanceTimersByTime(1050)
    })
    expect(result.current.copiedAll).toBe(true) // still true → first timer was cancelled

    // Advance another 1000ms (total t=3050 > 3000, the second timer's deadline).
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(result.current.copiedAll).toBe(false) // second timer fired
  })

  it('clipboard rejection: errorToast fires and copiedAll stays false', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('clipboard denied'))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    const conversation = makeConversation()
    const { result } = renderHook(() =>
      useConversationCopyAndExports({
        conversation,
        showToolCalls: false,
        includeCompactInExports: true,
      }),
    )

    await act(async () => {
      await result.current.handleCopyAll()
    })
    expect(errorToast).toHaveBeenCalledWith(
      'Failed to copy conversation to clipboard.',
    )
    expect(result.current.copiedAll).toBe(false)
  })
})

// ---- Conversation-null safety -------------------------------------------

describe('useConversationCopyAndExports — null conversation safety', () => {
  it('all handlers no-op when conversation is null', async () => {
    const writeText = installClipboardMock()
    const { result } = renderHook(() =>
      useConversationCopyAndExports({
        conversation: null,
        showToolCalls: false,
        includeCompactInExports: true,
      }),
    )

    await act(async () => {
      await result.current.handleCopyAll()
      await result.current.handleExportPdf()
    })
    expect(writeText).not.toHaveBeenCalled()
    expect(api.exportPdf).not.toHaveBeenCalled()
  })
})

// ---- PDF export ----------------------------------------------------------

describe('useConversationCopyAndExports — handleExportPdf', () => {
  it('happy path: flips isExportingPdf true, calls api.exportPdf, downloads blob, resets', async () => {
    const blob = new Blob(['%PDF-1.4'], { type: 'application/pdf' })
    ;(api.exportPdf as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      blob: () => Promise.resolve(blob),
    } as Response)

    const conversation = makeConversation()
    const { result } = renderHook(() =>
      useConversationCopyAndExports({
        conversation,
        showToolCalls: true,
        includeCompactInExports: false,
      }),
    )

    expect(result.current.isExportingPdf).toBe(false)
    await act(async () => {
      await result.current.handleExportPdf()
    })

    expect(api.exportPdf).toHaveBeenCalledWith(
      'conv-uuid-1',
      true,
      expect.any(AbortSignal),
      false,
    )
    expect(downloadBlob).toHaveBeenCalledWith(blob, 'Test_conversation.pdf')
    expect(result.current.isExportingPdf).toBe(false)
  })

  it('504 response: errorToast fires the timeout message, no download', async () => {
    ;(api.exportPdf as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 504,
      blob: () => Promise.resolve(new Blob()),
    } as Response)

    const conversation = makeConversation()
    const { result } = renderHook(() =>
      useConversationCopyAndExports({
        conversation,
        showToolCalls: false,
        includeCompactInExports: true,
      }),
    )

    await act(async () => {
      await result.current.handleExportPdf()
    })

    expect(errorToast).toHaveBeenCalledWith(
      expect.stringContaining('PDF generation timed out'),
    )
    expect(downloadBlob).not.toHaveBeenCalled()
  })

  it('AbortError: silent — no errorToast, no download', async () => {
    const abortErr = new DOMException('aborted', 'AbortError')
    ;(api.exportPdf as ReturnType<typeof vi.fn>).mockRejectedValue(abortErr)

    const conversation = makeConversation()
    const { result } = renderHook(() =>
      useConversationCopyAndExports({
        conversation,
        showToolCalls: false,
        includeCompactInExports: true,
      }),
    )

    await act(async () => {
      await result.current.handleExportPdf()
    })

    expect(errorToast).not.toHaveBeenCalled()
    expect(downloadBlob).not.toHaveBeenCalled()
  })

  it('re-entry guard: second handleExportPdf while first is in-flight is a no-op', async () => {
    // Use real timers for this test — waitFor polls in real time, and we
    // don't need fake-timer control for the re-entry guard check (it's
    // synchronous, set BEFORE any await).
    vi.useRealTimers()

    let resolveFirst: ((response: Response) => void) | undefined
    ;(api.exportPdf as ReturnType<typeof vi.fn>).mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveFirst = resolve
        }),
    )

    const conversation = makeConversation()
    const { result } = renderHook(() =>
      useConversationCopyAndExports({
        conversation,
        showToolCalls: false,
        includeCompactInExports: true,
      }),
    )

    // Fire the first call — synchronously sets isExportingPdfRef.current=true
    // BEFORE awaiting api.exportPdf. Don't await the returned promise here;
    // the api mock never resolves until we call resolveFirst.
    let firstPromise: Promise<void> = Promise.resolve()
    act(() => {
      firstPromise = result.current.handleExportPdf()
    })

    // Second call: the synchronous re-entry guard at the top of the handler
    // returns immediately because isExportingPdfRef.current is already true.
    act(() => {
      // Fire and forget — the early-return resolves synchronously.
      void result.current.handleExportPdf()
    })

    expect(api.exportPdf).toHaveBeenCalledTimes(1)

    // Cleanup: resolve the first call so it doesn't leak across tests.
    await act(async () => {
      resolveFirst?.({
        ok: true,
        status: 200,
        blob: () => Promise.resolve(new Blob()),
      } as Response)
      await firstPromise
    })
    await waitFor(() => expect(result.current.isExportingPdf).toBe(false))
  })
})
