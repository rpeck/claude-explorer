import { useEffect, useMemo, useRef, useState } from 'react'
import { Command } from 'cmdk'
import { ChevronDown, FolderCode } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * The sidebar's project picker: a button that opens a list of projects,
 * newest first, with a field to filter them by typing
 * (PLANS/2026.10.26-list-filter-dropdowns.md; UX.md → "Conversation List
 * filters").
 *
 * "All projects" stays first, also while the user types, so that the way
 * back to the full list never scrolls away. The filter is a plain
 * substring match, ignoring case, and it keeps the newest-first order;
 * cmdk's own fuzzy ranking would reorder the projects, so the component
 * filters the items itself (`shouldFilter={false}`) and uses cmdk only
 * for the highlight and the arrow keys.
 */
export function ProjectPicker({
  projects,
  project,
  path,
  onChange,
}: {
  /** Project names, newest first. */
  projects: string[]
  /** The `?project=` filter, or ''. */
  project: string
  /** The `?path=` filter, or ''. */
  path: string
  onChange: (next: { project?: string; path?: string }) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  // A `?project=` from a typed URL may name no loaded project; list it anyway.
  const current = project
    ? (projects.find((p) => p.toLowerCase() === project.toLowerCase()) ?? project)
    : ''
  const choices = useMemo(
    () => (current && !projects.includes(current) ? [current, ...projects] : projects),
    [current, projects],
  )
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? choices.filter((name) => name.toLowerCase().includes(q)) : choices
  }, [choices, query])

  const label = path ? `Path: ${path}` : current || 'All projects'

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
    'flex w-full cursor-pointer items-center rounded px-2 py-1.5 text-sm text-zinc-800 data-[selected=true]:bg-zinc-100 dark:text-zinc-200 dark:data-[selected=true]:bg-zinc-800'

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={path || current || 'Show the sessions of one project'}
        data-testid="sidebar-project-select"
        className="flex h-9 w-full items-center gap-1 rounded-md border border-zinc-200 bg-white px-3 text-sm text-zinc-900 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100"
      >
        <FolderCode className="mr-1 h-3 w-3 shrink-0 text-amber-500" />
        <span className="flex-1 truncate text-left">{label}</span>
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
          <Command.List className="max-h-72 overflow-y-auto">
            <Command.Item value="__all__" onSelect={() => choose({})} className={itemClass}>
              All projects
            </Command.Item>
            {path && (
              <Command.Item value="__path__" onSelect={close} className={cn(itemClass, 'font-mono text-xs')}>
                Path: {path}
              </Command.Item>
            )}
            {visible.map((name) => (
              <Command.Item
                key={name}
                value={`project:${name}`}
                onSelect={() => choose({ project: name })}
                className={itemClass}
              >
                {name}
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
