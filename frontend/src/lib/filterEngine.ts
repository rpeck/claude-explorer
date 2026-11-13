/**
 * Title-based filter engine.
 *
 * Two coexisting layers:
 *
 * 1. Legacy flat-list (Build-5/6): a `Filter` has patterns, polarity, mode,
 *    target. `applyFilters(items, filters)` AND-composes a list of filters.
 *    Atoms in the new graph reuse the `patternMatches` primitive from here.
 *
 * 2. Composable graph (CF1, 2026-05-07): every saved filter is a named
 *    `FilterNode` — either an `AtomFilter` or a `GroupFilter`. Exactly one
 *    filter is "active" via `FiltersState.activeId`. The evaluator is
 *    cycle-safe and drops disabled children at the GROUP level (not via
 *    early-return) so `match: 'any'` groups don't short-circuit on a
 *    disabled member.
 *
 *    v2 (CFR1, 2026-05-07): the council reconvened after the v1 polarity
 *    radio failed first-contact UX (the "hide cron1 OR cron2" case took 5
 *    clicks across 2 screens). v2 attribute renames + invariant:
 *      - Atoms carry `behavior: 'hide' | 'show-only'` (replaces
 *        `polarity: 'include' | 'exclude'`). Atoms still hold an OR'd
 *        patterns list — multiple patterns are now first-class.
 *      - Groups are pure boolean combinators: `match: 'all' | 'any'`
 *        composes the children's PASSES results. Groups DO NOT carry
 *        behavior. (Council deferred adding group-level NOT until a real
 *        user story demands it.)
 *      - Predicate: each atom's `evaluate` returns the keep/drop decision
 *        (apply behavior); each group's `evaluate` ANDs/ORs its children's
 *        evaluations. This preserves the CF1 invariant unchanged.
 *
 *    The plan-as-written had behavior on every node + a `rawMatches` /
 *    `passes` decomposition. Council (Gemini + GPT-5.2, 2026-05-07 second
 *    pass) converged on behaviorless groups instead, because rawMatches
 *    composition broke the documented recovery case
 *    "show meetings, hide cancelled = Group(show-only, all,
 *    [Atom(show-only meetings), Atom(hide cancelled)])": rawMatches AND
 *    over the children's PATTERN matches resolves to "show only items
 *    that are BOTH meeting AND cancelled", which is the opposite of
 *    intent. The compose-passes invariant gets it right by construction.
 *
 * Glob mode: shell-style globbing (`*`, `?`, `[abc]`); patterns without
 * wildcards become substring matches. A leading `^` or a trailing `$`
 * anchors the pattern at that end. Regex mode: case-insensitive JS
 * RegExp; invalid regexes treated as no-match (caller validates).
 */

export type FilterPolarity = 'include' | 'exclude'
export type Behavior = 'hide' | 'show-only'
export type FilterMode = 'glob' | 'regex'
export type FilterTarget = 'title'

// ---------------------------------------------------------------------------
// Legacy single-filter shape (still used internally by the new AtomFilter).
// ---------------------------------------------------------------------------

export interface Filter {
  id: string
  name: string
  patterns: string[]
  polarity: FilterPolarity
  mode: FilterMode
  target: FilterTarget
  // Note: `pinned` was dropped in CF1. Kept off the type so the compiler
  // surfaces any leftover reads as errors.
}

function escapeRegex(s: string): string {
  return s.replace(/[.+^${}()|\\]/g, '\\$&')
}

/**
 * A leading `^` and a trailing `$` anchor the glob at the start and the end
 * of the title, as in a regex (issue #42). Elsewhere they are literal
 * characters. A pattern of anchors only (`^`, `$`, `^$`) has no text to
 * anchor, so it stays literal, as it was before anchors existed.
 */
export function globToRegex(raw: string): RegExp {
  const start = raw.startsWith('^')
  const end = raw.length > (start ? 1 : 0) && raw.endsWith('$')
  const stripped = raw.slice(start ? 1 : 0, end ? -1 : undefined)
  const anchorStart = start && stripped.length > 0
  const anchorEnd = end && stripped.length > 0
  const pattern = stripped.length > 0 ? stripped : raw
  if (!pattern.includes('*') && !pattern.includes('?') && !pattern.includes('[')) {
    // The wildcard branch below already matches the whole title, so the
    // anchors matter only here.
    const body = escapeRegex(pattern)
    return new RegExp((anchorStart ? '^' : '') + body + (anchorEnd ? '$' : ''), 'i')
  }
  let out = ''
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i]
    if (ch === '*') out += '.*'
    else if (ch === '?') out += '.'
    else if (ch === '[') {
      // copy class until ]
      let j = i + 1
      while (j < pattern.length && pattern[j] !== ']') j++
      out += pattern.slice(i, j + 1)
      i = j
    } else {
      out += escapeRegex(ch)
    }
  }
  return new RegExp('^' + out + '$', 'i')
}

function compilePattern(pattern: string, mode: FilterMode): RegExp | null {
  try {
    if (mode === 'glob') return globToRegex(pattern)
    return new RegExp(pattern, 'i')
  } catch {
    return null
  }
}

export function patternMatches(text: string, pattern: string, mode: FilterMode): boolean {
  const re = compilePattern(pattern, mode)
  if (!re) return false
  return re.test(text)
}

export function filterPasses(text: string, filter: Filter): boolean {
  if (filter.patterns.length === 0) return true
  const anyMatch = filter.patterns.some((p) => patternMatches(text, p, filter.mode))
  return filter.polarity === 'include' ? anyMatch : !anyMatch
}

export function applyFilters<T extends { name: string }>(items: T[], filters: Filter[]): T[] {
  if (filters.length === 0) return items
  return items.filter((item) => filters.every((f) => filterPasses(item.name, f)))
}

export function parseCommaPatterns(raw: string): string[] {
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

// ---------------------------------------------------------------------------
// Composable graph (CF1, 2026-05-07).
// ---------------------------------------------------------------------------

export type FilterId = string

export interface BaseFilterNode {
  id: FilterId
  name: string
  enabled: boolean
}

export interface AtomFilter extends BaseFilterNode {
  type: 'atom'
  patterns: string[]
  /**
   * v2 (CFR1): replaces `polarity`. Hide → drop matches; show-only → keep
   * only matches. The legacy mapping (1:1) is exclude → hide, include →
   * show-only. The migration in FilterContext.tsx normalizes any v1 data
   * carrying `polarity` into `behavior` and drops the `polarity` key.
   */
  behavior: Behavior
  mode: FilterMode
  target: FilterTarget
}

export interface GroupFilter extends BaseFilterNode {
  type: 'group'
  match: 'all' | 'any'
  childIds: FilterId[]
}

export type FilterNode = AtomFilter | GroupFilter

export interface FiltersState {
  nodes: Record<FilterId, FilterNode>
  activeId: FilterId | null
  /**
   * Sentinel set true once the legacy → composable migration has run.
   * The migration is idempotent and skipped on subsequent mounts.
   */
  _migratedV1?: boolean
  /**
   * v2 (CFR1) sentinel: set true once the polarity → behavior rename has
   * run. Distinct from `_migratedV1` so we can track both phases on disk.
   * The v2 migration runs iff `_migratedV1 === true` AND `_migratedV2 !==
   * true` AND any atom node still carries `polarity` (or lacks
   * `behavior`). Idempotent.
   */
  _migratedV2?: boolean
  /**
   * CF3: dismissal flag for the one-time migration banner. Lives in the
   * same blob as `_migratedV1` so the migration sentinel and the dismiss
   * flag travel together (one preference key, one lifecycle event).
   * The banner renders iff `_migratedV1 === true && !migrationBannerDismissed`.
   */
  migrationBannerDismissed?: boolean
}

/**
 * Predicate composition with cycle defense.
 *
 * Invariant (council convergence, 2026-05-07 second pass):
 *   - Atoms are the ONLY nodes that decide keep/drop. Behavior applies
 *     here: `show-only` keeps when any pattern matches; `hide` keeps
 *     when no pattern matches.
 *   - Groups are pure boolean combinators over their children's
 *     keep/drop decisions (recurse via `evaluate`, then AND/OR via
 *     `match`). Groups DO NOT carry behavior.
 *   - Empty atom (no patterns): vacuously passes.
 *   - Empty group (no enabled children): vacuously passes.
 *
 * Council fix (Gemini, 2026-05-07 first pass, retained): the `enabled`
 * check is applied at the GROUP's child step, NOT as an early-return
 * inside evaluate(). If we early-returned `true` for a disabled node, a
 * `match: 'any'` group containing one disabled member would pass for
 * every conversation (`some()` short-circuits on the first true). The
 * right semantic is "disabled members are removed before the group's
 * quantifier runs".
 *
 * Why no `behavior` on groups: see the module header. The plan-as-
 * written had `rawMatches` composition + behavior on every node, but
 * council (2026-05-07 second pass) found that the rawMatches model
 * cannot represent "show meetings, hide cancelled" without inverting
 * its meaning. Compose-passes (this implementation) gets it right.
 */
export function evaluate(
  node: FilterNode,
  text: string,
  state: FiltersState,
  visited: Set<FilterId> = new Set(),
): boolean {
  if (visited.has(node.id)) return true // cycle: no-op (also caught at save time)
  const nextVisited = new Set(visited)
  nextVisited.add(node.id)

  if (node.type === 'atom') {
    if (node.patterns.length === 0) return true
    const hit = node.patterns.some((p) => patternMatches(text, p, node.mode))
    return node.behavior === 'show-only' ? hit : !hit
  }

  // Group: drop orphans AND disabled children before applying the
  // quantifier. THIS is where `enabled` gates evaluation.
  const children = node.childIds
    .map((id) => state.nodes[id])
    .filter((c): c is FilterNode => Boolean(c) && c.enabled)
  if (children.length === 0) return true
  if (node.match === 'all') {
    return children.every((c) => evaluate(c, text, state, nextVisited))
  }
  return children.some((c) => evaluate(c, text, state, nextVisited))
}

/**
 * Top-level wrapper for the active filter.
 * - null activeId  → no filter active (all texts pass)
 * - missing/disabled active node → treated as no filter active
 */
export function applyActiveFilter(text: string, state: FiltersState): boolean {
  if (!state.activeId) return true
  const node = state.nodes[state.activeId]
  if (!node || !node.enabled) return true
  return evaluate(node, text, state)
}

/**
 * Save-time cycle check. Returns true when the graph is acyclic, false if
 * any group transitively references itself.
 */
export function validateNoCycle(state: FiltersState): boolean {
  const WHITE = 0
  const GRAY = 1
  const BLACK = 2
  const color: Record<FilterId, number> = {}
  for (const id of Object.keys(state.nodes)) color[id] = WHITE

  function dfs(id: FilterId): boolean {
    const node = state.nodes[id]
    if (!node) return true // orphan — no edges, so no cycle through it
    if (color[id] === GRAY) return false // back-edge: cycle
    if (color[id] === BLACK) return true
    color[id] = GRAY
    if (node.type === 'group') {
      for (const childId of node.childIds) {
        if (!dfs(childId)) return false
      }
    }
    color[id] = BLACK
    return true
  }

  for (const id of Object.keys(state.nodes)) {
    if (!dfs(id)) return false
  }
  return true
}

/**
 * Returns every group that references `targetId` as a direct child.
 * Used for the "Used by:" line in the modal and for delete-block UX.
 */
export function findReferencingGroups(targetId: FilterId, state: FiltersState): GroupFilter[] {
  const out: GroupFilter[] = []
  for (const node of Object.values(state.nodes)) {
    if (node.type === 'group' && node.childIds.includes(targetId)) {
      out.push(node)
    }
  }
  return out
}

/**
 * Strip glob/regex meta-characters and surrounding whitespace from a single
 * line, returning a usable label or '' if nothing usable remains.
 */
function stripMetacharsSingle(raw: string): string {
  return raw
    .replace(/\\(.)/g, '$1')          // unescape \X -> X
    .replace(/\[[^\]]*\]/g, '')       // drop character classes [...]
    .replace(/[*?^$]/g, '')           // drop standalone glob/regex anchors
    .trim()
}

/**
 * Multi-line/multi-pattern fallback: try each pattern in order, returning
 * the first stripped result that has ≥3 alphanumeric characters. Falls back
 * to '' when nothing qualifies.
 */
export function stripMetacharsForName(raw: string): string {
  // Accept either newline-separated patterns or a single one.
  const lines = raw.split(/\r?\n/)
  for (const line of lines) {
    const stripped = stripMetacharsSingle(line)
    if (!stripped) continue
    const alnum = stripped.replace(/[^a-zA-Z0-9]/g, '')
    if (alnum.length >= 3) return stripped
  }
  return ''
}
