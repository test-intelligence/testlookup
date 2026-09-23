/**
 * Where each day sits on the range brush's strip — the SAME day → x mapping as
 * the chart above it, so the strip reads as the plot's own overview and its
 * days line up with the plot's days.
 *
 * Recharts places a category x axis one of two ways, and the three zoomable
 * charts use both:
 *
 *   band   every day owns an equal SLOT and is drawn at its centre. A chart
 *          with bars on the axis (`TimeSeriesChart`'s execution bars) is a
 *          band scale: the first bar sits half a slot in from the plot's left
 *          edge.
 *   point  the days are POINTS spread edge to edge: the first at the plot's
 *          left edge, the last at its right. Line and area charts with no bar
 *          (`MultiSeriesChart`, `DurationTrend`) are point scales.
 *
 * The strip is laid over the plot's x-range (the brush measures it), so with
 * the same kind here, the strip's first and last days sit exactly under the
 * plot's first and last days when the chart shows the whole window.
 *
 * Every position is a FRACTION 0..1 of the strip's width. A day's slot, for
 * the selection and the handles, runs from its left edge to its right edge:
 * on a point scale that is halfway to each neighbour (so the two end days own
 * half a slot each), and on a band scale it is the band.
 */
export type BrushScale = 'band' | 'point'

/** The centre of day `i` of `count`. */
export function dayCentre(scale: BrushScale, i: number, count: number): number {
  if (count <= 1) return 0.5
  return scale === 'band' ? (i + 0.5) / count : i / (count - 1)
}

/**
 * The position of EDGE `k` (0..count): the boundary before day `k`, so edge 0
 * is the start of the first day and edge `count` the end of the last.
 */
export function edgePosition(scale: BrushScale, k: number, count: number): number {
  if (count <= 1) return k <= 0 ? 0 : 1
  if (scale === 'band') return k / count
  if (k <= 0) return 0
  if (k >= count) return 1
  return (k - 0.5) / (count - 1)
}

/** The day under a fraction of the strip. */
export function dayAtFraction(scale: BrushScale, fraction: number, count: number): number {
  const last = Math.max(0, count - 1)
  const f = Math.min(1, Math.max(0, fraction))
  const day = scale === 'band' ? Math.floor(f * count) : Math.round(f * last)
  return Math.min(last, Math.max(0, day))
}

/** The day EDGE (0..count) nearest a fraction of the strip: where a dragged handle lands. */
export function edgeAtFraction(scale: BrushScale, fraction: number, count: number): number {
  const f = Math.min(1, Math.max(0, fraction))
  if (scale === 'band') return Math.round(f * count)
  // Point: the edges are unevenly spaced at the two ends (0 and 1 are whole
  // edges, the inner ones sit between days), so pick the nearest of the
  // candidates around the estimate rather than rounding once.
  const estimate = Math.round(f * Math.max(1, count - 1) + 0.5)
  let best = 0
  let bestDistance = Number.POSITIVE_INFINITY
  for (let k = Math.max(0, estimate - 1); k <= Math.min(count, estimate + 1); k++) {
    const distance = Math.abs(edgePosition(scale, k, count) - f)
    if (distance < bestDistance) {
      best = k
      bestDistance = distance
    }
  }
  return best
}

/**
 * Which days get a tick on the strip: every day while they are at least
 * `minGapPx` apart, otherwise every 7th (a week) or 30th, counted back from
 * the LAST day so the newest day always has one. The first day always has one
 * too — the two ends are where the strip meets the plot.
 */
export function tickDays(count: number, widthPx: number, minGapPx = 6): number[] {
  if (count <= 0) return []
  const gap = count > 1 ? widthPx / (count - 1) : widthPx
  const step = [1, 7, 30, 91, 365].find((candidate) => gap * candidate >= minGapPx) ?? 365
  const days = new Set<number>([0])
  // Counted back from the last day; one that would crowd the first day's own tick is left out.
  for (let i = count - 1; i >= 0; i -= step) if (i === 0 || i >= step / 2) days.add(i)
  return [...days].sort((a, b) => a - b)
}
