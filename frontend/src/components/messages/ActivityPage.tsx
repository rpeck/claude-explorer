/**
 * The Activity page (issue #91, UX.md "Activity page"): one panel with the
 * tabs Messages, Code Mode, and Subagents (issue #46, not built yet).
 * `?center=<uuid>` names the open session; without it the page shows every
 * active session.
 */
import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { CodeModeTab } from './CodeModeTab'
import { MessagesTab } from './MessagesTab'

type Tab = 'messages' | 'code_mode'

export function ActivityPage() {
  const [params] = useSearchParams()
  const center = params.get('center') || null
  const [tab, setTab] = useState<Tab>('messages')
  const tabClass = (active: boolean) =>
    `border-b-2 px-3 py-1.5 text-sm ${active ? 'border-zinc-800 font-medium dark:border-zinc-200' : 'border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'}`
  return (
    <div data-testid="activity-panel" className="flex h-full min-h-0 flex-col gap-3 overflow-auto p-4">
      <div role="tablist" aria-label="Activity" className="flex gap-1 border-b border-zinc-200 dark:border-zinc-800">
        <button type="button" role="tab" aria-selected={tab === 'messages'} className={tabClass(tab === 'messages')} onClick={() => setTab('messages')}>
          Messages
        </button>
        <button type="button" role="tab" aria-selected={tab === 'code_mode'} className={tabClass(tab === 'code_mode')} onClick={() => setTab('code_mode')}>
          Code Mode
        </button>
        <button type="button" role="tab" aria-selected={false} disabled className={`${tabClass(false)} cursor-not-allowed opacity-60`}>
          Subagents: coming in #46
        </button>
      </div>
      {tab === 'messages' ? <MessagesTab key={center ?? ''} center={center} /> : <CodeModeTab center={center} />}
    </div>
  )
}
