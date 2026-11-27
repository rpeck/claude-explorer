import type { SequenceStep } from '@/components/diagram/SequenceDiagram'
import type { CodeRun } from '@/lib/types'

/** The data flow of a run: model, script, and one lane per tool. An output that
 *  went into a later call's input (`inputs_from`) is a step from tool to tool. */
export function flowOf(run: CodeRun): { lanes: string[]; steps: SequenceStep[] } {
  const lanes = ['Model', 'Script']
  for (const c of run.inner_calls) if (!lanes.includes(c.name)) lanes.push(c.name)
  const steps: SequenceStep[] = [{ from: 0, to: 1, label: `script (${run.line_count} lines)` }]
  run.inner_calls.forEach((c, i) => {
    const lane = lanes.indexOf(c.name)
    for (const j of c.inputs_from ?? []) {
      steps.push({ from: lanes.indexOf(run.inner_calls[j].name), to: lane, label: `output of call ${j + 1} → call ${i + 1}` })
    }
    steps.push({ from: 1, to: lane, label: `call ${i + 1}` })
    steps.push({ from: lane, to: 1, label: c.refused ? 'refused' : c.is_error ? 'error' : 'result' })
  })
  if (run.result || run.error) steps.push({ from: 1, to: 0, label: run.error ? 'error' : 'returned' })
  return { lanes, steps }
}
