/**
 * The bar charts' model (VIZ-402) — ranked, grouped and stacked, as pure data.
 *
 *   ranked      horizontal, sorted DESCENDING, labelled with full-precision
 *               counts, on a value axis that STARTS AT ZERO. A bar chart
 *               encodes value as length, so an axis that starts anywhere else
 *               draws a 2% difference as a 10× one.
 *   ties        a top-N cut that splits equal values is arbitrary: the ties at
 *               the boundary come too, up to a cap of N + `TIE_CAP_EXTRA`, and
 *               the chart SAYS so — an unexplained 14th bar in a "top 10" is
 *               worse than no 14th bar.
 *   change      any negative value makes it a diverging chart around zero, on
 *               a symmetric axis so "down 8" and "up 8" are the same length.
 *   stacked     segments in the FIXED status order, with a toggle between
 *               absolute counts and 100%; the 100% segments sum to exactly
 *               100.0 (largest remainder, `chartCatalog`).
 *   grouped     the same segments side by side, so the axis is sized by the
 *               largest SEGMENT rather than the largest total.
 *   labels      a long test name is middle-truncated — two tests in one class
 *               differ at the ends, not in the middle — and the full name is
 *               what the tooltip and the table view read.
 *   pagination  at most `MAX_BARS_PER_PAGE` bars are drawn; past that the
 *               chart paginates and states the page and the total.
 */
import { VIZ_STATUSES, type ChartSeries, type EnvelopeMeta, type SeriesChart, type VizStatus } from '@/lib/viz/contracts'
import { formatNumber } from '@/utils/formatters'
import type { ChartResponse } from './chartState'
import { largestRemainderPercents } from './chartCatalog'
import { STATUS_ENCODING } from './tokens'

/** At most this many bars are drawn at once; past it the chart paginates. */
export const MAX_BARS_PER_PAGE = 50
/** Ties at the top-N boundary are included up to N + this many bars. */
export const TIE_CAP_EXTRA = 5
/** Longest drawn bar label, before middle truncation. */
export const MAX_BAR_LABEL = 34
/** The one character that marks a middle truncation. */
export const ELLIPSIS = '…'

export interface BarInput {
  key: string
  /** The FULL label. The tooltip and the table always show this one. */
  label: string
  value: number
}

export interface RankedBar extends BarInput {
  /** The drawn label: middle-truncated when the full one is too long. */
  short: string
  /** The full-precision value, formatted once. */
  valueLabel: string
  /** This bar is at the top-N boundary value, and it shares it. */
  tied: boolean
}

export interface RankedModel {
  bars: RankedBar[]
  /**
   * The value axis. `domain[0]` is ZERO for counts, and for a diverging chart
   * the axis is symmetric about zero — either way zero is on it.
   */
  domain: [number, number]
  diverging: boolean
  /**
   * Every kept value is zero. Not a chart of zero-length bars on a [0, 1]
   * axis: the caller hands over to the frame's `filtered-empty` state, exactly
   * as the donut does for an all-zero breakdown.
   */
  allZero: boolean
  /** Ties admitted past the top-N cut. */
  ties: number
  page: number
  pages: number
  /** Bars in the whole (ranked, tie-extended) set, across every page. */
  total: number
  /** What the chart says about itself: ties, pagination, a diverging axis. */
  notes: string[]
}

export interface RankedOptions {
  /** The server's top N. Ties AT the boundary are kept beyond it. */
  topN?: number
  /** 0-based; clamped into range. */
  page?: number
}

/**
 * `text` shortened to `max` characters by cutting the MIDDLE out:
 * `tests.integration…retries_once`. Two tests in one class share a long
 * prefix and differ at the end, so an end-truncated label renames them both to
 * the same thing.
 */
export function middleTruncate(text: string, max = MAX_BAR_LABEL): string {
  // CODE POINTS, not UTF-16 units: `slice` on a surrogate pair leaves a lone
  // surrogate behind, which is not a character at all — the axis draws a
  // replacement glyph and the label is no longer the test's name.
  const points = [...text]
  if (points.length <= max) return text
  const head = Math.ceil((max - 1) / 2)
  const tail = max - 1 - head
  return `${points.slice(0, head).join('')}${ELLIPSIS}${tail > 0 ? points.slice(points.length - tail).join('') : ''}`
}

const byValueThenLabel = (a: BarInput, b: BarInput) =>
  b.value - a.value || (a.label < b.label ? -1 : a.label > b.label ? 1 : 0)

/** `page` clamped into `[0, pages - 1]`, and the page count for `total` rows. */
function paginate(total: number, page: number): { page: number; pages: number } {
  const pages = Math.max(1, Math.ceil(total / MAX_BARS_PER_PAGE))
  return { page: Math.min(Math.max(page, 0), pages - 1), pages }
}

/**
 * How BIG the set is — not WHERE in it the reader is. The position belongs
 * beside the page buttons, where it is their `aria-describedby`; saying it in
 * a note as well put "Page 2 of 3" on the screen twice.
 */
function pageNote(page: number, pages: number, total: number, noun: string): string[] {
  if (pages <= 1) return []
  return [`${formatNumber(total)} ${noun} in all`]
}

/** The ranked (and possibly diverging) horizontal bar model. */
export function rankedModel(items: readonly BarInput[], options: RankedOptions = {}): RankedModel {
  const sorted = [...items].filter((item) => Number.isFinite(item.value)).sort(byValueThenLabel)
  const notes: string[] = []

  // ── ties at the top-N boundary ────────────────────────────────────────────
  const { topN } = options
  let kept = sorted
  let ties = 0
  let boundary: number | null = null
  if (topN !== undefined && topN > 0 && sorted.length > topN) {
    boundary = sorted[topN - 1].value
    const cap = topN + TIE_CAP_EXTRA
    let end = topN
    while (end < sorted.length && end < cap && sorted[end].value === boundary) end += 1
    ties = end - topN
    kept = sorted.slice(0, end)
    if (ties > 0) {
      const capped = kept.length === cap && sorted.length > cap && sorted[cap]?.value === boundary
      // "bars in all", never "bars shown": past `MAX_BARS_PER_PAGE` the page
      // beside this note draws 50 of them, and a note that says 55 are shown
      // next to 50 bars is simply wrong.
      notes.push(
        `Includes ${formatNumber(ties)} more tied with the ${topN}th${capped ? `, capped at ${cap}` : ''}: ` +
          `${formatNumber(kept.length)} bars in all.`,
      )
    }
  }

  const diverging = kept.some((item) => item.value < 0)
  const extreme = kept.reduce((most, item) => Math.max(most, Math.abs(item.value)), 0)
  const domain: [number, number] = diverging
    ? [-extreme, extreme]
    : [0, extreme > 0 ? extreme : 1]
  if (diverging) notes.push('Bars diverge from a zero baseline: left is a fall, right is a rise.')

  const { page, pages } = paginate(kept.length, options.page ?? 0)
  notes.push(...pageNote(page, pages, kept.length, 'bars'))

  const bars: RankedBar[] = kept
    .slice(page * MAX_BARS_PER_PAGE, page * MAX_BARS_PER_PAGE + MAX_BARS_PER_PAGE)
    .map((item) => ({
      ...item,
      short: middleTruncate(item.label),
      valueLabel: formatNumber(item.value, { maximumFractionDigits: 2 }),
      tied: ties > 0 && boundary !== null && item.value === boundary,
    }))

  return {
    bars,
    domain,
    diverging,
    allZero: kept.length > 0 && kept.every((item) => item.value === 0),
    ties,
    page,
    pages,
    total: kept.length,
    notes,
  }
}

// ── Grouped and stacked by status ────────────────────────────────────────────

export interface StatusBarInput {
  key: string
  label: string
  counts: Readonly<Partial<Record<VizStatus, number>>>
}

export interface StatusSegment {
  status: VizStatus
  /** The TRUE count, whichever mode is on: the tooltip and the table read it. */
  value: number
  /** Share of this bar, to one decimal; a bar's percents sum to exactly 100.0. */
  percent: number
  /** What is DRAWN: the count, or the percent in 100% mode. */
  plotted: number
}

export interface StatusBar {
  key: string
  label: string
  short: string
  total: number
  segments: StatusSegment[]
}

/** What a 100%-mode chart says about itself, in the footer and in the table. */
export const PERCENT_MODE_NOTE = 'Each bar is drawn as 100%: the segments are shares of that bar, not counts.'

export type StackMode = 'absolute' | 'percent'
export type BarLayout = 'stacked' | 'grouped'

export interface StatusBarModel {
  bars: StatusBar[]
  /** The statuses drawn, in the FIXED status order. */
  statuses: VizStatus[]
  layout: BarLayout
  mode: StackMode
  domain: [number, number]
  /** Nothing measured in any bar. */
  empty: boolean
  page: number
  pages: number
  total: number
  notes: string[]
}

export interface StatusBarOptions {
  layout?: BarLayout
  mode?: StackMode
  page?: number
}

/**
 * Grouped or stacked bars by status. The bars keep the order they arrived in
 * (a composition is not a ranking), and the statuses are always in
 * `VIZ_STATUSES` order — a status that nothing has is left out, the rest keep
 * their places.
 */
export function statusBarModel(
  rows: readonly StatusBarInput[],
  options: StatusBarOptions = {},
): StatusBarModel {
  const layout = options.layout ?? 'stacked'
  const mode = options.mode ?? 'absolute'
  const statuses = VIZ_STATUSES.filter((status) => rows.some((row) => (row.counts[status] ?? 0) > 0))

  const all: StatusBar[] = rows.map((row) => {
    const values = statuses.map((status) => row.counts[status] ?? 0)
    const percents = largestRemainderPercents(values)
    return {
      key: row.key,
      label: row.label,
      short: middleTruncate(row.label),
      total: values.reduce((sum, value) => sum + value, 0),
      segments: statuses.map((status, index) => ({
        status,
        value: values[index],
        percent: percents[index],
        plotted: mode === 'percent' ? percents[index] : values[index],
      })),
    }
  })

  const biggestTotal = all.reduce((most, drawn) => Math.max(most, drawn.total), 0)
  const biggestSegment = all.reduce(
    (most, drawn) => drawn.segments.reduce((inner, segment) => Math.max(inner, segment.value), most),
    0,
  )
  const ceiling = layout === 'grouped' ? biggestSegment : biggestTotal
  const domain: [number, number] = mode === 'percent' ? [0, 100] : [0, ceiling > 0 ? ceiling : 1]

  const { page, pages } = paginate(all.length, options.page ?? 0)
  const notes = pageNote(page, pages, all.length, 'bars')
  // Which side of the toggle is live, in words. The drawing alone cannot say
  // it: two bars of equal length are either two equal totals or two 100%s.
  if (mode === 'percent') notes.unshift(PERCENT_MODE_NOTE)

  return {
    bars: all.slice(page * MAX_BARS_PER_PAGE, page * MAX_BARS_PER_PAGE + MAX_BARS_PER_PAGE),
    statuses: [...statuses],
    layout,
    mode,
    domain,
    empty: all.every((drawn) => drawn.total === 0),
    page,
    pages,
    total: all.length,
    notes,
  }
}

// ── The C3 series the frame's table view and summary read ────────────────────

/** A count for contract C3's `n`: a non-negative integer. */
const sampleSize = (value: number) => Math.max(0, Math.round(Math.abs(value)))

export function barSeries(model: RankedModel, dimension = 'category'): ChartSeries {
  const chart: SeriesChart = {
    kind: 'series',
    dimensions: [dimension],
    x_type: 'category',
    series: [
      {
        key: 'value',
        label: 'Value',
        // The FULL label, never the truncated one.
        points: model.bars.map((drawn) => ({ x: drawn.label, y: drawn.value, n: sampleSize(drawn.value) })),
      },
    ],
  }
  return chart
}

/**
 * The table's (and the summary's) series for a grouped / stacked chart —
 * WHAT IS DRAWN, whichever side of the toggle is live. A table that keeps the
 * absolute counts while the plot shows 100% is a second, disagreeing chart:
 * the reader switched to shares and the table still answers a different
 * question. `n` stays the true count either way, because a sample size is not
 * a percentage.
 */
export function statusBarSeries(model: StatusBarModel, dimension = 'category'): ChartSeries {
  const percent = model.mode === 'percent'
  const chart: SeriesChart = {
    kind: 'series',
    dimensions: [dimension, 'status'],
    x_type: 'category',
    series: model.statuses.map((status, index) => ({
      key: status,
      label: STATUS_ENCODING[status].label,
      points: model.bars.map((drawn) => {
        const segment = drawn.segments[index]
        const value = (percent ? segment?.percent : segment?.value) ?? 0
        return { x: drawn.label, y: value, n: sampleSize(segment?.value ?? 0) }
      }),
    })),
  }
  return chart
}

// ── Adapters: both existing endpoints and the VIZ-203 chart-data shape ───────

/**
 * A chart-data (C3) category series as bars: one bar per x, summed across the
 * series. An x with no MEASURED value anywhere is left out — a null is no
 * data, not a zero-length bar.
 */
export function barsFromSeries(series: ChartSeries): BarInput[] {
  if (series.kind !== 'series') return []
  const totals = new Map<string, number | null>()
  for (const entry of series.series) {
    for (const point of entry.points) {
      const current = totals.get(point.x) ?? null
      if (point.y === null) {
        if (!totals.has(point.x)) totals.set(point.x, null)
        continue
      }
      totals.set(point.x, (current ?? 0) + point.y)
    }
  }
  const labels = series.x_labels ?? {}
  return [...totals]
    .filter(([, value]) => value !== null)
    .map(([key, value]) => ({ key, label: labels[key] ?? key, value: value as number }))
}

/** A chart-data series whose SERIES are statuses, as grouped/stacked rows. */
export function statusRowsFromSeries(series: ChartSeries): StatusBarInput[] {
  if (series.kind !== 'series') return []
  const statuses = new Set<string>(VIZ_STATUSES)
  const labels = series.x_labels ?? {}
  const rows = new Map<string, StatusBarInput>()
  for (const entry of series.series) {
    if (!statuses.has(entry.key)) continue
    const status = entry.key as VizStatus
    for (const point of entry.points) {
      if (point.y === null) continue
      const row = rows.get(point.x) ?? { key: point.x, label: labels[point.x] ?? point.x, counts: {} }
      row.counts = { ...row.counts, [status]: ((row.counts[status] ?? 0) as number) + point.y }
      rows.set(point.x, row)
    }
  }
  return [...rows.values()]
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

function itemsOf(payload: unknown): unknown[] {
  if (!isRecord(payload)) return []
  const items = payload.items
  return Array.isArray(items) ? items : []
}

/**
 * The C2 envelope the legacy endpoints already send. Both answer
 * `with_meta(payload, build_meta(...))` (routers/analytics.py), so throwing it
 * away here is what stopped `not-measured` and `truncated` ever firing for
 * these two sources: an unmeasured scope rendered as an ordinary empty chart
 * with no reason given. It is passed through UNVALIDATED on purpose — this
 * adapter is thin, and `validateChartResponse` has the last word downstream.
 */
function envelopeOf(payload: unknown): EnvelopeMeta | null {
  if (!isRecord(payload)) return null
  const meta = payload.meta
  return isRecord(meta) ? (meta as unknown as EnvelopeMeta) : null
}

function categorySeries(
  dimension: string,
  key: string,
  name: string,
  points: SeriesChart['series'][number]['points'],
  meta: EnvelopeMeta | null,
  xLabels?: Record<string, string>,
): ChartResponse {
  return {
    meta,
    series: {
      kind: 'series',
      dimensions: [dimension],
      x_type: 'category',
      series: [{ key, label: name, points }],
      ...(xLabels ? { x_labels: xLabels } : {}),
    },
  }
}

/** Why a legacy row could not be read. A gap, never a measured zero. */
export const UNREADABLE_COUNT_REASON = 'the server sent no usable count for this row'

/**
 * A legacy row's count. A missing field, a string `"12"` or a NaN is NOT zero
 * — "this test never failed" is a measurement, and this is the absence of one.
 */
function legacyCount(raw: unknown): { y: number | null; n: number; measured?: false; reason?: string } {
  if (typeof raw === 'number' && Number.isFinite(raw)) return { y: raw, n: sampleSize(raw) }
  return { y: null, n: 0, measured: false, reason: UNREADABLE_COUNT_REASON }
}

/**
 * The legacy `/analytics/top-failing` payload as the canonical `ChartResponse`
 * — so one pipeline (validate → state → model → plot → table) serves both the
 * old endpoints and VIZ-203's `chart-data`.
 *
 * Bars are keyed on `test_fingerprint`, with the display name in `x_labels`.
 * Keying on the NAME merged two different tests that share one — a
 * parameterised test, or the same name in two suites — into a single bar whose
 * value belonged to neither of them, and `BreakdownChart` then counted one
 * category where there were two. A row with no fingerprint gets its own key
 * from its position, so rows are never merged by accident.
 */
export function chartResponseFromTopFailing(payload: unknown): ChartResponse {
  // `displayNames`, not `labels`: the chart-kit guard reads a computed write
  // into anything called *label* as an ECharts label sink.
  const displayNames: Record<string, string> = {}
  const points = itemsOf(payload)
    .filter(isRecord)
    .map((item, index) => {
      const name = String(item.test_name ?? '')
      const fingerprint = typeof item.test_fingerprint === 'string' ? item.test_fingerprint.trim() : ''
      const key = fingerprint || `${name}#${index}`
      displayNames[key] = name
      return { x: key, ...legacyCount(item.fail_count) }
    })
  return categorySeries('test', 'failures', 'Failures', points, envelopeOf(payload), displayNames)
}

/** The legacy `/analytics/failure-categories` payload as a `ChartResponse`. */
export function chartResponseFromFailureCategories(payload: unknown): ChartResponse {
  const points = itemsOf(payload)
    .filter(isRecord)
    .map((item) => ({ x: String(item.category ?? ''), ...legacyCount(item.count) }))
  return categorySeries('failure_category', 'failures', 'Failures', points, envelopeOf(payload))
}
