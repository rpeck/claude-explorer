/**
 * Council fix (2026-05-22) — `useSearch` per-keystroke cancellation.
 *
 * The user's explicit requirement: "at most one search active at a time
 * PER CLIENT instance. A second search from the same client should
 * cancel the prior one before starting."
 *
 * This pins the contract that when the user types a second character
 * while the first search is still in flight, the FIRST search's
 * AbortSignal is fired and the new search's AbortSignal is NOT.
 *
 * Mechanism: React Query v5 with `queryFn: ({ signal }) =>
 * api.search(..., signal)`. When the queryKey changes (debouncedQuery
 * updates), React Query aborts the prior observer's in-flight fetch
 * via the AbortSignal passed to queryFn.
 *
 * The existing `useSearch.abort.test.tsx` covers the unmount-cancels
 * branch (Hunt #5). This file covers the SAME-component
 * keystroke-cancels-prior branch — which is the user-facing UX
 * property the council fix's spec mandates.
 */

import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';

import { server } from '../mocks/server';
import { useSearch } from '../../hooks/useConversations';

function makeWrapper() {
  // Mirror PRODUCTION cache TTLs (staleTime: 60s, gcTime: 5min) so the
  // test catches the live behavior, not the convenient-test behavior.
  //
  // History (2026-05-22): an earlier version of this file used
  // ``staleTime: 0, gcTime: 0`` and the test passed even when the
  // production app was NOT actually cancelling on queryKey change.
  // React Query v5 only auto-aborts via AbortController when an
  // observer is garbage-collected; with ``gcTime: 0`` that's
  // instantaneous, so the test saw the abort. With production's
  // ``gcTime: 5min``, the old query stays alive in the cache and
  // its in-flight fetch is NOT cancelled. Without an explicit
  // ``cancelQueries`` call in the hook's debounce cleanup, the
  // backend would keep grinding on results the user no longer
  // cares about — directly violating the user's spec that "a
  // second search from the same client should cancel the prior
  // one before starting." Mirroring production TTLs in the test
  // wrapper is the bidirectional-verification pattern from
  // TESTING.md §5.13.
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 60 * 1000, gcTime: 5 * 60 * 1000 },
      mutations: { retry: false },
    },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }
  return Wrapper;
}

describe('useSearch — per-keystroke cancellation (council 2026-05-22)', () => {
  it('aborts the prior in-flight search when the query changes mid-flight', async () => {
    // Track each request's signal so we can assert which got aborted.
    // Map by ?q= value (which is the only thing that differs).
    const signalByQ = new Map<string, AbortSignal>();
    const receivedQ: string[] = [];

    server.use(
      http.get('/api/search', async ({ request }) => {
        const url = new URL(request.url);
        const q = url.searchParams.get('q') ?? '';
        receivedQ.push(q);
        signalByQ.set(q, request.signal);
        // Hang long enough for the second keystroke + debounce to fire
        // and React Query to abort us.
        await new Promise((resolve) => setTimeout(resolve, 5000));
        return HttpResponse.json([]);
      }),
    );

    const Wrapper = makeWrapper();

    // First render with query="ab" (length >= 2 → past the
    // `enabled: debouncedQuery.length >= 2` gate in useSearch).
    const { rerender } = renderHook(
      ({ q }: { q: string }) =>
        useSearch(q, 'all', 'snippet', 'updated_at', 'desc', undefined, true, true),
      {
        wrapper: Wrapper,
        initialProps: { q: 'ab' },
      },
    );

    // Wait for the first request to reach MSW (past the 200ms debounce).
    await waitFor(
      () => expect(receivedQ).toContain('ab'),
      { timeout: 2000 },
    );
    expect(signalByQ.get('ab')!.aborted).toBe(false);

    // Type more characters — React state changes from "ab" to "abc".
    // The 200ms debounce will fire, queryKey changes from [..., 'ab', ...]
    // to [..., 'abc', ...], React Query cancels the prior observer's
    // queryFn (which aborts its AbortController), and fires a new
    // queryFn for "abc".
    rerender({ q: 'abc' });

    // The new query must reach MSW.
    await waitFor(
      () => expect(receivedQ).toContain('abc'),
      { timeout: 2000 },
    );

    // INVARIANT 1 — the prior 'ab' search MUST have been aborted.
    await waitFor(
      () => expect(signalByQ.get('ab')!.aborted).toBe(true),
      { timeout: 2000 },
    );

    // INVARIANT 2 — the new 'abc' search MUST still be in flight (not
    // aborted). This guards against an over-eager cancellation that
    // would also kill the new request.
    expect(signalByQ.get('abc')!.aborted).toBe(false);
  });

  it('cancelQueries fires AbortSignal on in-flight /api/search (mechanism check)', async () => {
    // Direct mechanism test, complementary to the higher-level
    // "rerender + observe" test above. This bypasses React Query's
    // observer machinery and exercises the same cancelQueries path
    // the hook's cleanup uses. Pins the contract that:
    //
    //   queryClient.cancelQueries({ queryKey: ['search'] })
    //
    // actually propagates ``AbortController.abort()`` to the
    // ``signal`` we passed into the queryFn — for queries with
    // prefix ``['search']``.
    //
    // Live-Playwright observation (2026-05-22) confirmed this
    // mechanism works directly against a fetchQuery-launched query
    // (cancelQueries → signal.aborted=true → fetch rejects with
    // AbortError). This unit test pins that mechanism so a future
    // change to the queryKey shape (e.g. nesting the key under a
    // different prefix) doesn't silently break it.
    const qc = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 60 * 1000, gcTime: 5 * 60 * 1000 },
      },
    });

    let observedSignal: AbortSignal | undefined;
    const fetchStarted = vi.fn();
    server.use(
      http.get('/api/search', async ({ request }) => {
        fetchStarted();
        observedSignal = request.signal;
        await new Promise((resolve) => setTimeout(resolve, 5000));
        return HttpResponse.json([]);
      }),
    );

    // Kick off a fetchQuery; do NOT await it.
    const fetchPromise = qc.fetchQuery({
      queryKey: ['search', 'mythical-creature'],
      queryFn: async ({ signal }) => {
        const r = await fetch('/api/search?q=mythical-creature', { signal });
        return r.json();
      },
    });

    // Wait for the fetch to reach MSW.
    await new Promise<void>((resolve) => {
      const i = setInterval(() => {
        if (fetchStarted.mock.calls.length > 0) {
          clearInterval(i);
          resolve();
        }
      }, 10);
    });
    expect(observedSignal).toBeDefined();
    expect(observedSignal!.aborted).toBe(false);

    // Fire the same cancel call the hook's cleanup uses.
    await qc.cancelQueries({ queryKey: ['search'] });

    // Signal MUST be aborted, fetch MUST reject.
    expect(observedSignal!.aborted).toBe(true);
    await expect(fetchPromise).rejects.toBeDefined();
  });

  it('two QueryClient instances (= two browser tabs) search independently', async () => {
    // Spec from the user: "Multiple frontend instances per backend
    // (e.g., user opens the app in two browser tabs). Each instance
    // must be able to have its own search in flight."
    //
    // Two QueryClient instances ≈ two tabs (each tab has its own
    // QueryClientProvider in app/root.tsx). The invariant is that
    // cancelling tab A's search must NOT cancel tab B's search.

    const signalByCallId: Array<{ q: string; signal: AbortSignal }> = [];

    server.use(
      http.get('/api/search', async ({ request }) => {
        const url = new URL(request.url);
        const q = url.searchParams.get('q') ?? '';
        signalByCallId.push({ q, signal: request.signal });
        await new Promise((resolve) => setTimeout(resolve, 5000));
        return HttpResponse.json([]);
      }),
    );

    function makeIsolatedWrapper() {
      // Same production-matching TTLs as ``makeWrapper`` — see the
      // longer rationale above. The two-tab test would also have
      // false-passed under the convenient ``gcTime: 0`` setup.
      const qc = new QueryClient({
        defaultOptions: {
          queries: { retry: false, staleTime: 60 * 1000, gcTime: 5 * 60 * 1000 },
          mutations: { retry: false },
        },
      });
      return function Wrapper({ children }: { children: ReactNode }) {
        return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
      };
    }

    // Render "tab A" with query="aa".
    const { unmount: unmountA } = renderHook(
      () => useSearch('aa', 'all', 'snippet', 'updated_at', 'desc', undefined, true, true),
      { wrapper: makeIsolatedWrapper() },
    );

    // Render "tab B" with query="bb" in a separate QueryClient.
    renderHook(
      () => useSearch('bb', 'all', 'snippet', 'updated_at', 'desc', undefined, true, true),
      { wrapper: makeIsolatedWrapper() },
    );

    // Wait for both to reach MSW.
    await waitFor(
      () => {
        const qs = signalByCallId.map((s) => s.q);
        expect(qs).toContain('aa');
        expect(qs).toContain('bb');
      },
      { timeout: 2000 },
    );

    const sigA = signalByCallId.find((s) => s.q === 'aa')!.signal;
    const sigB = signalByCallId.find((s) => s.q === 'bb')!.signal;

    expect(sigA.aborted).toBe(false);
    expect(sigB.aborted).toBe(false);

    // Tear down tab A. Tab B's search MUST still be in flight (its
    // signal MUST NOT be aborted by tab A's cleanup).
    unmountA();

    await waitFor(
      () => expect(sigA.aborted).toBe(true),
      { timeout: 2000 },
    );

    // The discriminating check — tab B is untouched.
    expect(sigB.aborted).toBe(false);
  });
});
