import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { NoticeKind } from '@/lib/types'
import { cn } from '@/lib/utils'
import { NOTICE_LABELS } from '@/lib/modelName'

interface NoticeRowProps {
  kind: NoticeKind
  text: string
}

/** A notice that Claude Code wrote into the session: muted and folded (issue #52, part B). */
export function NoticeRow({ kind, text }: NoticeRowProps) {
  const [open, setOpen] = useState(false)
  return (
    <div data-testid="notice-row" data-kind={kind} className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 self-start italic hover:text-zinc-700 dark:hover:text-zinc-200"
      >
        <ChevronRight className={cn('h-3 w-3 transition-transform', open && 'rotate-90')} />
        {NOTICE_LABELS[kind]}
      </button>
      {open && <pre className="whitespace-pre-wrap pl-4 font-mono">{text}</pre>}
    </div>
  )
}
