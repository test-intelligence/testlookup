/**
 * What a chart needs to know about the `ChartFrame` it is drawn in (VIZ-608).
 *
 * A chart is sized by the `height` its page passes and by the width it
 * measures (`useContainerWidth`, `useEChart`'s ResizeObserver). Width follows
 * the frame into full screen on its own; HEIGHT cannot — a page asked for a
 * 240 px chart, and a 240 px chart in the middle of a projector screen is not
 * something a room can read. So the frame publishes the height its body has
 * while it is full screen, and every chart asks for it:
 *
 *   const h = useChartFrameHeight(height)   // `height` itself outside full screen
 *
 * The second thing a chart needs is WHERE TO PORTAL. Under the Fullscreen API
 * only the full-screen element and its descendants are painted: a tooltip or
 * popover portalled to `document.body` is still in the DOM, still focusable,
 * and invisible. `useChartPortalContainer()` is the full-screen element while
 * the frame is full screen and `null` otherwise — `null` meaning "your usual
 * container" (for `createPortal`, pass `container ?? document.body`).
 *
 * Outside any frame (a chart rendered bare, in a test or the gallery) every
 * hook returns the pass-through value, so adopting them changes nothing there.
 */
import { createContext, useContext } from 'react'

export interface ChartFrameContextValue {
  /** True while the enclosing frame is full screen (either the API or the maximised overlay). */
  fullscreen: boolean
  /** The chart body's height in CSS px while full screen; `null` outside full screen. */
  bodyHeight: number | null
  /** The element portals must render into while full screen; `null` otherwise. */
  portalContainer: HTMLElement | null
}

const OUTSIDE_A_FRAME: ChartFrameContextValue = { fullscreen: false, bodyHeight: null, portalContainer: null }

/** `ChartFrame` provides this around its body, table and toolbar; nothing else should. */
export const ChartFrameContext = createContext<ChartFrameContextValue>(OUTSIDE_A_FRAME)

/**
 * The height a chart should draw at: the full-screen body height while its
 * frame is full screen, else the `height` it was given — unchanged, so a chart
 * that adopts this hook renders exactly as before outside full screen.
 */
export function useChartFrameHeight(height: number): number {
  const { fullscreen, bodyHeight } = useContext(ChartFrameContext)
  return fullscreen && bodyHeight !== null ? bodyHeight : height
}

/**
 * Where anything that must portal (a tooltip, a popover, a menu) should
 * portal: the full-screen element while the frame is full screen, else `null`
 * (= the caller's default, usually `document.body`).
 */
export function useChartPortalContainer(): HTMLElement | null {
  return useContext(ChartFrameContext).portalContainer
}

/** True while the enclosing frame is full screen. */
export function useChartFullscreen(): boolean {
  return useContext(ChartFrameContext).fullscreen
}
