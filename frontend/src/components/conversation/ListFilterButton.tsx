import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ListFilter } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useSetListProjectFilter, useUrlFilters } from '@/hooks/useUrlFilters'
import { cn } from '@/lib/utils'
import { useSettings } from '@/contexts/SettingsContext'
import { useConfig } from '@/hooks/useConversations'
import { displayPath } from '@/lib/conversationListView'

/**
 * "List" menu in the conversation header: filter the Conversation List to
 * this conversation's project or path, or show all projects again. It
 * writes `?project=` or `?path=` (UX.md → "Conversation List filters").
 *
 * Separate from the search scope pin (PinScopeButton), which dims rows
 * and scopes search but does not hide rows.
 */
export function ListFilterButton({
  projectPath,
  projectName,
}: {
  projectPath: string
  projectName: string
}) {
  const { project, path } = useUrlFilters()
  const setListProjectFilter = useSetListProjectFilter()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // react-doctor-disable-next-line react-doctor/no-cascading-set-state -- the setOpen(false) calls are independent dismiss paths (click outside, Escape); only one fires per event. Same shape as PinScopeButton.
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!(e.target instanceof Node) || !ref.current?.contains(e.target)) setOpen(false)
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

  // What a "project" is: one folder path (default) or one folder name
  // (PLANS/2026.10.26-list-project-key.md). In path mode, "this project"
  // is this conversation's folder, and the menu needs no separate
  // folder item.
  const { listProjectKey } = useSettings()
  const { data: appConfig } = useConfig()
  const shownPath = displayPath(projectPath, appConfig?.home_dir)
  const byPath = listProjectKey === 'path'

  let state: 'all' | 'project' | 'path' | 'other' = 'all'
  if (path) state = path === projectPath ? 'path' : 'other'
  else if (project) state = !byPath && project.toLowerCase() === projectName.toLowerCase() ? 'project' : 'other'
  const label =
    state === 'all'
      ? 'Filter list'
      : state === 'other'
        ? 'List: other project'
        : state === 'path' && !byPath
          ? 'List: this folder'
          : 'List: this project'

  const choose = (next: { project?: string; path?: string }) => {
    setListProjectFilter(next)
    setOpen(false)
  }

  return (
    <div ref={ref} className="relative inline-block">
      <Button
        type="button"
        variant={state === 'all' ? 'outline' : 'default'}
        size="sm"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Filter the conversation list to this project"
        data-testid="list-filter-button"
        data-list-filter={state}
      >
        <ListFilter className="h-4 w-4" />
        <span className="ml-2">{label}</span>
        <ChevronDown className="ml-1 h-3 w-3 opacity-60" />
      </Button>
      {open && (
        <div
          role="menu"
          className={cn(
            'absolute right-0 z-30 mt-1 w-72 rounded-md border border-zinc-200 bg-white p-1 shadow-lg',
            'dark:border-zinc-700 dark:bg-zinc-900',
          )}
        >
          {byPath ? (
            <MenuItem
              onClick={() => choose({ path: projectPath })}
              active={state === 'path'}
              label="Show only this project"
              sub={shownPath}
              testId="list-filter-project"
            />
          ) : (
            <>
              <MenuItem
                onClick={() => choose({ project: projectName })}
                active={state === 'project'}
                label="Show only this project"
                sub={projectName}
                testId="list-filter-project"
              />
              <MenuItem
                onClick={() => choose({ path: projectPath })}
                active={state === 'path'}
                label="Show only this folder"
                sub={shownPath}
                testId="list-filter-path"
              />
            </>
          )}
          <div className="my-1 h-px bg-zinc-200 dark:bg-zinc-700" />
          <button
            type="button"
            role="menuitem"
            onClick={() => choose({})}
            disabled={state === 'all'}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent dark:text-zinc-200 dark:hover:bg-zinc-800 dark:disabled:hover:bg-transparent"
            data-testid="list-filter-clear"
          >
            Show all projects
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
      <span className="w-full truncate font-mono text-xs text-zinc-500 dark:text-zinc-400">{sub}</span>
    </button>
  )
}
