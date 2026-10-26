/**
 * ConversationHeader — title + source badge + metadata + details
 * collapsible. The static-information cluster that lives at the top of
 * the conversation detail pane, above the toolbar action cluster.
 *
 * Renders:
 *   - Conversation title (truncated, "Untitled" fallback)
 *   - PinScopeButton (turns the title into a search-scope pin)
 *   - ListFilterButton (Claude Code only: filter the Conversation List
 *     to this project or path)
 *   - Source badge (Code/Cowork/Desktop), model badge, date, message
 *     count, and "View branches" button when has_branches.
 *   - <details> collapsible (open by default) with:
 *       * Claude Code: project_path + git_branch
 *       * Claude Cowork: sandbox_path (labelled, not clickable)
 *       * UUID copy button (always shown)
 *       * file_path copy button (when present)
 *
 * Clean prop surface (Commit 5 of the decomposition plan):
 *   - `conversation`: data
 *   - `copiedUuid`, `copiedPath`: feedback flags from
 *     useConversationCopyAndExports (Commit 4)
 *   - `onCopyUuid`, `onCopyPath`: callbacks from same hook
 *   - `onOpenTree`: opens the TreeViewModal (state owned by parent)
 *
 * Extracted from ConversationPage.tsx (2026-05-31, Commit 5 of
 * PLANS/2026.05.31-conversationpage-decomposition.md). Behavior-preserving.
 */
import { GitBranch, Copy, Check, FolderCode, ChevronDown } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { PinScopeButton } from '@/components/search/PinScopeButton'
import { ListFilterButton } from '@/components/conversation/ListFilterButton'
import { SourceBadge } from '@/components/conversation/SourceBadge'
import { formatFullDate } from '@/lib/utils'
import type { ConversationDetail } from '@/lib/types'

interface ConversationHeaderProps {
  conversation: ConversationDetail
  copiedUuid: boolean
  copiedPath: boolean
  onCopyUuid: () => void
  onCopyPath: () => void
  onOpenTree: () => void
}

export function ConversationHeader({
  conversation,
  copiedUuid,
  copiedPath,
  onCopyUuid,
  onCopyPath,
  onOpenTree,
}: ConversationHeaderProps) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2">
        <h1 className="truncate text-xl font-semibold text-zinc-900 dark:text-zinc-100">
          {conversation.name || 'Untitled'}
        </h1>
        <PinScopeButton
          conversationUuid={conversation.uuid}
          conversationName={conversation.name || 'Untitled'}
          projectPath={conversation.project_path}
          projectName={conversation.project_path?.split('/').filter(Boolean).pop() || null}
        />
        {conversation.source === 'CLAUDE_CODE' && conversation.project_path && (
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
        <Badge variant="secondary">{conversation.model}</Badge>
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
          {conversation.source === 'CLAUDE_CODE' && conversation.project_path && (
            <div className="flex items-center gap-1 text-xs text-zinc-400 dark:text-zinc-500">
              <FolderCode className="h-3 w-3" />
              <span className="font-mono">{conversation.project_path}</span>
              {conversation.git_branch && (
                <>
                  <GitBranch className="ml-2 h-3 w-3" />
                  <span className="font-mono">{conversation.git_branch}</span>
                </>
              )}
            </div>
          )}
          {/* D10 (Cowork): label cwd as "Sandbox path" — it's
              typically /sessions/<vm>, not a host filesystem path,
              so don't render as a clickable link. */}
          {conversation.source === 'CLAUDE_COWORK' && conversation.sandbox_path && (
            <div
              className="flex items-center gap-1 text-xs text-zinc-400 dark:text-zinc-500"
              data-testid="cowork-sandbox-path"
            >
              <FolderCode className="h-3 w-3" />
              <span className="text-zinc-500">Sandbox path:</span>
              <span className="font-mono">{conversation.sandbox_path}</span>
            </div>
          )}
          <button
            type="button"
            onClick={onCopyUuid}
            className="flex items-center gap-1 font-mono text-xs text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300"
            title="Click to copy UUID"
          >
            {copiedUuid ? (
              <Check className="h-3 w-3 text-green-500" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
            <span>{conversation.uuid}</span>
          </button>
          {conversation.file_path && (
            <button
              type="button"
              onClick={onCopyPath}
              className="flex items-center gap-1 font-mono text-xs text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300"
              title="Click to copy file path"
            >
              {copiedPath ? (
                <Check className="h-3 w-3 text-green-500" />
              ) : (
                <Copy className="h-3 w-3" />
              )}
              <span className="truncate max-w-lg">{conversation.file_path}</span>
            </button>
          )}
        </div>
      </details>
    </div>
  )
}
