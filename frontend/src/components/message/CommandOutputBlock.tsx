import { useState } from 'react'
import { cn } from '@/lib/utils'

/** Output longer than this many lines is folded (decision D3). */
export const COMMAND_OUTPUT_FOLD_LINES = 20

interface CommandOutputBlockProps {
  output: string
  isError?: boolean
}

/**
 * The output of a local slash command, under its command (issue #52, D1).
 * Labelled "Claude Code": the program answered, not a model.
 */
export function CommandOutputBlock({ output, isError = false }: CommandOutputBlockProps) {
  const [showAll, setShowAll] = useState(false)
  const lines = output.split('\n')
  const folded = !showAll && lines.length > COMMAND_OUTPUT_FOLD_LINES
  return (
    <div
      data-testid="command-output"
      {...(isError ? { 'data-error': 'true' } : {})}
      className={cn(
        'flex flex-col gap-1 rounded-md border px-3 py-2',
        isError
          ? 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950'
          : 'border-zinc-200 bg-white dark:border-zinc-700 dark:bg-zinc-900',
      )}
    >
      <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
        Claude Code{isError ? ' (error)' : ''}
      </span>
      <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs text-zinc-800 dark:text-zinc-200">
        {folded ? lines.slice(0, COMMAND_OUTPUT_FOLD_LINES).join('\n') : output}
      </pre>
      {folded && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="self-start text-xs text-blue-600 hover:underline dark:text-blue-400"
        >
          Show all
        </button>
      )}
    </div>
  )
}
