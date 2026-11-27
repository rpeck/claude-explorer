/**
 * The Code Mode tab of the Activity page (issue #91): the Code Mode runs of
 * the center session, each with its data-flow diagram (issue #90).
 */
import { useNavigate } from 'react-router'
import { SequenceDiagram } from '@/components/diagram/SequenceDiagram'
import { flowOf } from './codeRunFlow'
import { useConversation } from '@/hooks/useConversations'
import type { CodeRun } from '@/lib/types'

export function CodeModeTab({ center }: { center: string | null }) {
  if (!center) {
    return (
      <p data-testid="code-mode-no-center" className="text-sm text-zinc-500">
        Pick a session to see its Code Mode runs.
      </p>
    )
  }
  return <CenterRuns center={center} />
}

function CenterRuns({ center }: { center: string }) {
  const navigate = useNavigate()
  const { data, isLoading } = useConversation(center)
  const runs: { messageUuid: string; run: CodeRun; key: string }[] = []
  for (const m of data?.messages ?? []) {
    m.content.forEach((b, i) => {
      if (b.type === 'code_run' && b.code_run) runs.push({ messageUuid: m.uuid, run: b.code_run, key: b.id ?? `${m.uuid}:${i}` })
    })
  }
  return (
    <div data-testid="code-mode-tab" className="flex flex-col gap-3">
      {isLoading && <p className="text-xs text-zinc-500">Loading…</p>}
      {data && runs.length === 0 && <p className="text-sm text-zinc-500">No Code Mode runs in this session.</p>}
      {runs.map(({ messageUuid, run, key }) => {
        const flow = flowOf(run)
        return (
          <button
            key={key}
            type="button"
            data-testid="code-mode-run-entry"
            className="rounded border border-zinc-200 p-2 text-left hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900"
            onClick={() => navigate(`/conversations/${center}?m=${encodeURIComponent(messageUuid)}`)}
          >
            <span className="mb-1 block truncate text-xs font-medium">
              {run.description || `${run.language} script, ${run.line_count} lines`}
            </span>
            <SequenceDiagram lanes={flow.lanes} steps={flow.steps} />
          </button>
        )
      })}
    </div>
  )
}
