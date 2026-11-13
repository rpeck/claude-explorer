/**
 * Manage Filters modal — CFR1 two-pane atom + group editor (v2 of CF2).
 *
 * Layout:
 *   [Manage filters]                                     [+ New filter]
 *   ┌──────────────────────┬──────────────────────────────────────┐
 *   │ Search...            │ Editing: <name input>                 │
 *   │  Filter A   [Atom]   │ Used by: G1, G2 (under name field)    │
 *   │  Filter B   [Group]  │ Type: ( • Atom )( Group )              │
 *   │  ...                 │ Enabled: [toggle]                      │
 *   │                      │ <type-specific editor>                  │
 *   │                      │ Summary: ...                            │
 *   │                      │                                         │
 *   │                      │                              [Cancel] [Save]
 *   └──────────────────────┴──────────────────────────────────────┘
 *
 * Atom editor (CFR1):
 *   - Name input with placeholder "Name (auto-filled from first pattern)".
 *     Prefill rule unchanged from CF2 (debounce 300ms; refs gate the effect).
 *   - Behavior radio (Hide matches / Show only matches) at the TOP — this
 *     is the highest-impact decision. Replaces the v1 polarity radio.
 *   - Mode radio (Glob / Regex).
 *   - Patterns textarea (one pattern per line; OR'd at evaluation).
 *   - A help line under the textarea with the rules of the current mode.
 *   - Plain-English summary line below the textarea.
 *
 * Group editor (CFR1):
 *   - Match radio: "all of these" / "any of these" (no AND/OR jargon).
 *   - Members chip rail with × on each chip.
 *   - "Add member" Select listing every other filter except (a) self, and
 *     (b) any candidate that already transitively references this group
 *     (DFS from candidate; if it reaches G, it's a cycle). Disabled
 *     candidates appear with "(disabled)" suffix.
 *   - Plain-English summary line below the members rail.
 *
 * v2 design notes:
 *   - Atoms carry `behavior: 'hide' | 'show-only'`. Groups DO NOT — they
 *     are pure boolean combinators over their children's keep/drop
 *     decisions (council convergence; rawMatches model rejected — see
 *     filterEngine.ts module header).
 *   - The "exclude + any" warning from CF2 is GONE. With Behavior at the
 *     atom level the failure mode it caught (a group of all-exclude
 *     atoms set to "any" passing for everything) is no longer reachable.
 *
 * Type switch:
 *   - Single unified draft retains type-specific fields across switches
 *     (council recommendation: drop the confirm dialog entirely; submit
 *     handler picks fields based on current type).
 *
 * Delete flow:
 *   - Each row has a delete affordance. If `findReferencingGroups()` is
 *     non-empty, the click reveals an inline "Used by ..." popover
 *     (`filter-delete-blocked-{id}`) and deletion is blocked.
 *   - If unreferenced, the click reveals an inline [Cancel][Confirm] pair
 *     in an aria-live region; Confirm calls removeNode().
 *
 * Save flow:
 *   - validateNoCycle() over the prospective state.
 *   - empty-name check.
 *   - graceful orphan-strip (chip childIds whose nodes are missing in state
 *     are dropped on save).
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { Plus, Trash2, X } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useFilters } from '@/contexts/FilterContext'
import {
  findReferencingGroups,
  stripMetacharsForName,
  validateNoCycle,
  type AtomFilter,
  type Behavior,
  type FilterId,
  type FilterMode,
  type FilterNode,
  type FiltersState,
  type GroupFilter,
} from '@/lib/filterEngine'
import { cn } from '@/lib/utils'

interface ManageFiltersModalProps {
  isOpen: boolean
  onClose: () => void
}

// Unified draft holds every field — submit-time pruning by `type`.
//
// CFR1: `behavior` lives only on atoms. Groups have no behavior (pure
// boolean combinator over children's evaluations).
interface Draft {
  id: string                   // existing id when editing; freshly-generated for new
  isNew: boolean
  type: 'atom' | 'group'
  name: string
  enabled: boolean
  // atom fields
  patterns: string             // textarea raw, one per line
  behavior: Behavior
  mode: FilterMode
  // group fields
  match: 'all' | 'any'
  childIds: FilterId[]
}

// Stable reference for the "no-references" case below. `referencingById.get()`
// returns `undefined` for nodes with zero referencing groups; we coalesce to
// this frozen constant so the `referencingGroups` prop holds the same identity
// across renders (lets `FilterRow`'s memoization, if any, treat the prop as
// unchanged).
// Cast through `unknown` because Object.freeze([]) is typed
// `readonly never[]`, which TS strict mode refuses to widen to the
// mutable `GroupFilter[]` consumer signature. The runtime value is a
// frozen empty array; downstream callers only read from it (the freeze
// also blocks any accidental mutation), so the lie about mutability is
// inert.
const EMPTY_GROUP_ARRAY: GroupFilter[] = Object.freeze([]) as unknown as GroupFilter[]

function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return 'flt-' + Math.random().toString(36).slice(2, 10)
}

function emptyDraft(): Draft {
  return {
    id: newId(),
    isNew: true,
    type: 'atom',
    name: '',
    enabled: true,
    patterns: '',
    // CFR1: default new atoms to 'hide' — the single most common request
    // from the user ("hide cron1 OR cron2") is a hide filter; show-only
    // is the rarer case (and easier to reason about once selected).
    behavior: 'hide',
    mode: 'glob',
    match: 'all',
    childIds: [],
  }
}

function nodeToDraft(node: FilterNode): Draft {
  if (node.type === 'atom') {
    return {
      id: node.id,
      isNew: false,
      type: 'atom',
      name: node.name,
      enabled: node.enabled,
      patterns: node.patterns.join('\n'),
      behavior: node.behavior,
      mode: node.mode,
      match: 'all',
      childIds: [],
    }
  }
  return {
    id: node.id,
    isNew: false,
    type: 'group',
    name: node.name,
    enabled: node.enabled,
    patterns: '',
    behavior: 'hide',
    mode: 'glob',
    match: node.match,
    childIds: [...node.childIds],
  }
}

/**
 * "Would adding `candidateId` as a child of group with id `groupId` create
 * a cycle?" — DFS from candidate through state.nodes; if groupId is reached,
 * yes. Caveat: if groupId doesn't exist in state.nodes (a brand-new group),
 * no node can reference it, so no cycle is structurally possible.
 */
function wouldCreateCycle(state: FiltersState, groupId: FilterId, candidateId: FilterId): boolean {
  if (candidateId === groupId) return true
  const stack: FilterId[] = [candidateId]
  const seen = new Set<FilterId>()
  while (stack.length > 0) {
    // Hunt #2: while-guard proves stack.length > 0, but Array.pop is
    // typed `T | undefined` regardless. Explicit narrowing instead of
    // the old `stack.pop()!` lie.
    const cur = stack.pop()
    if (cur === undefined) break
    if (seen.has(cur)) continue
    seen.add(cur)
    if (cur === groupId) return true
    const node = state.nodes[cur]
    if (node && node.type === 'group') {
      for (const child of node.childIds) stack.push(child)
    }
  }
  return false
}

export function ManageFiltersModal({ isOpen, onClose }: ManageFiltersModalProps) {
  const { filtersState, addNode, updateNode, removeNode } = useFilters()

  const [draft, setDraft] = useState<Draft | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [saveError, setSaveError] = useState<string | null>(null)
  // Per-row delete UI state: 'idle' | 'blocked' | 'confirm'
  const [deleteUi, setDeleteUi] = useState<Record<FilterId, 'blocked' | 'confirm' | undefined>>({})

  const allNodes = useMemo(
    () => Object.values(filtersState.nodes).sort((a, b) => a.name.localeCompare(b.name)),
    [filtersState.nodes],
  )

  const visibleNodes = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return allNodes
    return allNodes.filter((n) => n.name.toLowerCase().includes(q))
  }, [allNodes, searchQuery])

  // LOW-2 (council follow-up): pre-compute child -> referencing-groups
  // map once per filtersState.nodes change. Before this memo, the row
  // map at L370-396 called `findReferencingGroups(n.id, filtersState)`
  // per visible row per render — O(V * (V+E)) on every keystroke into
  // searchQuery, every draft selection, every deleteUi update. At
  // today's typical filter counts (~5-20 nodes) sub-millisecond, but
  // the cliff is steep and the fix is trivial.
  //
  // Memo key is `filtersState.nodes` (not the full `filtersState`) on
  // the same rationale as `allNodes` above: FilterContext rebuilds
  // `filtersState` identity on activeId / migration-banner / sentinel
  // changes too, but `findReferencingGroups` only reads `nodes`.
  const referencingById = useMemo(() => {
    const map = new Map<FilterId, GroupFilter[]>()
    for (const node of Object.values(filtersState.nodes)) {
      if (node.type !== 'group') continue
      for (const childId of node.childIds) {
        const list = map.get(childId)
        if (list) {
          list.push(node)
        } else {
          map.set(childId, [node])
        }
      }
    }
    return map
  }, [filtersState.nodes])

  const handleClose = () => {
    setDraft(null)
    setSaveError(null)
    setDeleteUi({})
    setSearchQuery('')
    onClose()
  }

  const handleStartCreate = () => {
    setDraft(emptyDraft())
    setSaveError(null)
  }

  const handleSelectRow = (node: FilterNode) => {
    setDraft(nodeToDraft(node))
    setSaveError(null)
    // Clear any open delete UI on selection.
    setDeleteUi({})
  }

  const handleToggleEnabled = (node: FilterNode) => {
    // Sync rule (UX.md "Atom editor: ... synced both directions"): when
    // the row IS the active draft (not isNew), toggling on the row is
    // part of the active edit session and only mutates the draft.
    // Cancel reverts; Save commits. When the row is NOT the active
    // draft, the row toggle is a live action that writes through to
    // the persisted node immediately. This keeps the Save/Cancel
    // transactional contract from leaking into rows that aren't being
    // edited, while still giving instant feedback to the user.
    if (draft && draft.id === node.id && !draft.isNew) {
      setDraft({ ...draft, enabled: !draft.enabled })
      return
    }
    updateNode(node.id, { enabled: !node.enabled })
  }

  const handleRequestDelete = (node: FilterNode) => {
    const refs = findReferencingGroups(node.id, filtersState)
    if (refs.length > 0) {
      setDeleteUi((s) => ({ ...s, [node.id]: 'blocked' }))
    } else {
      setDeleteUi((s) => ({ ...s, [node.id]: 'confirm' }))
    }
  }

  const handleCancelDelete = (id: FilterId) => {
    setDeleteUi((s) => ({ ...s, [id]: undefined }))
  }

  const handleConfirmDelete = (id: FilterId) => {
    removeNode(id)
    setDeleteUi((s) => ({ ...s, [id]: undefined }))
    if (draft && draft.id === id) setDraft(null)
  }

  const handleSave = () => {
    if (!draft) return
    const trimmedName = draft.name.trim()
    if (!trimmedName) {
      setSaveError('Name is required.')
      return
    }
    let prospective: FilterNode
    if (draft.type === 'atom') {
      const patterns = draft.patterns
        .split(/\r?\n/)
        .map((s) => s.trim())
        .filter((s) => s.length > 0)
      const atom: AtomFilter = {
        type: 'atom',
        id: draft.id,
        name: trimmedName,
        enabled: draft.enabled,
        patterns,
        behavior: draft.behavior,
        mode: draft.mode,
        target: 'title',
      }
      prospective = atom
    } else {
      // Strip orphan childIds defensively.
      const cleanChildren = draft.childIds.filter((cid) => cid in filtersState.nodes)
      const group: GroupFilter = {
        type: 'group',
        id: draft.id,
        name: trimmedName,
        enabled: draft.enabled,
        match: draft.match,
        childIds: cleanChildren,
      }
      prospective = group
    }
    // Cycle check on the prospective state.
    const nextNodes = { ...filtersState.nodes, [prospective.id]: prospective }
    const ok = validateNoCycle({ ...filtersState, nodes: nextNodes })
    if (!ok) {
      setSaveError('This change would create a cycle.')
      return
    }

    if (draft.isNew) {
      addNode(prospective)
    } else {
      updateNode(prospective.id, prospective)
    }
    setDraft(null)
    setSaveError(null)
  }

  return (
    <Dialog open={isOpen} onOpenChange={(o) => { if (!o) handleClose() }}>
      <DialogContent className="sm:max-w-4xl" aria-label="Manage filters">
        <DialogHeader>
          <DialogTitle>Manage filters</DialogTitle>
          <DialogDescription>
            Create, edit, and combine saved title-pattern filters for the conversation list.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-1 sm:grid-cols-[18rem_minmax(0,1fr)] gap-4 min-h-[24rem]">
          {/* Left pane */}
          <div className="flex flex-col gap-2 border-r border-zinc-200 dark:border-zinc-800 pr-3 min-w-0">
            <div className="flex gap-2">
              <Input
                data-testid="manage-filters-search"
                placeholder="Search filters"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-8"
              />
            </div>
            <Button
              data-testid="manage-filters-new"
              onClick={handleStartCreate}
              size="sm"
              className="h-8"
            >
              <Plus className="h-3 w-3 mr-1" /> New filter
            </Button>
            <ScrollArea className="flex-1 max-h-[28rem] [&>div>div]:!block">
              {/* The [&>div>div]:!block override drops the Radix Viewport's
                  inner display:table wrapper that otherwise auto-sizes to
                  content width and lets the rows overflow past the Viewport
                  (clipping the Enabled checkbox + trash button at narrow
                  pane widths). */}
              <div className="space-y-1 pr-1" data-testid="manage-filters-list">
                {visibleNodes.length === 0 && (
                  <div className="text-xs text-zinc-500 py-4 text-center">
                    {allNodes.length === 0 ? 'No filters yet.' : 'No matches.'}
                  </div>
                )}
                {visibleNodes.map((n) => {
                  // CFR1 sync rule (UX.md "synced both directions"):
                  // when this row is the active draft, the row's enabled
                  // checkbox reflects the DRAFT value rather than the
                  // node's persisted value. That preserves the
                  // Save/Cancel transactional contract for editor edits
                  // (Cancel reverts the draft AND the row visual)
                  // while still showing the user the immediate visual
                  // effect of toggling either control.
                  const effectiveEnabled =
                    draft && draft.id === n.id && !draft.isNew ? draft.enabled : n.enabled
                  return (
                    <FilterRow
                      key={n.id}
                      node={n}
                      effectiveEnabled={effectiveEnabled}
                      selected={draft?.id === n.id && !draft?.isNew}
                      deleteUi={deleteUi[n.id]}
                      onSelect={() => handleSelectRow(n)}
                      onToggleEnabled={() => handleToggleEnabled(n)}
                      onRequestDelete={() => handleRequestDelete(n)}
                      onCancelDelete={() => handleCancelDelete(n.id)}
                      onConfirmDelete={() => handleConfirmDelete(n.id)}
                      referencingGroups={referencingById.get(n.id) ?? EMPTY_GROUP_ARRAY}
                    />
                  )
                })}
              </div>
            </ScrollArea>
          </div>

          {/* Right pane (editor) */}
          <div data-testid="filter-editor" className="flex flex-col gap-3 min-w-0">
            {!draft ? (
              <div className="text-sm text-zinc-500 flex items-center justify-center h-full py-12">
                Select a filter on the left or click <span className="font-medium mx-1">+ New filter</span> to begin.
              </div>
            ) : (
              <DraftEditor
                draft={draft}
                state={filtersState}
                onChange={setDraft}
                onSave={handleSave}
                onCancel={() => { setDraft(null); setSaveError(null) }}
                saveError={saveError}
              />
            )}
          </div>
        </div>

        <DialogFooter className="mt-2">
          <Button variant="outline" onClick={handleClose}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Filter row (left pane)
// ---------------------------------------------------------------------------

interface FilterRowProps {
  node: FilterNode
  /**
   * Display-time enabled state. May differ from `node.enabled` when this
   * row is the active draft and the user has toggled the editor's
   * Enabled checkbox without saving yet (CFR1 sync rule).
   */
  effectiveEnabled: boolean
  selected: boolean
  deleteUi: 'blocked' | 'confirm' | undefined
  onSelect: () => void
  onToggleEnabled: () => void
  onRequestDelete: () => void
  onCancelDelete: () => void
  onConfirmDelete: () => void
  referencingGroups: GroupFilter[]
}

function FilterRow({
  node,
  effectiveEnabled,
  selected,
  deleteUi,
  onSelect,
  onToggleEnabled,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
  referencingGroups,
}: FilterRowProps) {
  // Phase 1 a11y: the row IS the selection control (no separate
  // "select" button). It contains an enable/disable checkbox and a
  // delete button as inner controls; wrapping the whole row in
  // <button> would produce invalid HTML (button-in-button). Use
  // role="button" + tabIndex + onKeyDown so keyboard users can select
  // filters with Enter/Space, matching the click affordance. The
  // prefer-tag-over-role suppression below is required because the
  // rule can't see the nested-interactive constraint.
  return (
    <div
      data-testid={`filter-row-${node.id}`}
      /* react-doctor-disable-next-line react-doctor/prefer-tag-over-role */
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      className={cn(
        'flex flex-col gap-1 rounded border px-2 py-1.5 text-sm cursor-pointer',
        selected
          ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/30'
          : 'border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900',
      )}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          // Don't hijack Enter/Space when the user is inside an inner
          // control (the toggle checkbox or delete button); those have
          // their own native handlers.
          if (e.target instanceof Element && e.target.closest('input, button')) return
          e.preventDefault()
          onSelect()
        }
      }}
    >
      <div className="flex items-center gap-2 min-w-0">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="font-medium truncate text-zinc-900 dark:text-zinc-100">{node.name || '(unnamed)'}</span>
            <span className="text-[9px] uppercase tracking-wide text-zinc-400 dark:text-zinc-500 shrink-0">
              {node.type}
            </span>
          </div>
        </div>
        {/* Phase 1 a11y side-effect: the row above is now keyboard-
            interactive (role=button), which made this <label>'s
            onClick={stopPropagation} surface a new diagnostic. The
            stopPropagation is deliberate — clicking the toggle
            checkbox should NOT also fire the row's onSelect. The
            corresponding onKeyDown stopPropagation lives in the row's
            handler (the closest('input,button') guard). */}
        {/* react-doctor-disable-next-line react-doctor/no-noninteractive-element-interactions */}
        <label
          className="flex items-center text-xs text-zinc-500 shrink-0"
          onClick={(e) => e.stopPropagation()}
          title={effectiveEnabled ? 'Enabled (click to disable)' : 'Disabled (click to enable)'}
        >
          <input
            type="checkbox"
            data-testid={`filter-row-toggle-${node.id}`}
            checked={effectiveEnabled}
            onChange={onToggleEnabled}
            aria-label={effectiveEnabled ? 'Enabled' : 'Disabled'}
          />
        </label>
        <Button
          variant="ghost"
          size="sm"
          data-testid={`filter-row-delete-${node.id}`}
          onClick={(e) => { e.stopPropagation(); onRequestDelete() }}
          aria-label={`Delete ${node.name}`}
          title={`Delete ${node.name}`}
          className="h-6 w-6 p-0 shrink-0 text-zinc-400 hover:text-red-600 dark:hover:text-red-400"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
      {/* Inline delete state */}
      {deleteUi === 'blocked' && (
        <div
          data-testid={`filter-delete-blocked-${node.id}`}
          aria-live="polite"
          className="text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/30 rounded px-2 py-1"
          onClick={(e) => e.stopPropagation()}
        >
          Can&rsquo;t delete: used by {referencingGroups.map((g) => g.name).join(', ')}. Remove it from those groups first.
          <button
            type="button"
            className="ml-2 underline"
            onClick={onCancelDelete}
            aria-label="Dismiss"
          >Dismiss</button>
        </div>
      )}
      {deleteUi === 'confirm' && (
        <div
          aria-live="polite"
          className="flex items-center gap-2 text-xs"
          onClick={(e) => e.stopPropagation()}
        >
          <span>Delete &ldquo;{node.name}&rdquo;?</span>
          <Button
            variant="outline"
            size="sm"
            onClick={onCancelDelete}
            className="h-6 px-2 text-xs"
            data-testid={`filter-row-delete-cancel-${node.id}`}
          >Cancel</Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={onConfirmDelete}
            className="h-6 px-2 text-xs"
            data-testid={`filter-row-delete-confirm-${node.id}`}
          >Confirm</Button>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Draft editor (right pane)
// ---------------------------------------------------------------------------

interface DraftEditorProps {
  draft: Draft
  state: FiltersState
  onChange: (d: Draft) => void
  onSave: () => void
  onCancel: () => void
  saveError: string | null
}

function DraftEditor({ draft, state, onChange, onSave, onCancel, saveError }: DraftEditorProps) {
  // Sentinels: useRef so they gate the prefill effect without forcing
  // re-renders. They are NEVER persisted.
  // - userEditedNameRef: true once the user types into Name.
  //   Initial value: !draft.isNew (editing existing → don't auto-overwrite),
  //                   false for new drafts.
  // - nameFocusedRef: true while the Name input is focused (don't shift
  //   under the cursor).
  const userEditedNameRef = useRef<boolean>(!draft.isNew)
  const nameFocusedRef = useRef<boolean>(false)

  // Re-initialize sentinels when the draft id changes (selecting a different
  // filter, or starting a new one). Re-keying on id only — pure ref reset.
  const lastDraftIdRef = useRef<string>(draft.id)
  // eslint-disable-next-line react-hooks/refs -- Sync re-init of sentinel refs on draft.id change is structurally required: deferring to useEffect([draft.id]) would create a one-render staleness window where the prefill effect (below) could fire against the previous draft's sentinel values. The "useState + initialValue prop" alternative would re-create state, but these are NEVER persisted refs by design (see comment block above).
  if (lastDraftIdRef.current !== draft.id) {
    // eslint-disable-next-line react-hooks/refs -- Same sync-reset rationale as the read above; this is the cache-update half of the id-change detection.
    lastDraftIdRef.current = draft.id
    // eslint-disable-next-line react-hooks/refs -- Same sync-reset rationale: must reset to the post-id-change initial value in the same render that detects the change.
    userEditedNameRef.current = !draft.isNew
    // eslint-disable-next-line react-hooks/refs -- Same sync-reset rationale.
    nameFocusedRef.current = false
  }

  // Prefill effect — keyed ONLY on patterns string. Refs read inside the
  // closure so focus/edit toggles don't re-run the timer.
  useEffect(() => {
    if (draft.type !== 'atom') return
    const patternsText = draft.patterns
    const t = setTimeout(() => {
      if (userEditedNameRef.current) return
      if (nameFocusedRef.current) return
      const generated = stripMetacharsForName(patternsText)
      if (!generated) return
      // Only update if it would actually change — prevents redundant renders.
      if (generated === draft.name) return
      // Guard against stale fires after draft swap.
      if (lastDraftIdRef.current !== draft.id) return
      onChange({ ...draft, name: generated })
    }, 300)
    return () => clearTimeout(t)
    // We deliberately depend only on patterns + type so focus/edit toggles
    // don't reset the debounce.
    // oxlint-disable-next-line react-doctor/exhaustive-deps -- same rationale: debounce intentionally scoped to (patterns, type); refs are read fresh inside the closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.patterns, draft.type])

  const onNameChange = (v: string) => {
    // Mark as edited when the user types (rather than the prefill writing).
    // Resume on clear: empty value sets the sentinel back to false.
    if (v === '') {
      userEditedNameRef.current = false
    } else {
      userEditedNameRef.current = true
    }
    onChange({ ...draft, name: v })
  }

  const referencing = findReferencingGroups(draft.id, state)

  return (
    <div className="flex flex-col gap-3">
      <div>
        <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1" htmlFor="filter-editor-name">
          Name
        </label>
        <Input
          id="filter-editor-name"
          data-testid="filter-editor-name"
          value={draft.name}
          placeholder={draft.type === 'atom' ? 'Name (auto-filled from first pattern)' : 'Group filter name'}
          onChange={(e) => onNameChange(e.target.value)}
          onFocus={() => { nameFocusedRef.current = true }}
          onBlur={() => { nameFocusedRef.current = false }}
        />
        {referencing.length > 0 && (
          <div
            data-testid="filter-editor-used-by"
            className="text-xs italic text-zinc-500 mt-1"
          >
            Used by: {referencing.map((g) => g.name).join(', ')}
          </div>
        )}
      </div>

      <div className="flex items-center gap-4 text-sm" role="radiogroup" aria-label="Type">
        <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">Type:</span>
        <RadioPill
          name="type"
          value="atom"
          current={draft.type}
          onPick={(v) => onChange({ ...draft, type: v })}
          testId="filter-editor-type-atom"
        >
          Atom
        </RadioPill>
        <RadioPill
          name="type"
          value="group"
          current={draft.type}
          onPick={(v) => onChange({ ...draft, type: v })}
          testId="filter-editor-type-group"
        >
          Group
        </RadioPill>
        <label className="ml-auto flex items-center gap-1 text-xs text-zinc-500">
          <input
            type="checkbox"
            data-testid="filter-editor-enabled"
            aria-label="Enabled"
            checked={draft.enabled}
            onChange={(e) => onChange({ ...draft, enabled: e.target.checked })}
          />
          <span>Enabled</span>
        </label>
      </div>

      {draft.type === 'atom' ? (
        <AtomEditor draft={draft} onChange={onChange} />
      ) : (
        <GroupEditor draft={draft} state={state} onChange={onChange} />
      )}

      {saveError && (
        <div className="text-xs text-red-600 dark:text-red-400">{saveError}</div>
      )}

      <DialogFooter>
        <Button variant="outline" onClick={onCancel} data-testid="filter-editor-cancel">Cancel</Button>
        <Button onClick={onSave} data-testid="filter-editor-save">Save</Button>
      </DialogFooter>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Atom editor body
// ---------------------------------------------------------------------------

function AtomEditor({ draft, onChange }: { draft: Draft; onChange: (d: Draft) => void }) {
  // Plain-English summary. Composes the user's two highest-leverage
  // choices (behavior + patterns) into a single sentence so the editor
  // is self-documenting. CFR1 council convergence: the summary is
  // load-bearing because (a) atoms now compose patterns via OR (an
  // important reinterpretation vs the v1 single-pattern atom) and
  // (b) Hide vs Show-only is the one decision the v1 polarity radio
  // failed to communicate clearly.
  const patternList = draft.patterns
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
  const summary =
    patternList.length === 0
      ? 'This filter has no patterns yet.'
      : draft.behavior === 'hide'
        ? `Hides conversations whose titles match any of: ${patternList.join(', ')}.`
        : `Shows only conversations whose titles match any of: ${patternList.join(', ')}.`

  return (
    <div className="flex flex-col gap-3">
      {/* Behavior is the highest-impact decision — top of the editor. */}
      <div>
        <span className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">Behavior</span>
        <div className="flex gap-2" role="radiogroup" aria-label="Behavior">
          <RadioPill
            name="behavior"
            value="hide"
            current={draft.behavior}
            onPick={(v) => onChange({ ...draft, behavior: v })}
            testId="filter-editor-behavior-hide"
          >Hide matches</RadioPill>
          <RadioPill
            name="behavior"
            value="show-only"
            current={draft.behavior}
            onPick={(v) => onChange({ ...draft, behavior: v })}
            testId="filter-editor-behavior-show-only"
          >Show only matches</RadioPill>
        </div>
      </div>
      <div>
        <span className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">Mode</span>
        <div className="flex gap-2" role="radiogroup" aria-label="Mode">
          <RadioPill
            name="mode"
            value="glob"
            current={draft.mode}
            onPick={(v) => onChange({ ...draft, mode: v })}
            testId="filter-editor-mode-glob"
          >glob</RadioPill>
          <RadioPill
            name="mode"
            value="regex"
            current={draft.mode}
            onPick={(v) => onChange({ ...draft, mode: v })}
            testId="filter-editor-mode-regex"
          >regex</RadioPill>
        </div>
      </div>
      <div>
        <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1" htmlFor="filter-editor-patterns">
          Patterns (one per line)
        </label>
        {/* Phase 1 a11y: the <label htmlFor> two lines up associates
            this textarea correctly; Oxlint's rule misses the cross-
            element association. False positive. */}
        {/* oxlint-disable-next-line react-doctor/control-has-associated-label */}
        <textarea
          id="filter-editor-patterns"
          data-testid="filter-editor-patterns"
          rows={5}
          value={draft.patterns}
          onChange={(e) => onChange({ ...draft, patterns: e.target.value })}
          className="w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm font-mono shadow-sm focus:outline-none focus:ring-1 focus:ring-zinc-400 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-50"
          placeholder={'*react*\n*typescript*'}
        />
        <p
          data-testid="filter-editor-pattern-help"
          className="mt-1 text-xs text-zinc-500 dark:text-zinc-400"
        >
          {draft.mode === 'glob'
            ? 'Glob: * matches any text, ? matches one character, and [abc] matches one of a set. A pattern with them must match the whole title. A pattern without them matches anywhere in the title. A leading ^ or a trailing $ anchors the pattern at that end. Patterns match the exact title text, case-insensitively.'
            : 'Regex: a JavaScript regular expression. Patterns match the exact title text, case-insensitively. An invalid regex matches nothing.'}
        </p>
      </div>
      <div
        data-testid="filter-editor-summary"
        className="text-xs text-zinc-600 dark:text-zinc-400 italic"
      >
        {summary}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Group editor body
// ---------------------------------------------------------------------------

function GroupEditor({ draft, state, onChange }: { draft: Draft; state: FiltersState; onChange: (d: Draft) => void }) {
  // Pending Add-member selection (Select drives this; clicking [Add] commits).
  const [pendingMemberId, setPendingMemberId] = useState<string>('')

  // Cycle-safe candidate set. Disabled candidates DO appear (suffix
  // "(disabled)") so the user can re-enable + add in one flow.
  const candidates = useMemo(() => {
    const out: FilterNode[] = []
    for (const n of Object.values(state.nodes)) {
      if (n.id === draft.id) continue                       // self
      if (draft.childIds.includes(n.id)) continue           // already a member
      if (wouldCreateCycle(state, draft.id, n.id)) continue // cycle
      out.push(n)
    }
    return out.sort((a, b) => a.name.localeCompare(b.name))
  }, [state, draft.id, draft.childIds])

  const memberNodes: Array<{ id: FilterId; node: FilterNode | undefined }> = draft.childIds.map((id) => ({
    id,
    node: state.nodes[id],
  }))

  // CFR1: the v1 "exclude + any" warning is gone. Behavior at the atom
  // level makes the failure mode the warning caught (a group of
  // all-exclude atoms set to "any" passing for everything) no longer
  // reachable in v2.
  //
  // Plain-English summary. Groups in v2 are pure boolean combinators
  // over their children's keep/drop decisions — the summary phrases
  // it that way to reinforce the model.
  const memberNames = memberNodes
    .map(({ node, id }) => node?.name ?? `(missing ${id})`)
    .filter((s) => s.length > 0)
  const groupSummary =
    memberNames.length === 0
      ? 'This group has no members yet.'
      : draft.match === 'all'
        ? `Keeps a conversation only if every member keeps it: ${memberNames.join(', ')}.`
        : `Keeps a conversation if any member keeps it: ${memberNames.join(', ')}.`

  const handleAdd = () => {
    if (!pendingMemberId) return
    if (draft.childIds.includes(pendingMemberId)) {
      setPendingMemberId('')
      return
    }
    onChange({ ...draft, childIds: [...draft.childIds, pendingMemberId] })
    setPendingMemberId('')
  }

  const handleRemove = (id: FilterId) => {
    onChange({ ...draft, childIds: draft.childIds.filter((c) => c !== id) })
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <span className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">Match</span>
        <div className="flex gap-2" role="radiogroup" aria-label="Match">
          <RadioPill
            name="match"
            value="all"
            current={draft.match}
            onPick={(v) => onChange({ ...draft, match: v })}
            testId="filter-editor-match-all"
          >all of these</RadioPill>
          <RadioPill
            name="match"
            value="any"
            current={draft.match}
            onPick={(v) => onChange({ ...draft, match: v })}
            testId="filter-editor-match-any"
          >any of these</RadioPill>
        </div>
      </div>

      <div>
        <span className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">Members</span>
        {memberNodes.length === 0 ? (
          <div className="text-xs italic text-zinc-500">No members yet; add some below.</div>
        ) : (
          <div className="flex flex-wrap gap-1" data-testid="filter-editor-members-list">
            {memberNodes.map(({ id, node }) => (
              <span
                key={id}
                data-testid={`filter-editor-member-${id}`}
                className={cn(
                  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs',
                  node
                    ? 'border-zinc-300 bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-800'
                    : 'border-red-300 bg-red-50 text-red-800 dark:border-red-700 dark:bg-red-950/40 dark:text-red-200',
                )}
              >
                <span className="truncate max-w-[12rem]">{node ? node.name : `(missing ${id})`}</span>
                {node && !node.enabled && (
                  <span className="text-zinc-500">(disabled)</span>
                )}
                <button
                  type="button"
                  data-testid={`filter-editor-member-remove-${id}`}
                  onClick={() => handleRemove(id)}
                  aria-label={`Remove member ${node ? node.name : id}`}
                  className="ml-0.5 text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-end gap-2">
        <div className="flex-1 min-w-0">
          {/* Phase 1 a11y: visible label for the Select; the Radix
              SelectTrigger already carries aria-label="Add member" so
              this <div> is the visual label only. Using <label> here
              would attempt a form association the Select doesn't honor. */}
          <div className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">Add member</div>
          <Select value={pendingMemberId} onValueChange={setPendingMemberId}>
            <SelectTrigger data-testid="filter-editor-add-member-trigger" aria-label="Add member">
              <SelectValue placeholder={candidates.length === 0 ? 'No more filters available' : 'Pick a filter'} />
            </SelectTrigger>
            <SelectContent>
              {candidates.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}{!c.enabled ? ' (disabled)' : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          data-testid="filter-editor-add-member-button"
          onClick={handleAdd}
          disabled={!pendingMemberId}
        >
          Add
        </Button>
      </div>

      <div
        data-testid="filter-editor-summary"
        className="text-xs text-zinc-600 dark:text-zinc-400 italic"
      >
        {groupSummary}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function RadioPill<T extends string>({
  value,
  current,
  onPick,
  testId,
  children,
}: {
  /** Reserved for future native-radio rendering; currently unused. */
  name?: string
  value: T
  current: T
  onPick: (v: T) => void
  testId: string
  children: ReactNode
}) {
  const active = current === value
  // ARIA: the spec ("UX.md § Composable filters") describes these
  // controls as Behavior/Mode/Match "radios". We expose role="radio" +
  // aria-checked so screen readers and Playwright's getByRole('radio')
  // match the contract. NOTE: keyboard interaction is currently
  // Tab+Space (button default), not Arrow-key (standard radiogroup).
  // A future refactor to shadcn RadioGroup would add roving-tabindex
  // for full WCAG compliance.
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      data-testid={testId}
      onClick={() => onPick(value)}
      className={cn(
        'rounded border px-3 py-1 text-sm',
        active
          ? 'border-blue-500 bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200'
          : 'border-zinc-300 dark:border-zinc-700',
      )}
    >
      {children}
    </button>
  )
}
