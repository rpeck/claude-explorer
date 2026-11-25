/**
 * ConversationMessageStream — unit contract.
 *
 * Pins:
 *   1. Cowork error banner: rendered when conversation.error is non-empty;
 *      hidden when null.
 *   2. SessionPreludeAffordance: rendered with hiddenCount/expanded props.
 *   3. jsdom path: renders the visible messages non-virtualized
 *      (`.space-y-6` container).
 *   4. scrollControls slot: rendered inside the relative wrapper.
 *   5. handleScroll fires on scroll events on the scroll-area div.
 *
 * Test environment: vitest = jsdom; `isJsdom` prop is wired true so we
 * exercise the non-virtualized branch (we don't need to mock the
 * virtualizer; the production branch is exercised by Playwright e2e).
 */
import { describe, it, expect, vi } from 'vitest'
import { fireEvent } from '@testing-library/react'
// utils.tsx's `render` wraps in QueryClient + Settings + SourceFilter +
// Filter + Bookmark + BrowserRouter + KeyboardNavigation. MessageBubble
// (rendered transitively by renderBubbleRow) needs all of those.
import { render, screen } from '../utils'
import { useRef } from 'react'
import { ConversationMessageStream } from '../../components/conversation/ConversationMessageStream'
import type { ConversationDetail, Message } from '../../lib/types'
import type { Virtualizer } from '@tanstack/react-virtual'

function makeMessage(uuid: string, text = 'Hello'): Message {
  return {
    uuid,
    sender: 'human',
    text,
    content: [],
    created_at: '2026-05-30T12:00:00Z',
    updated_at: '2026-05-30T12:00:00Z',
  } as unknown as Message
}

function makeConversation(overrides: Partial<ConversationDetail> = {}): ConversationDetail {
  return {
    uuid: 'conv-1',
    source: 'CLAUDE_AI',
    error: null,
    messages: [makeMessage('m-1')],
    ...overrides,
  } as unknown as ConversationDetail
}

function noopVirtualizer(): Virtualizer<HTMLDivElement, Element> {
  return {
    getTotalSize: () => 0,
    getVirtualItems: () => [],
    measureElement: () => 0,
    scrollToIndex: () => {},
  } as unknown as Virtualizer<HTMLDivElement, Element>
}

interface RenderOpts {
  conversation?: ConversationDetail
  visibleMessages?: Message[]
  isJsdom?: boolean
  preludeHiddenCount?: number
  showPrelude?: boolean
  scrollControls?: React.ReactNode
  handleScroll?: (e: React.UIEvent<HTMLDivElement>) => void
  onTogglePrelude?: () => void
  error?: string | null
}

function StreamHarness(opts: RenderOpts) {
  const scrollAreaRef = useRef<HTMLDivElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const conversation =
    opts.conversation ??
    makeConversation(opts.error !== undefined ? { error: opts.error } : {})
  const visibleMessages = opts.visibleMessages ?? conversation.messages
  return (
    <ConversationMessageStream
      conversation={conversation}
      visibleMessages={visibleMessages}
      messages={visibleMessages.map((m) => ({ uuid: m.uuid, sender: m.sender }))}
      scrollAreaRef={scrollAreaRef}
      messagesEndRef={messagesEndRef}
      virtualizer={noopVirtualizer()}
      isJsdom={opts.isJsdom ?? true}
      getSetRef={() => () => {}}
      handleScroll={opts.handleScroll ?? vi.fn()}
      markDemonstratedFocus={vi.fn()}
      manualScrollSentinelUuid="__manual_scroll__"
      preludeHiddenCount={opts.preludeHiddenCount ?? 0}
      showPrelude={opts.showPrelude ?? false}
      onTogglePrelude={opts.onTogglePrelude ?? vi.fn()}
      getSelectedMessageId={() => null}
      focusArea="detail"
      compactMarkerByUuid={new Map()}
      compactMarkers={[]}
      activeCompactIdx={null}
      focusCompactMarker={vi.fn()}
      highlightMessageId={null}
      activeMatchUuid={null}
      deferredSearchQuery=""
      showToolCalls={false}
      showCodeMode={false}
      expandAllTools={false}
      setSelectedMessageIndex={vi.fn()}
      scrollControls={opts.scrollControls ?? null}
    />
  )
}

function renderStream(opts: RenderOpts = {}) {
  return render(<StreamHarness {...opts} />)
}

describe('ConversationMessageStream — Cowork error banner', () => {
  it('renders the banner when conversation.error is non-empty', () => {
    renderStream({ error: 'The session ended unexpectedly.' })
    expect(screen.getByTestId('cowork-error-banner')).toBeInTheDocument()
    expect(screen.getByText('The session ended unexpectedly.')).toBeInTheDocument()
  })

  it('hides the banner when conversation.error is null', () => {
    renderStream({ error: null })
    expect(screen.queryByTestId('cowork-error-banner')).toBeNull()
  })
})

describe('ConversationMessageStream — prelude affordance', () => {
  it('does NOT render the affordance when preludeHiddenCount=0', () => {
    renderStream({ preludeHiddenCount: 0 })
    // SessionPreludeAffordance internally returns null at hiddenCount=0;
    // we observe by absence of its button.
    expect(screen.queryByRole('button', { name: /prelude/i })).toBeNull()
  })

  it('renders the affordance button when preludeHiddenCount > 0', () => {
    renderStream({ preludeHiddenCount: 3 })
    // The component renders some interactive surface for revealing prelude
    // rows; assert that at least one button-with-test-id or text exists.
    // The exact label is owned by SessionPreludeAffordance — we just need
    // ANY button that fires onTogglePrelude on click in the next test.
    const buttons = screen.getAllByRole('button')
    expect(buttons.length).toBeGreaterThan(0)
  })

  it('clicking the affordance fires onTogglePrelude', () => {
    const onTogglePrelude = vi.fn()
    renderStream({ preludeHiddenCount: 3, onTogglePrelude })
    const buttons = screen.getAllByRole('button')
    // SessionPreludeAffordance is the only button in the stream at this
    // setup (no scroll controls passed in).
    fireEvent.click(buttons[0]!)
    expect(onTogglePrelude).toHaveBeenCalled()
  })
})

describe('ConversationMessageStream — jsdom non-virtualized path', () => {
  it('renders the message-stream test-id container', () => {
    renderStream()
    expect(screen.getByTestId('message-stream')).toBeInTheDocument()
  })

  it('renders all visibleMessages as bubble rows (jsdom branch)', () => {
    const visibleMessages = [
      makeMessage('m-1', 'first message'),
      makeMessage('m-2', 'second message'),
      makeMessage('m-3', 'third message'),
    ]
    renderStream({ visibleMessages })
    expect(screen.getByText('first message')).toBeInTheDocument()
    expect(screen.getByText('second message')).toBeInTheDocument()
    expect(screen.getByText('third message')).toBeInTheDocument()
  })
})

describe('ConversationMessageStream — handleScroll', () => {
  it('fires handleScroll when the scroll-area is scrolled', () => {
    const handleScroll = vi.fn()
    renderStream({ handleScroll })
    const area = screen.getByTestId('message-stream')
    fireEvent.scroll(area)
    expect(handleScroll).toHaveBeenCalledTimes(1)
  })
})

describe('ConversationMessageStream — scrollControls slot', () => {
  it('renders whatever is passed via the scrollControls prop', () => {
    renderStream({
      scrollControls: <div data-testid="custom-scroll-controls">slot</div>,
    })
    expect(screen.getByTestId('custom-scroll-controls')).toBeInTheDocument()
  })
})
