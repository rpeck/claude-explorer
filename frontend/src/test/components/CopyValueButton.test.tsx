/**
 * CopyValueButton — unit contract (black-box, from
 * PLANS/2026.10.28-header-copy-buttons.md, "Test Cases", unit 2-5, and
 * "Event Orders to Check" 1-5).
 *
 * Interface under test: `<CopyValueButton value label iconOnly? >children</…>`.
 *   - Tooltip (`title`): "Click to copy <label>".
 *   - A click writes `value` (never the displayed children) to the clipboard.
 *   - Success: the copy icon becomes a check mark with class `text-green-500`
 *     for 2 seconds, then the copy icon (`.lucide-copy`) returns.
 *   - Failure: errorToast("Failed to copy <label> to clipboard."), no check.
 *   - The feedback belongs to the value that was copied: a change of `value`
 *     clears the check mark at once, and a write that resolves after `value`
 *     changed shows no check mark.
 *
 * The clipboard is stubbed per test with `Object.defineProperty`; clicks use
 * fireEvent inside act (userEvent.setup() would swap in its own clipboard).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { CopyValueButton } from '../../components/conversation/CopyValueButton'

vi.mock('@/lib/errorToast', () => ({
  errorToast: vi.fn(),
}))

import { errorToast } from '@/lib/errorToast'

const LONG_PATH =
  '/Users/tester/Source/a-very-long-project-folder-name/with/many/nested/levels/' +
  'that/the/header/truncates/when/the/pane/is/narrow/session-0000-1111-2222.jsonl'

function stubClipboard(writeText: (text: string) => Promise<void>) {
  const spy = vi.fn(writeText)
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: spy },
  })
  return spy
}

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

async function click(el: HTMLElement) {
  await act(async () => {
    fireEvent.click(el)
  })
}

function hasCheck(btn: HTMLElement) {
  return btn.querySelector('.text-green-500') !== null
}

function hasCopyIcon(btn: HTMLElement) {
  return btn.querySelector('.lucide-copy') !== null
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('CopyValueButton — success (unit case 2)', () => {
  it('writes the exact value, not the truncated display text', async () => {
    const writeText = stubClipboard(() => Promise.resolve())
    render(
      <CopyValueButton value={LONG_PATH} label="file path">
        …/session-0000-1111-2222.jsonl
      </CopyValueButton>,
    )
    await click(screen.getByTitle('Click to copy file path'))
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText).toHaveBeenCalledWith(LONG_PATH)
  })

  it('writes a Windows path unchanged', async () => {
    const win = 'C:\\Users\\tester\\.claude-explorer\\conversations\\conv one.json'
    const writeText = stubClipboard(() => Promise.resolve())
    render(
      <CopyValueButton value={win} label="file path">
        {win}
      </CopyValueButton>,
    )
    await click(screen.getByTitle('Click to copy file path'))
    expect(writeText).toHaveBeenCalledWith(win)
  })

  it('shows the green check mark, then the copy icon again after 2 seconds', async () => {
    stubClipboard(() => Promise.resolve())
    render(
      <CopyValueButton value="/w/project" label="project path">
        /w/project
      </CopyValueButton>,
    )
    const btn = screen.getByTitle('Click to copy project path')
    expect(hasCopyIcon(btn)).toBe(true)
    expect(hasCheck(btn)).toBe(false)

    await click(btn)
    expect(hasCheck(btn)).toBe(true)
    expect(hasCopyIcon(btn)).toBe(false)

    act(() => {
      vi.advanceTimersByTime(1900)
    })
    expect(hasCheck(btn), 'the check mark must last about 2 seconds').toBe(true)

    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(hasCheck(btn), 'the check mark must go after 2 seconds').toBe(false)
    expect(hasCopyIcon(btn)).toBe(true)
  })

  it('shows the displayed value (children) inside the button', () => {
    stubClipboard(() => Promise.resolve())
    render(
      <CopyValueButton value="feat/x" label="git branch">
        feat/x
      </CopyValueButton>,
    )
    expect(screen.getByTitle('Click to copy git branch')).toHaveTextContent('feat/x')
  })
})

describe('CopyValueButton — failure (unit case 3)', () => {
  for (const label of ['session name', 'UUID', 'file path', 'project path', 'git branch', 'sandbox path']) {
    it(`a rejected write shows "Failed to copy ${label} to clipboard." and no check mark`, async () => {
      stubClipboard(() => Promise.reject(new Error('Document is not focused')))
      render(
        <CopyValueButton value="v" label={label}>
          v
        </CopyValueButton>,
      )
      const btn = screen.getByTitle(`Click to copy ${label}`)
      await click(btn)
      expect(errorToast).toHaveBeenCalledTimes(1)
      expect(errorToast).toHaveBeenCalledWith(`Failed to copy ${label} to clipboard.`)
      expect(hasCheck(btn)).toBe(false)
      expect(hasCopyIcon(btn)).toBe(true)
    })
  }
})

describe('CopyValueButton — the feedback belongs to the copied value', () => {
  it('a change of value clears the check mark at once (unit case 4)', async () => {
    stubClipboard(() => Promise.resolve())
    const { rerender } = render(
      <CopyValueButton value="uuid-A" label="UUID">
        uuid-A
      </CopyValueButton>,
    )
    const btn = screen.getByTitle('Click to copy UUID')
    await click(btn)
    expect(hasCheck(btn)).toBe(true)

    // No timer advance: the clear must not wait for the 2 s reset.
    rerender(
      <CopyValueButton value="uuid-B" label="UUID">
        uuid-B
      </CopyValueButton>,
    )
    const after = screen.getByTitle('Click to copy UUID')
    expect(after).toHaveTextContent('uuid-B')
    expect(hasCheck(after), 'the check mark of uuid-A must not show on uuid-B').toBe(false)
    expect(hasCopyIcon(after)).toBe(true)
  })

  it('control: a held write that resolves with the value unchanged shows the check mark', async () => {
    const held = deferred()
    stubClipboard(() => held.promise)
    render(
      <CopyValueButton value="uuid-A" label="UUID">
        uuid-A
      </CopyValueButton>,
    )
    const btn = screen.getByTitle('Click to copy UUID')
    await click(btn)
    expect(hasCheck(btn), 'no check mark before the write resolves').toBe(false)
    await act(async () => {
      held.resolve()
      await held.promise
    })
    expect(hasCheck(btn)).toBe(true)
  })

  it('a write that resolves after value changed shows no check mark (unit case 5)', async () => {
    const held = deferred()
    const writeText = stubClipboard(() => held.promise)
    const { rerender } = render(
      <CopyValueButton value="uuid-A" label="UUID">
        uuid-A
      </CopyValueButton>,
    )
    await click(screen.getByTitle('Click to copy UUID'))
    expect(writeText).toHaveBeenCalledWith('uuid-A')

    rerender(
      <CopyValueButton value="uuid-B" label="UUID">
        uuid-B
      </CopyValueButton>,
    )
    await act(async () => {
      held.resolve()
      await held.promise
    })
    const btn = screen.getByTitle('Click to copy UUID')
    expect(btn).toHaveTextContent('uuid-B')
    expect(hasCheck(btn), 'the late write for uuid-A must not mark uuid-B as copied').toBe(false)
    expect(hasCopyIcon(btn)).toBe(true)
  })
})

describe('CopyValueButton — repeated and parallel clicks', () => {
  it('two clicks within 2 seconds keep the check mark until 2 seconds after the last click', async () => {
    stubClipboard(() => Promise.resolve())
    render(
      <CopyValueButton value="/w/project" label="project path">
        /w/project
      </CopyValueButton>,
    )
    const btn = screen.getByTitle('Click to copy project path')
    await click(btn)
    act(() => {
      vi.advanceTimersByTime(1500)
    })
    await click(btn)
    act(() => {
      vi.advanceTimersByTime(1000) // 2.5 s after the first click, 1 s after the second
    })
    expect(hasCheck(btn), 'the first click must not clear the check mark of the second').toBe(true)
    act(() => {
      vi.advanceTimersByTime(1100) // 2.1 s after the second click
    })
    expect(hasCheck(btn)).toBe(false)
  })

  it('two different buttons each keep their own check mark', async () => {
    stubClipboard(() => Promise.resolve())
    render(
      <>
        <CopyValueButton value="uuid-A" label="UUID">
          uuid-A
        </CopyValueButton>
        <CopyValueButton value="/w/project" label="project path">
          /w/project
        </CopyValueButton>
      </>,
    )
    const uuidBtn = screen.getByTitle('Click to copy UUID')
    const projectBtn = screen.getByTitle('Click to copy project path')
    await click(uuidBtn)
    act(() => {
      vi.advanceTimersByTime(500)
    })
    await click(projectBtn)
    expect(hasCheck(uuidBtn)).toBe(true)
    expect(hasCheck(projectBtn)).toBe(true)
    act(() => {
      vi.advanceTimersByTime(1600) // 2.1 s after the UUID click, 1.6 s after the project click
    })
    expect(hasCheck(uuidBtn)).toBe(false)
    expect(hasCheck(projectBtn), 'the UUID reset must not clear the project-path check').toBe(true)
  })
})
