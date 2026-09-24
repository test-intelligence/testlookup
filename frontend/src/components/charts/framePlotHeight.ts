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

/** `PRESENTATION_SCALE` while the chart's frame is full screen, else 1. */
export function usePresentationScale(): number {
  return useChartFullscreen() ? PRESENTATION_SCALE : 1
}
