import { describe, it, expect } from 'vitest'
import { computeVisibleMessages } from '@/lib/utils'
import type { Message } from '@/lib/types'

/**
 * NIT-1 + image-only nav-alignment (council follow-up, 2026-05-22):
 * `computeVisibleMessages` is the pure helper that drives both the
 * detail-pane render loop and the keyboard-navigation registration
 * in `ConversationPage`. Two surfaces, one predicate — keeps users
 * from clicking an empty `<div onClick>` wrapper that no-ops because
 * `messages.findIndex(uuid) === -1`.
 *
 * Bidirectional contracts pinned here:
 *
 *   POSITIVE — message survives the filter when:
 *     - it carries plain text content
 *     - it has image attachments (image-only message)
 *     - it has tool blocks AND `showToolCalls=true`
 *     - it's a prelude message AND `showPrelude=true`
 *     - it's referenced by a compact marker (regardless of showToolCalls)
 *
 *   NEGATIVE — message dropped when:
 *     - it's tool-only AND `showToolCalls=false`
 *     - it's `is_prelude=true` AND `showPrelude=false`
 *     - it has no text, no images, no content blocks
 *
 *   ORDER INVARIANT: surviving messages keep their original order.
 */

function makeMessage(over: Partial<Message>): Message {
  return {
    uuid: over.uuid ?? 'u-default',
    sender: over.sender ?? 'human',
    text: over.text ?? '',
    content: over.content ?? [{ type: 'text', text: over.text ?? '' }],
    created_at: over.created_at ?? '2026-05-22T00:00:00Z',
    updated_at: over.updated_at ?? '2026-05-22T00:00:00Z',
    truncated: over.truncated ?? false,
    parent_message_uuid: over.parent_message_uuid ?? null,
    attachments: over.attachments ?? [],
    files: over.files ?? [],
    is_command_marker: over.is_command_marker,
    is_prelude: over.is_prelude,
    slash_command: over.slash_command,
  }
}

const NO_COMPACT_MARKERS: ReadonlySet<string> = new Set()

describe('computeVisibleMessages — positive', () => {
  it('keeps a plain text message', () => {
    const m = makeMessage({ uuid: 'u1', text: 'hello world' })
    const result = computeVisibleMessages([m], {
      showPrelude: false,
      showToolCalls: false,
      compactMarkerUuids: NO_COMPACT_MARKERS,
    })
    expect(result).toHaveLength(1)
    expect(result[0]?.uuid).toBe('u1')
  })

  it('keeps an image-only message (text empty, files non-empty)', () => {
    // `messageHasVisibleContent` counts ImageFile entries in `files` /
    // `files_v2` via `dedupeImageFiles`. The legacy `attachments` array
    // is not part of the predicate. Pin that here so future drift on
    // image-only handling surfaces immediately.
    const m = makeMessage({
      uuid: 'u-img',
      text: '',
      content: [],
      files: [
        {
          file_uuid: 'f1',
          file_name: 'screenshot.png',
          file_kind: 'image',
          created_at: '2026-05-22T00:00:00Z',
        },
      ],
    })
    const result = computeVisibleMessages([m], {
      showPrelude: false,
      showToolCalls: false,
      compactMarkerUuids: NO_COMPACT_MARKERS,
    })
    expect(result).toHaveLength(1)
    expect(result[0]?.uuid).toBe('u-img')
  })

  it('keeps a tool-only message when showToolCalls=true', () => {
    const m = makeMessage({
      uuid: 'u-tool',
      text: '',
      content: [{ type: 'tool_use', name: 'Bash', input: {} }],
    })
    const result = computeVisibleMessages([m], {
      showPrelude: false,
      showToolCalls: true,
      compactMarkerUuids: NO_COMPACT_MARKERS,
    })
    expect(result).toHaveLength(1)
    expect(result[0]?.uuid).toBe('u-tool')
  })

  it('keeps a prelude message when showPrelude=true', () => {
    const m = makeMessage({ uuid: 'u-prelude', text: 'prelude text', is_prelude: true })
    const result = computeVisibleMessages([m], {
      showPrelude: true,
      showToolCalls: false,
      compactMarkerUuids: NO_COMPACT_MARKERS,
    })
    expect(result).toHaveLength(1)
    expect(result[0]?.uuid).toBe('u-prelude')
  })

  it('keeps an empty message when its UUID is in the compact-marker set (even with showToolCalls=false)', () => {
    // Compact-marker-anchor messages may have NO visible content of their
    // own — the CompactMarker affordance renders from `conversation.compact_markers`,
    // not from the message body. The filter must keep these UUIDs so the
    // separate `compactMarkerByUuid.get()` branch in the render loop fires.
    const m = makeMessage({ uuid: 'u-compact', text: '', content: [] })
    const result = computeVisibleMessages([m], {
      showPrelude: false,
      showToolCalls: false,
      compactMarkerUuids: new Set(['u-compact']),
    })
    expect(result).toHaveLength(1)
    expect(result[0]?.uuid).toBe('u-compact')
  })
})

describe('computeVisibleMessages — negative', () => {
  it('drops a tool-only message when showToolCalls=false', () => {
    // This IS the NIT-1 fix: without this filter, the outer `<div onClick>`
    // wrapper would render an empty 0-height band that produced a no-op
    // click in the gutter. Pins the dead-zone fix.
    const m = makeMessage({
      uuid: 'u-tool-hidden',
      text: '',
      content: [{ type: 'tool_use', name: 'Bash', input: {} }],
    })
    const result = computeVisibleMessages([m], {
      showPrelude: false,
      showToolCalls: false,
      compactMarkerUuids: NO_COMPACT_MARKERS,
    })
    expect(result).toHaveLength(0)
  })

  it('drops a prelude message when showPrelude=false (compact-markers off)', () => {
    const m = makeMessage({ uuid: 'u-pre-hidden', text: 'prelude', is_prelude: true })
    const result = computeVisibleMessages([m], {
      showPrelude: false,
      showToolCalls: true,
      compactMarkerUuids: NO_COMPACT_MARKERS,
    })
    expect(result).toHaveLength(0)
  })

  it('drops a prelude message even if showToolCalls=true (the gate is showPrelude)', () => {
    const m = makeMessage({
      uuid: 'u-pre-tool',
      text: '',
      content: [{ type: 'tool_use', name: 'Bash', input: {} }],
      is_prelude: true,
    })
    const result = computeVisibleMessages([m], {
      showPrelude: false,
      showToolCalls: true,
      compactMarkerUuids: NO_COMPACT_MARKERS,
    })
    expect(result).toHaveLength(0)
  })

  it('drops a message with no text, no content, no attachments, no compact-marker entry', () => {
    const m = makeMessage({ uuid: 'u-empty', text: '', content: [] })
    const result = computeVisibleMessages([m], {
      showPrelude: true,
      showToolCalls: true,
      compactMarkerUuids: NO_COMPACT_MARKERS,
    })
    expect(result).toHaveLength(0)
  })
})

describe('computeVisibleMessages — order + mixed', () => {
  it('preserves original order across a mixed list', () => {
    const messages: Message[] = [
      makeMessage({ uuid: 'u1', text: 'first' }),
      makeMessage({
        uuid: 'u2',
        text: '',
        content: [{ type: 'tool_use', name: 'Bash', input: {} }],
      }),
      makeMessage({ uuid: 'u3', text: 'third' }),
      makeMessage({ uuid: 'u4', text: '', is_prelude: true }),
      makeMessage({ uuid: 'u5', text: 'fifth' }),
    ]
    const result = computeVisibleMessages(messages, {
      showPrelude: false,
      showToolCalls: false,
      compactMarkerUuids: NO_COMPACT_MARKERS,
    })
    // u2 dropped (tool-only + showToolCalls=false); u4 dropped (prelude + showPrelude=false).
    expect(result.map((m) => m.uuid)).toEqual(['u1', 'u3', 'u5'])
  })

  it('compact-marker override survives the prelude gate (prelude+compact-marker rare but possible)', () => {
    // Defensive: if a backend ever marked a compact-marker-anchor message
    // as also is_prelude=true, the compact-marker branch should win
    // (compact summaries are always-on summary chrome, not session prelude).
    // Current behavior: prelude check fires FIRST, so a prelude marker
    // gets dropped. Pin this so a future flip is conscious.
    const m = makeMessage({
      uuid: 'u-pre-compact',
      text: '',
      content: [],
      is_prelude: true,
    })
    const result = computeVisibleMessages([m], {
      showPrelude: false,
      showToolCalls: false,
      compactMarkerUuids: new Set(['u-pre-compact']),
    })
    // Prelude-gate fires first: dropped.
    expect(result).toHaveLength(0)
  })

  it('returns an empty array for an empty input regardless of options', () => {
    const result = computeVisibleMessages([], {
      showPrelude: true,
      showToolCalls: true,
      compactMarkerUuids: new Set(['u-anything']),
    })
    expect(result).toEqual([])
  })
})

describe('computeVisibleMessages — the active search hit (2026-10-21)', () => {
  // A click on a search hit inside a tool-only message did nothing while
  // tool calls were hidden: the filter dropped the message, so the pane had
  // no element for it. The active-match message now stays in the list.
  const toolOnly = makeMessage({
    uuid: 'tool-only',
    content: [{ type: 'tool_result', content: [{ type: 'text', text: 'boom' }] }],
  })
  const other = makeMessage({
    uuid: 'other-tool-only',
    content: [{ type: 'tool_use', name: 'Bash', input: { command: 'ls' } }],
  })

  it('keeps the active-match message although tool calls are hidden', () => {
    const out = computeVisibleMessages([toolOnly, other], {
      showPrelude: true,
      showToolCalls: false,
      compactMarkerUuids: NO_COMPACT_MARKERS,
      revealUuid: 'tool-only',
    })
    expect(out.map((m) => m.uuid)).toEqual(['tool-only'])
  })

  it('drops it again when no hit is active', () => {
    const out = computeVisibleMessages([toolOnly, other], {
      showPrelude: true,
      showToolCalls: false,
      compactMarkerUuids: NO_COMPACT_MARKERS,
      revealUuid: null,
    })
    expect(out).toEqual([])
  })
})
