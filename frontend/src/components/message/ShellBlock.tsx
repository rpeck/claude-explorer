interface ShellBlockProps {
  command?: string | null
  output?: string | null
}

/** A `!` shell command and its output, as one block (issue #52, part B). */
export function ShellBlock({ command, output }: ShellBlockProps) {
  return (
    <div
      data-testid="shell-block"
      className="flex flex-col gap-1 rounded-md border border-zinc-200 bg-white px-3 py-2 dark:border-zinc-700 dark:bg-zinc-900"
    >
      <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Shell</span>
      {command && (
        <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs font-semibold text-zinc-900 dark:text-zinc-100">
          {`$ ${command}`}
        </pre>
      )}
      {output && (
        <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs text-zinc-800 dark:text-zinc-200">
          {output}
        </pre>
      )}
    </div>
  )
}
