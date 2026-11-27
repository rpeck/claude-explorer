/**
 * ConversationToolbar — the action-button cluster that lives in the
 * ConversationPage header, to the right of the conversation title +
 * metadata row.
 *
 * Extracted from ConversationPage.tsx (2026-05-30, P1.4 Commit C from
 * PLANS/2026.05.30-STRICT-CODE-QUALITY-REVIEW.md). Behavior-preserving;
 * the parent owns all state and callbacks, this component is
 * presentation-only.
 *
 * Controls and their visibility rules:
 *   - Show Tools          (always)
 *   - Expand/Collapse All (only when showToolCalls)
 *   - Re-download         (only when the source is refetchable)
 *   - Show Compactions    (only when hasCompactMarkers)
 *   - Copy as Markdown    (always)
 *   - Markdown            (always — opens export dialog)
 *   - PDF                 (always)
 *
 * 2026-05-24 UX rationale (preserved): the Show Tools and Show
 * Compactions checkboxes replaced earlier Button variant-toggles
 * because the `default` vs `outline` visual difference was too subtle
 * for users to tell the toggle state at a glance.
 */
import { ChevronsUpDown, Wrench, Download, Scissors, Copy, Check, FileText, FileType, SquareTerminal, Network } from 'lucide-react'
import { Link, useParams } from 'react-router'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { expandAllToolsButtonLabel } from '@/components/conversation/expandAllToolsLabel'
import type { ConversationSource } from '@/lib/types'
import { sourceHas } from '@/lib/sources'

interface ConversationToolbarProps {
  // Show Tools
  showToolCalls: boolean
  setShowToolCalls: (next: boolean) => void
  markPendingRecenter: () => void
  // Show Code Mode (issue #90; only when the conversation has a run)
  hasCodeRuns?: boolean
  showCodeMode?: boolean
  setShowCodeMode?: (next: boolean) => void
  // Expand / Collapse All
  expandAllTools: boolean
  handleToggleExpandAll: () => void
  isExpandPending: boolean
  // Re-download (gated on the refetchable flag)
  conversationSource: ConversationSource
  handleForceRefetch: () => void
  isRefetching: boolean
  // Show Compactions (gated on hasCompactMarkers)
  hasCompactMarkers: boolean
  hideCompactMarkers: boolean
  setHideCompactMarkers: (next: boolean) => void
  // Copy as Markdown
  copiedAll: boolean
  handleCopyAll: () => void
  // Markdown dialog
  setMarkdownDialogOpen: (open: boolean) => void
  // PDF
  handleExportPdf: () => void
  isExportingPdf: boolean
}

export function ConversationToolbar({
  showToolCalls,
  setShowToolCalls,
  markPendingRecenter,
  hasCodeRuns = false,
  showCodeMode = false,
  setShowCodeMode,
  expandAllTools,
  handleToggleExpandAll,
  isExpandPending,
  conversationSource,
  handleForceRefetch,
  isRefetching,
  hasCompactMarkers,
  hideCompactMarkers,
  setHideCompactMarkers,
  copiedAll,
  handleCopyAll,
  setMarkdownDialogOpen,
  handleExportPdf,
  isExportingPdf,
}: ConversationToolbarProps) {
  // Issue #91: the Activity page centered on this session.
  const { uuid } = useParams()
  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* 2026-05-24 UX fix: the Tools toggle used to be a Button with
          `variant={showToolCalls ? 'default' : 'outline'}`. The variant
          difference is too subtle for users to tell whether the toggle
          is ON or OFF at a glance. Native checkbox with an inline label
          removes the ambiguity. */}
      <label
        className="flex cursor-pointer items-center gap-2 text-sm text-zinc-700 dark:text-zinc-200"
        title={showToolCalls ? 'Hide tool calls' : 'Show tool calls'}
        data-testid="header-show-tools-control"
      >
        {/* Phase 1 a11y: nested label with sibling text "Show Tools" — WCAG-conformant implicit label. */}
        {/* oxlint-disable-next-line react-doctor/control-has-associated-label */}
        <input
          type="checkbox"
          checked={showToolCalls}
          onChange={(e) => {
            markPendingRecenter()
            setShowToolCalls(e.target.checked)
          }}
          className="h-4 w-4 cursor-pointer rounded border-zinc-300 text-zinc-900 focus:ring-zinc-500 dark:border-zinc-600"
          data-testid="header-show-tools-checkbox"
        />
        <Wrench className="h-4 w-4" />
        <span>Show Tools</span>
      </label>
      {hasCodeRuns && setShowCodeMode && (
        <label
          className="flex cursor-pointer items-center gap-2 text-sm text-zinc-700 dark:text-zinc-200"
          title={showCodeMode ? 'Hide Code Mode runs' : 'Show Code Mode runs'}
          data-testid="header-show-code-mode-control"
        >
          {/* oxlint-disable-next-line react-doctor/control-has-associated-label */}
          <input
            type="checkbox"
            checked={showCodeMode}
            onChange={(e) => {
              markPendingRecenter()
              setShowCodeMode(e.target.checked)
            }}
            className="h-4 w-4 cursor-pointer rounded border-zinc-300 text-zinc-900 focus:ring-zinc-500 dark:border-zinc-600"
            data-testid="header-show-code-mode-checkbox"
          />
          <SquareTerminal className="h-4 w-4" />
          <span>Show Code Mode</span>
        </label>
      )}
      {showToolCalls && (
        <Button
          variant={expandAllTools ? 'default' : 'outline'}
          size="sm"
          onClick={handleToggleExpandAll}
          title={expandAllTools ? 'Collapse all tools' : 'Expand all tools'}
          disabled={isExpandPending}
        >
          <ChevronsUpDown className={cn('h-4 w-4', isExpandPending && 'animate-pulse')} />
          <span className="ml-2">{expandAllToolsButtonLabel(expandAllTools, isExpandPending)}</span>
        </Button>
      )}
      {sourceHas(conversationSource, 'refetchable') && (
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
          onClick={handleForceRefetch}
          disabled={isRefetching}
          title="Re-download this conversation from Anthropic"
          aria-label="Re-download this conversation"
        >
          <Download className={cn('h-4 w-4', isRefetching && 'animate-pulse')} />
        </Button>
      )}
      {hasCompactMarkers && (
        // 2026-05-24 UX fix: same rationale as Show Tools — the
        // variant-toggle Button hid the enabled state and the semantic
        // inversion ("Show compact markers" label appearing when
        // `hideCompactMarkers=true`) compounded the confusion. The
        // checkbox reads as plain English: checked = compactions are
        // visible.
        <label
          className="flex cursor-pointer items-center gap-2 text-sm text-zinc-700 dark:text-zinc-200"
          title={hideCompactMarkers ? 'Show compact markers' : 'Hide compact markers'}
        >
          {/* Phase 1 a11y: nested label with sibling "Show Compactions". */}
          {/* oxlint-disable-next-line react-doctor/control-has-associated-label */}
          <input
            type="checkbox"
            checked={!hideCompactMarkers}
            onChange={(e) => {
              markPendingRecenter()
              setHideCompactMarkers(!e.target.checked)
            }}
            className="h-4 w-4 cursor-pointer rounded border-zinc-300 text-zinc-900 focus:ring-zinc-500 dark:border-zinc-600"
            data-testid="header-show-compactions-checkbox"
          />
          <Scissors className="h-4 w-4" />
          <span>Show Compactions</span>
        </label>
      )}
      <Button
        variant="outline"
        size="sm"
        onClick={handleCopyAll}
        title="Copy conversation as Markdown"
        aria-label="Copy as Markdown"
      >
        {copiedAll ? (
          <Check className="h-4 w-4 text-green-500" />
        ) : (
          <Copy className="h-4 w-4" />
        )}
        <span className="ml-2">Copy as Markdown</span>
      </Button>
      <Button variant="outline" size="sm" onClick={() => setMarkdownDialogOpen(true)}>
        <FileText className="h-4 w-4" />
        <span className="ml-2">Markdown</span>
      </Button>
      <Button
        variant="outline"
        size="sm"
        onClick={handleExportPdf}
        disabled={isExportingPdf}
        aria-busy={isExportingPdf}
      >
        <FileType className="h-4 w-4" />
        <span className="ml-2">PDF</span>
      </Button>
      {uuid && (
        <Button variant="outline" size="sm" asChild>
          <Link to={`/activity?center=${encodeURIComponent(uuid)}`} data-testid="conversation-activity-button">
            <Network className="h-4 w-4" />
            <span className="ml-2">Activity</span>
          </Link>
        </Button>
      )}
    </div>
  )
}
