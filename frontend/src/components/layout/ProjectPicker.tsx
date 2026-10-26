import { useEffect, useMemo, useRef, useState } from 'react'
import { Command } from 'cmdk'
import { ChevronDown, FolderCode } from 'lucide-react'
import { cn } from '@/lib/utils'
import { displayPath, entryMatches, type ProjectEntry, type ProjectKey } from '@/lib/conversationListView'

/**
 * The sidebar's project picker: a button that opens a list of projects,
 * newest first, with a field to filter them by typing
 * (PLANS/2026.10.26-list-filter-dropdowns.md, PLANS/2026.10.26-list-project-key.md;
 * UX.md → "Conversation List filters").
 *
 * Each entry shows a label and, below it, its folder: in path mode an
 * entry is one folder, so "ai" in `~/Source/clientco/ai` reads
 * "clientco/ai" with the full path below.
 *
 * "All projects" stays first, also while the user types, so that the way
 * back to the full list never scrolls away. The filter is a plain
 * substring match on the label and the paths, ignoring case, and it keeps
 * the newest-first order; cmdk's own fuzzy ranking would reorder the
 * projects, so the component filters the items itself
 * (`shouldFilter={false}`) and uses cmdk only for the highlight and the
 * arrow keys.
 */
export function ProjectPicker({
  entries,
  mode,
  project,
  path,
  home,
  onChange,
}: {
  /** The picker's entries, newest first (projectEntries). */
  entries: ProjectEntry[]
  mode: ProjectKey
  /** The `?project=` filter, or ''. */
  project: string
  /** The `?path=` filter, or ''. */
  path: string
  home: string | undefined
  onChange: (next: { project?: string; path?: string }) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  const visible = useMemo(() => entries.filter((e) => entryMatches(e, query)), [entries, query])

  // The active filter, when no entry of this mode stands for it: an old
  // `?project=` link in path mode, a `?path=` in name mode, or a filter
  // from a typed URL that names no loaded project.
  const pathEntry = path ? entries.find((e) => mode === 'path' && e.id === path) : undefined
  const nameEntry = project
    ? entries.find((e) => mode === 'name' && e.id.toLowerCase() === project.toLowerCase())
    : undefined
  let current = ''
  let extra = ''
  if (path) {
    current = pathEntry ? pathEntry.label : mode === 'path' ? displayPath(path, home) : `Path: ${displayPath(path, home)}`
    if (!pathEntry) extra = current
  } else if (project) {
    current = nameEntry ? nameEntry.label : mode === 'path' ? `${project} (all folders)` : project
    if (!nameEntry) extra = current
  }

  const close = () => {
    setOpen(false)
    setQuery('')
  }
  const choose = (next: { project?: string; path?: string }) => {
    onChange(next)
    close()
  }

  // Close on a click outside the picker. Escape closes from the field.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!(e.target instanceof Node) || !ref.current?.contains(e.target)) {
        setOpen(false)
        setQuery('')
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const itemClass =
    'flex w-full cursor-pointer flex-col items-start rounded px-2 py-1.5 text-sm text-zinc-800 data-[selected=true]:bg-zinc-100 dark:text-zinc-200 dark:data-[selected=true]:bg-zinc-800'

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={pathEntry?.title || nameEntry?.title || current || 'Show the sessions of one project'}
        data-testid="sidebar-project-select"
        className="flex h-9 w-full items-center gap-1 rounded-md border border-zinc-200 bg-white px-3 text-sm text-zinc-900 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100"
      >
        <FolderCode className="mr-1 h-3 w-3 shrink-0 text-amber-500" />
        <span className="flex-1 truncate text-left">{current || 'All projects'}</span>
        <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
      </button>
      {open && (
        <Command
          shouldFilter={false}
          loop
          label="Projects"
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              close()
            }
          }}
          className={cn(
            'absolute left-0 right-0 z-30 mt-1 rounded-md border border-zinc-200 bg-white p-1 shadow-lg',
            'dark:border-zinc-700 dark:bg-zinc-900',
          )}
        >
          <Command.Input
            value={query}
            onValueChange={setQuery}
            placeholder="Type to filter projects"
            autoFocus
            data-testid="sidebar-project-filter"
            className="mb-1 h-8 w-full rounded border border-zinc-200 bg-transparent px-2 text-sm outline-none dark:border-zinc-700"
          />
          <Command.List className="max-h-80 overflow-y-auto">
            <Command.Item value="__all__" onSelect={() => choose({})} className={itemClass}>
              All projects
            </Command.Item>
            {extra && (
              <Command.Item value="__current__" onSelect={close} className={itemClass}>
                {extra}
              </Command.Item>
            )}
            {visible.map((entry) => (
              <Command.Item
                key={entry.id}
                value={`entry:${entry.id}`}
                onSelect={() => choose(mode === 'path' ? { path: entry.id } : { project: entry.id })}
                className={itemClass}
                title={entry.title}
              >
                <span className="w-full truncate">{entry.label}</span>
                <span
                  className="w-full truncate font-mono text-[11px] text-zinc-500 dark:text-zinc-400"
                  data-testid="project-entry-detail"
                >
                  {entry.detail}
                </span>
              </Command.Item>
            ))}
            {visible.length === 0 && (
              <div className="px-2 py-1.5 text-sm text-zinc-500">
                {query.trim() ? 'No matching project' : 'No projects'}
              </div>
            )}
          </Command.List>
        </Command>
      )}
    </div>
  )
}
