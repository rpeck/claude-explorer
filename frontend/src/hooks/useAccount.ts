/**
 * useAccount — the signed-in Anthropic account, for the sidebar footer.
 *
 * Reads `/api/account`. See UX.md → "Signed-in account (sidebar footer)".
 *
 * Freshness:
 *   - `invalidateAfterCapture` refetches it when Refresh captures new
 *     credentials, and `invalidateAfterFetch` when a Refresh completes.
 *     Refresh may capture credentials for a different account.
 *   - A failed lookup (`data.error`, e.g. offline start) is retried by the
 *     backend after 60 s, so the badge polls every 60 s until it succeeds.
 *     A successful lookup keeps the 5-minute staleTime.
 *   - A 503 (corrupt credentials file) is not retried: the file does not
 *     fix itself, and the badge shows the reason at once.
 */

import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/queryClient'
import { ApiError } from '@/lib/types'

/** Matches the backend's retry of a failed lookup (`FAILURE_TTL`). */
export const ACCOUNT_ERROR_REFETCH_MS = 60 * 1000

export function isCredentialsCorrupt(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 503
}

export function useAccount() {
  return useQuery({
    queryKey: queryKeys.account,
    queryFn: ({ signal }) => api.getAccount(signal),
    staleTime: 5 * 60 * 1000, // identity changes only on recapture
    refetchInterval: (query) =>
      query.state.data?.error ? ACCOUNT_ERROR_REFETCH_MS : false,
    retry: (failureCount, error) => !isCredentialsCorrupt(error) && failureCount < 5,
  })
}
