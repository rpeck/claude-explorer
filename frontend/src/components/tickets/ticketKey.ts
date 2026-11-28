/**
 * Issue #93: a ticket key is "owner/repo#84", "#84" when the repo is not
 * known, or a Linear or Jira key such as "ENG-123". The GitHub page path uses
 * "_/_" for the unknown repo; a Linear or Jira key has /tickets/key/<KEY>.
 */
export interface TicketParts {
  repo: string | null
  number: string
  /** Set for a Linear or Jira key ("ENG"). */
  prefix?: string
}

const GITHUB_KEY_RE = /^(?:([^/#\s]+\/[^/#\s]+))?#(\d+)$/
const SYSTEM_KEY_RE = /^([A-Z][A-Z0-9_]*)-(\d+)$/

export function splitTicketKey(key: string): TicketParts | null {
  const m = key.match(GITHUB_KEY_RE)
  if (m) return { repo: m[1] ?? null, number: m[2] }
  const k = key.match(SYSTEM_KEY_RE)
  return k ? { repo: null, number: k[2], prefix: k[1] } : null
}

export function ticketPath(key: string): string | null {
  const parts = splitTicketKey(key)
  if (!parts) return null
  if (parts.prefix) return '/tickets/key/' + encodeURIComponent(key)
  return '/tickets/' + (parts.repo ?? '_/_') + '/' + parts.number
}

export function ticketLabel(key: string): string {
  const parts = splitTicketKey(key)
  return parts && !parts.repo && !parts.prefix ? '#' + parts.number + ' (unknown repo)' : key
}
