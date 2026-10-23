/**
 * The newest search jump wins (2026-10-23).
 *
 * A search jump takes one of two paths (`components/search/navigateToMatch.ts`):
 *   - the fast path scrolls straight to a mounted bubble and leaves the URL;
 *   - the URL path sets `?highlight=<uuid>`, and `useScrollToHighlight`
 *     scrolls once the bubble mounts.
 *
 * Two jumps 50 ms apart could therefore finish in the wrong order: on a slow
 * machine, the first jump's `?highlight=` was processed after the second
 * jump's fast-path scroll, and the view went back to the older hit.
 *
 * Each jump starts a new search navigation. A URL highlight that an OLDER
 * navigation set is stale: `useScrollToHighlight` skips it. A highlight that
 * no search jump set (a pasted deep link, a bookmark) is never stale. The
 * URL alone could not tell, because under load the page may not render
 * between two clicks, so the second click can read an old URL.
 */

let currentSeq = 0
let urlJump: { seq: number; uuid: string } | null = null

/**
 * Start a search navigation. Pass the message uuid when the jump takes the
 * URL path (`?highlight=`), or `null` for a fast-path jump.
 */
export function beginSearchNavigation(urlTarget: string | null): void {
  currentSeq += 1
  if (urlTarget !== null) urlJump = { seq: currentSeq, uuid: urlTarget }
}

/** True when an older search jump set `?highlight=<uuid>`. */
export function isStaleUrlHighlight(uuid: string): boolean {
  return urlJump !== null && urlJump.uuid === uuid && urlJump.seq !== currentSeq
}

/**
 * Forget the stale URL highlight once it has been skipped, so that a later
 * link to the same message (a bookmark, a pasted URL) works again.
 */
export function consumeStaleUrlHighlight(): void {
  if (urlJump !== null && urlJump.seq !== currentSeq) urlJump = null
}

export function __resetSearchNavigationForTests(): void {
  currentSeq = 0
  urlJump = null
}
