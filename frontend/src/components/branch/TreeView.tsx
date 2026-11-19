import { User, Bot, ChevronRight } from 'lucide-react'
import { cn, formatDate } from '@/lib/utils'
import { sourceDescriptor } from '@/lib/sources'
import type { MessageNode, ConversationTree, ConversationSource } from '@/lib/types'

interface TreeViewProps {
  tree: ConversationTree
  onSelectPath: (path: string[]) => void
  /** The conversation's source; it names the assistant ("Claude", "Codex"). */
  source?: ConversationSource
}

export function TreeView({ tree, onSelectPath, source }: TreeViewProps) {
  const activePath = new Set(tree.active_path)
  const assistantLabel = sourceDescriptor(source)?.assistant_label ?? 'Claude'

  return (
    <div className="p-4">
      <div className="space-y-0">
        {tree.root_messages.map((node) => (
          <TreeNode
            key={node.message.uuid}
            node={node}
            depth={0}
            activePath={activePath}
            pathSoFar={[]}
            onSelectPath={onSelectPath}
            assistantLabel={assistantLabel}
          />
        ))}
      </div>
    </div>
  )
}

interface TreeNodeProps {
  node: MessageNode
  depth: number
  activePath: Set<string>
  pathSoFar: string[]
  onSelectPath: (path: string[]) => void
  assistantLabel: string
}

function TreeNode({
  node,
  depth,
  activePath,
  pathSoFar,
  onSelectPath,
  assistantLabel,
}: TreeNodeProps) {
  const isActive = activePath.has(node.message.uuid)
  const currentPath = [...pathSoFar, node.message.uuid]
  const hasChildren = node.children.length > 0
  const hasBranches = node.children.length > 1

  // Get preview text (first 60 chars)
  const previewText = node.message.text.slice(0, 60) + (node.message.text.length > 60 ? '...' : '')

  const handleClick = () => {
    // Build path to this node's deepest child on current branch
    const fullPath = buildPathToLeaf(node, currentPath)
    onSelectPath(fullPath)
  }

  return (
    <div className="relative">
      {/* Connector line from parent */}
      {depth > 0 && (
        <div
          className={cn(
            'absolute left-0 top-0 h-4 w-4 border-l-2 border-b-2 rounded-bl-lg',
            isActive
              ? 'border-amber-400 dark:border-amber-600'
              : 'border-zinc-300 dark:border-zinc-600'
          )}
          style={{ marginLeft: (depth - 1) * 24 + 8 }}
        />
      )}

      {/* Node — real <button> so Enter/Space activate natively (Phase 1
          a11y, React Doctor click-events-have-key-events). Reset
          `text-left` because <button> defaults to text-align:center, and
          `w-full` so the button fills the row exactly like the previous
          <div>. */}
      <button
        type="button"
        aria-label={`${node.message.sender === 'human' ? 'You' : assistantLabel}: ${previewText}`}
        className={cn(
          'group flex w-full cursor-pointer items-start gap-2 rounded-lg p-2 text-left transition-colors',
          isActive
            ? 'bg-amber-50 dark:bg-amber-950/50'
            : 'hover:bg-zinc-100 dark:hover:bg-zinc-800/50'
        )}
        style={{ marginLeft: depth * 24 }}
        onClick={handleClick}
      >
        {/* Avatar */}
        <div
          className={cn(
            'flex h-6 w-6 shrink-0 items-center justify-center rounded-full',
            node.message.sender === 'human'
              ? 'bg-blue-100 text-blue-600 dark:bg-blue-900 dark:text-blue-300'
              : 'bg-zinc-200 text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300'
          )}
        >
          {node.message.sender === 'human' ? (
            <User className="h-3 w-3" />
          ) : (
            <Bot className="h-3 w-3" />
          )}
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'text-xs font-medium',
                node.message.sender === 'human'
                  ? 'text-blue-600 dark:text-blue-400'
                  : 'text-zinc-600 dark:text-zinc-400'
              )}
            >
              {node.message.sender === 'human' ? 'You' : assistantLabel}
            </span>
            <span className="text-xs text-zinc-400">
              {formatDate(node.message.created_at)}
            </span>
            {hasBranches && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-900 dark:text-amber-300">
                {node.children.length} branches
              </span>
            )}
          </div>
          <p
            className={cn(
              'mt-0.5 truncate text-sm',
              isActive
                ? 'text-zinc-800 dark:text-zinc-200'
                : 'text-zinc-600 dark:text-zinc-400'
            )}
          >
            {previewText}
          </p>
        </div>

        {/* Expand indicator */}
        {hasChildren && (
          <ChevronRight
            className={cn(
              'h-4 w-4 shrink-0 transition-transform',
              'text-zinc-400 group-hover:text-zinc-600 dark:group-hover:text-zinc-300'
            )}
          />
        )}
      </button>

      {/* Children */}
      {hasChildren && (
        <div className="relative">
          {/* Vertical connector line for multiple children */}
          {hasBranches && (
            <div
              className={cn(
                'absolute left-0 top-0 w-0.5 bg-zinc-200 dark:bg-zinc-700',
                isActive ? 'bg-amber-300 dark:bg-amber-700' : ''
              )}
              style={{
                marginLeft: depth * 24 + 11,
                height: `calc(100% - 16px)`,
              }}
            />
          )}
          {node.children.map((child) => (
            <TreeNode
              key={child.message.uuid}
              node={child}
              depth={depth + 1}
              activePath={activePath}
              pathSoFar={currentPath}
              onSelectPath={onSelectPath}
              assistantLabel={assistantLabel}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// Helper to build path from a node to its first leaf
function buildPathToLeaf(node: MessageNode, pathSoFar: string[]): string[] {
  if (node.children.length === 0) {
    return pathSoFar
  }
  // Follow first child to leaf
  return buildPathToLeaf(node.children[0], [...pathSoFar, node.children[0].message.uuid])
}
