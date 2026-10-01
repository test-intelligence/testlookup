/**
 * The model behind `StackedColumnChart` (VIZ-104, Wave 2.5 K1) — pure, so every
 * rule below is table-tested without a DOM.
 *
 * A stacked column chart is one column per BUCKET (a day, a month) split into
 * SERIES. Two kinds of series share it:
 *
 *   - a STATUS series (`status: 'failed'`) is drawn in that status's fixed
 *     colour AND its fixed decal (`STATUS_ENCODING`): the five status hues are
 *     only 1.07–1.38:1 apart, so the decal is what tells Skipped from Broken;
 *   - any other series (hours saved per model leg) takes the next series
 *     colour and the next category decal, counted over the non-status series
 *     only, so a chart of three legs gets series colours 1, 2, 3 and never a
 *     status colour for something that is not a status.
 *
 * `null` is NOT MEASURED. It is a gap in its column, "—" in the tooltip and
 * the table, and it adds nothing to the column's total — it is never drawn,
 * summed or stated as 0. A bucket where no series was measured has no total at
 * all (`total: null`), and the chart says how many there were. A measured 0
 * is a real zero: the column is empty but present, and the chart marks it on
 * the baseline so the two are never confused.
 *
 * A value that is not a finite, non-negative number (NaN, Infinity, a
 * negative count) is not a measurement either: it is treated as `null`, and
 * counted in `invalid` so a caller's bad data is visible rather than drawn.
 */
import type { SeriesChart, SeriesPoint, VizStatus } from '@/lib/viz/contracts'
import { formatPercent } from '@/utils/formatters'
import { formatPlainValue, NO_VALUE, ownValue, type ValueFormatter } from './chartText'
import { zeroBasedScale, type NiceScale } from './niceScale'
import { CHART_VARS, STATUS_ENCODING, STATUS_STACK_ORDER, seriesColor, statusStackRank, type DecalKind } from './tokens'
import { tipContent, type TooltipContent, type TooltipRow } from './tooltip'

export interface StackedColumnBucketInput {
  /** Unique per chart: the x value (a day, a month key). */
  key: string
  /** What the reader sees under the column. Ingested text: always rendered as text. */
  label: string
  /** One value per series key. Missing or `null` = not measured. */
  values: Readonly<Record<string, number | null | undefined>>
}

export interface StackedColumnSeriesInput {
  key: string
  label: string
  /** A status series takes the status's fixed colour and decal. */
  status?: VizStatus
}

export interface StackedColumnInput {
  buckets: readonly StackedColumnBucketInput[]
  /**
   * Bottom of the stack first. Status series are put in the kit's one order
   * (`STATUS_STACK_ORDER`) whatever order they come in; others keep theirs.
   */
  series: readonly StackedColumnSeriesInput[]
  /** What a column's height MEANS: the value axis's title ("Executions", "Hours saved"). */
  valueTitle: string
  /** What a column IS: the bucket axis's title ("Day (UTC)", "Month"). */
  bucketTitle: string
  /** How one value is printed in the tooltip, the table and the summary. Default: up to two decimals. */
  format?: ValueFormatter
  /**
   * `'time'` when the buckets are in time order (the summary then names the
   * latest value). Default `'category'`.
   */
  xType?: 'time' | 'category'
}

/** Category decals for non-status series, in order — the donut's (`DonutChart.tsx`). */
export const SERIES_DECALS: readonly DecalKind[] = ['solid', 'diagonal', 'crosshatch', 'dashes', 'dots']

export interface StackedColumnSeries {
  key: string
  label: string
  status: VizStatus | null
  /** A `var(--…)` reference from `CHART_VARS`. */
  color: string
  decal: DecalKind
  /** The Recharts row field this series is drawn from (`s0`, `s1`, …): never the caller's key, which may contain a dot. */
  field: string
}

export interface StackedColumnBucket {
  key: string
  label: string
  /** Per series, in series order. `null` = not measured. */
  values: (number | null)[]
  /** The sum of the MEASURED values; `null` when nothing in the bucket was measured. */
  total: number | null
  /** Every series measured and the sum is exactly 0: an empty column that is really there. */
  zero: boolean
}

export interface StackedColumnModel {
  buckets: StackedColumnBucket[]
  series: StackedColumnSeries[]
  valueTitle: string
  bucketTitle: string
  format: ValueFormatter
  xType: 'time' | 'category'
  /** Zero-based and nice: it ends on a tick, above the tallest column. */
  axis: NiceScale
  /** Buckets in which NO series was measured (drawn as a gap, stated under the plot). */
  gaps: number
  /** Buckets in which SOME, but not all, series were measured. */
  partial: number
  /** Values that were not a finite non-negative number, and so were treated as not measured. */
  invalid: number
  /** Nothing to draw: no bucket, or nothing measured anywhere. */
  empty: boolean
  /** Something was measured, and every measured value is 0: a chart of zeros is not drawn (`handOverWhenEmpty`). */
  allZero: boolean
}

/** The reason a not-measured value carries into the table and the export. */
export const NOT_MEASURED_REASON = 'not measured'
/** A column's total, in the tooltip and the table. */
export const TOTAL_LABEL = 'Total'
/** The table column that carries each bucket's total (a key no caller series can take: it has a space). */
export const TOTAL_SERIES_KEY = 'column total'

/** `value` if it is a measurement, else `null` (and whether it was a bad value rather than an absent one). */
function measurement(value: number | null | undefined): { value: number | null; invalid: boolean } {
  if (value === null || value === undefined) return { value: null, invalid: false }
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return { value: null, invalid: true }
  return { value, invalid: false }
}

/** The four statuses a test-run chart stacks, in the kit's one order (`STATUS_STACK_ORDER`), labelled as the legend says them. */
export const STATUS_STACK_SERIES: readonly StackedColumnSeriesInput[] = STATUS_STACK_ORDER.filter(
  (status) => status !== 'unknown',
).map((status) => ({ key: status, label: STATUS_ENCODING[status].label, status }))

/**
 * The caller's series with its STATUS series put in the kit's one order
 * (`STATUS_STACK_ORDER`, R2 F4): the slots status series hold keep being
 * status slots, filled bottom-up in that order, and every other series keeps
 * its place. So "Passed, Failed, Skipped, Broken" from one page and "Passed,
 * Failed, Broken, Skipped" from another stack, list and read out the same way;
 * a chart of non-status series (hours per model leg) is untouched.
 */
export function orderStatusSeries(series: readonly StackedColumnSeriesInput[]): StackedColumnSeriesInput[] {
  const statuses = series
    .filter((entry) => entry.status)
    .sort((a, b) => statusStackRank(a.status as VizStatus) - statusStackRank(b.status as VizStatus))
  let next = 0
  return series.map((entry) => (entry.status ? statuses[next++] : entry))
}

export function buildStackedColumnModel({
  buckets,
  series: callerSeries,
  valueTitle,
  bucketTitle,
  format = formatPlainValue,
  xType = 'category',
}: StackedColumnInput): StackedColumnModel {
  const series = orderStatusSeries(callerSeries)
  let categoryIndex = 0
  const drawnSeries: StackedColumnSeries[] = series.map((entry, index) => {
    if (entry.status) {
      return {
        key: entry.key,
        label: entry.label,
        status: entry.status,
        color: CHART_VARS.status[entry.status],
        decal: STATUS_ENCODING[entry.status].decal,
        field: `s${index}`,
      }
    }
    const i = categoryIndex++
    return {
      key: entry.key,
      label: entry.label,
      status: null,
      // Past the eighth series `seriesColor` answers the "Other" colour, never a recycled hue.
      color: seriesColor(CHART_VARS, i),
      decal: SERIES_DECALS[i % SERIES_DECALS.length],
      field: `s${index}`,
    }
  })

  let gaps = 0
  let partial = 0
  let invalid = 0
  let tallest = 0
  const drawnBuckets: StackedColumnBucket[] = buckets.map((bucket) => {
    const values = series.map((entry) => {
      const found = measurement(ownValue(bucket.values, entry.key))
      if (found.invalid) invalid += 1
      return found.value
    })
    const measured = values.filter((value): value is number => value !== null)
    const total = measured.length === 0 ? null : measured.reduce((sum, value) => sum + value, 0)
    if (total === null) gaps += 1
    else if (measured.length < values.length) partial += 1
    if (total !== null && total > tallest) tallest = total
    return {
      key: bucket.key,
      label: bucket.label,
      values,
      total,
      zero: total === 0 && measured.length === values.length,
    }
  })

  const empty = drawnBuckets.length === 0 || series.length === 0 || drawnBuckets.every((bucket) => bucket.total === null)
  const integer = drawnBuckets.every((bucket) => bucket.values.every((value) => value === null || Number.isInteger(value)))
  return {
    buckets: drawnBuckets,
    series: drawnSeries,
    valueTitle,
    bucketTitle,
    format,
    xType,
    axis: zeroBasedScale(tallest, { intervals: 5, integer }),
    gaps,
    partial,
    invalid,
    empty,
    allZero: !empty && tallest === 0,
  }
}

/**
 * One column's tooltip content (VIZ-601): every series in stack order — top
 * of the stack first, as the eye reads the column — with its value and its
 * share of the column, "—" and "not measured" for a gap; then the column's
 * total, the whole every share is of. The pointer's tooltip, the keyboard
 * readout and the announcer all read THIS.
 */
export function stackedColumnTipContent(model: StackedColumnModel, index: number): TooltipContent {
  const bucket = model.buckets[index]
  if (!bucket) return tipContent(undefined, [])
  const rows: TooltipRow[] = []
  for (let s = model.series.length - 1; s >= 0; s--) {
    const entry = model.series[s]
    const value = bucket.values[s]
    rows.push({
      kind: 'value',
      key: entry.key,
      label: entry.label,
      value: value === null ? NO_VALUE : model.format(value),
      detail:
        value === null
          ? NOT_MEASURED_REASON
          : bucket.total !== null && bucket.total > 0
            ? `${formatPercent((value / bucket.total) * 100)} of column`
            : undefined,
      color: entry.color,
    })
  }
  rows.push({
    kind: 'sample',
    key: 'total',
    label: TOTAL_LABEL,
    value: bucket.total === null ? NO_VALUE : model.format(bucket.total),
    detail: bucket.total === null ? NOT_MEASURED_REASON : undefined,
  })
  return tipContent(bucket.label, rows)
}

const notMeasuredPoint = (x: string): SeriesPoint => ({ x, y: null, n: 0, measured: false, reason: NOT_MEASURED_REASON })

/**
 * The frame's `series` (summary, table view, export), built FROM the drawn
 * model, so the plot, the summary and the table cannot disagree: a gap in a
 * column is "—" in its table cell, and the last column is each bucket's total.
 * `x_labels` carries the display labels (bucket keys stay unique even when two
 * labels are the same).
 */
export function stackedColumnToChartSeries(model: StackedColumnModel): SeriesChart {
  const point = (x: string, value: number | null): SeriesPoint =>
    value === null ? notMeasuredPoint(x) : { x, y: value, n: Number.isInteger(value) ? value : 0 }
  return {
    kind: 'series',
    dimensions: [model.bucketTitle],
    x_type: model.xType,
    x_labels: Object.fromEntries(model.buckets.map((bucket) => [bucket.key, bucket.label])),
    series: [
      ...model.series.map((entry, s) => ({
        key: entry.key,
        label: entry.label,
        points: model.buckets.map((bucket) => point(bucket.key, bucket.values[s])),
      })),
      {
        key: TOTAL_SERIES_KEY,
        label: TOTAL_LABEL,
        points: model.buckets.map((bucket) => point(bucket.key, bucket.total)),
      },
    ],
  }
}

/** The sentence under the plot when some buckets hold no measurement at all; `null` when none do. */
export function gapNote(model: StackedColumnModel, noun = 'column'): string | null {
  if (model.gaps === 0) return null
  const which = model.gaps === 1 ? `1 ${noun} has` : `${model.gaps} ${noun}s have`
  return `${which} no measured value (${NO_VALUE}), drawn as a gap rather than 0.`
}

/** The sentence under the plot when some values were not usable numbers; `null` when all were. */
export function invalidNote(model: StackedColumnModel): string | null {
  if (model.invalid === 0) return null
  return `${model.invalid} ${model.invalid === 1 ? 'value was' : 'values were'} not a count or amount and ${model.invalid === 1 ? 'is' : 'are'} shown as not measured (${NO_VALUE}).`
}

/** The kit's one short day label ("Mar 4"); it lives in `chartText.ts`, which the time series shares (R2 F5). */
export { utcDayLabel } from './chartText'
