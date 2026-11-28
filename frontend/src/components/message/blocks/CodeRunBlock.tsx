import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github-dark.css'
import { Check, Copy, Maximize2, SquareTerminal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { HighlightedText } from '@/components/HighlightedText'
import { SequenceDiagram } from '@/components/diagram/SequenceDiagram'
import { flowOf } from '@/components/messages/codeRunFlow'
import { computeHighlightRanges } from '@/components/search/highlightRanges'
import { useCopyFeedback } from '@/hooks/useCopyFeedback'
import { errorToast } from '@/lib/errorToast'
import { cn } from '@/lib/utils'
import type { CodeRun, CodeRunCall, ContentBlock } from '@/lib/types'
import { imageSourceUrl } from './imageCollection'

/** The bubble shows this many script lines; Show all and the overlay show the rest. */
const CODE_RUN_CUT_LINES = 15

const STATUS_CLASS: Record<CodeRun['status'], string> = {
  completed: 'bg-emerald-900 text-emerald-200',
  failed: 'bg-red-900 text-red-200',
  terminated: 'bg-red-900 text-red-200',
  running: 'bg-sky-900 text-sky-200',
  unknown: 'bg-zinc-700 text-zinc-200',
}

/** True when the search query matches anywhere in the run (issue #90). */
function codeRunMatches(run: CodeRun, query: string): boolean {
  if (!query.trim()) return false
  const texts = [run.code, run.result, run.stdout, run.stderr, run.error, run.refused]
  for (const c of run.inner_calls) texts.push(c.name, JSON.stringify(c.input ?? {}), c.result)
  return texts.some((t) => !!t && computeHighlightRanges(t, query, -1, -1).length > 0)
}

function Script({ code, language, maxLines, searchQuery }: {
  code: string
  language: string
  maxLines?: number
  searchQuery: string
}) {
  const lines = code.split('\n')
  const shown = maxLines ? lines.slice(0, maxLines) : lines
  const text = shown.join('\n')
  return (
    <div data-testid="code-run-script" className="flex overflow-x-auto font-mono text-xs leading-5">
      <div aria-hidden className="select-none border-r border-zinc-800 px-2 py-2 text-right text-zinc-500">
        {shown.map((_, i) => (
          <div key={i} data-testid="code-run-line-number">{i + 1}</div>
        ))}
      </div>
      {searchQuery.trim() ? (
        // A search hit: plain text, so the <mark> of the match shows.
        <pre className="m-0 flex-1 whitespace-pre px-3 py-2">
          <HighlightedText text={text} query={searchQuery} />
        </pre>
      ) : (
        <div className="flex-1 [&_code]:!bg-transparent [&_code]:!p-0 [&_pre]:m-0 [&_pre]:px-3 [&_pre]:py-2">
          <ReactMarkdown rehypePlugins={[[rehypeHighlight, { detect: false }]]}>
            {`\`\`\`\`${language}\n${text}\n\`\`\`\``}
          </ReactMarkdown>
        </div>
      )}
    </div>
  )
}

function Calls({ calls, searchQuery }: { calls: CodeRunCall[]; searchQuery: string }) {
  if (calls.length === 0) return null
  return (
    <ol className="space-y-1 px-3 py-2 text-xs">
      {calls.map((call, i) => (
        <li key={call.id} data-testid="code-run-call" className="rounded border border-zinc-800 p-2">
          <div className="flex items-center gap-2">
            <span className="text-zinc-500">{i + 1}</span>
            <span className="font-medium text-amber-300">
              <HighlightedText text={call.name} query={searchQuery} />
            </span>
            {(call.inputs_from ?? []).length > 0 && (
              <span className="rounded bg-sky-900 px-1 text-sky-200">
                input from call {(call.inputs_from ?? []).map((j) => j + 1).join(', ')}
              </span>
            )}
            {call.refused && <span className="rounded bg-red-900 px-1 text-red-200">refused</span>}
            {!call.refused && call.is_error && <span className="rounded bg-red-900 px-1 text-red-200">error</span>}
          </div>
          {call.input && (
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-zinc-300">
              <HighlightedText text={JSON.stringify(call.input, null, 2)} query={searchQuery} />
            </pre>
          )}
          {call.result && (
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap border-t border-zinc-800 pt-1 text-zinc-400">
              <HighlightedText text={call.result} query={searchQuery} />
            </pre>
          )}
        </li>
      ))}
    </ol>
  )
}

/** The images (shown) and documents (a download link) that went back with the run. */
function Media({ media }: { media: ContentBlock[] }) {
  if (media.length === 0) return null
  return (
    <div data-testid="code-run-media" className="border-t border-zinc-800 px-3 py-2 text-xs">
      <div className="mb-1 font-medium text-zinc-400">Media returned to the model</div>
      <div className="flex flex-wrap items-start gap-2">
        {media.map((m, i) => {
          const src = m.source ? imageSourceUrl(m.source) : null
          if (!src) return null
          if (m.type === 'image') {
            return <img key={i} src={src} alt={`Image ${i + 1} from the script`} className="max-h-60 max-w-full rounded border border-zinc-800" />
          }
          const n = media.filter((x, j) => j <= i && x.type !== 'image').length
          const mediaType = m.source?.media_type ?? 'unknown'
          const ext = mediaType === 'application/pdf' ? 'pdf' : 'bin'
          return (
            <a key={i} href={src} download={`code-mode-document-${n}.${ext}`} className="rounded bg-zinc-800 px-2 py-1 text-sky-300 underline">
              Download document {n} ({mediaType})
            </a>
          )
        })}
      </div>
    </div>
  )
}

function Outputs({ run, searchQuery }: { run: CodeRun; searchQuery: string }) {
  const parts: [string, string, string | null][] = [
    ['code-run-returned', 'Returned to the model', run.result],
    ['code-run-cut-note', 'Cut output', run.uncut_output_note ?? null],
    ['code-run-stdout', 'stdout', run.stdout],
    ['code-run-stderr', 'stderr', run.stderr],
    ['code-run-error', 'Error', run.error],
    ['code-run-refused', 'Refused calls', run.refused],
  ]
  return (
    <>
      {parts.map(([testId, label, value]) =>
        value ? (
          <div key={testId} data-testid={testId} className="border-t border-zinc-800 px-3 py-2 text-xs">
            <div className="mb-1 font-medium text-zinc-400">{label}</div>
            <pre className="max-h-60 overflow-auto whitespace-pre-wrap text-zinc-200">
              <HighlightedText text={value} query={searchQuery} />
            </pre>
          </div>
        ) : null,
      )}
      <Media media={run.media ?? []} />
      {run.status === 'running' && !run.result && (
        <div className="border-t border-zinc-800 px-3 py-2 text-xs text-zinc-400">
          Still running: the session has no result for this script.
        </div>
      )}
    </>
  )
}

/** How much tool output the run handled, and how much of it reached the model. */
function contextOf(run: CodeRun): string {
  const total = run.inner_calls.reduce((n, c) => n + c.result.length, 0)
  const parts = [`Tool output: ${total} characters. Returned to the model: ${(run.result ?? '').length} characters.`]
  const back = (run.returned_from ?? []).map((j) => j + 1)
  if (back.length === 1) parts.push(`The output of call ${back[0]} went back to the model.`)
  if (back.length > 1) parts.push(`The outputs of calls ${back.slice(0, -1).join(', ')} and ${back[back.length - 1]} went back to the model.`)
  return parts.join(' ')
}

const TABS = ['Script', 'Calls', 'Data flow', 'Returned'] as const

function CodeRunDialog({ run, open, onOpenChange }: {
  run: CodeRun
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [tab, setTab] = useState<(typeof TABS)[number]>('Script')
  const flow = flowOf(run)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-4xl overflow-y-auto">
        <DialogTitle>Code Mode script</DialogTitle>
        <DialogDescription>
          {run.language}, {run.status}, {run.line_count} lines, {run.inner_calls.length} calls
        </DialogDescription>
        <div role="tablist" className="flex gap-1 border-b border-zinc-200 dark:border-zinc-700">
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={cn(
                'px-3 py-1.5 text-sm',
                tab === t ? 'border-b-2 border-zinc-900 font-medium dark:border-zinc-100' : 'text-zinc-500',
              )}
            >
              {t}
            </button>
          ))}
        </div>
        <div role="tabpanel" className="rounded-md bg-zinc-950 text-zinc-100">
          {tab === 'Script' && <Script code={run.code} language={run.language} searchQuery="" />}
          {tab === 'Calls' && (run.inner_calls.length > 0
            ? <Calls calls={run.inner_calls} searchQuery="" />
            : <div className="p-3 text-xs text-zinc-400">This script made no tool calls.</div>)}
          {tab === 'Data flow' && (
            <div className="space-y-3 bg-white p-3 dark:bg-zinc-900">
              <p data-testid="code-run-context" className="text-xs text-zinc-700 dark:text-zinc-300">{contextOf(run)}</p>
              <SequenceDiagram lanes={flow.lanes} steps={flow.steps} />
            </div>
          )}
          {tab === 'Returned' && <Outputs run={run} searchQuery="" />}
        </div>
      </DialogContent>
    </Dialog>
  )
}

interface CodeRunBlockProps {
  block: ContentBlock
  /** The active search query. A run that holds the match shows in full, with the match marked. */
  searchQuery?: string
}

/** A Code Mode run as one dark terminal bubble (issue #90). */
export function CodeRunBlock({ block, searchQuery = '' }: CodeRunBlockProps) {
  const [showAll, setShowAll] = useState(false)
  const [open, setOpen] = useState(false)
  const { copied, trigger } = useCopyFeedback()
  const run = block.code_run
  if (!run) return null
  const hit = codeRunMatches(run, searchQuery)
  const query = hit ? searchQuery : ''
  const lineCount = run.code.split('\n').length
  const cut = !showAll && !hit && lineCount > CODE_RUN_CUT_LINES

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(run.code)
      trigger()
    } catch {
      errorToast('Failed to copy to clipboard.')
    }
  }

  return (
    <div
      data-testid="code-run"
      data-code-run-id={block.id}
      className="my-2 overflow-hidden rounded-md border border-zinc-700 bg-zinc-950 text-zinc-100"
    >
      <div data-testid="code-run-title" className="flex items-center gap-2 border-b border-zinc-800 px-3 py-1.5 text-xs">
        <SquareTerminal className="h-4 w-4 text-zinc-400" />
        <span className="font-medium">Code Mode</span>
        <span className="text-zinc-400">{run.language}</span>
        <span className={cn('rounded px-1.5', STATUS_CLASS[run.status])}>{run.status}</span>
        <span className="text-zinc-400">{lineCount} lines</span>
        {run.background && <span className="text-zinc-400">background</span>}
        <span className="ml-auto" />
        <Button variant="ghost" size="icon" className="h-6 w-6 text-zinc-300" onClick={handleCopy} aria-label="Copy script">
          {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
        </Button>
        <Button variant="ghost" size="sm" className="h-6 px-2 text-zinc-300" onClick={() => setOpen(true)} aria-label="Expand">
          <Maximize2 className="mr-1 h-3 w-3" />
          Expand
        </Button>
      </div>
      {run.description && <div className="border-b border-zinc-800 px-3 py-1 text-xs text-zinc-400">{run.description}</div>}
      <Script code={run.code} language={run.language} maxLines={cut ? CODE_RUN_CUT_LINES : undefined} searchQuery={query} />
      {cut && (
        <button
          type="button"
          className="w-full border-t border-zinc-800 py-1 text-xs text-zinc-400 hover:text-zinc-200"
          onClick={() => setShowAll(true)}
        >
          Show all {lineCount} lines
        </button>
      )}
      <Calls calls={run.inner_calls} searchQuery={query} />
      <Outputs run={run} searchQuery={query} />
      {open && <CodeRunDialog run={run} open={open} onOpenChange={setOpen} />}
    </div>
  )
}
