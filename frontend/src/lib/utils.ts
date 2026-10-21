import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { format, formatDistanceToNow, isToday, isYesterday } from 'date-fns'
import type { Message, ContentBlock } from './types'
import { dedupeImageFiles, imageAltText, previewSrc } from './imageFiles'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// 2026-05-18 council audit (mirror of backend H1-H4): all four date
// formatters previously accepted `string | Date` non-null. But the
// nullable wire fields (MessageSnippet.created_at: string | null;
// any drift on Message/ConversationSummary date fields) and bad-string
// inputs (e.g. backend serialization regression) could reach these
// helpers and:
//   - `new Date(null)` returns 1970-01-01 — silently renders epoch
//     dates that look like data corruption ("Jan 1, 1970" in UI).
//   - `new Date('bad-string')` returns Invalid Date, and date-fns
//     `format(invalidDate, ...)` throws RangeError, crashing the page.
//
// All four now accept `null | undefined | invalid-Date` and return the
// industry-standard em-dash placeholder. Preserves layout (the span
// still has content) and surfaces the absent-date case to the user
// without crashing. Aligns with the backend `(data.get(k) or "")`
// "missing is empty, don't crash" invariant.
const ABSENT_DATE_PLACEHOLDER = '—'

function toValidDate(date: string | Date | null | undefined): Date | null {
  if (date == null) return null
  const d = typeof date === 'string' ? new Date(date) : date
  return Number.isNaN(d.getTime()) ? null : d
}

export function formatDate(date: string | Date | null | undefined): string {
  const d = toValidDate(date)
  if (!d) return ABSENT_DATE_PLACEHOLDER

  if (isToday(d)) {
    return format(d, 'h:mm a')
  }
  if (isYesterday(d)) {
    return 'Yesterday'
  }
  return format(d, 'MMM d')
}

export function formatMessageTimestamp(date: string | Date | null | undefined): string {
  const d = toValidDate(date)
  if (!d) return ABSENT_DATE_PLACEHOLDER

  if (isToday(d)) {
    return format(d, 'h:mm:ss a')
  }
  if (isYesterday(d)) {
    return 'Yesterday ' + format(d, 'h:mm:ss a')
  }
  return format(d, 'MMM d, yyyy h:mm:ss a')
}

export function formatRelativeDate(date: string | Date | null | undefined): string {
  const d = toValidDate(date)
  if (!d) return ABSENT_DATE_PLACEHOLDER
  return formatDistanceToNow(d, { addSuffix: true })
}

export function formatFullDate(date: string | Date | null | undefined): string {
  const d = toValidDate(date)
  if (!d) return ABSENT_DATE_PLACEHOLDER
  return format(d, 'PPpp')
}

/**
 * Check if a message has any visible content (considering tool call visibility).
 * A message with image attachments is always visible regardless of text/tool
 * content (Council Q7: images are primary content, not gated by toggles).
 */
export function messageHasVisibleContent(message: Message, showToolCalls: boolean): boolean {
  if (dedupeImageFiles(message).length > 0) return true
  if (message.text && message.text.trim()) {
    // Check if it's only tool placeholders
    if (!showToolCalls) {
      const filtered = message.text
        .replace(
          /```\s*\n?\s*(?:This block is not supported on your current device yet\.|Viewing artifacts created via the Analysis Tool web feature preview isn't yet supported on mobile\.)\s*\n?\s*```/g,
          ''
        )
        .trim()
      if (!filtered) return false
    }
    return true
  }
  if (message.content && message.content.length > 0) {
    return message.content.some((block) => {
      if (block.type === 'text' && block.text?.trim()) return true
      if ((block.type === 'tool_use' || block.type === 'tool_result') && showToolCalls) return true
      // Claude Code inline image content blocks count as visible
      // content (always — never gated by showToolCalls).
      if (block.type === 'image' && block.source) return true
      return false
    })
  }
  return false
}

/**
 * Compute the "visible" subset of a conversation's messages for the
 * detail-pane render loop. Keeps render and keyboard-nav registration
 * in lockstep so the user can't click an empty wrapper that produces
 * a no-op `findIndex(uuid) === -1`.
 *
 * Rules:
 *   - Drop `is_prelude` messages when `showPrelude=false` (matches the
 *     SessionPreludeAffordance contract).
 *   - 2026-05-24 fix: when `hideCompactSummaries=true`, drop messages
 *     whose UUID is in `compactMarkerUuids` entirely. The
 *     isCompactSummary message body (the LLM-written summary text) and
 *     the /compact trigger row are both chrome the user has chosen
 *     to suppress. Without this drop, those messages fell through to
 *     `messageHasVisibleContent` (which returns true because their
 *     text is non-empty) and rendered as plain user-prompt-styled
 *     bubbles — the bug the user reported in the screenshot.
 *   - When `hideCompactSummaries=false`: keep any message whose UUID
 *     appears in `compactMarkerUuids`. The CompactMarker affordance
 *     renders from a separate Map; the bare message body never
 *     reaches MessageBubble (renderBubbleRow swaps it for a
 *     CompactMarker via compactMarkerByUuid).
 *   - Otherwise keep iff `messageHasVisibleContent(m, showToolCalls)`
 *     — same predicate the keyboard-nav registration uses.
 *
 * NIT-1 + image-only nav-alignment (council follow-up, 2026-05-22):
 * was previously a `.filter((m) => !m.is_prelude)` only, which left
 * empty wrappers for tool-only messages and produced a click dead zone.
 */
export function computeVisibleMessages(
  messages: readonly Message[],
  opts: {
    showPrelude: boolean
    showToolCalls: boolean
    compactMarkerUuids: ReadonlySet<string>
    /** When true, drop any message whose UUID is in
     *  `compactMarkerUuids` from the rendered list entirely. Default
     *  false preserves the existing "always keep markers" behavior
     *  for non-CC / show-on call sites. The conversation view passes
     *  true when the user unchecks "Show Compactions". */
    hideCompactSummaries?: boolean
    /** The message of the active search hit (2026-10-21). It stays in
     *  the list although tool calls are hidden, because its bubble then
     *  shows the tool block that holds the match (`toolReveal.ts`). */
    revealUuid?: string | null
  },
): Message[] {
  const hide = opts.hideCompactSummaries === true
  return messages.filter((m) => {
    if (!opts.showPrelude && m.is_prelude) return false
    if (opts.compactMarkerUuids.has(m.uuid)) return !hide
    if (opts.revealUuid && m.uuid === opts.revealUuid) return true
    return messageHasVisibleContent(m, opts.showToolCalls)
  })
}

export function sanitizeFilename(name: string): string {
  return name
    .replace(/[<>:"/\\|?*]/g, '-')
    .replace(/\s+/g, '_')
    .slice(0, 100)
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// Remove tool placeholder blocks from markdown text. Matches both the
// "tool / artifact" placeholder and the "mobile artifact preview" placeholder
// Claude Desktop emits — see backend/export.py::TOOL_PLACEHOLDERS.
function filterToolPlaceholders(text: string): string {
  const pattern =
    /```\s*\n?\s*(?:This block is not supported on your current device yet\.|Viewing artifacts created via the Analysis Tool web feature preview isn't yet supported on mobile\.)\s*\n?\s*```/g
  return text.replace(pattern, '').replace(/\n{3,}/g, '\n\n')
}

function contentBlockToMarkdown(block: ContentBlock, showToolCalls: boolean): string {
  switch (block.type) {
    case 'text':
      return block.text || ''
    case 'tool_use':
      if (!showToolCalls) return ''
      return `\n\n<details>\n<summary>Tool: ${block.name}</summary>\n\n\`\`\`json\n${JSON.stringify(block.input, null, 2)}\n\`\`\`\n</details>\n`
    case 'tool_result': {
      if (!showToolCalls) return ''
      const textContent = (block.content || [])
        .filter((b) => b.type === 'text')
        .map((b) => b.text)
        .join('\n')
      return `\n\n<details>\n<summary>Tool Result</summary>\n\n\`\`\`\n${textContent}\n\`\`\`\n</details>\n`
    }
    default:
      return ''
  }
}

export function messageToMarkdown(message: Message, showToolCalls: boolean): string {
  const sender = message.sender === 'human' ? 'You' : 'Claude'
  let content = ''

  if (message.content && message.content.length > 0) {
    content = message.content
      .map((block) => contentBlockToMarkdown(block, showToolCalls))
      .join('')
  } else {
    // 2026-05-18 council audit: `message.text` is typed `string` but
    // can surface null/undefined on partial wire-format drift. The
    // subsequent `content.trim()` at the bottom of this function would
    // throw `TypeError: Cannot read properties of null (reading
    // 'trim')` and crash the export pipeline. Coalesce defensively.
    content = message.text ?? ''
  }

  // Filter out tool placeholders if showToolCalls is false
  if (!showToolCalls) {
    content = filterToolPlaceholders(content)
  }

  // Append image attachments as Markdown image references. The URLs are
  // claude.ai-relative (e.g. /api/.../preview) and resolve via the local
  // Claude Explorer backend proxy — they will 404 if pasted into a
  // serverless Markdown viewer, which is documented in the article.
  const images = dedupeImageFiles(message)
  let imagesMd = ''
  if (images.length > 0) {
    imagesMd = '\n\n' + images
      .map((img) => {
        const url = previewSrc(img)
        const alt = imageAltText(img)
        return url ? `![${alt}](${url})` : `_(image attachment unavailable: ${img.file_name})_`
      })
      .join('\n\n')
  }

  return `**${sender}:**\n\n${content.trim()}${imagesMd}`
}

/**
 * Mirror of `backend/export.py::_is_excludable_marker` (export.py:159) —
 * V1 polish cleanup (2026-05-13).
 *
 * Argless slash markers (`is_command_marker=True`: `/exit`, `/clear`,
 * `/compact`) and leading prelude rows (`is_prelude=True`, which post-Fix-2
 * implies `is_command_marker=True`) are CHROME, not user content. The
 * viewer hides them behind `SessionPreludeAffordance` / `SlashCommandBadge`,
 * the backend export drops them via `_is_excludable_marker`, and search
 * excludes them via `_extract_searchable_text`'s early-return. This mirror
 * applies the same exclusion to the client-side "Copy as Markdown" action
 * so the clipboard payload matches the viewer's rendered output and the
 * backend export bundles. Spec invariant "one truth, three (now four)
 * surfaces": viewer + search + server export + client copy.
 *
 * Argful markers (`/coding <prose>`, `/plan <prose>`) carry
 * `is_command_marker=False` post-Fix-2, so they pass through this filter
 * and copy normally — they carry the user's real prose.
 *
 * Why duplicate the predicate in the frontend instead of routing copy
 * through the backend's `/api/conversations/{uuid}/export/markdown`
 * endpoint? Copy is a clipboard action that must feel instant; a network
 * round-trip would introduce latency and a failure mode (offline / slow
 * backend) for a hotpath the user invokes often. The predicate is also
 * trivial — a single boolean field check — so duplication is cheap.
 * Keep the two implementations in sync: any change to `_is_excludable_marker`
 * in `backend/export.py` MUST be mirrored here.
 */
export function isExcludableMarker(message: Message): boolean {
  return message.is_command_marker === true
}

export function conversationToMarkdown(
  title: string,
  messages: Message[],
  showToolCalls: boolean
): string {
  const header = `# ${title}\n\n`
  const body = messages
    .filter((msg) => !isExcludableMarker(msg))
    .filter((msg) => messageHasVisibleContent(msg, showToolCalls))
    .map((msg) => messageToMarkdown(msg, showToolCalls))
    .join('\n\n---\n\n')
  return header + body
}