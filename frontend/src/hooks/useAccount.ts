/**
 * useAccount — the signed-in Anthropic account, for the sidebar footer.
 *
 * Reads `/api/account`. `invalidateAfterFetch` refetches it when a Refresh
 * pipeline completes, because Refresh may capture credentials for a
 * different account. See UX.md → "Signed-in account (sidebar footer)".
 */

import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryClient'

export function useAccount() {
  return useQuery({
    queryKey: queryKeys.account,
    queryFn: ({ signal }) => api.getAccount(signal),
    staleTime: 5 * 60 * 1000, // identity changes only on recapture
  })
}
