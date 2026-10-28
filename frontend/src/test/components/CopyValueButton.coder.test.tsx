/**
 * CopyValueButton — coder's unit tests (the tester owns
 * CopyValueButton.test.tsx).
 *
 * Covers the event orders in PLANS/2026.10.28-header-copy-buttons.md,
 * "Event Orders to Check":
 *   1. a change of `value` clears the check mark at once;
 *   2. a write that resolves after `value` changed shows no check mark;
 *   3. a second click restarts the 2 s;
 *   4. two buttons keep separate state;
 *   5. a failed write shows the error toast and no check mark.
 * Plus the tooltip / accessible name contract and the 2 s reset.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { CopyValueButton } from '../../components/conversation/CopyValueButton'

vi.mock('@/lib/errorToast', () => ({
  errorToast: vi.fn(),
}))

import { errorToast } from '@/lib/errorToast'

let writeText: ReturnType<typeof vi.fn>

function installClipboard(impl?: (text: string) => Promise<void>) {
  writeText = vi.fn(impl ?? (() => Promise.resolve()))
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  })
}

const hasCheck = (el: HTMLElement) => el.querySelector('.lucide-check') !== null
const hasCopy = (el: HTMLElement) => el.querySelector('.lucide-copy') !== null

async function click(el: HTMLElement) {
  await act(async () => {
    fireEvent.click(el)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  installClipboard()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('CopyValueButton — contract', () => {
  it('uses "Click to copy <label>" as the tooltip and shows the children', () => {
    render(
      <CopyValueButton value="/a/b" label="project path">
        <span>/a/b</span>
      </CopyValueButton>,
    )
    const btn = screen.getByTitle('Click to copy project path')
    expect(btn).toHaveTextContent('/a/b')
    expect(btn).not.toHaveAttribute('aria-label')
    expect(hasCopy(btn)).toBe(true)
  })

  it('iconOnly adds the accessible name "Copy <label>" and no text', () => {
    render(<CopyValueButton value="My chat" label="session name" iconOnly />)
    const btn = screen.getByRole('button', { name: 'Copy session name' })
    expect(btn).toHaveAttribute('title', 'Click to copy session name')
    expect(btn.textContent).toBe('')
  })

  it('copies the exact value, not the displayed children', async () => {
    const long = '/very/long/path/' + 'x'.repeat(300)
    render(
      <CopyValueButton value={long} label="file path">
        <span>shortened…</span>
      </CopyValueButton>,
    )
    await click(screen.getByTitle('Click to copy file path'))
    expect(writeText).toHaveBeenCalledWith(long)
  })
})

describe('CopyValueButton — success feedback', () => {
  it('shows a green check for 2 s, then the copy icon again', async () => {
    render(<CopyValueButton value="abc" label="UUID" />)
    const btn = screen.getByTitle('Click to copy UUID')
    await click(btn)
    const check = btn.querySelector('.lucide-check')
    expect(check).not.toBeNull()
    expect(check).toHaveClass('h-3', 'w-3', 'text-green-500')
    expect(hasCopy(btn)).toBe(false)

    act(() => {
      vi.advanceTimersByTime(1999)
    })
    expect(hasCheck(btn)).toBe(true)
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(hasCheck(btn)).toBe(false)
    expect(hasCopy(btn)).toBe(true)
  })

  it('a second click restarts the 2 s', async () => {
    render(<CopyValueButton value="abc" label="UUID" />)
    const btn = screen.getByTitle('Click to copy UUID')
    await click(btn)
    act(() => {
      vi.advanceTimersByTime(1500)
    })
    await click(btn)
    act(() => {
      vi.advanceTimersByTime(1500)
    })
    // 3000 ms after the first click, 1500 ms after the second.
    expect(hasCheck(btn)).toBe(true)
    act(() => {
      vi.advanceTimersByTime(500)
    })
    expect(hasCheck(btn)).toBe(false)
  })

  it('two buttons keep separate state', async () => {
    render(
      <>
        <CopyValueButton value="abc" label="UUID" />
        <CopyValueButton value="/p" label="file path" />
      </>,
    )
    const uuid = screen.getByTitle('Click to copy UUID')
    const path = screen.getByTitle('Click to copy file path')
    await click(uuid)
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    await click(path)
    expect(hasCheck(uuid)).toBe(true)
    expect(hasCheck(path)).toBe(true)
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    // The UUID's 2 s are over; the path's are not.
    expect(hasCheck(uuid)).toBe(false)
    expect(hasCheck(path)).toBe(true)
  })
})

describe('CopyValueButton — failure', () => {
  it('a rejected write shows the error toast and no check mark', async () => {
    installClipboard(() => Promise.reject(new Error('denied')))
    render(<CopyValueButton value="/p" label="sandbox path" />)
    const btn = screen.getByTitle('Click to copy sandbox path')
    await click(btn)
    expect(errorToast).toHaveBeenCalledWith('Failed to copy sandbox path to clipboard.')
    expect(hasCheck(btn)).toBe(false)
    expect(hasCopy(btn)).toBe(true)
  })

  it('a failed second click removes the check mark of the first', async () => {
    render(<CopyValueButton value="/p" label="file path" />)
    const btn = screen.getByTitle('Click to copy file path')
    await click(btn)
    expect(hasCheck(btn)).toBe(true)
    installClipboard(() => Promise.reject(new Error('denied')))
    await click(btn)
    expect(errorToast).toHaveBeenCalledTimes(1)
    expect(hasCheck(btn)).toBe(false)
  })
})

describe('CopyValueButton — value changes (another conversation opened)', () => {
  it('a change of value clears the check mark at once', async () => {
    const { rerender } = render(<CopyValueButton value="uuid-A" label="UUID" />)
    const btn = screen.getByTitle('Click to copy UUID')
    await click(btn)
    expect(hasCheck(btn)).toBe(true)
    rerender(<CopyValueButton value="uuid-B" label="UUID" />)
    expect(hasCheck(btn)).toBe(false)
    expect(hasCopy(btn)).toBe(true)
  })

  it('going back to the copied value within 2 s does not bring the check back', async () => {
    const { rerender } = render(<CopyValueButton value="uuid-A" label="UUID" />)
    const btn = screen.getByTitle('Click to copy UUID')
    await click(btn)
    rerender(<CopyValueButton value="uuid-B" label="UUID" />)
    rerender(<CopyValueButton value="uuid-A" label="UUID" />)
    expect(hasCheck(btn)).toBe(false)
  })

  it('a write that resolves after the value changed shows no check mark', async () => {
    let resolveWrite: () => void = () => {}
    installClipboard(
      () =>
        new Promise<void>((resolve) => {
          resolveWrite = resolve
        }),
    )
    const { rerender } = render(<CopyValueButton value="uuid-A" label="UUID" />)
    const btn = screen.getByTitle('Click to copy UUID')
    await click(btn)
    expect(writeText).toHaveBeenCalledWith('uuid-A')
    rerender(<CopyValueButton value="uuid-B" label="UUID" />)
    await act(async () => {
      resolveWrite()
    })
    expect(hasCheck(btn)).toBe(false)
    expect(errorToast).not.toHaveBeenCalled()
  })

  it('a pending write that resolves with the value unchanged shows the check', async () => {
    let resolveWrite: () => void = () => {}
    installClipboard(
      () =>
        new Promise<void>((resolve) => {
          resolveWrite = resolve
        }),
    )
    render(<CopyValueButton value="uuid-A" label="UUID" />)
    const btn = screen.getByTitle('Click to copy UUID')
    await click(btn)
    expect(hasCheck(btn)).toBe(false)
    await act(async () => {
      resolveWrite()
    })
    expect(hasCheck(btn)).toBe(true)
  })
})
