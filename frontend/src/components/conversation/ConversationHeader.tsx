/**
 * ConversationHeader — title + source badge + metadata + details
 * collapsible. The static-information cluster that lives at the top of
 * the conversation detail pane, above the toolbar action cluster.
 *
 * Renders:
 *   - Conversation title (truncated, "Untitled" fallback). Its emoji are
 *     grayscale (TitleText, issue #44), and a hover shows the exact title.
 *   - Icon-only copy button for the session name (only when the session
 *     has a name: "Untitled" is not its name)
 *   - PinScopeButton (turns the title into a search-scope pin)
 *   - ListFilterButton (Claude Code only: filter the Conversation List
 *     to this project or path)
 *   - Source badge (Code/Cowork/Desktop), model badge, date, message
 *     count, and "View branches" button when has_branches.
 *   - NoticeList: the conversation's `notices`, above the messages
 *   - <details> collapsible (open by default) with:
 *       * Claude Code: project_path + git_branch copy buttons, after
 *         their "Project:" and "Branch:" labels
 *       * Claude Cowork: sandbox_path copy button, after its
 *         "Sandbox path:" label
 *       * UUID copy button, after "Session ID:" (every source but Codex)
 *       * Codex: "Thread ID", "Codex name" (or "none"), and "Codex home",
 *         then the "Parent:" and "Forks and subagents:" links
 *         (CodexHistoryLinks)
 *       * file_path copy button, after "File:" (when present)
 *       * Claude Code: the "Subagents:" links (SubagentsDetailRow, issue #96)
 *       * Any source: the "Messages with:" links (PeerSessionsRow, issue #91)
 *   - On an agent page (issue #96): the "Session › <agent>" breadcrumb
 *     above the title, and an "Agent ID:" row
 *
 * Every copy button is a CopyValueButton, which owns its own "copied"
 * state and copies the exact API value (UX.md, "Conversation header:
 * copy buttons"; PLANS/2026.10.28-header-copy-buttons.md).
 *
 * Props:
 *   - `conversation`: data
 *   - `onOpenTree`: opens the TreeViewModal (state owned by parent)
 *
 * Extracted from ConversationPage.tsx (2026-05-31, Commit 5 of
 * PLANS/2026.05.31-conversationpage-decomposition.md). Behavior-preserving.
 */
import { GitBranch, FolderCode, ChevronDown } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { PinScopeButton } from '@/components/search/PinScopeButton'
import { ListFilterButton } from '@/components/conversation/ListFilterButton'
import { CopyValueButton } from '@/components/conversation/CopyValueButton'
import { SourceBadge } from '@/components/conversation/SourceBadge'
import { SessionStateChip } from '@/components/messages/SessionStateChip'
import { MachineChip } from '@/components/settings/MachineChip'
import { NoticeList } from '@/components/conversation/NoticeList'
import { CodexHistoryLinks } from '@/components/conversation/CodexHistoryLinks'
import { PeerSessionsRow } from '@/components/conversation/PeerSessionsRow'
import { EntrypointChip } from '@/components/tickets/EntrypointChip'
import { TicketsRow } from '@/components/tickets/TicketsRow'
import { PrsRow } from '@/components/tickets/PrsRow'
import { AgentBreadcrumb, SubagentsDetailRow } from '@/components/conversation/SubagentLinks'
import { TitleText } from '@/components/TitleText'
import { formatFullDate } from '@/lib/utils'
import { modelDisplayName } from '@/lib/modelName'
import { sourceHas } from '@/lib/sources'
import type { ConversationDetail } from '@/lib/types'

interface ConversationHeaderProps {
  conversation: ConversationDetail
  onOpenTree: () => void
}

export function ConversationHeader({ conversation, onOpenTree }: ConversationHeaderProps) {
  return (
    <div className="min-w-0">
      <AgentBreadcrumb conversation={conversation} />
      <div className="flex items-center gap-2">
        <h1
          className="truncate text-xl font-semibold text-zinc-900 dark:text-zinc-100"
          title={conversation.name || undefined}
        >
          <TitleText text={conversation.name || 'Untitled'} />
        </h1>
        {/* D1: an unnamed session has no title copy button, because
            "Untitled" is not its name. */}
        {conversation.name && (
          <CopyValueButton value={conversation.name} label="session name" iconOnly />
        )}
        <PinScopeButton
          conversationUuid={conversation.uuid}
          conversationName={conversation.name || 'Untitled'}
          projectPath={conversation.project_path}
          projectName={conversation.project_path?.split('/').filter(Boolean).pop() || null}
        />
        {sourceHas(conversation.source, 'has_projects') && conversation.project_path && (
          <ListFilterButton
            projectPath={conversation.project_path}
            // The backend's project_name: the last folder of the path.
            projectName={
              conversation.project_name ||
              conversation.project_path.split('/').filter(Boolean).pop() ||
              conversation.project_path
            }
          />
        )}
      </div>
      <div className="mt-1 flex items-center gap-3 text-sm text-zinc-500 dark:text-zinc-400">
        {/* F12 (2026-05-29) → SourceBadge (2026-05-30 P1.2): Cowork is
            a first-class source. The three-way source→icon+color map
            lives in SourceBadge so a future source rename / recolor
            / addition is a single-file change. */}
        <SourceBadge source={conversation.source} variant="header" />
        <SessionStateChip state={conversation.state} />
        <EntrypointChip entrypoint={conversation.entrypoint} />
        <MachineChip
          machineId={conversation.machine_id}
          label={conversation.machine_label}
          labels={conversation.machine_labels}
        />
        <Badge variant="secondary" data-testid="model-badge" title={conversation.model}>
          {modelDisplayName(conversation.model)}
        </Badge>
        <span>{formatFullDate(conversation.created_at)}</span>
        <span>{conversation.message_count} messages</span>
        {conversation.has_branches && (
          <button
            type="button"
            onClick={onOpenTree}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-amber-600 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-950"
          >
            <GitBranch className="h-3 w-3" />
            View branches
          </button>
        )}
      </div>
      <details open className="group mt-1 grid grid-cols-[auto_1fr] items-start gap-x-3">
        <summary
          className="flex cursor-pointer list-none items-center gap-1 text-xs text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300 [&::-webkit-details-marker]:hidden"
          title="Show conversation details"
        >
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-0 -rotate-90" />
          <span>Details</span>
        </summary>
        <div className="space-y-0.5">
          {sourceHas(conversation.source, 'has_projects') && conversation.project_path && (
            <div className={DETAIL_ROW}>
              <FolderCode className="h-3 w-3" />
              <span className="text-zinc-500">Project:</span>
              <CopyValueButton value={conversation.project_path} label="project path">
                <span>{conversation.project_path}</span>
              </CopyValueButton>
              {conversation.git_branch && (
                <>
                  <GitBranch className="ml-2 h-3 w-3" />
                  <span className="text-zinc-500">Branch:</span>
                  <CopyValueButton value={conversation.git_branch} label="git branch">
                    <span>{conversation.git_branch}</span>
                  </CopyValueButton>
                </>
              )}
            </div>
          )}
          {/* D10 (Cowork): label cwd as "Sandbox path" — it's
              typically /sessions/<vm>, not a host filesystem path,
              so don't render it as a link. It still gets a copy
              button, like every other value in the header. */}
          {conversation.source === 'CLAUDE_COWORK' && conversation.sandbox_path && (
            <div
              className="flex items-center gap-1 text-xs text-zinc-400 dark:text-zinc-500"
              data-testid="cowork-sandbox-path"
            >
              <FolderCode className="h-3 w-3" />
              <span className="text-zinc-500">Sandbox path:</span>
              <CopyValueButton value={conversation.sandbox_path} label="sandbox path">
                <span>{conversation.sandbox_path}</span>
              </CopyValueButton>
            </div>
          )}
          {conversation.source === 'CODEX' ? (
            <CodexDetails conversation={conversation} />
          ) : (
            <div className={DETAIL_ROW}>
              <span className="text-zinc-500">Session ID:</span>
              <CopyValueButton value={conversation.uuid} label="UUID">
                <span>{conversation.uuid}</span>
              </CopyValueButton>
            </div>
          )}
          {conversation.agent_id && (
            <div className={DETAIL_ROW}>
              <span className="text-zinc-500">Agent ID:</span>
              <CopyValueButton value={conversation.agent_id} label="agent ID">
                <span>{conversation.agent_id}</span>
              </CopyValueButton>
            </div>
          )}
          {conversation.file_path && (
            <div className={DETAIL_ROW}>
              <span className="text-zinc-500">File:</span>
              <CopyValueButton value={conversation.file_path} label="file path">
                <span className="truncate max-w-lg">{conversation.file_path}</span>
              </CopyValueButton>
            </div>
          )}
          <SubagentsDetailRow conversation={conversation} />
          <PeerSessionsRow conversation={conversation} />
          {/* Data without named_keys (an older server) keeps the old row. */}
          <TicketsRow tickets={conversation.named_keys ?? conversation.tickets} />
          <PrsRow prKeys={conversation.pr_keys} />
        </div>
      </details>
      <NoticeList notices={conversation.notices ?? []} />
    </div>
  )
}

const DETAIL_ROW = 'flex items-center gap-1 text-xs text-zinc-400 dark:text-zinc-500'

/** Codex plan §4.5 and D8: the thread ID is what `codex resume <id>` takes. */
function CodexDetails({ conversation }: { conversation: ConversationDetail }) {
  const home = conversation.source_home
  return (
    <>
      <div className={DETAIL_ROW}>
        <span className="text-zinc-500">Thread ID:</span>
        <CopyValueButton value={conversation.uuid} label="thread ID">
          <span>{conversation.uuid}</span>
        </CopyValueButton>
      </div>
      <div className={DETAIL_ROW}>
        <span className="text-zinc-500">Codex name:</span>
        {conversation.source_name ? (
          <CopyValueButton value={conversation.source_name} label="Codex name">
            <span>{conversation.source_name}</span>
          </CopyValueButton>
        ) : (
          <span>none</span>
        )}
      </div>
      {home && (
        <div className={DETAIL_ROW}>
          <span className="text-zinc-500">Codex home:</span>
          {/* D14: show ~/..., copy the absolute path. */}
          <CopyValueButton value={conversation.source_home_abs || home} label="Codex home">
            <span>{home}</span>
          </CopyValueButton>
        </div>
      )}
      <CodexHistoryLinks conversation={conversation} />
    </>
  )
}
