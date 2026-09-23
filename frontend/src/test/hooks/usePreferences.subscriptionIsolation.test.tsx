/**
 * usePreferences subscription-isolation invariant (2026-05-22 perf fix).
 *
 * Pins the architectural invariant that fixes the 9.4-second Long Task
 * the user hit when toggling Snippet/Full on the 16K-message conversation.
 *
 * # The bug (empirically reproduced 2026-05-22)
 *
 * Clicking Snippet/Full in the SearchPanel emitted a single ~9590 ms
 * synchronous JS Long Task. Instrumented render counters showed:
 *   - MessageBubble executed 3964× during the toggle (= full conv size)
 *   - SettingsProvider rendered once
 *
 * Root cause: `usePreferences` registers EVERY caller as an observer of
 * the single `['preferences']` query key. When ANY preference flips,
 * `qc.setQueryData(['preferences'], newEnvelope)` notifies all 14
 * observers — even the 13 whose own key didn't change. SettingsProvider
 * re-renders. Because MessageBubble consumes `useSettings()`
 * (MessageBubble.tsx:50), every bubble re-runs in full — React.memo
 * cannot block context invalidation, only prop changes. ~2.4ms × 3964
 * bubbles = ~9.6 s of sync work.
 *
 * # The architectural invariant pinned here
 *
 * **Mutating preference key A through `qc.setQueryData(['preferences'],
 * envelopeWithAChanged)` MUST NOT cause a re-render in a `usePreferences`
 * instance that selected key B.**
 *
 * This is the user-observable contract from TESTING.md §5.13:
 * the user cannot tolerate a 9.4-second main-thread freeze when toggling
 * an unrelated preference. We test the contract at the hook level (fast,
 * deterministic, JSDOM-safe) rather than measuring wall-clock latency
 * (flaky, hardware-dependent, requires a real 3964-bubble fixture).
 *
 * # Why renderHook + render-count probe, not Playwright
 *
 * - JSDOM has no real layout; wall-clock measurements are detached from
 *   browser reality.
 * - The architectural invariant is the proximate cause. If it holds,
 *   the user-observable freeze is eliminated by construction (verified
 *   empirically via Playwright on the real corpus — see the commit
 *   message for the wall-clock numbers).
 * - StrictMode doubles renders deterministically (every-mount × 2); we
 *   compare RELATIVE counts after settle, never absolute.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { type ReactNode } from 'react';

import { server } from '../../test/mocks/server';
import { usePreferences } from '../../hooks/usePreferences';

// jsdom needs an in-memory localStorage shim (same pattern as the
// sibling usePreferences.test.tsx).
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    get store() { return store; },
    getItem: vi.fn((key: string) => (key in store ? store[key] : null)),
    setItem: vi.fn((key: string, value: string) => { store[key] = value; }),
    removeItem: vi.fn((key: string) => { delete store[key]; }),
    clear: vi.fn(() => { store = {}; }),
  };
})();
Object.defineProperty(window, 'localStorage', { value: localStorageMock });

function makeWrapper() {
  // gcTime: Infinity so a hook that unmounts mid-test (e.g. a sibling
  // observer in a different renderHook) doesn't garbage-collect the
  // query before we finish asserting.
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }
  return { Wrapper, qc };
}

function installPrefsHandlers(initial: Record<string, unknown> = {}) {
  const store = { data: { ...initial } };
  server.use(
    http.get('/api/preferences', () =>
      HttpResponse.json({ version: 1, data: store.data }),
    ),
    http.patch('/api/preferences', async ({ request }) => {
      const json = (await request.json()) as { data?: Record<string, unknown> };
      Object.assign(store.data, json.data ?? {});
      return HttpResponse.json({ version: 1, data: store.data });
    }),
  );
  return { store };
}

/**
 * A `usePreferences` instance wrapped with a per-instance render counter.
 * `renderCounters` lives at module scope, keyed by `id`, so each test
 * can read the count without violating React's "no ref-during-render"
 * rule (the eslint react-hooks/refs plugin rejects the canonical
 * `renders.current += 1` ref-counter idiom). Tests assert on the DELTA
 * between a pre-mutation snapshot and a post-mutation snapshot — never
 * absolute, so React StrictMode's double-mount doesn't make the test
 * brittle.
 */
const renderCounters = new Map<string, number>();

function useCountedPreference<T>(id: string, key: string, fallback: T) {
  renderCounters.set(id, (renderCounters.get(id) ?? 0) + 1);
  const [value, setValue] = usePreferences<T>(key, fallback);
  return {
    value,
    setValue,
    getRenders: () => renderCounters.get(id) ?? 0,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  renderCounters.clear();
});

afterEach(() => {
  window.localStorage.clear();
  renderCounters.clear();
});

describe('usePreferences subscription isolation (2026-05-22 perf fix)', () => {
  it('does NOT re-render an instance for key B when key A is mutated via setQueryData', async () => {
    // This is THE invariant. Pre-fix this test goes RED: mutating
    // `searchPanel.contextSize` via setQueryData notifies ALL observers
    // of `['preferences']` (because that's the entire subscription
    // graph), so the `theme` hook re-renders too. Post-fix, with a
    // per-key `select`, the `theme` hook's selected slice is unchanged,
    // TanStack short-circuits the notification, and the renderer stays
    // at the post-settle baseline.
    installPrefsHandlers({
      theme: 'dark',
      'searchPanel.contextSize': 'full',
    });
    const { Wrapper, qc } = makeWrapper();

    const themeHook = renderHook(
      () => useCountedPreference<string>('theme', 'theme', 'system'),
      { wrapper: Wrapper },
    );
    const ctxHook = renderHook(
      () => useCountedPreference<string>('ctx', 'searchPanel.contextSize', 'snippet'),
      { wrapper: Wrapper },
    );

    // Force both hooks to settle (initial fetch lands, value resolves).
    await waitFor(() => {
      expect(themeHook.result.current.value).toBe('dark');
      expect(ctxHook.result.current.value).toBe('full');
    });

    // Snapshot the render counts AFTER settle. Anything that happens
    // from here forward is what the architectural invariant gates.
    const themeBaseline = themeHook.result.current.getRenders();

    // Simulate the production code path: a sibling write to a DIFFERENT
    // key flows through `qc.setQueryData(['preferences'], …)` in
    // `usePreferences.ts`'s `onSuccess`. We bypass the mutation
    // round-trip and write the cache directly — same notification path.
    act(() => {
      qc.setQueryData(['preferences'], {
        version: 1,
        data: {
          theme: 'dark',                       // unchanged
          'searchPanel.contextSize': 'snippet', // flipped from 'full'
        },
      });
    });

    // Give React a microtask to flush any synchronous renders the
    // notification triggered. The post-fix expectation: zero. The pre-fix
    // observation: at least +1 (the storm).
    await waitFor(() => {
      // The contextSize hook MUST have re-rendered (its value changed).
      expect(ctxHook.result.current.value).toBe('snippet');
    });

    const themeAfter = themeHook.result.current.getRenders();
    expect(themeAfter - themeBaseline).toBe(0);
  });

  it('DOES re-render the instance whose key was actually mutated', async () => {
    // Counter-test: prove the subscription is still LIVE for the
    // affected key. Without this, a broken `select` that returned a
    // constant would falsely pass the isolation test above.
    installPrefsHandlers({ theme: 'dark' });
    const { Wrapper, qc } = makeWrapper();

    const themeHook = renderHook(
      () => useCountedPreference<string>('theme', 'theme', 'system'),
      { wrapper: Wrapper },
    );

    await waitFor(() => {
      expect(themeHook.result.current.value).toBe('dark');
    });
    const baseline = themeHook.result.current.getRenders();

    act(() => {
      qc.setQueryData(['preferences'], {
        version: 1,
        data: { theme: 'light' },
      });
    });

    await waitFor(() => {
      expect(themeHook.result.current.value).toBe('light');
    });
    expect(themeHook.result.current.getRenders() - baseline).toBeGreaterThan(0);
  });

  it('preserves local-first contract even with per-key selection', async () => {
    // Regression guard: the perf fix MUST NOT silently weaken the
    // local-first contract that usePreferences.test.tsx pins. If a
    // future refactor moves the localStorage merge INSIDE the `select`
    // selector, this test catches it — because `select` is invoked on
    // cache changes, NOT on localStorage writes, so a setValue() that
    // doesn't immediately update the cache would lose the local-first
    // immediate-visibility.
    installPrefsHandlers({ theme: 'dark' });
    window.localStorage.setItem('theme', JSON.stringify('sepia'));
    const { Wrapper, qc } = makeWrapper();

    const { result } = renderHook(
      () => usePreferences<string>('theme', 'system'),
      { wrapper: Wrapper },
    );

    await waitFor(() => {
      expect(qc.getQueryData(['preferences'])).toBeDefined();
    });

    expect(result.current[0]).toBe('sepia');
  });
});
