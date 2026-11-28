/**
 * Issue #92: the query keys and the hook of the Machines settings.
 * UX.md → "Machines".
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export const machinesKeys = {
  all: ['machines'] as const,
  rules: ['machines', 'export-rules'] as const,
  preview: ['machines', 'export-preview'] as const,
}

/** The machines in this archive. The list and the viewer show a machine
 *  chip only when there are two or more. */
export function useMachines() {
  return useQuery({
    queryKey: machinesKeys.all,
    queryFn: ({ signal }) => api.getMachines(signal),
    staleTime: 60_000,
  })
}
