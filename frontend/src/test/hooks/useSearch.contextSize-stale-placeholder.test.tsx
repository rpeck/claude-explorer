/**
 * Regression: toggling `contextSize` (Snippet ↔ Full) must NOT keep
 * the previous-mode's results visible during the new fetch.
 *
 * User report (2026-05-22): clicking "Snippet" after a Full search
 * resulted in a screenshot where the Snippet button was highlighted
 * but the cards still showed long full-message bodies. Root cause:
 * `useSearch` used `placeholderData: keepPreviousData`, which is
 * correct for narrowing the same query (typing more characters keeps
 * stale-but-close results visible) but wrong for a contextSize toggle
 * — the snippet shapes are categorically different (~200 chars vs
 * full message body, often 10K+ chars). The visual mismatch IS the
 * bug; the user perceives "reversed sense" because the button says
 * one thing and the cards show the other.
 *
 * The contract we're pinning here is **user-observable**, per the
 * CLAUDE-TESTING §5.13 rule that surfaced from this exact bug class:
 * "after a contextSize change, the hook MUST NOT return data that
 * was fetched under the previous contextSize, even briefly."
 *
 * Bidirectional pair:
 *   * full → snippet: previous full data is NOT kept
 *   * snippet → full: previous snippet data is NOT kept
 *
 * Negative pair (must NOT break the narrowing-query UX):
 *   * same contextSize, query lengthens: previous data IS kept
 */

import { describe, it, expect } from 'vitest';
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

/** Synthesize a /api/search response whose snippets are clearly
 *  identifiable as "full mode" or "snippet mode" so we can verify
 *  WHICH mode's data the hook is currently returning. */
function searchResponseFor(mode: 'snippet' | 'full') {
  const snippet =
    mode === 'full'
      ? 'FULL_MODE_BODY: ' + 'lorem ipsum '.repeat(500) // ~6KB
      : 'SNIPPET_MODE_WINDOW';
  return {
    results: [
      {
        conversation_uuid: 'conv-1',
        conversation_name: 'Test',
        conversation_source: 'CLAUDE_AI',
        conversation_created_at: '2026-01-01T00:00:00Z',
        conversation_updated_at: '2026-01-01T00:00:00Z',
        project_path: null,
        matching_messages: [
          {
            message_uuid: 'msg-1',
            sender: 'human',
            snippet,
            match_start: 0,
            match_end: 5,
            created_at: '2026-01-01T00:00:00Z',
            fragments: null,
          },
        ],
      },
    ],
    total_messages_matched: 1,
    returned_messages: 1,
    truncated: false,
  };
}

describe('useSearch — contextSize toggle MUST NOT return stale previous-mode data', () => {
  it('full → snippet: previous full data is NOT used as placeholder', async () => {
    server.use(
      http.get('/api/search', ({ request }) => {
        const url = new URL(request.url);
        // `context_size` is omitted in the URL when it's the default
        // ('snippet'). The api.ts only appends it when != 'snippet'.
        const mode = url.searchParams.get('context_size') === 'full'
          ? 'full' : 'snippet';
        return HttpResponse.json(searchResponseFor(mode));
      }),
    );

    const Wrapper = makeWrapper();
    const { result, rerender } = renderHook(
      ({ ctx }: { ctx: 'snippet' | 'full' }) =>
        useSearch('hello', 'all', ctx, 'updated_at', 'desc', undefined, true, true, false),
      { wrapper: Wrapper, initialProps: { ctx: 'full' as 'snippet' | 'full' } },
    );

    // Wait for the full-mode fetch to populate.
    await waitFor(() => {
      const snippet = result.current.data?.results?.[0]
        ?.matching_messages?.[0]?.snippet ?? '';
      expect(snippet).toMatch(/^FULL_MODE_BODY/);
    });

    // Now flip to snippet mode. React Query starts a new fetch; under
    // the buggy `keepPreviousData` config, `data` continues to return
    // the FULL_MODE_BODY snippet while the new fetch is in flight.
    // With the fix, `data` becomes undefined (or returns snippet data
    // synchronously if the new fetch already resolved).
    rerender({ ctx: 'snippet' });

    // Discriminator: while the new fetch is pending, data must NOT
    // contain the previous mode's snippet body. We allow either
    // `data === undefined` (placeholder dropped) OR `data` reflects
    // the NEW mode (fetch already landed). The forbidden outcome is
    // FULL_MODE_BODY in snippet context.
    //
    // We poll for a brief window to give the bug a chance to surface
    // if present. If the previous-mode data leaks into the new
    // contextSize EVEN ONCE during this window, fail.
    const samples: Array<string | undefined> = [];
    for (let i = 0; i < 5; i++) {
      const snippet = result.current.data?.results?.[0]
        ?.matching_messages?.[0]?.snippet;
      samples.push(snippet);
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    const leaked = samples.some((s) => s?.startsWith('FULL_MODE_BODY'));
    expect(leaked, `previous full-mode snippet leaked into snippet ctx; samples=${JSON.stringify(samples)}`).toBe(false);

    // Sanity: eventually the new mode's data arrives.
    await waitFor(() => {
      const snippet = result.current.data?.results?.[0]
        ?.matching_messages?.[0]?.snippet ?? '';
      expect(snippet).toBe('SNIPPET_MODE_WINDOW');
    });
  });

  it('snippet → full: previous snippet data is NOT used as placeholder', async () => {
    server.use(
      http.get('/api/search', ({ request }) => {
        const url = new URL(request.url);
        const mode = url.searchParams.get('context_size') === 'full'
          ? 'full' : 'snippet';
        return HttpResponse.json(searchResponseFor(mode));
      }),
    );

    const Wrapper = makeWrapper();
    const { result, rerender } = renderHook(
      ({ ctx }: { ctx: 'snippet' | 'full' }) =>
        useSearch('hello', 'all', ctx, 'updated_at', 'desc', undefined, true, true, false),
      { wrapper: Wrapper, initialProps: { ctx: 'snippet' as 'snippet' | 'full' } },
    );

    await waitFor(() => {
      const snippet = result.current.data?.results?.[0]
        ?.matching_messages?.[0]?.snippet ?? '';
      expect(snippet).toBe('SNIPPET_MODE_WINDOW');
    });

    rerender({ ctx: 'full' });

    const samples: Array<string | undefined> = [];
    for (let i = 0; i < 5; i++) {
      const snippet = result.current.data?.results?.[0]
        ?.matching_messages?.[0]?.snippet;
      samples.push(snippet);
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    const leaked = samples.some((s) => s === 'SNIPPET_MODE_WINDOW');
    expect(leaked, `previous snippet-mode data leaked into full ctx; samples=${JSON.stringify(samples)}`).toBe(false);

    await waitFor(() => {
      const snippet = result.current.data?.results?.[0]
        ?.matching_messages?.[0]?.snippet ?? '';
      expect(snippet).toMatch(/^FULL_MODE_BODY/);
    });
  });

  it('negative pair: same contextSize + lengthening query DOES keep previous data (placeholder works for narrowing)', async () => {
    // Pins that the fix doesn't over-prune — narrowing a query within
    // the same contextSize must still keep prior results visible (the
    // "feels instantaneous as I type" UX `placeholderData` was added
    // for in the first place).
    let requestCount = 0;
    server.use(
      http.get('/api/search', () => {
        requestCount += 1;
        return HttpResponse.json(searchResponseFor('snippet'));
      }),
    );

    const Wrapper = makeWrapper();
    const { result, rerender } = renderHook(
      ({ q }: { q: string }) =>
        useSearch(q, 'all', 'snippet', 'updated_at', 'desc', undefined, true, true, false),
      { wrapper: Wrapper, initialProps: { q: 'hello' } },
    );

    await waitFor(() => {
      expect(result.current.data).toBeDefined();
    });
    const firstFetchCount = requestCount;
    expect(firstFetchCount).toBeGreaterThan(0);

    // Lengthen the query (still under contextSize='snippet'). The new
    // queryKey fires a new fetch, but during the window the previous
    // results MUST stay visible.
    rerender({ q: 'hello world' });
    // Immediately (before the second debounce + fetch lands): data
    // should still be defined — keepPreviousData is doing its job.
    expect(result.current.data?.results?.[0]?.matching_messages?.[0]?.snippet)
      .toBe('SNIPPET_MODE_WINDOW');
  });
});
