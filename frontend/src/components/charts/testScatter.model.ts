/**
 * The test scatter's model (VIZ-506): p95 duration x failure rate x volume,
 * one point per test. Pure: no React, no DOM, no engine, no recharts — so the
 * selection rules below are unit-tested without ECharts, as spike S2 asks.
 *
 * QUADRANTS. The medians (sent by `/analytics/test-scatter`, unweighted over
 * the returned points) split the plot. A test is SLOW when its p95 is ABOVE
 * the median p95 and FLAKY when its failure rate is ABOVE the median failure
 * rate; a test ON a median line is not above it. Strictly above matters: on a
 * healthy suite the median failure rate is 0%, and "at or above" would call
 * every never-failing test flaky.
 *
 * SELECTION. Spike S2 is binding: ECharts' own `brushSelected` is not used
 * (in `large` mode it returns nothing, and it is not a model we can test).
 * The selection is OUR filter over the data rectangle `brushEnd` reports
 * (`areas[0].coordRange`, data space), inclusive on every edge, which the
 * spike measured equal to ECharts' own selection at 500 / 3,000 / 5,000
 * points. The keyboard path ("Select slow and flaky") is a programmatic brush
 * with a rectangle built HERE (`quadrantRect`): its lower edges sit on the
 * first data value strictly above each median, so the same inclusive filter
 * selects exactly the quadrant — one filter for the mouse and the keyboard.
 */
import type { PointsChart, PointsChartPoint } from '@/lib/viz/contracts'
import { formatNumber } from '@/utils/formatters'
import { axisValueFormatter, formatPlainValue } from './chartText'
import { sampleRow, tipContent, type TooltipContent } from './tooltip'
import type { NavRequest } from './useChartKeyboard'
import type { ChartMark } from './marks'

// ── Quadrants ────────────────────────────────────────────────────────────────

export type Quadrant = 'slow-flaky' | 'fast-flaky' | 'slow-stable' | 'fast-stable'

/** Legend order: the salient quadrant first. */
export const QUADRANTS: readonly Quadrant[] = ['slow-flaky', 'fast-flaky', 'slow-stable', 'fast-stable']

/** The salient quadrant: what "Select slow and flaky" selects. */
export const SALIENT_QUADRANT: Quadrant = 'slow-flaky'

export const QUADRANT_LABELS: Record<Quadrant, string> = {
  'slow-flaky': 'Slow and flaky',
  'fast-flaky': 'Fast and flaky',
  'slow-stable': 'Slow and stable',
  'fast-stable': 'Fast and stable',
}

/** What the quadrant names mean, once, under the plot. */
export const QUADRANT_RULE =
  'Slow: p95 above the median p95. Flaky: failure rate above the median failure rate. A test on a median line is not above it.'

export interface Medians {
  x: number
  y: number
}

const isSlow = (x: number, medians: Medians) => x > medians.x
const isFlaky = (y: number, medians: Medians) => y > medians.y

/** The quadrant a point sits in; strictly above a median counts as above. */
export function quadrantOf(point: { x: number; y: number }, medians: Medians): Quadrant {
  const slow = isSlow(point.x, medians)
  const flaky = isFlaky(point.y, medians)
  if (slow) return flaky ? 'slow-flaky' : 'slow-stable'
  return flaky ? 'fast-flaky' : 'fast-stable'
}

/** How many points sit in each quadrant (all four keys, zeros included). */
export function quadrantCounts(chart: PointsChart): Record<Quadrant, number> {
  const counts: Record<Quadrant, number> = { 'slow-flaky': 0, 'fast-flaky': 0, 'slow-stable': 0, 'fast-stable': 0 }
  if (!chart.medians) return counts
  for (const point of chart.points) counts[quadrantOf(point, chart.medians)] += 1
  return counts
}

// ── Data rectangles and the selection filter ────────────────────────────────

/** A rectangle in DATA space (ms, percent), each range low to high. */
export interface DataRect {
  x: readonly [number, number]
  y: readonly [number, number]
}

const ordered = (a: number, b: number): readonly [number, number] => (a <= b ? [a, b] : [b, a])

/** A rectangle from two corners in any order. */
export function dataRect(x0: number, x1: number, y0: number, y1: number): DataRect {
  return { x: ordered(x0, x1), y: ordered(y0, y1) }
}

/**
 * The indices (data order) of the points inside `rect`, every edge
 * INCLUSIVE: a point drawn on the brush's border is in it, as ECharts draws it.
 */
export function pointsInRect(points: readonly PointsChartPoint[], rect: DataRect): number[] {
  const out: number[] = []
  points.forEach((point, index) => {
    if (point.x >= rect.x[0] && point.x <= rect.x[1] && point.y >= rect.y[0] && point.y <= rect.y[1]) out.push(index)
  })
  return out
}

const finitePair = (value: unknown): value is [number, number] =>
  Array.isArray(value) && value.length === 2 && value.every((v) => typeof v === 'number' && Number.isFinite(v))

/**
 * The data rectangle of a `brushEnd` event, read without trusting its shape:
 * `areas[0].coordRange = [[x0, x1], [y0, y1]]` of a `rect` brush. `null` when
 * the brush was cleared (no area) or the event is not a rectangle we can read.
 */
export function rectFromBrushEnd(params: unknown): DataRect | null {
  const areas = (params as { areas?: unknown } | null | undefined)?.areas
  if (!Array.isArray(areas) || areas.length === 0) return null
  const area = areas[0] as { brushType?: unknown; coordRange?: unknown } | null
  if (!area || area.brushType !== 'rect' || !Array.isArray(area.coordRange) || area.coordRange.length !== 2) return null
  const [xs, ys] = area.coordRange as unknown[]
  if (!finitePair(xs) || !finitePair(ys)) return null
  return dataRect(xs[0], xs[1], ys[0], ys[1])
}

/**
 * A data rectangle that holds EXACTLY the points of `quadrant` under the
 * inclusive filter: on the "above" side of an axis its low edge is the
 * smallest value strictly above the median (nothing lies between the median
 * and it), on the "not above" side its high edge is the largest value at or
 * below the median. `null` when the quadrant holds no point (nothing to brush).
 */
export function quadrantRect(chart: PointsChart, quadrant: Quadrant): DataRect | null {
  const medians = chart.medians
  if (!medians) return null
  const members = chart.points.filter((point) => quadrantOf(point, medians) === quadrant)
  if (members.length === 0) return null
  const slowSide = quadrant === 'slow-flaky' || quadrant === 'slow-stable'
  const flakySide = quadrant === 'slow-flaky' || quadrant === 'fast-flaky'
  // The extent of every point on this side of each median, not only the
  // members': a rectangle edge must not stop short of a member, and every
  // point between the edges is on the right side of both medians anyway.
  const xs = chart.points.map((p) => p.x).filter((x) => isSlow(x, medians) === slowSide)
  const ys = chart.points.map((p) => p.y).filter((y) => isFlaky(y, medians) === flakySide)
  return dataRect(minOf(xs), maxOf(xs), minOf(ys), maxOf(ys))
}

// A loop, not Math.min(...spread): 5,000 arguments is fine, but the kit never spreads data.
function minOf(values: readonly number[]): number {
  let least = Infinity
  for (const v of values) if (v < least) least = v
  return least
}
function maxOf(values: readonly number[]): number {
  let most = -Infinity
  for (const v of values) if (v > most) most = v
  return most
}

/** Two rectangles (or two "none"s) that select the same thing. */
export function sameRect(a: DataRect | null, b: DataRect | null): boolean {
  if (a === null || b === null) return a === b
  return a.x[0] === b.x[0] && a.x[1] === b.x[1] && a.y[0] === b.y[0] && a.y[1] === b.y[1]
}

// ── Marks ────────────────────────────────────────────────────────────────────

/** A point as a mark: the TEST, keyed by its fingerprint (what a rows request sends back). */
export function scatterMark(data: PointsChart, index: number): ChartMark | null {
  const point = data.points[index]
  return point ? { dimension: 'test', value: point.id, label: point.label, y: point.y, n: point.n } : null
}

// ── The keyboard walk ────────────────────────────────────────────────────────

/** Byte order, never `localeCompare`: the order must not depend on the reader's locale. */
const byCodeUnit = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

/** The keyboard's walking order: by p95 (x), then failure rate, then id. */
export function keyboardOrder(points: readonly PointsChartPoint[]): number[] {
  return points
    .map((_, index) => index)
    .sort((a, b) => points[a].x - points[b].x || points[a].y - points[b].y || byCodeUnit(points[a].id, points[b].id))
}

/**
 * The next point of the walk: Right / Down = the next slower test, Left / Up =
 * the previous one, Home / End = the fastest / slowest. Nothing highlighted
 * yet: the first point (End: the last). Stays put at either end.
 */
export function moveInOrder(order: readonly number[], current: number | null, { key }: NavRequest): number | null {
  if (order.length === 0) return null
  const position = current === null ? -1 : order.indexOf(current)
  if (position === -1) return key === 'End' ? order[order.length - 1] : order[0]
  switch (key) {
    case 'ArrowRight':
    case 'ArrowDown':
      return order[Math.min(order.length - 1, position + 1)]
    case 'ArrowLeft':
    case 'ArrowUp':
      return order[Math.max(0, position - 1)]
    case 'Home':
      return order[0]
    case 'End':
      return order[order.length - 1]
  }
}

// ── The selection list's order ───────────────────────────────────────────────

export type ScatterSortKey = 'label' | 'x' | 'y' | 'size'
export type SortDirection = 'asc' | 'desc'

/** The list's default: the most failing first, then the slowest. */
export const DEFAULT_SORT: { key: ScatterSortKey; direction: SortDirection } = { key: 'y', direction: 'desc' }

/** `indices` (into `points`) in the list's order; ties by name, then id, ascending whatever the direction. */
export function sortSelection(
  points: readonly PointsChartPoint[],
  indices: readonly number[],
  key: ScatterSortKey,
  direction: SortDirection,
): number[] {
  const sign = direction === 'asc' ? 1 : -1
  const primary = (a: PointsChartPoint, b: PointsChartPoint) =>
    key === 'label' ? byCodeUnit(a.label, b.label) : a[key] - b[key]
  return [...indices].sort((i, j) => {
    const a = points[i]
    const b = points[j]
    return sign * primary(a, b) || byCodeUnit(a.label, b.label) || byCodeUnit(a.id, b.id)
  })
}

/** At most this many selected tests are listed; the rest are counted ("and 12 more"). */
export const SELECTION_LIST_LIMIT = 200

// ── Words ────────────────────────────────────────────────────────────────────

/** A p95 below this is drawn AT it: `duration_ms` is an integer and a log axis cannot hold 0. */
export const X_FLOOR_MS = 1

/** The x value in words; a value on the log axis' floor says it may be lower. */
export function formatX(chart: PointsChart, x: number): string {
  const text = axisValueFormatter(chart.x.unit)(x)
  return chart.x.scale === 'log' && chart.x.unit === 'ms' && x <= X_FLOOR_MS ? `${text} or less` : text
}

export function formatY(chart: PointsChart, y: number): string {
  return axisValueFormatter(chart.y.unit)(y)
}

/** The sample row's label: the evaluated executions behind the failure rate. */
export const EVALUATED_LABEL = 'Evaluated'

/**
 * One point's tooltip, in the shared model (VIZ-601): the test as the title,
 * its p95, failure rate and executions, the evaluated sample, and its
 * quadrant in words (so the colour is never the only channel). The mouse
 * tooltip and the keyboard announcement both come from here.
 */
export function scatterTooltipContent(chart: PointsChart, index: number): TooltipContent {
  const point = chart.points[index]
  if (!point) return { rows: [] }
  return tipContent(point.label, [
    { kind: 'value', key: 'x', label: chart.x.label, value: formatX(chart, point.x) },
    { kind: 'value', key: 'y', label: chart.y.label, value: formatY(chart, point.y) },
    { kind: 'value', key: 'size', label: chart.size.label, value: formatPlainValue(point.size) },
    sampleRow(point.n, EVALUATED_LABEL),
    chart.medians ? { kind: 'note', key: 'quadrant', label: 'Quadrant', value: QUADRANT_LABELS[quadrantOf(point, chart.medians)] } : null,
  ])
}

const tests = (n: number) => `${formatNumber(n)} ${n === 1 ? 'test' : 'tests'}`

export interface Exclusions {
  below_min_executions: number
  no_duration: number
  no_evaluated: number
}

/** How many tests were left out, all reasons. */
export function excludedTotal(excluded: Exclusions | undefined): number {
  return excluded ? excluded.below_min_executions + excluded.no_duration + excluded.no_evaluated : 0
}

/**
 * The tests left out, by reason, in words ("14 tests with fewer than 5
 * executions, 2 with no duration"), or `null` when none was. Each count is a
 * TEST, under the first reason that applies (the endpoint's rule).
 */
export function exclusionParts(excluded: Exclusions | undefined, minExecutions: number): string | null {
  if (!excluded) return null
  const parts: string[] = []
  if (excluded.below_min_executions) parts.push(`${tests(excluded.below_min_executions)} with fewer than ${formatNumber(minExecutions)} executions`)
  if (excluded.no_duration) parts.push(`${tests(excluded.no_duration)} with no duration`)
  if (excluded.no_evaluated) parts.push(`${tests(excluded.no_evaluated)} with only skipped or unknown results`)
  return parts.length ? parts.join(', ') : null
}

/** The footer's sentence: what is NOT on the plot, always stated. */
export function exclusionSentence(excluded: Exclusions | undefined, minExecutions: number): string {
  const parts = exclusionParts(excluded, minExecutions)
  return parts ? `Not shown: ${parts}.` : 'No test left out.'
}

/** The body of a scatter with nothing to place: why, never an empty plot. */
export function nothingPlacedSentence(excluded: Exclusions | undefined, minExecutions: number): string {
  const parts = exclusionParts(excluded, minExecutions)
  return parts ? `No test can be placed on this chart: ${parts}.` : 'No test can be placed on this chart.'
}

/** The one-line takeaway: how many tests sit in the salient quadrant. */
export function scatterTakeaway(chart: PointsChart): string | undefined {
  if (chart.points.length === 0 || !chart.medians) return undefined
  const salient = quadrantCounts(chart)[SALIENT_QUADRANT]
  return `${formatNumber(salient)} of ${tests(chart.points.length)} slow and flaky (above both medians)`
}

/** The renderer's own one-sentence name for assistive tech. */
export function scatterDescription(title: string, chart: PointsChart): string {
  const scale = chart.x.scale === 'log' ? ', log scale' : ''
  return `${title}: ${tests(chart.points.length)}, ${chart.x.label}${scale} across, ${chart.y.label} up, size is ${chart.size.label.toLowerCase()}.`
}

/** The selection's count in words, for the list's heading and the announcement. */
export function selectionSentence(count: number): string {
  return count === 0 ? 'No test selected' : `${tests(count)} selected`
}
