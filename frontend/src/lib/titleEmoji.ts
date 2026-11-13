/**
 * Emoji in conversation titles (issue #44).
 *
 * The API sends titles such as "⚡ Weekly report". In color, a leading
 * emoji looked like one of the app's colored source icons, so users did
 * not see it as part of the title. `TitleText` puts each emoji in a span
 * with `TITLE_EMOJI_CLASS` and Tailwind's `grayscale` class, so the emoji
 * has no color. The text of the title does not change.
 *
 * Why grayscale and not `font-variant-emoji: text`: measured on macOS
 * (2026-11-12), `font-variant-emoji: text` changed only ⚡ in Chromium,
 * had no effect in WebKit, and drew empty "missing glyph" boxes in
 * Firefox. Firefox then also drew boxes for the same emoji in later
 * elements of the page, without that property. Do not add
 * `font-variant-emoji` anywhere in the app. `filter: grayscale(1)` works
 * in all three engines.
 */

/** Marks each emoji span of a title. Tests find the spans by this class. */
export const TITLE_EMOJI_CLASS = 'title-emoji'

export interface TitlePart {
  text: string
  emoji: boolean
}

// One emoji cluster:
//   - a flag (two regional indicators);
//   - a keycap (digit, # or *, optional VS16, U+20E3);
//   - a pictographic character with an optional VS16 or skin-tone modifier,
//     optional tag characters (subdivision flags), and ZWJ-joined parts.
const EMOJI_CLUSTER =
  /\p{Regional_Indicator}{2}|[#*0-9]️?⃣|\p{Extended_Pictographic}(?:️|\p{Emoji_Modifier})?[\u{E0020}-\u{E007F}]*(?:‍\p{Extended_Pictographic}(?:️|\p{Emoji_Modifier})?)*/gu

/** Split a title into emoji clusters and plain text, in order. */
export function splitTitleEmoji(text: string): TitlePart[] {
  const parts: TitlePart[] = []
  let cursor = 0
  for (const m of text.matchAll(EMOJI_CLUSTER)) {
    const start = m.index ?? 0
    if (start > cursor) parts.push({ text: text.slice(cursor, start), emoji: false })
    parts.push({ text: m[0], emoji: true })
    cursor = start + m[0].length
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), emoji: false })
  return parts
}
