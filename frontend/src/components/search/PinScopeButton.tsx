import { useState, useRef, useEffect } from 'react'
import { Pin, PinOff, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useSearchPin } from '@/contexts/SearchPinContext'
import { TitleText } from '@/components/TitleText'
import { cn } from '@/lib/utils'

/**
 * Pin button for the conversation header. Lets the user pin search to
 * (a) this conversation OR (b) this project. Pin is sticky until the
 * user unpins or runs a sidebar title-search (handled in Sidebar).
 *
 * See SearchPinContext.tsx for the URL-encoded scope state.
 */
export function PinScopeButton({
  conversationUuid,
  conversationName,
  projectPath,
  projectName,
}: {
  conversationUuid: string
  conversationName: string
  projectPath?: string | null
  projectName?: string | null
}) {
  const { scope, pinConversation, pinProject, unpin } = useSearchPin()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // react-doctor-disable-next-line react-doctor/no-cascading-set-state -- Phase 2: the 3 setOpen(false) calls are 3 independent dismiss paths (non-Node target, click-outside, Escape key); only ONE fires per user event. Counting them as "cascading" is a rule miscount. useReducer wouldn't reduce the count or improve clarity.
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      // Hunt #2: global mousedown listener — e.target is EventTarget,
      // which doesn't satisfy Node.contains(). Guard with instanceof
      // instead of `as Node` so a non-Node target (shouldn't happen in
      // practice, but the type system can't rule it out) is treated as
      // "outside the popover" and closes it.
      if (!(e.target instanceof Node)) {
        setOpen(false)
        return
      }
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const isPinnedToThisConv = scope.kind === 'conversation' && scope.uuid === conversationUuid
  const isPinnedToThisProject =
    scope.kind === 'project' && !!projectPath && scope.path === projectPath
  const isAnyActive = isPinnedToThisConv || isPinnedToThisProject

  const onPinConv = () => {
    pinConversation(conversationUuid, conversationName || 'Untitled')
    setOpen(false)
  }
  const onPinProj = () => {
    if (!projectPath) return
    pinProject(projectPath, projectName || projectPath.split('/').filter(Boolean).pop() || projectPath)
    setOpen(false)
  }
  const onUnpin = () => {
    unpin()
    setOpen(false)
  }

  return (
    <div ref={ref} className="relative inline-block" data-testid="pin-scope-button">
      <Button
        type="button"
        variant={isAnyActive ? 'default' : 'outline'}
        size="sm"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={
          isPinnedToThisConv
            ? 'Search pinned to this conversation'
            : isPinnedToThisProject
              ? 'Search pinned to this project'
              : 'Pin search scope'
        }
        data-pin-active={isAnyActive ? 'true' : 'false'}
      >
        {isAnyActive ? <Pin className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
        <span className="ml-2">
          {isPinnedToThisConv ? 'Pinned: conversation' : isPinnedToThisProject ? 'Pinned: project' : 'Search scope'}
        </span>
        <ChevronDown className="ml-1 h-3 w-3 opacity-60" />
      </Button>
      {open && (
        <div
          role="menu"
          className={cn(
            'absolute right-0 z-30 mt-1 w-64 rounded-md border border-zinc-200 bg-white p-1 shadow-lg',
            'dark:border-zinc-700 dark:bg-zinc-900',
          )}
          data-testid="pin-scope-menu"
        >
          <MenuItem
            onClick={onPinConv}
            active={isPinnedToThisConv}
            label="Pin this conversation"
            sub={conversationName || 'Untitled'}
            testId="pin-this-conversation"
          />
          {projectPath && (
            <MenuItem
              onClick={onPinProj}
              active={isPinnedToThisProject}
              label="Pin this project"
              sub={projectName || projectPath}
              testId="pin-this-project"
            />
          )}
          <div className="my-1 h-px bg-zinc-200 dark:bg-zinc-700" />
          <button
            type="button"
            role="menuitem"
            onClick={onUnpin}
            disabled={!isAnyActive}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent dark:disabled:hover:bg-transparent"
            data-testid="pin-unpin"
          >
            <PinOff className="h-4 w-4" />
            Unpin search scope
          </button>
        </div>
      )}
    </div>
  )
}

function MenuItem({
  onClick,
  active,
  label,
  sub,
  testId,
}: {
  onClick: () => void
  active: boolean
  label: string
  sub: string
  testId: string
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      data-testid={testId}
      className={cn(
        'flex w-full flex-col items-start rounded px-2 py-1.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800',
        active ? 'bg-zinc-100 dark:bg-zinc-800' : '',
      )}
    >
      <span className="font-medium text-zinc-900 dark:text-zinc-100">{label}</span>
      {/* `sub` is the conversation title or the project name. */}
      <span className="truncate text-xs text-zinc-500 dark:text-zinc-400">
        <TitleText text={sub} />
      </span>
    </button>
  )
}
