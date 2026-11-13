import { Fragment } from 'react'
import { splitTitleEmoji, TITLE_EMOJI_CLASS } from '@/lib/titleEmoji'

/**
 * Render a conversation title, with each emoji in grayscale (issue #44).
 *
 * Use it everywhere the app shows a title, so that a new place cannot miss
 * the rule. The rendered text is exactly `text`; only the style changes.
 * See `lib/titleEmoji.ts` for the reason and the browser results.
 */
export function TitleText({ text }: { text: string }) {
  const parts = splitTitleEmoji(text)
  if (!parts.some((p) => p.emoji)) return <>{text}</>
  return (
    <>
      {parts.map((p, i) =>
        p.emoji ? (
          <span key={i} className={`${TITLE_EMOJI_CLASS} grayscale`}>
            {p.text}
          </span>
        ) : (
          <Fragment key={i}>{p.text}</Fragment>
        ),
      )}
    </>
  )
}
