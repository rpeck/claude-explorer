/**
 * searchNavigation — the newest search jump wins (2026-10-23).
 *
 * Two jumps 50 ms apart: the first took the URL path (`?highlight=`), the
 * second the fast path (direct scroll, no URL change). On a slow machine,
 * useScrollToHighlight processed the first jump's `?highlight=` after the
 * second jump and scrolled back to the older hit. Each jump now starts a
 * new search navigation; a URL highlight from an older one is stale.
 */
import { describe, it, expect, beforeEach } from 'vitest'

import {
  beginSearchNavigation,
  consumeStaleUrlHighlight,
  isStaleUrlHighlight,
  __resetSearchNavigationForTests,
} from '@/lib/searchNavigation'

describe('searchNavigation', () => {
  beforeEach(() => __resetSearchNavigationForTests())

  it('a URL highlight from the newest jump is current', () => {
    beginSearchNavigation('late')
    expect(isStaleUrlHighlight('late')).toBe(false)
  })

  it('a newer fast-path jump makes the older URL highlight stale', () => {
    beginSearchNavigation('late')
    beginSearchNavigation(null) // fast path: no URL change
    expect(isStaleUrlHighlight('late')).toBe(true)
  })

  it('a newer URL jump makes the older URL highlight stale, not its own', () => {
    beginSearchNavigation('late')
    beginSearchNavigation('early')
    expect(isStaleUrlHighlight('late')).toBe(false) // not the recorded target any more
    expect(isStaleUrlHighlight('early')).toBe(false)
  })

  it('a highlight that no search jump made (a deep link, a bookmark) is never stale', () => {
    expect(isStaleUrlHighlight('pasted')).toBe(false)
    beginSearchNavigation(null)
    expect(isStaleUrlHighlight('pasted')).toBe(false)
  })

  it('a consumed stale highlight does not block a later link to the same message', () => {
    beginSearchNavigation('late')
    beginSearchNavigation(null)
    expect(isStaleUrlHighlight('late')).toBe(true)
    consumeStaleUrlHighlight()
    expect(isStaleUrlHighlight('late')).toBe(false)
  })
})
