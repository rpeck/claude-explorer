/**
 * A plain sequence diagram: one column (lane) per party, time goes down,
 * one row per message between two lanes. CSS grid only, no library.
 * Issue #90 uses it for the data flow of a Code Mode run; issue #91 will
 * use it for messages between sessions.
 */
export interface SequenceStep {
  from: number
  to: number
  label: string
}

interface SequenceDiagramProps {
  lanes: string[]
  steps: SequenceStep[]
}

export function SequenceDiagram({ lanes, steps }: SequenceDiagramProps) {
  const columns = `repeat(${lanes.length}, minmax(6rem, 1fr))`
  return (
    <div className="overflow-x-auto text-xs" data-testid="sequence-diagram">
      <div className="grid gap-x-2" style={{ gridTemplateColumns: columns }}>
        {lanes.map((lane) => (
          <div
            key={lane}
            data-testid="sequence-lane"
            className="truncate rounded bg-zinc-200 px-2 py-1 text-center font-medium text-zinc-800 dark:bg-zinc-700 dark:text-zinc-100"
          >
            {lane}
          </div>
        ))}
        {steps.map((step, i) => {
          const left = Math.min(step.from, step.to)
          const right = Math.max(step.from, step.to)
          const rightward = step.to >= step.from
          return (
            <div
              // Steps never reorder, so the index is a stable key.
              key={i}
              data-testid="sequence-step"
              className="mt-2 flex flex-col"
              style={{ gridColumn: `${left + 1} / ${right + 2}` }}
            >
              <span className="truncate px-1 text-zinc-600 dark:text-zinc-300" title={step.label}>
                {step.label}
              </span>
              <span
                aria-hidden
                className="flex items-center text-zinc-500 dark:text-zinc-400"
              >
                {!rightward && <span>◀</span>}
                <span className="h-px flex-1 bg-zinc-400 dark:bg-zinc-500" />
                {rightward && <span>▶</span>}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
