import { describe, it, expect } from 'vitest'
import { QueryClient } from '@tanstack/react-query'

import { invalidateAfterFetch, queryClient, queryKeys } from '../../lib/queryClient'

// 2026-05-18 (Task A6): React Query staleTime/gcTime tuning.
//
// Why: `/api/conversations` (sidebar list) dropped from 5s warm to ~87ms
// warm. The previous 5-minute default staleTime made the sidebar feel
// stale on return-to-app; tighten it now that refetches are cheap.
//
// The config lives in two places to stay introspectable from a single
// QueryClient instance (vs. inline per-hook overrides that can drift):
//
//   1. defaultOptions.queries.{staleTime, gcTime} on the QueryClient.
//   2. setQueryDefaults() for the conversations.list / conversations.detail
//      query-key prefixes.
//
// This test is the executable contract for those values.
describe('queryClient configuration (Task A6)', () => {
  it('default staleTime is 30 seconds', () => {
    const defaults = queryClient.getDefaultOptions()
    expect(defaults.queries?.staleTime).toBe(30 * 1000)
  })

  it('default gcTime is 10 minutes', () => {
    const defaults = queryClient.getDefaultOptions()
    // React Query v5 renamed cacheTime → gcTime.
    expect(defaults.queries?.gcTime).toBe(10 * 60 * 1000)
  })

  it('conversations.list staleTime is 30 seconds', () => {
    const opts = queryClient.getQueryDefaults(['conversations', 'list'])
    expect(opts.staleTime).toBe(30 * 1000)
  })

  it('conversations.detail staleTime is 5 minutes', () => {
    const opts = queryClient.getQueryDefaults(['conversations', 'detail'])
    expect(opts.staleTime).toBe(5 * 60 * 1000)
  })
})

// 2026-05-18 (type-assertion-lies audit): the retry callback used to read
// `(error as any).status` — `as any` disables the type checker, so a
// non-numeric `status` (e.g. the string '404' from a hand-rolled fake)
// silently slipped through `=== 404` as false and we'd retry instead of
// short-circuiting. These tests pin the contract so the narrowing rewrite
// stays honest: only Errors with a numeric `status` of 404 short-circuit.
describe('queryClient retry behavior (type-assertion-lies audit)', () => {
  function retryFn() {
    const opts = queryClient.getDefaultOptions()
    const retry = opts.queries?.retry
    if (typeof retry !== 'function') throw new Error('expected retry function')
    return retry
  }

  it('does NOT retry when error is Error with numeric status 404', () => {
    const err = Object.assign(new Error('not found'), { status: 404 })
    expect(retryFn()(0, err)).toBe(false)
  })

  it('DOES retry when error is Error with status as a string "404"', () => {
    // The whole point of dropping `as any`: typeof status === 'number' must
    // be required. A string '404' is malformed; we keep retrying.
    const err = Object.assign(new Error('boom'), { status: '404' })
    expect(retryFn()(0, err)).not.toBe(false)
  })

  it('DOES retry when error is a plain object with status 404 (not instanceof Error)', () => {
    // instanceof Error must remain part of the guard; the previous cast
    // didn't check this, the narrowed form must.
    const err = { status: 404, message: 'not found' } as unknown as Error
    expect(retryFn()(0, err)).not.toBe(false)
  })

  it('DOES retry when error has no status field', () => {
    const err = new Error('network')
    expect(retryFn()(0, err)).not.toBe(false)
  })

  it('stops retrying after 5 attempts on retriable errors', () => {
    const err = new Error('network')
    expect(retryFn()(5, err)).toBe(false)
  })
})

// 2026-09-23: after the sidebar Refresh fetched a new Desktop conversation,
// the Conversation List showed it but the Search Pane kept its old results.
// A repeat of the same search also showed the old results, because the
// search query stays fresh for 60 s. Every fetch completion must mark the
// search cache stale together with the conversations cache.
describe('invalidateAfterFetch', () => {
  it('marks the conversations and the search queries stale', async () => {
    const qc = new QueryClient()
    const searchKey = queryKeys.search('hermes', 'all', 'snippet')
    const listKey = queryKeys.conversations.list({})
    const configKey = queryKeys.config
    qc.setQueryData(searchKey, { results: [] })
    qc.setQueryData(listKey, [])
    qc.setQueryData(configKey, {})

    await invalidateAfterFetch(qc)

    expect(qc.getQueryState(searchKey)?.isInvalidated).toBe(true)
    expect(qc.getQueryState(listKey)?.isInvalidated).toBe(true)
    // Unrelated queries keep their cache.
    expect(qc.getQueryState(configKey)?.isInvalidated).toBe(false)
  })
})
