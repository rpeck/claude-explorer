/**
 * Issue #96: the links between a Claude Code session and its subagent
 * transcripts (UX.md, "Subagent transcripts").
 *
 *   - AgentBreadcrumb: "Session › <agent description or id>" above the
 *     title of an agent page. The first part opens the session.
 *   - SubagentsDetailRow: the "Subagents:" row of the session's Details,
 *     one link per agent transcript that discovery found.
 *
 * #46 replaces both with the Agent call block and the Activity panel.
 */
import { Link } from 'react-router'
import { Bot } from 'lucide-react'
import type { ConversationDetail } from '@/lib/types'

const LINK = 'text-blue-600 hover:underline dark:text-blue-400'

function agentLabel(agentId: string, description?: string | null): string {
  return description || agentId
}

function agentPath(sessionUuid: string, agentId: string): string {
  return `/conversations/${sessionUuid}/agents/${encodeURIComponent(agentId)}`
}

export function AgentBreadcrumb({ conversation }: { conversation: ConversationDetail }) {
  if (!conversation.agent_id) return null
  return (
    <nav aria-label="Breadcrumb" className="mb-1 text-xs text-zinc-500 dark:text-zinc-400">
      <ol className="flex items-center gap-1">
        <li>
          <Link to={`/conversations/${conversation.uuid}`} className={LINK}>
            Session
          </Link>
        </li>
        <li aria-hidden="true">›</li>
        <li aria-current="page" className="flex items-center gap-1">
          <Bot className="h-3 w-3 text-purple-500" />
          <span>{agentLabel(conversation.agent_id, conversation.agent_description)}</span>
        </li>
      </ol>
    </nav>
  )
}

export function SubagentsDetailRow({ conversation }: { conversation: ConversationDetail }) {
  const agents = conversation.subagents_found ?? []
  if (agents.length === 0) return null
  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-zinc-400 dark:text-zinc-500"
      data-testid="details-subagents"
    >
      <Bot className="h-3 w-3" />
      <span className="text-zinc-500">Subagents:</span>
      {agents.map((a) => (
        <Link key={a.agent_id} to={agentPath(conversation.uuid, a.agent_id)} className={LINK}>
          {agentLabel(a.agent_id, a.agent_description)}
        </Link>
      ))}
    </div>
  )
}
