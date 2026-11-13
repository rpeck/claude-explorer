/**
 * TitleText — issue #44: an emoji in a title looked like an app icon.
 *
 * Contract:
 *   1. The text of the title does not change. Copy, export, search, and
 *      filters work on the real title, so `textContent` must equal the
 *      title exactly, emoji included.
 *   2. Each emoji (a whole cluster: ZWJ sequence, flag, keycap, skin tone,
 *      variation selector) is in one `span.title-emoji`. Plain text is not.
 *   3. Each emoji span has Tailwind's `grayscale` class. jsdom does not
 *      apply Tailwind, so the e2e spec `title-emoji.spec.ts` checks the
 *      computed style and the pixels in a real browser.
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { TitleText } from '@/components/TitleText'
import { splitTitleEmoji, TITLE_EMOJI_CLASS } from '@/lib/titleEmoji'

function emojiSpans(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll(`span.${TITLE_EMOJI_CLASS}`)).map(
    (el) => el.textContent ?? '',
  )
}

describe('splitTitleEmoji', () => {
  it('splits a leading emoji from the text', () => {
    expect(splitTitleEmoji('⚡ Weekly report')).toEqual([
      { text: '⚡', emoji: true },
      { text: ' Weekly report', emoji: false },
    ])
  })

  it('returns one plain part for a title without emoji', () => {
    expect(splitTitleEmoji('Weekly report')).toEqual([{ text: 'Weekly report', emoji: false }])
  })

  it('returns no parts for an empty title', () => {
    expect(splitTitleEmoji('')).toEqual([])
  })

  const clusters: Array<[string, string]> = [
    ['an emoji outside the BMP', '📊'],
    ['a variation selector 16', '❤️'],
    ['a ZWJ sequence', '👩‍💻'],
    ['a skin-tone modifier', '👍🏽'],
    ['a flag', '🇺🇸'],
    ['a keycap', '1️⃣'],
    ['a tag sequence', '🏴󠁧󠁢󠁳󠁣󠁴󠁿'],
  ]
  for (const [label, cluster] of clusters) {
    it(`keeps ${label} in one part`, () => {
      const parts = splitTitleEmoji(`${cluster} Report`)
      expect(parts).toEqual([
        { text: cluster, emoji: true },
        { text: ' Report', emoji: false },
      ])
    })
  }

  it('does not treat a plain digit as an emoji', () => {
    expect(splitTitleEmoji('Q1 2026 plan')).toEqual([{ text: 'Q1 2026 plan', emoji: false }])
  })

  it('finds emoji in the middle and at the end of a title', () => {
    expect(splitTitleEmoji('Fix 🐛 in parser 🚀')).toEqual([
      { text: 'Fix ', emoji: false },
      { text: '🐛', emoji: true },
      { text: ' in parser ', emoji: false },
      { text: '🚀', emoji: true },
    ])
  })
})

describe('TitleText', () => {
  const titles = [
    '⚡ Weekly report',
    '📊 Sales ❤️ data 👩‍💻',
    'Weekly report',
    '🇺🇸1️⃣',
  ]
  for (const title of titles) {
    it(`keeps the exact text of "${title}"`, () => {
      const { container } = render(
        <span>
          <TitleText text={title} />
        </span>,
      )
      expect(container.textContent).toBe(title)
    })
  }

  it('puts each emoji in a title-emoji span', () => {
    const { container } = render(
      <span>
        <TitleText text="📊 Sales ❤️ data 👩‍💻" />
      </span>,
    )
    expect(emojiSpans(container)).toEqual(['📊', '❤️', '👩‍💻'])
  })

  it('adds no span to a title without emoji', () => {
    const { container } = render(
      <span>
        <TitleText text="Weekly report" />
      </span>,
    )
    expect(container.querySelectorAll('span span')).toHaveLength(0)
  })
})

describe('the style of an emoji span', () => {
  it('has the grayscale class', () => {
    const { container } = render(
      <span>
        <TitleText text="⚡ Weekly report" />
      </span>,
    )
    const span = container.querySelector(`span.${TITLE_EMOJI_CLASS}`)
    expect(span).toHaveClass('grayscale')
  })
})
