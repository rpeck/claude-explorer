import { describe, expect, it } from 'vitest'
import {
  computeVisibleMessages,
  conversationHasCodeRun,
  conversationToMarkdown,
  messageHasVisibleContent,
  messageToMarkdown,
} from '@/lib/utils'
import { CM_MESSAGES, CM_WORDS } from '../fixtures/codeMode'

/**
 * Issue #90: the Show Code Mode toggle in the helpers that the viewer, the
 * keyboard navigation, and Copy as Markdown share. It is independent of
 * Show Tools: a run shows only with Show Code Mode on, whatever Show Tools
 * is; an unlinked Code Mode call is an ordinary tool call.
 */
const runMessage = CM_MESSAGES[1]
const unlinkedCall = CM_MESSAGES[3]

describe('messageHasVisibleContent with Show Code Mode', () => {
  it.each([
    [false, false, false],
    [true, false, false],
    [false, true, true],
    [true, true, true],
  ])('a run-only message: tools=%s code=%s -> visible=%s', (tools, code, want) => {
    expect(messageHasVisibleContent(runMessage, tools, code)).toBe(want)
  })

  it('an unlinked Code Mode call follows Show Tools only', () => {
    expect(messageHasVisibleContent(unlinkedCall, true, false)).toBe(true)
    expect(messageHasVisibleContent(unlinkedCall, false, true)).toBe(false)
  })
})

describe('computeVisibleMessages with Show Code Mode', () => {
  const base = { showPrelude: false, showToolCalls: false, compactMarkerUuids: new Set<string>() }
  it('drops the run message when Show Code Mode is off', () => {
    expect(computeVisibleMessages(CM_MESSAGES, base).map((m) => m.uuid)).toEqual(['cm-u1', 'cm-a2'])
  })
  it('keeps it when Show Code Mode is on', () => {
    expect(computeVisibleMessages(CM_MESSAGES, { ...base, showCodeMode: true }).map((m) => m.uuid)).toEqual([
      'cm-u1', 'cm-a1', 'cm-a2',
    ])
  })
})

describe('conversationHasCodeRun', () => {
  it('is true only when a message holds a code_run block', () => {
    expect(conversationHasCodeRun(CM_MESSAGES)).toBe(true)
    expect(conversationHasCodeRun([CM_MESSAGES[0], CM_MESSAGES[2], CM_MESSAGES[3]])).toBe(false)
  })
})

describe('Copy as Markdown with Show Code Mode', () => {
  it('on: the script in a javascript fence, the numbered calls, and the returned value', () => {
    const md = messageToMarkdown(runMessage, false, 'Claude', true)
    expect(md).toContain('```javascript')
    expect(md).toContain(CM_WORDS.code)
    expect(md).toContain('1. `Bash`')
    expect(md).toContain('2. `Write` (refused)')
    expect(md).toContain('Returned to the model')
    expect(md).toContain(CM_WORDS.returned)
  })

  it('off: no run text, and the run-only message leaves the conversation copy', () => {
    expect(messageToMarkdown(runMessage, true, 'Claude', false)).not.toContain(CM_WORDS.code)
    const md = conversationToMarkdown('T', CM_MESSAGES, false, 'Claude', false)
    expect(md).not.toContain(CM_WORDS.code)
    expect(md).toContain(CM_WORDS.reply)
  })
})
