import { describe, it, expect } from 'vitest'
import { completeToastFor, formatProgressText } from '../../components/fetch/FetchToast'

/**
 * Build-9 Bug 2: live progress feedback in the Refresh toast.
 *
 * The hook in FetchToast.tsx wires `formatProgressText` into the SSE event
 * loop. The integration is covered by a Playwright spec; these unit tests
 * pin the formatter contract so the integration is built on solid ground.
 *
 * Spec:
 *   - When `total > 0` AND `conversation_name` is present:
 *     "Fetching N/M: <truncated name>"
 *   - When `total > 0` only: "Fetching N/M…"
 *   - When neither: data.message ?? "Fetching…"
 *   - Names longer than 40 chars get truncated with an ellipsis.
 */

describe('formatProgressText (Bug 2)', () => {
  it('returns "Fetching N/M: name" when both counts and name are present', () => {
    expect(
      formatProgressText({
        type: 'progress',
        message: 'Fetching: Foo',
        current: 2,
        total: 5,
        conversation_name: 'Foo',
      }),
    ).toBe('Fetching 2/5: Foo')
  })

  it('returns "Fetching N/M…" when name is missing', () => {
    expect(
      formatProgressText({
        type: 'progress',
        message: 'Fetching list',
        current: 0,
        total: 10,
      }),
    ).toBe('Fetching 0/10…')
  })

  it('returns the raw message when total is 0', () => {
    expect(
      formatProgressText({
        type: 'start',
        message: 'Fetching conversation list...',
        current: 0,
        total: 0,
      }),
    ).toBe('Fetching conversation list...')
  })

  it('falls back to "Fetching…" when message is empty', () => {
    expect(
      formatProgressText({
        type: 'progress',
        message: '',
      }),
    ).toBe('Fetching…')
  })

  it('truncates conversation_name longer than 40 chars to 40 chars total', () => {
    const longName = 'a'.repeat(60)
    const out = formatProgressText({
      type: 'progress',
      message: 'x',
      current: 1,
      total: 2,
      conversation_name: longName,
    })
    // Format: "Fetching 1/2: <truncated>"
    const colonIdx = out.indexOf(': ')
    expect(colonIdx).toBeGreaterThan(0)
    const namePart = out.slice(colonIdx + 2)
    // 40 chars total including the trailing ellipsis.
    expect(namePart.length).toBe(40)
    // Last char is the ellipsis.
    expect(namePart.endsWith('…')).toBe(true)
    // No occurrences of the full untruncated name.
    expect(out.includes(longName)).toBe(false)
  })

  it('does not touch names shorter than the cap', () => {
    const out = formatProgressText({
      type: 'progress',
      message: 'x',
      current: 1,
      total: 2,
      conversation_name: 'Short',
    })
    expect(out).toBe('Fetching 1/2: Short')
  })

  it('trims whitespace-only conversation_name', () => {
    const out = formatProgressText({
      type: 'progress',
      message: 'x',
      current: 1,
      total: 2,
      conversation_name: '   ',
    })
    // Trimmed to empty -> behave as if name absent.
    expect(out).toBe('Fetching 1/2…')
  })
})

describe('completeToastFor (failed Cowork tasks, 2026-10-19)', () => {
  // A Cowork task whose download kept failing used to end in a green
  // "successfully" toast, so the user never learned that it was missing.

  it('is a success when nothing failed', () => {
    expect(
      completeToastFor({ type: 'complete', message: 'Fetched 3 conversations.', cowork_tasks_failed: 0 }),
    ).toEqual({ kind: 'success', message: 'Fetched 3 conversations.' })
  })

  it('is a warning when a Cowork task failed', () => {
    const message =
      'Fetched 0 conversations. 1 Cowork task could not be downloaded (Daily summary). The next Refresh tries it again.'
    expect(completeToastFor({ type: 'complete', message, cowork_tasks_failed: 1 })).toEqual({
      kind: 'warning',
      message,
    })
  })

  it('treats a missing count as no failures (older servers)', () => {
    expect(completeToastFor({ type: 'complete', message: 'Done.' }).kind).toBe('success')
  })

  it('falls back to a default message', () => {
    expect(completeToastFor({ type: 'complete', message: '' }, 'Refresh complete.').message).toBe(
      'Refresh complete.',
    )
  })
})
