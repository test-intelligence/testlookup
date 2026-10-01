/**
 * How big a donut's ring can be drawn in the room Recharts leaves it, with
 * every slice label beside it measured (VIZ-106 fix round, B0 findings 3 and
 * 4). Pure: `DonutPlot` hands it the plot area and the measured labels.
 */
import { formatNumber, formatPercent } from '@/utils/formatters'
import type { DonutSlice } from './DonutChart.model'
import type { TipSide } from './tipPlacement'

/**
 * Where a slice's tooltip may go: beside the ring, right or left, else the
 * readout slot below the plot. Never below or above the ring: its mark is the
 * WHOLE ring, so the way down from a slice on the ring's right edge crosses
 * another slice, the tooltip turns into that slice's, and the one asked for
 * cannot be reached (SC 1.4.13). The old fixed ring never left room below it
 * in a narrow frame; a ring fitted to its plot does, at 320 px.
 */
export const DONUT_TIP_SIDES: readonly TipSide[] = ['right', 'left']

/** The slice labels' size, in chart (layout) px. */
export const SLICE_LABEL_FONT_SIZE = 11
/** How far outside the ring a slice label is anchored. */
export const SLICE_LABEL_OFFSET = 14
/** Half a slice label's height (it is vertically centred on its anchor). */
const SLICE_LABEL_HALF_HEIGHT = 7
/** Recharts' default chart margin: a label may use it, the ring does not. */
const CHART_MARGIN = 5
/**
 * The smallest ring that still carries its labels outside it. Below this the
 * labels leave the drawing and every slice's count and share is named in the
 * legend instead (as a tiny slice's always is): a ring squeezed to a coin to
 * make room for its own labels says less than a readable ring and a legend.
 */
export const MIN_LABELLED_OUTER_RADIUS = 44

/** The Recharts plot area a donut is drawn in (chart px: the layout before any presentation scale). */
export interface DonutPlotArea {
  width: number
  height: number
}

/** The radii and label placement a donut is drawn with. */
export interface DonutFit {
  outerRadius: number
  innerRadius: number
  /** False: the ring is too small for labels beside it; the legend names every slice. */
  sliceLabels: boolean
}

/** Every slice's MID angle as Recharts computes it (start 0, end 360, no minimum angle). */
export function sliceMidAngles(arcs: readonly number[], paddingAngle: number): number[] {
  const padding = arcs.length <= 1 ? 0 : paddingAngle
  const nonZero = arcs.filter((arc) => arc !== 0).length
  const sum = arcs.reduce((total, arc) => total + arc, 0)
  if (sum <= 0) return arcs.map(() => 0)
  const realTotal = 360 - nonZero * padding
  const mids: number[] = []
  let end = 0
  arcs.forEach((arc, index) => {
    const start = index === 0 ? 0 : end + (arc !== 0 ? padding : 0)
    end = start + (arc / sum) * realTotal
    mids.push((start + end) / 2)
  })
  return mids
}

/**
 * The largest ring, up to the requested radius, whose slice labels all stay
 * inside the drawing (VIZ-106 fix round, B0 findings 3 and 4).
 *
 * The ring used to be a fixed 88 px whatever the frame's width, with an 11 px
 * count-and-percent label 14 px outside it. In a 330 px frame that left
 * "3,910 (88.1%)" no room on the left: in DejaVu Sans (the CI runner's font)
 * its first digit fell off the svg at 1280 px, and at 375 px on every
 * platform. Presentation mode lays the drawing out 11/16 as wide and scales it
 * up, so the same ring cut its labels, and its own top, everywhere.
 *
 * Each label is MEASURED in the font the reader's browser draws it in
 * (`textMeasure`), at its own angle, against the svg's real edges: the plot
 * area plus Recharts' 5 px margin at the sides and the top, and never past the
 * plot's bottom, where the legend begins. A label leans outward from its
 * anchor (start on the right, end on the left), so it needs
 * (r + 14)|cos| + its width of room beside the centre, and
 * (r + 14)|sin| + half its height above or below it.
 *
 * Where everything fits at the requested radius, the requested radius is kept
 * exactly: nothing a committed screenshot shows moves. With no measurement
 * (jsdom, a chart not laid out yet) the ring alone is fitted.
 */
export function fitDonut({
  plot,
  outerRadius,
  innerRadius,
  midAngles,
  labelWidths,
}: {
  plot: DonutPlotArea | null
  outerRadius: number
  innerRadius: number
  /** Mid angle (degrees) of every LABELLED slice; empty when no slice carries a label. */
  midAngles: readonly number[]
  /** The measured width of each of those labels, in the same order; `null` when unmeasured. */
  labelWidths: readonly number[] | null
}): DonutFit {
  const requested: DonutFit = { outerRadius, innerRadius, sliceLabels: true }
  if (!plot || plot.width <= 0 || plot.height <= 0) return requested
  const halfW = plot.width / 2
  const halfH = plot.height / 2
  const ringOnly = Math.min(outerRadius, halfW, halfH)
  let fitted = ringOnly
  if (labelWidths) {
    midAngles.forEach((angle, index) => {
      const radians = (angle * Math.PI) / 180
      const across = Math.abs(Math.cos(radians))
      // Recharts runs counter-clockwise from 3 o'clock: a positive sine is ABOVE the centre.
      const up = Math.sin(radians)
      const width = labelWidths[index] ?? 0
      const side = halfW + CHART_MARGIN - width
      // Across: the label's far end stays inside the svg. Straight up or down
      // it hangs to the left of its anchor by its whole width.
      fitted = Math.min(fitted, across > 1e-6 ? side / across - SLICE_LABEL_OFFSET : side >= 0 ? fitted : 0)
      // Up or down: above the centre the top margin is usable; below it the legend begins at the plot's bottom.
      const room = (up > 0 ? halfH + CHART_MARGIN : halfH) - SLICE_LABEL_HALF_HEIGHT
      if (Math.abs(up) > 1e-6) fitted = Math.min(fitted, room / Math.abs(up) - SLICE_LABEL_OFFSET)
    })
  }
  const outer = Math.floor(fitted)
  if (outer >= outerRadius) return requested
  const scaled = (radius: number) => ({ outerRadius: radius, innerRadius: Math.round((radius * innerRadius) / outerRadius) })
  if (labelWidths && midAngles.length > 0 && outer < MIN_LABELLED_OUTER_RADIUS) {
    const bare = Math.floor(ringOnly)
    return bare >= outerRadius ? { ...requested, sliceLabels: false } : { ...scaled(bare), sliceLabels: false }
  }
  return { ...scaled(outer), sliceLabels: true }
}

/** The count-and-percent text drawn beside a slice. */
export function sliceArcLabel(slice: DonutSlice): string {
  return `${formatNumber(slice.value)} (${formatPercent(slice.percent)})`
}
