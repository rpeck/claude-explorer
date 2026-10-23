import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useScrollToHighlight } from '../../hooks/useScrollToHighlight';
import { beginSearchNavigation, __resetSearchNavigationForTests } from '@/lib/searchNavigation';
import type { Message, ConversationDetail } from '@/lib/types';
import type { Virtualizer } from '@tanstack/react-virtual';
import type { SetURLSearchParams } from 'react-router';
import type { FocusArea } from '../../contexts/KeyboardNavigationContext';

// Recovery 2026-05-30 REG-5: explicit signatures for every mock so the
// args object is assignable to UseScrollToHighlightArgs without
// `as unknown as` casts. Without these, `ReturnType<typeof vi.fn>`
// resolves to `Mock<Procedure | Constructable>` (28 tsc errors).
type SetFocusAreaFn = (area: FocusArea) => void;
type SetSelectedMessageIndexFn = (index: number) => void;
type ScheduleHighlightClearFn = (callback: () => void, delayMs: number) => void;
type ScrollToIndexFn = Virtualizer<HTMLDivElement, Element>['scrollToIndex'];

/**
 * P1.4 Commit B — useScrollToHighlight extracted from ConversationPage.
 *
 * The hook is BEHAVIOR-PRESERVING — we pin the contract via three
 * coarse-grained scenarios that cover the observable side effects:
 *
 *   1. No-op when highlightMessageId is null (early return).
 *   2. Calls setFocusArea('detail') + setSelectedMessageIndex(matchIdx).
 *   3. Calls virtualizer.scrollToIndex(visIdx) when target is in
 *      visibleMessages AND we are not in jsdom.
 *
 * The DOM polling + ring-flash + URL cleanup orchestration is left for
 * higher-level e2e specs (keyboard-nav-* / search-* in frontend/e2e) —
 * unit-testing rAF cycles in jsdom requires shimming
 * requestAnimationFrame and document.querySelector with brittle mocks
 * that don't catch the real-world failure modes.
 */

function makeMsg(uuid: string): Message {
  return {
    uuid,
    sender: 'human',
    text: 'hi',
    content: [{ type: 'text', text: 'hi' }],
    created_at: '2026-05-30T00:00:00Z',
    updated_at: null,
    parent_message_uuid: null,
  } as unknown as Message;
}

function makeConversation(): ConversationDetail {
  return {
    uuid: 'conv-1',
    name: 'Test',
    summary: null,
    messages: [],
    created_at: '2026-05-30T00:00:00Z',
    updated_at: '2026-05-30T00:00:00Z',
    settings: null,
    source: 'CLAUDE_AI',
    model: 'sonnet',
    has_branches: false,
    message_count: 0,
    project_uuid: null,
    project_path: null,
    project_name: null,
    compact_markers: [],
    git_branch: null,
    cwd: null,
    sandbox_path: null,
    parent_session_uuid: null,
    organization_uuid: null,
    organization_name: null,
    archived: false,
  } as unknown as ConversationDetail;
}

describe('useScrollToHighlight', () => {
  let setFocusArea: ReturnType<typeof vi.fn<SetFocusAreaFn>>;
  let setSelectedMessageIndex: ReturnType<typeof vi.fn<SetSelectedMessageIndexFn>>;
  let setSearchParams: ReturnType<typeof vi.fn<SetURLSearchParams>>;
  let scrollToIndex: ReturnType<typeof vi.fn<ScrollToIndexFn>>;
  let scheduleHighlightClear: ReturnType<typeof vi.fn<ScheduleHighlightClearFn>>;

  beforeEach(() => {
    setFocusArea = vi.fn<SetFocusAreaFn>();
    setSelectedMessageIndex = vi.fn<SetSelectedMessageIndexFn>();
    setSearchParams = vi.fn<SetURLSearchParams>();
    scrollToIndex = vi.fn<ScrollToIndexFn>();
    scheduleHighlightClear = vi.fn<ScheduleHighlightClearFn>();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('no-ops when highlightMessageId is null', () => {
    renderHook(() =>
      useScrollToHighlight({
        highlightMessageId: null,
        conversation: makeConversation(),
        isLoading: false,
        setSearchParams,
        setFocusArea,
        messages: [makeMsg('a'), makeMsg('b')],
        setSelectedMessageIndex,
        visibleMessages: [makeMsg('a'), makeMsg('b')],
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        virtualizer: { scrollToIndex } as any,
        isJsdom: true,
        shouldFocusOnHighlight: true,
        scheduleHighlightClear,
      })
    );
    expect(setFocusArea).not.toHaveBeenCalled();
    expect(setSelectedMessageIndex).not.toHaveBeenCalled();
    expect(scrollToIndex).not.toHaveBeenCalled();
  });

  it('no-ops while isLoading=true (avoids running before conversation hydrates)', () => {
    renderHook(() =>
      useScrollToHighlight({
        highlightMessageId: 'a',
        conversation: makeConversation(),
        isLoading: true,
        setSearchParams,
        setFocusArea,
        messages: [makeMsg('a'), makeMsg('b')],
        setSelectedMessageIndex,
        visibleMessages: [makeMsg('a'), makeMsg('b')],
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        virtualizer: { scrollToIndex } as any,
        isJsdom: true,
        shouldFocusOnHighlight: true,
        scheduleHighlightClear,
      })
    );
    expect(setFocusArea).not.toHaveBeenCalled();
  });

  it('sets focus=detail + selectedMessageIndex when target is in messages', () => {
    renderHook(() =>
      useScrollToHighlight({
        highlightMessageId: 'b',
        conversation: makeConversation(),
        isLoading: false,
        setSearchParams,
        setFocusArea,
        messages: [makeMsg('a'), makeMsg('b'), makeMsg('c')],
        setSelectedMessageIndex,
        visibleMessages: [makeMsg('a'), makeMsg('b'), makeMsg('c')],
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        virtualizer: { scrollToIndex } as any,
        isJsdom: true,
        shouldFocusOnHighlight: true,
        scheduleHighlightClear,
      })
    );
    expect(setFocusArea).toHaveBeenCalledWith('detail');
    expect(setSelectedMessageIndex).toHaveBeenCalledWith(1);
  });

  it('does NOT call virtualizer.scrollToIndex under jsdom (test env guard)', () => {
    renderHook(() =>
      useScrollToHighlight({
        highlightMessageId: 'b',
        conversation: makeConversation(),
        isLoading: false,
        setSearchParams,
        setFocusArea,
        messages: [makeMsg('a'), makeMsg('b'), makeMsg('c')],
        setSelectedMessageIndex,
        visibleMessages: [makeMsg('a'), makeMsg('b'), makeMsg('c')],
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        virtualizer: { scrollToIndex } as any,
        isJsdom: true,
        shouldFocusOnHighlight: true,
        scheduleHighlightClear,
      })
    );
    expect(scrollToIndex).not.toHaveBeenCalled();
  });

  it('calls virtualizer.scrollToIndex with the visibleMessages index when isJsdom=false', () => {
    renderHook(() =>
      useScrollToHighlight({
        highlightMessageId: 'c',
        conversation: makeConversation(),
        isLoading: false,
        setSearchParams,
        setFocusArea,
        messages: [makeMsg('a'), makeMsg('b'), makeMsg('c')],
        setSelectedMessageIndex,
        visibleMessages: [makeMsg('a'), makeMsg('c')], // 'b' filtered out
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        virtualizer: { scrollToIndex } as any,
        isJsdom: false,
        shouldFocusOnHighlight: true,
        scheduleHighlightClear,
      })
    );
    expect(scrollToIndex).toHaveBeenCalledWith(1, { align: 'center' });
  });

  it('does NOT call virtualizer.scrollToIndex when target is not in visibleMessages', () => {
    renderHook(() =>
      useScrollToHighlight({
        highlightMessageId: 'd',
        conversation: makeConversation(),
        isLoading: false,
        setSearchParams,
        setFocusArea,
        messages: [makeMsg('a'), makeMsg('b'), makeMsg('c'), makeMsg('d')],
        setSelectedMessageIndex,
        visibleMessages: [makeMsg('a'), makeMsg('b')], // 'd' filtered out
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        virtualizer: { scrollToIndex } as any,
        isJsdom: false,
        shouldFocusOnHighlight: true,
        scheduleHighlightClear,
      })
    );
    expect(scrollToIndex).not.toHaveBeenCalled();
  });

  // 2026-10-23: the newest search jump wins. A `?highlight=` that an older
  // search jump set must not scroll after a newer jump.
  describe('a highlight from an older search jump', () => {
    beforeEach(() => __resetSearchNavigationForTests());

    function run(highlight: string) {
      renderHook(() =>
        useScrollToHighlight({
          highlightMessageId: highlight,
          conversation: makeConversation(),
          isLoading: false,
          setSearchParams,
          setFocusArea,
          messages: [makeMsg('early'), makeMsg('late')],
          setSelectedMessageIndex,
          visibleMessages: [makeMsg('early'), makeMsg('late')],
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          virtualizer: { scrollToIndex } as any,
          isJsdom: false,
          shouldFocusOnHighlight: true,
          scheduleHighlightClear,
        })
      );
    }

    it('is skipped: no scroll, no selection, and the URL loses it', () => {
      beginSearchNavigation('late'); // URL path
      beginSearchNavigation(null); // newer fast-path jump
      run('late');

      expect(scrollToIndex).not.toHaveBeenCalled();
      expect(setSelectedMessageIndex).not.toHaveBeenCalled();
      expect(setSearchParams).toHaveBeenCalledTimes(1);
      const update = setSearchParams.mock.calls[0][0] as (p: URLSearchParams) => URLSearchParams;
      const next = update(new URLSearchParams('highlight=late&focus=0&pin=conv:x'));
      expect(next.get('highlight')).toBeNull();
      expect(next.get('focus')).toBeNull();
      expect(next.get('pin')).toBe('conv:x');
    });

    it('from the newest jump still scrolls', () => {
      beginSearchNavigation('late');
      run('late');
      expect(scrollToIndex).toHaveBeenCalledWith(1, { align: 'center' });
    });
  });
});
