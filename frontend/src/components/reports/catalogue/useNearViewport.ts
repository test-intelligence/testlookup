/**
 * K2 (Wave 2.6): "is this element near the part of the page the reader can
 * see?" — a one-way latch for lazy-mounting report sections below the fold.
 *
 * THE TRAP THIS EXISTS FOR. The app shell does not scroll the window: it is
 * `h-screen overflow-hidden`, and the page scrolls inside
 * `<main id="main-content" class="overflow-auto">` (`AppLayout.tsx`). An
 * `IntersectionObserver` with the default root (the viewport) clips its target
 * by every scrolling ancestor BEFORE the root margin is applied, so a section
 * 150 px below main's fold is clipped to nothing and a "200 px margin" never
 * sees it: the section would mount exactly when it scrolls into view, and the
 * reader would watch a skeleton and then a request. Rooting the observer at
 * the scroller itself makes the margin a margin on the box the reader actually
 * scrolls. (Plan 2.6; the scroll-margin e2e proves it in a real browser.)
 *
 * Why `#main-content` and not "the nearest ancestor whose overflow scrolls":
 * a report section often sits inside an `overflow-x-auto` table wrapper or an
 * `overflow-hidden` card, and CSS computes `overflow-y: auto` for the first
 * (one axis not visible makes the other `auto`). Rooting there would measure
 * against a box that never scrolls vertically, which mounts everything at
 * once. The shell's scroller is the one box the whole page moves in; outside
 * the shell (dev pages), the viewport is.
 *
 * Latching: once near, always near. A section that has fetched and drawn is
 * never unmounted by scrolling away (that would drop its state and refetch on
 * the way back). Where `IntersectionObserver` does not exist (jsdom, old
 * browsers) the answer is `true` at once, so tests and fallback browsers
 * behave as if the page had no lazy mounting at all.
 */
import { useEffect, useState, type RefObject } from 'react'

/** The app shell's scrolling `<main>` (`AppLayout.tsx`). */
export const MAIN_SCROLLER_SELECTOR = '#main-content'

/** How far ahead of the scroller's visible box a section starts to mount, px (ARCH 5.7). */
export const NEAR_VIEWPORT_MARGIN_PX = 200

export interface NearViewportOptions {
  marginPx?: number
}

const hasObserver = () => typeof IntersectionObserver !== 'undefined'

export function useNearViewport(
  ref: RefObject<Element | null>,
  { marginPx = NEAR_VIEWPORT_MARGIN_PX }: NearViewportOptions = {},
): boolean {
  const [near, setNear] = useState(() => !hasObserver())

  useEffect(() => {
    if (near) return
    const target = ref.current
    // No observer: `near` started true and this effect never gets here.
    if (!target || !hasObserver()) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return
        observer.disconnect()
        setNear(true)
      },
      {
        root: target.closest(MAIN_SCROLLER_SELECTOR),
        // Vertical only: a section is "near" by how far down the page it is.
        rootMargin: `${marginPx}px 0px ${marginPx}px 0px`,
      },
    )
    observer.observe(target)
    return () => observer.disconnect()
  }, [near, ref, marginPx])

  return near
}
