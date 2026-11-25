/**
 * Hunt #5 (2026-05-18) — `useSearch` AbortController plumbing.
 *
 * Verifies that the AbortSignal supplied by React Query v5's queryFn
 * `({ signal })` callback is wired through `api.search()` into the
 * `fetch()` options, so:
 *
 *   - Unmount mid-flight cancels the request (backend stops spending
 *     CPU on FTS-fallback work the user no longer sees).
 *   - The aborted query does NOT surface as a user-visible error.
 *
 * Without `signal` plumbed, React Query v5 dedupes by queryKey but does
 * NOT abort the in-flight network request on key change or unmount —
 * so the backend keeps computing and the response is discarded by the
 * cache. This test is RED before the api.ts + useConversations.ts wiring
 * lands.
 */

import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';

import { server } from '../mocks/server';
import { useSearch } from '../../hooks/useConversations';

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }
  return Wrapper;
}

describe('useSearch — AbortController plumbing (Hunt #5)', () => {
  it('aborts the in-flight /api/search request when the hook unmounts', async () => {
    // Capture the AbortSignal MSW saw on the incoming request. Once
    // the signal aborts, browser-fetch propagates the abort to the
    // server-side `request.signal` which MSW exposes 1:1.
    let capturedSignal: AbortSignal | undefined;
    const requestReceived = vi.fn();

    server.use(
      http.get('/api/search', async ({ request }) => {
        requestReceived();
        capturedSignal = request.signal;
        // Hang for a long time so the unmount fires while we're still
        // "computing." A real FTS-fallback search can be multi-second.
        await new Promise((resolve) => setTimeout(resolve, 5000));
        return HttpResponse.json([]);
      }),
    );

    const Wrapper = makeWrapper();
    // includeToolCalls is positional, REQUIRED — the search hook signature
    // mandates it (see useConversations.ts), so pass `true` to match the
    // common in-app default.
    const { unmount } = renderHook(
      () => useSearch('hello world', 'all', 'snippet', 'updated_at', 'desc', undefined, true, true, false),
      { wrapper: Wrapper },
    );

    // 200ms debounce inside the hook — wait for the GET to actually hit
    // MSW before unmounting. Otherwise we abort before the fetch starts
    // and the test is vacuously true.
    await waitFor(
      () => expect(requestReceived).toHaveBeenCalled(),
      { timeout: 2000 },
    );

    expect(capturedSignal).toBeDefined();
    expect(capturedSignal!.aborted).toBe(false);

    unmount();

    // React Query cancels the in-flight query on observer-count→0.
    // Browser fetch then aborts the underlying request; MSW's
    // `request.signal` reflects the cancellation.
    await waitFor(
      () => expect(capturedSignal!.aborted).toBe(true),
      { timeout: 2000 },
    );
  });
});
