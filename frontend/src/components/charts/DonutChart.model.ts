/**
 * The donut's model (VIZ-401) — every rule the story states, as pure data, so
 * the plot, the legend, the tooltip and the table view cannot disagree and
 * every edge case is testable without a DOM.
 *
 *   - slices are in FIXED order (the status vocabulary), never by size, so two
 *     donuts on two pages are comparable at a glance;
 *   - the total is the centre, and each slice carries its count AND its
 *     percent, with the percents summing to exactly 100.0 (largest remainder,
 *     `chartCatalog`);
 *   - `unknown` is the fifth slice when it is present, and flaky is NEVER a
 *     slice: it is an attribute drawn on top of a status (`FLAKY_MARKER`);
 *   - one status alone is a full ring, still labelled;
 *   - a slice under `TINY_SLICE_PERCENT` moves its LABEL to the legend and
 *     keeps a minimum visible arc — the padded arc is what is drawn, the true
 *     value is what the label, the tooltip and the table say;
 *   - all zero is not a donut of nothing: `empty` is true and the caller hands
 *     over to the frame's filtered-empty state.
 */
import { VIZ_STATUSES, type ChartSeries, type SeriesChart, type VizStatus } from '@/lib/viz/contracts'
import { formatNumber, formatPercent } from '@/utils/formatters'
import { largestRemainderPercents } from './chartCatalog'
import { STATUS_ENCODING } from './tokens'

/** Under this percent, a slice's label does not fit on it and moves to the legend. */
export const TINY_SLICE_PERCENT = 2
/** …and the arc it keeps, so the slice can still be seen, hovered and focused. */
export const MIN_SLICE_ARC_PERCENT = 1.5

export interface DonutSlice {
  /** Stable identity (the status, or the category key). */
  key: string
  /** What the legend and the table call it. */
  label: string
  /** The TRUE value. Never the padded arc. */
  value: number
  /** One decimal; the slices' percents sum to exactly 100.0. */
  percent: number
  /** What the ARC is drawn from: `value`, or the minimum for a tiny slice. */
  arc: number
  /** Under `TINY_SLICE_PERCENT`: the label belongs in the legend, not on the arc. */
  tiny: boolean
  /** Set for a status donut; absent for a category one. */
  status?: VizStatus
}

export interface DonutModel {
  slices: DonutSlice[]
  /** The number in the centre. */
  total: number
  /** Nothing measured at all: the caller shows the frame's filtered-empty state. */
  empty: boolean
  /** The slices whose label moved to the legend. */
  legendOnly: DonutSlice[]
  /** One slice only: a full ring (still labelled). */
  fullRing: boolean
}

export interface DonutInput {
  key: string
  label: string
  value: number
  status?: VizStatus
}

/**
 * The model for `items`, already in the order they must be drawn. Zero (and
 * negative, which has no share of a whole) buckets are dropped rather than
 * drawn as a slice of nothing.
 */
export function donutModel(items: readonly DonutInput[]): DonutModel {
  const kept = items.filter((item) => Number.isFinite(item.value) && item.value > 0)
  const total = kept.reduce((sum, item) => sum + item.value, 0)
  if (kept.length === 0 || total <= 0) {
    return { slices: [], total: 0, empty: true, legendOnly: [], fullRing: false }
  }

  const percents = largestRemainderPercents(kept.map((item) => item.value))
  const tiny = percents.map((percent) => percent < TINY_SLICE_PERCENT)

  // A tiny slice is padded to `MIN_SLICE_ARC_PERCENT` of the arc TOTAL — which
  // the padding itself grows, so solve for it rather than padding to a share
  // of the old total and landing just under the minimum:
  //   a = m(R + k·a)  →  a = m·R / (1 − m·k)
  // where R is the un-padded total and k the number of tiny slices.
  const minShare = MIN_SLICE_ARC_PERCENT / 100
  const rest = kept.reduce((sum, item, index) => (tiny[index] ? sum : sum + item.value), 0)
  const tinyCount = tiny.filter(Boolean).length
  const padded = tinyCount > 0 && minShare * tinyCount < 1 ? (minShare * rest) / (1 - minShare * tinyCount) : 0

  const slices: DonutSlice[] = kept.map((item, index) => ({
    key: item.key,
    label: item.label,
    value: item.value,
    percent: percents[index],
    arc: tiny[index] ? Math.max(item.value, padded) : item.value,
    tiny: tiny[index],
    ...(item.status ? { status: item.status } : {}),
  }))

  return {
    slices,
    total,
    empty: false,
    legendOnly: slices.filter((slice) => slice.tiny),
    fullRing: slices.length === 1,
  }
}

/**
 * The status donut. Order is `VIZ_STATUSES` — passed, failed, broken, skipped
 * and `unknown` fifth — whatever order (or extra keys, such as `flaky`) the
 * caller's object has.
 */
export function statusDonutModel(counts: Readonly<Record<string, number | null | undefined>>): DonutModel {
  return donutModel(
    VIZ_STATUSES.map((status) => ({
      key: status,
      label: STATUS_ENCODING[status].label,
      value: counts[status] ?? 0,
      status,
    })),
  )
}

/** A categorical donut (failure categories, …). The registry caps it at five. */
export function categoryDonutModel(items: readonly { key: string; label: string; value: number }[]): DonutModel {
  return donutModel(items)
}

/** "Passed 880 (88.0%)" — count AND percent, on the arc or in the legend. */
export function sliceLabel(slice: DonutSlice): string {
  return `${slice.label} ${formatNumber(slice.value)} (${formatPercent(slice.percent)})`
}

/** The status counts in a chart-data `group_by=status` series. */
export function statusCountsFromSeries(series: ChartSeries): Partial<Record<VizStatus, number>> {
  const counts: Partial<Record<VizStatus, number>> = {}
  if (series.kind !== 'series') return counts
  const statuses = new Set<string>(VIZ_STATUSES)
  for (const entry of series.series) {
    for (const point of entry.points) {
      // A null y is "not measured", not a zero: it contributes nothing.
      if (point.y === null || !statuses.has(point.x)) continue
      const status = point.x as VizStatus
      counts[status] = (counts[status] ?? 0) + point.y
    }
  }
  return counts
}

/**
 * The C3 series the frame's table view and generated summary read.
 *
 * TWO series: the count and the SHARE. The ring draws both — every arc carries
 * "880 (88.0%)" — so a table with only the counts is a lesser view of the same
 * chart, and a reader who wants the percentages has to divide by a total the
 * table does not state either (the frame puts that under the table).
 */
export function donutSeries(model: DonutModel, dimension = 'status'): ChartSeries {
  const chart: SeriesChart = {
    kind: 'series',
    dimensions: [dimension],
    x_type: 'category',
    series: [
      {
        key: 'count',
        label: 'Count',
        // The TRUE value, never the padded arc.
        points: model.slices.map((slice) => ({ x: slice.label, y: slice.value, n: slice.value })),
      },
      {
        key: 'percent',
        label: 'Share',
        points: model.slices.map((slice) => ({ x: slice.label, y: slice.percent, n: slice.value })),
      },
    ],
  }
  return chart
}
