/**
 * The zoom's slicers for the two models only catalogue sections draw: the
 * duration band (`DurationChartFrame` / `DurationTrend`) and the multi-series
 * chart (`MultiSeriesChartFrame`). The rules are `zoomModel.ts`'s (slice the
 * built model, keep the axes, re-derive what the drawn points decide).
 *
 * Apart from `zoomModel` on purpose: that module is in the time-series chunk
 * every flag-off Trends and Suite detail visit loads, and these two slicers
 * bring the duration-bucket model and the multi-series line finishing with
 * them. Once a section used them they were live, and rolldown, which chunks
 * by static reachability, placed them and their imports in that shared chunk
 * (Wave 2.6 R1-3).
 */
import { bandNotice, type DurationBandModel } from '../durationBuckets'
import { finishLine } from '../lineFinish'
import type { MultiSeriesModel } from '../multiSeriesModel'
import { clampRange, type ZoomRange } from './zoomModel'

// ── Slicing the duration band ───────────────────────────────────────────────

/**
 * The p50/p95 band cut to `range`. Each point is the built point, untouched —
 * its `inverted` flag included; only the COUNT of inverted days in view and
 * its sentence (`bandNotice`, the builder's own words) are re-derived, so the
 * figure's notice speaks of the days it shows.
 */
export function sliceDurationBand(model: DurationBandModel, range: ZoomRange | null): DurationBandModel {
  if (range === null) return model
  const clamped = clampRange(range, model.points.length)
  if (clamped === null) return model
  const points = model.points.slice(clamped.start, clamped.end + 1)
  const inverted = points.filter((point) => point.inverted).length
  return {
    ...model,
    points,
    inverted,
    notice: bandNotice(inverted),
    // The first day in view still has a previous day (Wave 2.4 F4).
    precedingPoint: clamped.start > 0 ? model.points[clamped.start - 1] : null,
  }
}

/**
 * The top of the WHOLE band, in ms: the largest `high` (the larger of p50 and
 * p95) over every day, or `undefined` when no day was measured. A zoomed trend
 * passes it to `DurationTrend` as `yMax`, so the slice is drawn on the
 * window's scale — as the time-series charts keep theirs — instead of a quiet
 * week being stretched until its tallest day touches the top of the plot.
 */
export function durationBandMax(model: DurationBandModel): number | undefined {
  let max: number | undefined
  for (const point of model.points) {
    if (point.high !== null && Number.isFinite(point.high) && (max === undefined || point.high > max)) max = point.high
  }
  return max
}

// ── Slicing the multi-series model ──────────────────────────────────────────

/**
 * `model` cut to `range`, keeping every line, its key, its colour slot and its
 * place in the fold — the "top 7 + Other" grouping was decided on the whole
 * window and a zoom must not reshuffle it. Each line's derived facts (gaps,
 * isolated points, where its direct label points) are re-derived over the
 * days in view; its `volume` stays the full window's, since that is what the
 * fold ranked it by.
 */
export function sliceMultiSeriesModel(model: MultiSeriesModel, range: ZoomRange | null): MultiSeriesModel {
  if (range === null) return model
  const clamped = clampRange(range, model.xs.length)
  if (clamped === null) return model
  const xs = model.xs.slice(clamped.start, clamped.end + 1)
  const lines = model.lines.map((line) => ({
    ...finishLine(
      { key: line.key, label: line.label, other: line.other, points: line.points.slice(clamped.start, clamped.end + 1) },
      line.styleIndex,
    ),
    volume: line.volume,
    // Each line's point on the day before the view: the first day in view
    // still states its change vs the previous day (Wave 2.4 F4).
    precedingPoint: clamped.start > 0 ? (line.points[clamped.start - 1] ?? null) : null,
  }))
  const partialInView = model.partialDay !== null && lines.some((line) => line.points.some((point) => point.partial))
  return {
    ...model,
    xs,
    lines,
    partialDay: partialInView ? model.partialDay : null,
    inProgressCount: partialInView ? model.inProgressCount : 0,
    gaps: lines.reduce((sum, line) => sum + line.gaps, 0),
  }
}
