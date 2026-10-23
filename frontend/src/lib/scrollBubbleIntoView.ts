/**
 * Distance-gated, layout-shift-resilient scroll-into-view for message
 * bubbles. Used by the search-hit fast path
 * (`navigateToMatch.ts`) and the URL-highlight effect
 * (`ConversationPage.tsx`).
 *
 * Bug background (2026-05-20, 3-model LLM council):
 *   Native `scrollIntoView({behavior:'smooth', block:'center'})` aims
 *   at the target's `offsetTop` AT CALL TIME and animates over ~500ms.
 *   During the animation, lazy-loaded images
 *   (`<img loading="lazy">`, no intrinsic dimensions) above the target
 *   enter the swept region, decode, and grow from ~0px up to 384px
 *   each (`max-h-96` in MessageBubble.tsx). On a 15K-msg conversation,
 *   `scrollHeight` grew by 16,109 px during one observed animation,
 *   landing the target 4,000–5,300 px BELOW viewport center. Smooth
 *   scrollIntoView does NOT re-aim mid-flight.
 *
 * Strategy — distance-gated two-mode:
 *   - **Long hop** (target > 1.5 viewports from container center):
 *     scroll instantly with `behavior: 'auto'`. The intermediate
 *     lazy-image region is never swept, so it never inflates the
 *     document. The motion cue is informational noise for a 16K-px
 *     jump anyway (just blurs).
 *   - **Short hop** (≤ 1.5 viewports): keep `behavior: 'smooth'` for
 *     the motion cue. Drift is small because few unmounted images are
 *     swept.
 *
 *   Then a **bounded multi-shot** post-settle correction at 250ms,
 *   750ms, and 1250ms. Any image with `decoding="async"` that lands
 *   in viewport after the scroll can push the target by up to ~384 px
 *   over the next few layout passes. The first 250ms tick covers the
 *   common case (single image cascade); the two later ticks catch the
 *   long-tail case (multi-image cascades on 15K+ message
 *   conversations where many lazy images decode sequentially). Each
 *   tick is conditional on drift > 100 px AND not superseded by a
 *   newer call; once drift settles within the threshold the chain
 *   stops early. Max 3 corrections — beyond that we stop yanking the
 *   user even if the page is still settling (they're trying to read).
 *
 * Concurrency — supersession token:
 *   Rapid Cmd+G hits (cycling through search matches) can queue
 *   multiple corrections. Without a token, hit-A's 250ms correction
 *   could fire AFTER hit-B's scroll, yanking the user back to hit A.
 *   `scrollToken` is a module-level monotonic counter; each call
 *   captures its token, and the correction no-ops if a newer call
 *   superseded it.
 *
 * Spec note — `behavior: 'auto'` vs `'instant'`:
 *   CSSOM-VIEW Level 1's `ScrollBehavior` enum is `'auto' | 'smooth'`.
 *   `'instant'` is a `scrollTo`-only value (Window/Element.scrollTo,
 *   Window/scrollBy) — TypeScript's `lib.dom.d.ts` rejects it for
 *   `scrollIntoView`. `'auto'` is the spec-correct way to request an
 *   instant scroll via scrollIntoView and is treated as instant in
 *   Chromium/Firefox/Safari when CSS `scroll-behavior` is unset (the
 *   default).
 *
 * Threshold note — 1.5 viewports:
 *   Empirical heuristic. Above ~1.5 viewports, the smooth-scroll
 *   motion provides no spatial context; below it, the motion cue is
 *   valuable and drift is bounded. WWCMM: smoke test on the 15K conv
 *   showing drift on hops <1.5 viewports → tighten threshold.
 *
 * Residual: this helper does NOT fix the root cause (images without
 * reserved dimensions). Layout shift will still occur AFTER landing
 * as the user scrolls further. Real fix is backend-provided image
 * dimensions + CSS `aspect-ratio`. Deferred — see follow-up issue.
 */

let scrollToken = 0

// Exported for testing only.
export const __getScrollToken = () => scrollToken
export const __resetScrollToken = () => { scrollToken = 0 }

const SCROLL_CONTAINER_SELECTOR = '[data-testid="message-stream"]'
const LONG_HOP_VIEWPORT_MULTIPLE = 1.5
// Cumulative ticks from the initial scroll: 250 / 750 / 1250 ms.
// Three corrections covers the multi-image lazy-decode tail observed
// on 15K+ message conversations without yanking the user mid-read.
const CORRECTION_SCHEDULE_MS = [250, 500, 500] as const
const CORRECTION_THRESHOLD_PX = 100
/** Present on the scroll container while a correction chain runs. */
export const SETTLING_ATTR = 'data-scroll-settling'

/**
 * Scroll a message bubble into the center of its message-stream
 * container, resilient to layout shift from lazy-loaded images. See
 * module docstring for full rationale.
 *
 * @param forceInstant — Skip the smooth-scroll animation even for short
 *   hops. Used by callers (e.g. the post-toggle re-center in
 *   ConversationPage) where smooth motion would feel disconnected from
 *   the originating action (a checkbox click). The multi-shot
 *   correction tail still runs to absorb any post-settle drift.
 */
export function scrollBubbleIntoView(element: HTMLElement, forceInstant = false): void {
  const myToken = ++scrollToken
  const container = element.closest(SCROLL_CONTAINER_SELECTOR) as HTMLElement | null

  // Fallback for the rare case the bubble isn't in our standard
  // scroll container (e.g. printing, alternate layouts, tests that
  // mount bubbles directly into document.body). Keep behavior simple
  // and let the browser default handle it.
  if (!container) {
    element.scrollIntoView({ behavior: forceInstant ? 'auto' : 'smooth', block: 'center' })
    return
  }

  const containerRect = container.getBoundingClientRect()
  const targetRect = element.getBoundingClientRect()
  const viewportH = containerRect.height
  const targetCenter = targetRect.top + targetRect.height / 2
  const containerCenter = containerRect.top + viewportH / 2
  const distancePx = Math.abs(targetCenter - containerCenter)
  const isLongHop = distancePx > viewportH * LONG_HOP_VIEWPORT_MULTIPLE

  element.scrollIntoView({
    behavior: isLongHop || forceInstant ? 'auto' : 'smooth',
    block: 'center',
  })

  // Settle signal (2026-10-22): set while a correction can still move
  // the scroll position. e2e tests wait for it to go away; a fixed delay
  // raced the correction chain on slow CI runners.
  container.setAttribute(SETTLING_ATTR, '')
  scheduleCorrections(myToken, element, container, 0)
}

/**
 * Walk the CORRECTION_SCHEDULE one step at a time. Each tick:
 *   - bail if a newer scroll call superseded our token
 *   - bail if the element is no longer in the DOM
 *   - if drift exceeds threshold, snap-correct AND schedule the next
 *     tick (if any remain)
 *   - if drift is within threshold, STOP the chain — we've landed
 *     cleanly and further corrections would be needless motion
 *
 * Implemented as a self-rescheduling timer rather than an array of
 * pre-queued timers so early-exit on settle is the natural default
 * (no orphaned no-op corrections lingering after we've landed).
 */
function scheduleCorrections(
  token: number,
  element: HTMLElement,
  container: HTMLElement,
  stepIdx: number,
): void {
  if (stepIdx >= CORRECTION_SCHEDULE_MS.length) {
    settled(token, container)
    return
  }
  window.setTimeout(() => {
    // A newer scroll owns the mark now: leave it.
    if (token !== scrollToken) return
    if (!document.body.contains(element)) {
      settled(token, container)
      return
    }
    const r = element.getBoundingClientRect()
    const c = container.getBoundingClientRect()
    const off = (r.top + r.height / 2) - (c.top + c.height / 2)
    if (Math.abs(off) > CORRECTION_THRESHOLD_PX) {
      element.scrollIntoView({ behavior: 'auto', block: 'center' })
      scheduleCorrections(token, element, container, stepIdx + 1)
    } else {
      settled(token, container)
    }
  }, CORRECTION_SCHEDULE_MS[stepIdx])
}

/** End of a correction chain: remove the settle mark, unless a newer
 *  scroll started its own chain. */
function settled(token: number, container: HTMLElement): void {
  if (token === scrollToken) container.removeAttribute(SETTLING_ATTR)
}
