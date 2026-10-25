import { useCallback, useMemo } from 'react'
import { useSearchParams, useLocation, useNavigate, useParams } from 'react-router'
import type { FilterMode } from '@/lib/filterEngine'

/**
 * URL-derived transient filter state for the conversation sidebar (Build-6).
 *
 * Reads `?q=`, `?title=`, `?filterMode=`, `?project=`, `?path=` from the
 * current URL, plus the `:projectSlug` path param for `/projects/:projectSlug`.
 */
export interface UrlFilters {
  q: string
  title: string
  filterMode: FilterMode
  project: string
  /** `?path=`: one exact `project_path` (UX.md → "Conversation List filters"). */
  path: string
}

export function useUrlFilters(): UrlFilters {
  const [params] = useSearchParams()
  const location = useLocation()
  const pathParams = useParams()

  return useMemo(() => {
    const q = params.get('q') ?? ''
    const title = params.get('title') ?? ''
    const rawMode = params.get('filterMode')
    const filterMode: FilterMode = rawMode === 'regex' ? 'regex' : 'glob'
    // Only one of the two is active; a path wins over a stale project.
    const path = params.get('path') ?? ''

    let project = path ? '' : (params.get('project') ?? '')
    if (!project && !path && location.pathname.startsWith('/projects/')) {
      project = pathParams.projectSlug ?? ''
    }
    return { q, title, filterMode, project, path }
  }, [params, location.pathname, pathParams.projectSlug])
}

/**
 * Set the list's project or path filter, or clear both. Only one of the
 * two is active. Every other URL parameter stays.
 *
 * Builds on `window.location`, not on the router's search params: the
 * scope pin writes `?pin=` with `history.replaceState` (SearchPinContext),
 * which the router does not see, and a router-based write would drop it.
 */
export function useSetListProjectFilter(): (next: { project?: string; path?: string }) => void {
  const navigate = useNavigate()
  return useCallback(
    (next) => {
      const url = new URL(window.location.href)
      url.searchParams.delete('project')
      url.searchParams.delete('path')
      if (next.path) url.searchParams.set('path', next.path)
      else if (next.project) url.searchParams.set('project', next.project)
      // `/projects/:slug` holds a project in the path itself. Leave that
      // route, or "All projects" could not clear the filter.
      const pathname = url.pathname.startsWith('/projects/') ? '/conversations' : url.pathname
      navigate({ pathname, search: url.search, hash: url.hash }, { replace: true })
    },
    [navigate],
  )
}
