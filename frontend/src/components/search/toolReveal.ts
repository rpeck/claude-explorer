import type { ContentBlock, Message } from '@/lib/types'

import { computeHighlightRanges } from './highlightRanges'

/**
 * Which tool blocks of the active search hit's message to reveal
 * (2026-10-21).
 *
 * "Show tool calls" is off by default. A click on a search hit inside a
 * tool call or a tool result then did nothing: the message rendered no
 * element. Now the Conversation Pane shows ONLY the tool blocks that hold
 * the match, expanded and highlighted, and the setting does not change.
 *
 * Rules:
 *   - A tool block matches when its visible text holds a query term:
 *     the tool name and the input JSON for a call, the text for a result.
 *     The matching is the same as for the in-bubble `<mark>` highlights.
 *   - When the message's visible text holds the match, reveal nothing.
 *   - When nothing matches (the server's full-text search stems words, so
 *     "running" can match "run"), reveal every tool block, so that the
 *     bubble is not empty.
 */
export function revealedToolBlockIndexes(message: Message, query: string): number[] {
  if (!query.trim()) return []
  const blocks = message.content ?? []
  const toolIndexes: number[] = []
  const matching: number[] = []
  blocks.forEach((block, index) => {
    if (block.type !== 'tool_use' && block.type !== 'tool_result') return
    toolIndexes.push(index)
    if (hasMatch(toolBlockText(block), query)) matching.push(index)
  })
  if (matching.length > 0) return matching
  if (toolIndexes.length === 0) return []
  const visibleText = [
    message.text ?? '',
    ...blocks.filter((b) => b.type === 'text').map((b) => b.text ?? ''),
  ].join('\n')
  return hasMatch(visibleText, query) ? [] : toolIndexes
}

/** The text that a tool block shows when it is expanded. */
export function toolBlockText(block: ContentBlock): string {
  if (block.type === 'tool_use') {
    return `${block.name ?? ''}\n${JSON.stringify(block.input ?? null, null, 2)}`
  }
  if (block.type === 'tool_result') {
    return (block.content ?? [])
      .filter((b) => b.type === 'text')
      .map((b) => b.text ?? '')
      .join('\n')
  }
  return ''
}

function hasMatch(text: string, query: string): boolean {
  return text.length > 0 && computeHighlightRanges(text, query, -1, -1).length > 0
}
