/**
 * CopyValueButton — one click copies a value from the conversation
 * header (session name, UUID, file path, project path, git branch,
 * sandbox path) to the clipboard.
 *
 * Contract (UX.md, "Conversation header: copy buttons"; plan
 * PLANS/2026.10.28-header-copy-buttons.md):
 *   - `title` is "Click to copy <label>". An `iconOnly` button also gets
 *     the accessible name "Copy <label>", because it has no text.
 *   - It copies `value` exactly: nothing added or removed, even when the
 *     displayed `children` are truncated.
 *   - Success: the Copy icon becomes a green Check for 2 s. A second
 *     click restarts the 2 s (`useUnmountSafeTimer` cancels the earlier
 *     timer). Failure: an error toast, and no check mark (LOW-1, see
 *     MessageBubble.handleCopyMessage).
 *   - The check mark belongs to the value that was copied.
 *     ConversationPage stays mounted when the user opens another
 *     conversation, so `value` can change under a live button:
 *       * a change of `value` clears the check mark at once;
 *       * a write that resolves after `value` changed shows no check
 *         mark (compared against the latest value through a ref, and
 *         the check-mark state records the value that it belongs to).
 *   - Each button owns its own state, so one button never clears
 *     another.
 *
 * It reads no context (CLAUDE.md, "Performance Work", rule 1), so it
 * adds no re-render path to the header.
 */
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Check, Copy } from 'lucide-react'
import { errorToast } from '@/lib/errorToast'
import { useUnmountSafeTimer } from '@/hooks/useUnmountSafeTimer'

interface CopyValueButtonProps {
  /** The exact text to copy. */
  value: string
  /** What the value is, for example "project path". Used in the tooltip,
   *  the accessible name, and the error toast. */
  label: string
  /** Show only the icon (the title copy button). */
  iconOnly?: boolean
  /** The displayed value, after the icon. Ignored when `iconOnly`. */
  children?: ReactNode
}

const ICON_CLASS = 'h-3 w-3'

export function CopyValueButton({ value, label, iconOnly = false, children }: CopyValueButtonProps) {
  // The value that the check mark belongs to, or null for no check mark.
  // Keyed by value, so a stale success can never mark a newer value.
  const [copiedValue, setCopiedValue] = useState<string | null>(null)
  // When `value` changes, clear the check mark during render, so the new
  // value never shows the old value's feedback, not even for one frame.
  const [renderedValue, setRenderedValue] = useState(value)
  if (renderedValue !== value) {
    setRenderedValue(value)
    setCopiedValue(null)
  }
  const copied = copiedValue === value

  // The latest committed value, read after the clipboard write resolves.
  const latestValueRef = useRef(value)
  useLayoutEffect(() => {
    latestValueRef.current = value
  }, [value])

  const scheduleClear = useUnmountSafeTimer()

  const handleClick = async () => {
    const valueAtClick = value
    try {
      await navigator.clipboard.writeText(valueAtClick)
    } catch {
      setCopiedValue(null)
      errorToast(`Failed to copy ${label} to clipboard.`)
      return
    }
    // The user opened another conversation while the write was pending:
    // a check mark now would claim that the new value was copied.
    if (latestValueRef.current !== valueAtClick) return
    setCopiedValue(valueAtClick)
    scheduleClear(() => setCopiedValue(null), 2000)
  }

  const icon = copied ? (
    <Check className={`${ICON_CLASS} text-green-500`} />
  ) : (
    <Copy className={ICON_CLASS} />
  )

  if (iconOnly) {
    return (
      <button
        type="button"
        onClick={handleClick}
        className="shrink-0 rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:text-zinc-500 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
        title={`Click to copy ${label}`}
        aria-label={`Copy ${label}`}
      >
        {icon}
      </button>
    )
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className="flex items-center gap-1 font-mono text-xs text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300"
      title={`Click to copy ${label}`}
    >
      {icon}
      {children}
    </button>
  )
}
