import { User } from 'lucide-react'
import { isCredentialsCorrupt, useAccount } from '@/hooks/useAccount'
import type { AccountResponse, ApiError } from '@/lib/types'

/**
 * Initials for the avatar: first letters of the first and last words that
 * contain a letter ("Ada Lovelace @ Engines.co" -> "AE"), one letter for a
 * one-word name.
 */
function initialsFor(name: string): string {
  const words = name
    .split(/\s+/)
    .map((w) => w.match(/\p{L}/u)?.[0])
    .filter((c): c is string => Boolean(c))
  if (words.length === 0) return ''
  const first = words[0]
  const last = words.length > 1 ? words[words.length - 1] : ''
  return (first + last).toUpperCase()
}

interface Lines {
  initials: string
  primary: string
  secondary: string
}

function linesFor(account: AccountResponse): Lines {
  if (!account.authenticated) {
    return { initials: '', primary: 'Not signed in', secondary: 'Click Refresh to sign in' }
  }
  if (account.error) {
    const primary = account.org_name || 'Signed in'
    return { initials: initialsFor(primary), primary, secondary: account.error }
  }
  const name = account.display_name || account.full_name
  const primary = name || account.email || 'Signed in'
  const initials = name ? initialsFor(name) : initialsFor(account.email ?? '')
  const secondary = [account.email, account.org_name].filter(Boolean).join(' · ')
  // With no name the email is already the first line; don't repeat it.
  return {
    initials,
    primary,
    secondary: name ? secondary : account.org_name ?? '',
  }
}

/** The reason from a 503 `{"detail": "..."}` body, else a generic one. */
function corruptReason(error: ApiError): string {
  try {
    const detail = JSON.parse(error.message)?.detail
    if (typeof detail === 'string' && detail) return detail
  } catch {
    // not JSON; fall through
  }
  return 'Credentials file is corrupt. Re-run capture to refresh credentials.'
}

/**
 * The signed-in Anthropic account, shown at the bottom of the sidebar as
 * claude.ai and Claude Desktop do. Contract: UX.md → "Signed-in account
 * (sidebar footer)".
 */
export function AccountBadge() {
  const { data, error } = useAccount()
  // A corrupt credentials file wins over older data: the account that data
  // names is no longer the one Refresh would use.
  let lines: Lines | null = null
  if (isCredentialsCorrupt(error)) {
    lines = { initials: '', primary: 'Account unavailable', secondary: corruptReason(error) }
  } else if (data) {
    lines = linesFor(data)
  }

  return (
    <section
      aria-label="Signed-in account"
      className="flex h-12 min-w-0 shrink-0 items-center gap-3 border-t border-zinc-200 px-4 dark:border-zinc-800"
    >
      {lines && (
        <>
          <div
            aria-hidden="true"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-700 text-xs font-medium text-white dark:bg-zinc-300 dark:text-zinc-900"
          >
            {lines.initials || <User className="h-4 w-4" />}
          </div>
          <div className="min-w-0 flex-1">
            <div
              className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100"
              title={lines.primary}
            >
              {lines.primary}
            </div>
            {lines.secondary && (
              <div
                className="truncate text-xs text-zinc-500 dark:text-zinc-400"
                title={lines.secondary}
              >
                {lines.secondary}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  )
}
