import { useState } from 'react'
import { ChevronDown, ChevronRight, Copy, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useCopyFeedback } from '@/hooks/useCopyFeedback'
import { errorToast } from '@/lib/errorToast'
import type { ContentBlock } from '@/lib/types'
import { HighlightedText } from '@/components/HighlightedText'

interface ToolUseBlockProps {
  name: string
  input: unknown
  forceExpanded?: boolean
  /** The active search query: its matches get `<mark>` highlights. */
  searchQuery?: string
}

export function ToolUseBlock({ name, input, forceExpanded, searchQuery = '' }: ToolUseBlockProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const { copied, trigger } = useCopyFeedback()

  const expanded = forceExpanded || isExpanded
  const inputJson = JSON.stringify(input, null, 2)

  const handleCopy = async () => {
    // LOW-1 (council follow-up): see MessageBubble.handleCopyMessage for
    // rationale. Flip the success affordance only on a resolved promise.
    try {
      await navigator.clipboard.writeText(inputJson)
      trigger()
    } catch {
      errorToast('Failed to copy to clipboard.')
    }
  }

  return (
    <div className="my-2 rounded-md border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-amber-800 dark:text-amber-200"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4" />
        ) : (
          <ChevronRight className="h-4 w-4" />
        )}
        <span>
          Tool: <HighlightedText text={name} query={searchQuery} />
        </span>
      </button>
      {expanded && (
        <div className="relative border-t border-amber-200 dark:border-amber-800">
          <Button
            variant="ghost"
            size="icon"
            className="absolute right-2 top-2 h-6 w-6"
            onClick={handleCopy}
          >
            {copied ? (
              <Check className="h-3 w-3 text-green-500" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
          </Button>
          <pre className="overflow-x-auto p-3 text-xs text-amber-900 dark:text-amber-100">
            <HighlightedText text={inputJson} query={searchQuery} />
          </pre>
        </div>
      )}
    </div>
  )
}

interface ToolResultBlockProps {
  content: ContentBlock[]
  forceExpanded?: boolean
  /** The active search query: its matches get `<mark>` highlights. */
  searchQuery?: string
}

export function ToolResultBlock({ content, forceExpanded, searchQuery = '' }: ToolResultBlockProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  const expanded = forceExpanded || isExpanded

  // Extract text content for preview
  const textContent = content
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('\n')

  const previewLength = 200
  const needsTruncation = textContent.length > previewLength

  return (
    <div className="my-2 rounded-md border border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-zinc-700 dark:text-zinc-300"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4" />
        ) : (
          <ChevronRight className="h-4 w-4" />
        )}
        <span>Tool Result</span>
        {!expanded && needsTruncation && (
          <span className="text-xs text-zinc-500">
            ({textContent.length} chars)
          </span>
        )}
      </button>
      {expanded && (
        <div className="border-t border-zinc-200 p-3 dark:border-zinc-700">
          <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-zinc-700 dark:text-zinc-300">
            <HighlightedText text={textContent} query={searchQuery} />
          </pre>
        </div>
      )}
    </div>
  )
}
