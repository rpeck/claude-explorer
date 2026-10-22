import { describe, it, expect } from 'vitest'

import { revealedToolBlockIndexes } from '@/components/search/toolReveal'
import type { Message } from '@/lib/types'

/**
 * Which tool blocks of the active-match message to reveal (2026-10-21).
 *
 * With tool calls hidden, a click on a search hit shows only the tool
 * block that holds the match, not all of the message's tool blocks.
 */

function message(content: Message['content'], text = ''): Message {
  return {
    uuid: 'm',
    sender: 'assistant',
    text,
    content,
    created_at: '2026-10-21T00:00:00Z',
    updated_at: '2026-10-21T00:00:00Z',
    truncated: false,
    parent_message_uuid: null,
    attachments: [],
    files: [],
  }
}

describe('revealedToolBlockIndexes', () => {
  it('picks only the tool call whose input holds the match', () => {
    const m = message([
      { type: 'tool_use', name: 'Read', input: { file_path: 'zoo.py' } },
      { type: 'tool_use', name: 'Edit', input: { old_string: 'wombat text' } },
    ])
    expect(revealedToolBlockIndexes(m, 'wombat')).toEqual([1])
  })

  it('picks a tool result by its text', () => {
    const m = message([
      { type: 'tool_result', content: [{ type: 'text', text: 'all green' }] },
      { type: 'tool_result', content: [{ type: 'text', text: '1 failed: kumquat' }] },
    ])
    expect(revealedToolBlockIndexes(m, 'Kumquat')).toEqual([1])
  })

  it('matches a tool name', () => {
    const m = message([{ type: 'tool_use', name: 'WebFetch', input: { url: 'x' } }])
    expect(revealedToolBlockIndexes(m, 'webfetch')).toEqual([0])
  })

  it('reveals nothing when the visible text holds the match', () => {
    const m = message(
      [
        { type: 'text', text: 'the pelican said hi' },
        { type: 'tool_use', name: 'Bash', input: { command: 'ls' } },
      ],
      'the pelican said hi',
    )
    expect(revealedToolBlockIndexes(m, 'pelican')).toEqual([])
  })

  it('reveals every tool block when nothing visible matches (for example a stemmed match)', () => {
    // The server's full-text search stems words, so "running" can match
    // "run". The bubble must not stay empty.
    const m = message([
      { type: 'tool_use', name: 'Bash', input: { command: 'run tests' } },
      { type: 'tool_result', content: [{ type: 'text', text: 'ok' }] },
    ])
    expect(revealedToolBlockIndexes(m, 'running')).toEqual([0, 1])
  })

  it('reveals nothing for an empty query', () => {
    const m = message([{ type: 'tool_use', name: 'Bash', input: { command: 'ls' } }])
    expect(revealedToolBlockIndexes(m, '')).toEqual([])
  })
})
