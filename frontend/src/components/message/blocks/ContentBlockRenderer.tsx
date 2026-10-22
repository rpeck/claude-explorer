import type { ContentBlock } from '@/lib/types'
import type { CcImageEntries } from './imageCollection'
import { ToolUseBlock, ToolResultBlock } from './ToolBlocks'
import { CcImageMarkerText, InlineImageBlock } from './ImageBlocks'

interface ContentBlockListProps {
  content: ContentBlock[]
  showToolCalls: boolean
  expandAll?: boolean
  /** Indexes of the tool blocks that hold the active search hit
   *  (2026-10-21). They render, expanded, even when tool calls are
   *  hidden. */
  revealedToolBlocks?: readonly number[]
  ccImageEntries: CcImageEntries
  onOpenCcImage: (index: number) => void
  searchQuery?: string
}

/**
 * Renders an ordered list of message content blocks. Thin pass-through:
 * threads each block + its block index into `ContentBlockRenderer`,
 * which is the discriminated switch over the block type.
 */
export function ContentBlockList({
  content,
  showToolCalls,
  expandAll,
  revealedToolBlocks,
  ccImageEntries,
  onOpenCcImage,
  searchQuery,
}: ContentBlockListProps) {
  return (
    <>
      {content.map((block, index) => (
        <ContentBlockRenderer
          key={index}
          block={block}
          blockIndex={index}
          showToolCalls={showToolCalls}
          expandAll={expandAll}
          revealed={revealedToolBlocks?.includes(index) ?? false}
          ccImageEntries={ccImageEntries}
          onOpenCcImage={onOpenCcImage}
          searchQuery={searchQuery}
        />
      ))}
    </>
  )
}

interface ContentBlockRendererProps {
  block: ContentBlock
  blockIndex: number
  showToolCalls: boolean
  expandAll?: boolean
  /** This tool block holds the active search hit: render it, expanded. */
  revealed?: boolean
  ccImageEntries: CcImageEntries
  onOpenCcImage: (index: number) => void
  searchQuery?: string
}

/**
 * Discriminated switch over `ContentBlock.type`.
 *
 * Note `ContentBlock.type` is closed on the TS side (`'text' |
 * 'tool_use' | 'tool_result' | 'image' | 'thinking'`) while open on the
 * backend side (`str`) — see lib/types.ts:120-129. The `default` arm
 * therefore must `return null` for forward-compat with new Anthropic
 * block types.
 */
export function ContentBlockRenderer({
  block,
  blockIndex,
  showToolCalls,
  expandAll,
  revealed = false,
  ccImageEntries,
  onOpenCcImage,
  searchQuery,
}: ContentBlockRendererProps) {
  switch (block.type) {
    case 'text':
      return (
        <CcImageMarkerText
          content={block.text || ''}
          showToolCalls={showToolCalls}
          startCcIndex={ccImageEntries.blockOffsets[blockIndex] ?? 0}
          onOpenCcImage={onOpenCcImage}
          searchQuery={searchQuery}
        />
      )
    case 'tool_use':
      return showToolCalls || revealed ? (
        <ToolUseBlock
          name={block.name || ''}
          input={block.input}
          forceExpanded={expandAll || revealed}
          searchQuery={searchQuery}
        />
      ) : null
    case 'tool_result':
      return showToolCalls || revealed ? (
        <ToolResultBlock
          content={block.content || []}
          forceExpanded={expandAll || revealed}
          searchQuery={searchQuery}
        />
      ) : null
    case 'image': {
      // Claude Code embeds images as inline content blocks of shape
      // { type: 'image', source: { type: 'base64', media_type: '...', data: '...' } }
      // alongside a sibling text block carrying the "[Image #N]"
      // marker. Click opens the in-page lightbox (Issue #1).
      const ccIndex = ccImageEntries.blockOffsets[blockIndex] ?? 0
      return (
        <InlineImageBlock
          source={block.source}
          onOpen={() => onOpenCcImage(ccIndex)}
        />
      )
    }
    default:
      return null
  }
}
