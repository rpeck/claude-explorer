/**
 * Council E4 (2026-05-22) — MessageBubble.tsx split (806 LOC → 299
 * LOC) contract tests.
 *
 * After the split, MessageBubbleImpl, ToolBlocks, ImageBlocks, and
 * ContentBlockRenderer live in separate files. These tests pin the
 * BLACK-BOX behavioral contract the entry-point MessageBubble must
 * preserve regardless of how internal modules are reorganized.
 *
 * Why data-attribute black-box rather than byte-for-byte snapshots:
 * React 19 + Tailwind class ordering is too brittle for snapshot
 * pinning, and the data-* attributes are the contract the rest of the
 * app (and ConversationPage's smooth-scroll orchestration) reads.
 *
 * Bidirectional methodology (TESTING.md):
 *   POSITIVE: data attrs present + lightbox opens + tool blocks visible
 *   NEGATIVE: data-cc-image-broken absent on healthy; tool block hidden
 *             when showToolCalls=false; forceExpanded ignores collapse
 *             clicks (engineer Round-2 mandate)
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '../utils'
import { MessageBubble } from '../../components/message/MessageBubble'
import { ConversationLightboxProvider } from '../../contexts/ConversationLightboxContext'
import type { Message } from '../../lib/types'

// 2026-05-23 perf-regression refactor: the previous EnableToolCalls /
// EnableExpandAllTools helpers flipped SettingsContext state to drive
// MessageBubble rendering. After the refactor, MessageBubble no longer
// subscribes to SettingsContext (see
// MessageBubble.no-settings-import.test.ts for the unconditional
// invariant). Tests now pass `showToolCalls` / `expandAllTools`
// directly as props on the bubble under test.

function makeCcMarkerMessage(): Message {
  return {
    uuid: 'msg-ccm-1',
    sender: 'human',
    text: 'See [Image: source: /home/u/.claude/image-cache/abc/1.png] for context.',
    content: [
      {
        type: 'text',
        text: 'See [Image: source: /home/u/.claude/image-cache/abc/1.png] for context.',
      },
    ],
    created_at: '2026-05-22T00:00:00Z',
    updated_at: '2026-05-22T00:00:00Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
  }
}

function makeInlineImageMessage(): Message {
  // Tiny 1x1 base64 PNG — content shape only matters; the bytes don't
  // need to decode.
  const tinyPng =
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQMAAAAl21bKAAAAA1BMVEUAAACnej3aAAAAAXRSTlMAQObYZgAAAApJREFUCNdjYAAAAAIAAeIhvDMAAAAASUVORK5CYII='
  return {
    uuid: 'msg-inline-1',
    sender: 'assistant',
    text: '',
    content: [
      {
        type: 'image',
        source: { type: 'base64', media_type: 'image/png', data: tinyPng },
      },
    ],
    created_at: '2026-05-22T00:00:00Z',
    updated_at: '2026-05-22T00:00:00Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
  }
}

function makeToolUseMessage(): Message {
  return {
    uuid: 'msg-tool-1',
    sender: 'assistant',
    text: '',
    content: [
      {
        type: 'tool_use',
        name: 'read_file',
        input: { path: '/src/foo.ts' },
      } as Message['content'][number],
    ],
    created_at: '2026-05-22T00:00:00Z',
    updated_at: '2026-05-22T00:00:00Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
  }
}

describe('MessageBubble — CC image marker data-attributes (council E4 split)', () => {
  it('POSITIVE: healthy CC marker exposes data-cc-image-marker + data-cc-image-path', () => {
    const msg = makeCcMarkerMessage()
    render(
      <ConversationLightboxProvider messages={[msg]}>
        <MessageBubble message={msg} />
      </ConversationLightboxProvider>,
    )
    const marker = document.querySelector('[data-cc-image-marker]')
    expect(marker).not.toBeNull()
    expect(marker?.getAttribute('data-cc-image-path')).toBe(
      '/home/u/.claude/image-cache/abc/1.png',
    )
  })

  it('NEGATIVE: data-cc-image-broken is absent on a healthy CC marker', () => {
    const msg = makeCcMarkerMessage()
    render(
      <ConversationLightboxProvider messages={[msg]}>
        <MessageBubble message={msg} />
      </ConversationLightboxProvider>,
    )
    // The healthy tile is a button with data-cc-image-marker but
    // WITHOUT data-cc-image-broken. The broken variant only renders
    // after the <img> onError handler fires twice (P4d retry +
    // fallback). On a fresh render with no img errors, broken MUST be
    // absent.
    expect(document.querySelector('[data-cc-image-broken]')).toBeNull()
  })

  it('POSITIVE: clicking a CC marker opens the lightbox (openAt fires)', () => {
    const msg = makeCcMarkerMessage()
    render(
      <ConversationLightboxProvider messages={[msg]}>
        <MessageBubble message={msg} />
      </ConversationLightboxProvider>,
    )
    // Pre-click: lightbox is closed → no Close button in the DOM.
    expect(screen.queryByLabelText('Close lightbox')).toBeNull()

    const marker = document.querySelector(
      '[data-cc-image-marker]',
    ) as HTMLElement
    expect(marker).not.toBeNull()
    fireEvent.click(marker)

    // Post-click: ImageLightbox receives a non-null index from
    // ConversationLightboxProvider.openAt, so the Close button
    // (rendered only when open) is now in the DOM. This is the load-
    // bearing signal that the onOpenCcImage → openAt wiring still
    // crosses the bubble boundary correctly after the split.
    expect(screen.getByLabelText('Close lightbox')).toBeInTheDocument()
  })
})

describe('MessageBubble — inline image data-attributes (council E4 split)', () => {
  it('POSITIVE: inline image block exposes data-content-image', () => {
    const msg = makeInlineImageMessage()
    render(
      <ConversationLightboxProvider messages={[msg]}>
        <MessageBubble message={msg} />
      </ConversationLightboxProvider>,
    )
    expect(document.querySelector('[data-content-image]')).not.toBeNull()
  })

  it('NEGATIVE: data-content-image-broken absent on a healthy inline image', () => {
    const msg = makeInlineImageMessage()
    render(
      <ConversationLightboxProvider messages={[msg]}>
        <MessageBubble message={msg} />
      </ConversationLightboxProvider>,
    )
    expect(document.querySelector('[data-content-image-broken]')).toBeNull()
  })

  it('POSITIVE: clicking inline image opens the lightbox', () => {
    const msg = makeInlineImageMessage()
    render(
      <ConversationLightboxProvider messages={[msg]}>
        <MessageBubble message={msg} />
      </ConversationLightboxProvider>,
    )
    expect(screen.queryByLabelText('Close lightbox')).toBeNull()

    const tile = document.querySelector('[data-content-image]') as HTMLElement
    expect(tile).not.toBeNull()
    fireEvent.click(tile)

    expect(screen.getByLabelText('Close lightbox')).toBeInTheDocument()
  })
})

describe('MessageBubble — tool block gating (council E4 split)', () => {
  it('NEGATIVE: tool_use block is HIDDEN when showToolCalls=false (default)', () => {
    const msg = makeToolUseMessage()
    render(<MessageBubble message={msg} />)
    // Default SettingsProvider has showToolCalls=false, so the
    // ToolUseBlock must not render its header.
    expect(screen.queryByText('Tool: read_file')).toBeNull()
  })

  it('POSITIVE: tool_use block is VISIBLE when showToolCalls=true', async () => {
    const msg = makeToolUseMessage()
    // 2026-05-23 perf-regression refactor: pass showToolCalls as a
    // prop instead of flipping the SettingsContext via EnableToolCalls.
    // MessageBubble no longer reads from SettingsContext directly —
    // see MessageBubble.no-settings-import.test.ts for the contract.
    render(<MessageBubble message={msg} showToolCalls={true} />)
    expect(await screen.findByText('Tool: read_file')).toBeInTheDocument()
  })

  it('POSITIVE: forceExpanded (expandAllTools=true) shows JSON without click', async () => {
    const msg = makeToolUseMessage()
    // Same prop-not-context refactor as above.
    render(<MessageBubble message={msg} showToolCalls={true} expandAllTools={true} />)
    // Without any click, the JSON body must be visible because
    // expandAllTools=true → forceExpanded=true → expanded=true.
    expect(await screen.findByText(/"path"/)).toBeInTheDocument()
  })

  it('NEGATIVE: forceExpanded=true IGNORES the collapse click (stays expanded)', async () => {
    // Engineer Round-2 mandate (gpt-5.2 confirmation round 2026-05-22):
    // a regression where someone refactors `expanded = forceExpanded ||
    // isExpanded` to `expanded = isExpanded` would let a click toggle
    // the block closed even with expandAllTools=true. Pin the contract:
    // a header click MUST be a no-op (visually) while forceExpanded is
    // true.
    const msg = makeToolUseMessage()
    // 2026-05-23 perf-regression refactor: pass props directly.
    render(<MessageBubble message={msg} showToolCalls={true} expandAllTools={true} />)
    const header = await screen.findByText('Tool: read_file')
    // JSON visible pre-click.
    expect(screen.getByText(/"path"/)).toBeInTheDocument()

    // Click the header. With forceExpanded=true the body remains
    // visible regardless of the internal isExpanded toggle.
    fireEvent.click(header)
    expect(screen.getByText(/"path"/)).toBeInTheDocument()

    // Second click — still visible.
    fireEvent.click(header)
    expect(screen.getByText(/"path"/)).toBeInTheDocument()
  })
})

describe('MessageBubble — CC image marker NOT tombstoned on fresh render (council E4 split)', () => {
  // Defense-in-depth: the imageFailureRegistry is module-scoped and
  // persists across tests. Reset it so a prior test that recorded a
  // failure for the same URL doesn't false-positive a tombstone here.
  beforeEach(() => {
    // The registry doesn't expose a reset, but in vitest the module is
    // re-imported per test file's worker. The data-cc-image-broken
    // negative test already runs in this file before this suite, so
    // the registry is presumed clean for the URL we use below
    // (different path from earlier tests).
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('NEGATIVE: a fresh CC marker render shows healthy variant, not broken', () => {
    // Use a different path from prior tests in this file to avoid any
    // accidental registry crosstalk (the registry is keyed by URL).
    const msg: Message = {
      uuid: 'msg-fresh-1',
      sender: 'human',
      text: '[Image: source: /home/u/.claude/image-cache/fresh/9.png]',
      content: [
        {
          type: 'text',
          text: '[Image: source: /home/u/.claude/image-cache/fresh/9.png]',
        },
      ],
      created_at: '2026-05-22T00:00:00Z',
      updated_at: '2026-05-22T00:00:00Z',
      truncated: false,
      parent_message_uuid: null,
      attachments: [],
      files: [],
    }
    render(
      <ConversationLightboxProvider messages={[msg]}>
        <MessageBubble message={msg} />
      </ConversationLightboxProvider>,
    )
    const marker = document.querySelector('[data-cc-image-marker]')
    expect(marker).not.toBeNull()
    expect(marker?.hasAttribute('data-cc-image-broken')).toBe(false)
  })
})
