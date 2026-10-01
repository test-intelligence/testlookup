/**
 * The height a chart's PLOT draws at, inside or outside full screen (VIZ-608,
 * adopted by the charts in VIZ-601).
 *
 * `useChartFrameHeight(height)` is the frame's whole body height while it is
 * full screen. A chart that draws notes, a caption or a legend of its own
 * UNDER its plot (the time series' gap and UTC notes, the multi-series legend
 * of toggle buttons) cannot give the plot all of it, or the body scrolls to
 * reach the notes and the plot's bottom axis is cut off at the fold. So each
 * chart names what it keeps back for its own text (`reserve`), and the plot
 * takes the rest — never less than the height the page asked for.
 *
 * Outside full screen this is exactly `height`: adopting it changes nothing
 * a page or a committed screenshot can see.
 */
import { usePresentationStore } from '@/store/presentationStore'
import { useChartFrameHeight, useChartFullscreen } from './chartFrameContext'

export function useFramePlotHeight(height: number, reserve = 0): number {
  const fullscreen = useChartFullscreen()
  const body = useChartFrameHeight(height)
  return fullscreen ? Math.max(height, Math.round(body - reserve)) : height
}

/**
 * How much bigger a Recharts drawing is shown in full screen: its 11 px axis
 * text reads at 15 px, for a room rather than a desk (VIZ-608).
 *
 * The drawing is LAID OUT at its page size in text — the category axis width,
 * the release labels over the plot, the value labels past the bars' ends are
 * all reserved for 11 px — and the whole drawing is then scaled up by this
 * (`ChartResponsive`). The first full-screen build enlarged the TEXT alone,
 * from CSS, inside a layout made for 11 px: ranked-bar names lost 17-30 px off
 * the svg's left edge, release and bucket labels were cut (Wave 2.4 review
 * A5). Scaling the drawing keeps every reserve in proportion, so nothing that
 * fits at 11 px can be cut at 15.
 */
export const PRESENTATION_SCALE = 15 / 11

/**
 * How much bigger every Recharts drawing is shown while presentation mode is
 * on (VIZ-106, PLAN OD-11): 11 px axis text reads at 16 px, the story's floor
 * for a wall monitor, on the page and in full screen alike.
 */
export const PRESENTATION_MODE_SCALE = 16 / 11

/**
 * The one scale seam for a chart's drawing: the LARGER of the full-screen
 * scale (while the chart's frame is full screen) and the presentation-mode
 * scale (while the mode is on); 1 when neither applies. Larger, never
 * smaller: going full screen inside presentation mode must not shrink a
 * drawing the room could already read, and presentation mode must not undo
 * full screen. Full screen alone stays 15/11.
 */
export function usePresentationScale(): number {
  const fullscreen = useChartFullscreen()
  const presenting = usePresentationStore((s) => s.enabled)
  return Math.max(fullscreen ? PRESENTATION_SCALE : 1, presenting ? PRESENTATION_MODE_SCALE : 1)
}

/**
 * The LAYOUT height of a Recharts drawing (`ChartResponsive`'s `height`): the
 * plot's share of the full-screen body, divided back down by the presentation
 * scale — and never less than the `height` the page lays it out at.
 *
 * `useFramePlotHeight` keeps the SCREEN height at the page's height at least.
 * Divided by the scale, that left a short window's full-screen drawing laid
 * out SMALLER than the page's: at 640×480 the time series was laid out 205 px
 * tall instead of 280, its legend wrapped to three lines in the narrower
 * layout, the plot kept 65 px, and the rate axis title — which runs up from
 * the plot's middle — stood 16 px above the svg. "Nothing that fits at 11 px
 * can be cut at 15" holds only for a drawing laid out at least as large as
 * the page's, so that is the floor. Outside full screen this is `height`.
 */
export function useFramePlotLayoutHeight(height: number, reserve = 0): number {
  const scale = usePresentationScale()
  const screen = useFramePlotHeight(height, reserve)
  return Math.max(height, Math.round(screen / scale))
}
