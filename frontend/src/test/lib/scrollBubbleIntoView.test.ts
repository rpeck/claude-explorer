/**
 * scrollBubbleIntoView — distance-gated scroll with post-settle
 * correction. Pins the 5 invariants the 3-model LLM council settled
 * on (2026-05-20):
 *
 *   1. Long hop (>1.5 viewports) → `behavior: 'auto'` (instant) on
 *      initial scroll. Avoids the lazy-image swept-region inflation.
 *   2. Short hop (≤1.5 viewports) → `behavior: 'smooth'` on initial
 *      scroll. Preserves motion cue for the working case.
 *   3. No scroll container → falls back to `smooth, center` direct.
 *      Keeps the helper safe for printing / alternate layouts / tests.
 *   4. Post-settle correction fires at 250ms and uses `behavior: 'auto'`
 *      (snap, not animate) ONLY when target is off-center by >100px.
 *      No-ops otherwise.
 *   5. Supersession token: a NEWER call after the initial scroll but
 *      BEFORE the 250ms correction cancels the older correction. Pins
 *      the rapid-Cmd+G race the council called out.
 *
 * These are bidirectional — for each rule we assert both that the
 * triggering condition produces the behavior AND that the inverse
 * condition does NOT.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  scrollBubbleIntoView,
  __resetScrollToken,
} from '@/lib/scrollBubbleIntoView'

interface RectInit {
  top: number
  left?: number
  width?: number
  height: number
}

/**
 * Stub `getBoundingClientRect` on a real DOM element. jsdom returns
 * all-zeros by default; the helper needs real geometry to take
 * branch decisions.
 */
function stubRect(el: Element, init: RectInit) {
  const { top, left = 0, width = 100, height } = init
  const rect: DOMRect = {
    top,
    left,
    width,
    height,
    right: left + width,
    bottom: top + height,
    x: left,
    y: top,
    toJSON: () => ({}),
  }
  vi.spyOn(el, 'getBoundingClientRect').mockReturnValue(rect)
}

describe('scrollBubbleIntoView — distance-gated scroll + supersession', () => {
  let scrollIntoViewSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    __resetScrollToken()
    vi.useFakeTimers()
    Element.prototype.scrollIntoView = vi.fn()
    scrollIntoViewSpy = vi.spyOn(Element.prototype, 'scrollIntoView')
  })

  afterEach(() => {
    vi.useRealTimers()
    scrollIntoViewSpy.mockRestore()
    document.body.innerHTML = ''
  })

  // ─────────────────────────────────────────────────────────────
  // Helper: build a `[data-testid="message-stream"]` container of
  // height=1000 with a target bubble inside positioned by `targetTop`
  // (relative to viewport, simulating where rect.top would be).
  // ─────────────────────────────────────────────────────────────
  function setupContainerAndTarget(targetTop: number, targetHeight = 80) {
    const container = document.createElement('div')
    container.setAttribute('data-testid', 'message-stream')
    const target = document.createElement('div')
    container.appendChild(target)
    document.body.appendChild(container)
    // Container is the viewport. height=1000, top=0 → center=500.
    stubRect(container, { top: 0, height: 1000 })
    stubRect(target, { top: targetTop, height: targetHeight })
    return { container, target }
  }

  // Rule 1 — long hop → instant ('auto').
  it('long hop (>1.5 viewports off-center) → behavior:auto on initial scroll', () => {
    // viewport h=1000, center=500. Target top=3000 → center=3040.
    // distance=2540px = 2.54 viewports >1.5 → long hop.
    const { target } = setupContainerAndTarget(3000)
    scrollBubbleIntoView(target as HTMLElement)
    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: 'auto', block: 'center' })
  })

  // Rule 2 — short hop → smooth.
  it('short hop (≤1.5 viewports off-center) → behavior:smooth on initial scroll', () => {
    // viewport h=1000, center=500. Target top=1000 → center=1040.
    // distance=540px = 0.54 viewports ≤1.5 → short hop.
    const { target } = setupContainerAndTarget(1000)
    scrollBubbleIntoView(target as HTMLElement)
    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
  })

  // Rule 3 — no container → fallback to smooth/center direct.
  it('no [data-testid="message-stream"] container → fallback to smooth/center', () => {
    const target = document.createElement('div')
    document.body.appendChild(target)
    scrollBubbleIntoView(target as HTMLElement)
    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
  })

  // forceInstant — bypasses smooth-scroll even on short hops. Used by
  // the post-toggle re-center in ConversationPage where smooth motion
  // would feel disconnected from the originating checkbox click. The
  // multi-shot correction tail still runs (unchanged from the default).
  it('forceInstant=true → behavior:auto even on a short hop', () => {
    // viewport h=1000, center=500. Target top=1000 → center=1040.
    // distance=540px = 0.54 viewports — short hop. Default behavior is
    // 'smooth' (Rule 2); forceInstant must override to 'auto'.
    const { target } = setupContainerAndTarget(1000)
    scrollBubbleIntoView(target as HTMLElement, true)
    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: 'auto', block: 'center' })
  })

  // forceInstant pair — explicit false still picks the distance-based
  // default (forceInstant defaults to false; tests the negative case).
  it('forceInstant=false → distance-based default (short hop stays smooth)', () => {
    const { target } = setupContainerAndTarget(1000)
    scrollBubbleIntoView(target as HTMLElement, false)
    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
  })

  // forceInstant with the no-container fallback — also instant.
  it('forceInstant=true with no container → fallback uses auto', () => {
    const target = document.createElement('div')
    document.body.appendChild(target)
    scrollBubbleIntoView(target as HTMLElement, true)
    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: 'auto', block: 'center' })
  })

  // Rule 4a — correction fires when target ends up off-center by >100px.
  it('post-settle correction fires when target drifted >100px during animation', () => {
    const { container, target } = setupContainerAndTarget(1000)
    scrollBubbleIntoView(target as HTMLElement)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(1)

    // Simulate lazy-image growth shifting the target's post-scroll
    // position to 200px below container center.
    stubRect(target, { top: 600, height: 80 })  // center=640, container center=500, off=140 >100
    stubRect(container, { top: 0, height: 1000 })

    vi.advanceTimersByTime(250)
    // Second call from the correction. Must be 'auto' (snap, not
    // animate) so the user doesn't see two competing smooth motions.
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(2)
    expect(scrollIntoViewSpy).toHaveBeenLastCalledWith({ behavior: 'auto', block: 'center' })
  })

  // Rule 4b — correction NO-OPS when target landed within ±100px.
  it('post-settle correction does NOT fire when target landed within ±100px', () => {
    const { container, target } = setupContainerAndTarget(1000)
    scrollBubbleIntoView(target as HTMLElement)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(1)

    // Simulate target landed close to center: top=450, height=80
    // → center=490, container center=500, off=10 ≤100. No correction.
    stubRect(target, { top: 450, height: 80 })
    stubRect(container, { top: 0, height: 1000 })

    vi.advanceTimersByTime(250)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(1)
  })

  // Rule 5 — supersession: newer call cancels older correction.
  //
  // Discriminator: targetA is positioned where it would TRIGGER a
  // correction (off by >100px) if the token logic were broken. targetB
  // is positioned where its OWN correction no-ops (landed on-center).
  // So:
  //   - Broken token logic → A's stale correction fires + B's no-ops
  //     = 3 total scrollIntoView calls (A init, B init, A stale).
  //   - Correct token logic → A's stale correction superseded, B's
  //     no-ops = 2 total calls (A init, B init).
  it('newer call supersedes the older correction (rapid Cmd+G race)', () => {
    const { container: containerA, target: targetA } = setupContainerAndTarget(1000)
    scrollBubbleIntoView(targetA as HTMLElement)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(1)

    // Second target — positioned EXACTLY on container center so its
    // own correction is guaranteed to no-op (off=0px ≤100). This
    // isolates the test to "did A's stale correction fire?".
    const targetB = document.createElement('div')
    containerA.appendChild(targetB)
    stubRect(targetB, { top: 460, height: 80 })  // center=500 = container center

    scrollBubbleIntoView(targetB as HTMLElement)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(2)

    // Make targetA appear off-center so its correction WOULD fire if
    // the token check didn't supersede it.
    stubRect(targetA, { top: 5000, height: 80 })
    stubRect(containerA, { top: 0, height: 1000 })

    vi.advanceTimersByTime(250)
    // Token-correct: only 2 calls. B's correction no-ops (on-center),
    // A's stale correction is superseded.
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(2)
  })

  // Rule 5 (negative) — if user does NOT click again, the correction
  // does fire (sanity check that the token logic isn't blanket-blocking).
  it('without a newer call, correction fires for the original target', () => {
    const { container, target } = setupContainerAndTarget(1000)
    scrollBubbleIntoView(target as HTMLElement)

    // Same drift as the 4a case.
    stubRect(target, { top: 600, height: 80 })
    stubRect(container, { top: 0, height: 1000 })

    vi.advanceTimersByTime(250)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(2)
  })

  // Rule 6 — bounded multi-shot correction. Real-world bug (2026-05-22):
  // even after the 250ms one-shot correction lands the target, MORE
  // images decode in the next ~1s and push the target out of view
  // again. Fix: up to 3 corrections at 250ms / 750ms / 1250ms (or
  // whatever schedule the implementation uses), each conditional on
  // drift exceeding the threshold.
  it('multi-shot: correction fires repeatedly while drift persists across the settle window', () => {
    const { container, target } = setupContainerAndTarget(1000)
    scrollBubbleIntoView(target as HTMLElement)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(1)  // initial

    // Persistent drift: each correction snaps to center, but lazy
    // images keep pushing the target back off-center. We model this by
    // re-stubbing the target as off-center after every advance.
    const reapplyDrift = () => {
      stubRect(target, { top: 600, height: 80 })  // off by 140 >100
      stubRect(container, { top: 0, height: 1000 })
    }

    reapplyDrift()
    vi.advanceTimersByTime(250)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(2)  // correction 1

    reapplyDrift()
    vi.advanceTimersByTime(500)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(3)  // correction 2

    reapplyDrift()
    vi.advanceTimersByTime(500)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(4)  // correction 3

    // BOUNDED: a fourth attempt MUST NOT fire even if drift persists.
    // Prevents infinite-correction loops if the page never settles.
    reapplyDrift()
    vi.advanceTimersByTime(2000)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(4)
  })

  // Rule 6 (pair) — multi-shot STOPS early once the target lands
  // within the threshold. Prevents pointless extra scrolls and keeps
  // the user's focus stable once we've settled.
  it('multi-shot: stops as soon as drift drops within ±100px', () => {
    const { container, target } = setupContainerAndTarget(1000)
    scrollBubbleIntoView(target as HTMLElement)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(1)  // initial

    // First check: drifted → correction fires.
    stubRect(target, { top: 600, height: 80 })
    stubRect(container, { top: 0, height: 1000 })
    vi.advanceTimersByTime(250)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(2)

    // Second check: settled within threshold → no correction, and
    // crucially no further followups scheduled either.
    stubRect(target, { top: 460, height: 80 })  // center=500 = container center
    stubRect(container, { top: 0, height: 1000 })
    vi.advanceTimersByTime(500)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(2)

    // Confirm no third correction ever fires (the chain stopped).
    vi.advanceTimersByTime(2000)
    expect(scrollIntoViewSpy).toHaveBeenCalledTimes(2)
  })

  // Settle signal (2026-10-22). The container carries
  // `data-scroll-settling` while a correction can still move the scroll
  // position. A test that scrolls away before the chain ends gets yanked
  // back by a pending correction; e2e tests wait for this mark to go
  // away instead of guessing a fixed delay.
  describe('data-scroll-settling', () => {
    it('is set while corrections are pending and removed when the target is centered', () => {
      const { container, target } = setupContainerAndTarget(460) // center 500: centered
      scrollBubbleIntoView(target as HTMLElement)
      expect(container.hasAttribute('data-scroll-settling')).toBe(true)

      vi.advanceTimersByTime(250)
      expect(container.hasAttribute('data-scroll-settling')).toBe(false)
    })

    it('stays set while the chain keeps correcting, and is removed after the last step', () => {
      const { container, target } = setupContainerAndTarget(3000) // far off: every check corrects
      scrollBubbleIntoView(target as HTMLElement)

      vi.advanceTimersByTime(250)
      expect(container.hasAttribute('data-scroll-settling')).toBe(true)
      vi.advanceTimersByTime(500)
      expect(container.hasAttribute('data-scroll-settling')).toBe(true)
      vi.advanceTimersByTime(500)
      // The last scheduled step ran; nothing can move the scroll any more.
      vi.advanceTimersByTime(1000)
      expect(container.hasAttribute('data-scroll-settling')).toBe(false)
    })

    it('a superseded chain does not remove the mark of the newer chain', () => {
      const first = setupContainerAndTarget(460)
      scrollBubbleIntoView(first.target as HTMLElement)
      vi.advanceTimersByTime(100)

      const second = document.createElement('div')
      first.container.appendChild(second)
      stubRect(second, { top: 3000, height: 80 }) // far off: keeps correcting
      scrollBubbleIntoView(second as HTMLElement)

      vi.advanceTimersByTime(150) // the first chain's 250 ms check
      expect(first.container.hasAttribute('data-scroll-settling')).toBe(true)
    })

    it('is removed when the target leaves the document', () => {
      const { container, target } = setupContainerAndTarget(3000)
      scrollBubbleIntoView(target as HTMLElement)
      target.remove()
      vi.advanceTimersByTime(250)
      expect(container.hasAttribute('data-scroll-settling')).toBe(false)
    })
  })
})
