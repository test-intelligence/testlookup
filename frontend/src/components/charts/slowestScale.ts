/**
 * The bar scale of `SlowestTests` (TRND-09, the VIZ-406 remainder; VIZ
 * follow-up 19: one outlier squashed every bar).
 *
 * The bars were scaled linearly against the longest p95, so one 40 s test
 * beside nineteen tests of 1-3 s drew those nineteen as slivers that all read
 * the same. The fix is a CLAMP, not a log axis: a ranked list of bars is read
 * as "how much longer is this one than that one", and a log length answers a
 * different question.
 *
 *   - The scale stops at the p90 of the measured p95s (nearest rank over the
 *     rows shown, at most 20), but only when the longest is more than
 *     `SLOWEST_CLAMP_FACTOR` times that: an even spread (1 s to 2 s) keeps
 *     its plain linear scale, because nothing in it is flattening anything.
 *   - A bar past the stop is drawn to the full width with an OFF-SCALE marker,
 *     and its real p95 stays printed beside it, so nothing is hidden: only the
 *     bar is cut, never the number.
 *   - An unmeasured p95 (`null`) has no bar and takes no part in the scale.
 *
 * Pure, so the rule is table-tested (`slowestScale.test.tsx`).
 */
import { formatDuration, formatNumber } from '@/utils/formatters'

/** The quantile of the shown p95s where a clamped scale stops. */
export const SLOWEST_CLAMP_QUANTILE = 0.9
/** The longest must exceed the stop by more than this factor before the scale is clamped. */
export const SLOWEST_CLAMP_FACTOR = 2
/** Fewer measured rows than this are never clamped: a p90 of three values is the largest of them. */
export const SLOWEST_CLAMP_MIN_ROWS = 5

export interface SlowestScale {
  /** The p95 that draws a full-width bar, ms. 0 when nothing is measured. */
  max: number
  /** True when `max` is the clamp (some bars run off the scale). */
  clamped: boolean
  /** How many rows run past `max` and carry the off-scale marker. */
  offScale: number
}

/** Nearest-rank quantile of an ASCENDING array (the same rule as `durationBuckets`). */
function nearestRank(sorted: readonly number[], q: number): number {
  const rank = Math.ceil(q * sorted.length)
  return sorted[Math.min(Math.max(rank, 1), sorted.length) - 1]
}

const isMeasured = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value > 0

/** The scale for the rows' p95s (ms; `null` = not measured). */
export function slowestScale(values: readonly (number | null | undefined)[]): SlowestScale {
  const measured = values.filter(isMeasured).sort((a, b) => a - b)
  if (measured.length === 0) return { max: 0, clamped: false, offScale: 0 }
  const longest = measured[measured.length - 1]
  if (measured.length < SLOWEST_CLAMP_MIN_ROWS) return { max: longest, clamped: false, offScale: 0 }
  const stop = nearestRank(measured, SLOWEST_CLAMP_QUANTILE)
  if (!(stop > 0) || longest <= stop * SLOWEST_CLAMP_FACTOR) return { max: longest, clamped: false, offScale: 0 }
  return { max: stop, clamped: true, offScale: measured.filter((value) => value > stop).length }
}

/** A bar's width on `scale`, percent (1 % at least for a measured value, so it is never invisible). */
export function slowestBarWidth(value: number | null, scale: SlowestScale): number {
  if (!isMeasured(value) || scale.max <= 0) return 0
  return Math.max(Math.min((value / scale.max) * 100, 100), 1)
}

/** The words under a clamped list: where the scale stops, and how many bars run past it. */
export function offScaleNote(max: number, offScale: number): string {
  return `Bars stop at ${formatDuration(max)}, the p90 of these tests; ${formatNumber(offScale)} longer ${
    offScale === 1 ? 'test runs' : 'tests run'
  } off the scale (hatched end), with ${offScale === 1 ? 'its' : 'their'} real p95 beside the bar.`
}

/** What a screen reader hears for a bar that runs off the scale (the bar itself is decorative). */
export const OFF_SCALE_TEXT = 'off scale'

/** Whether `value` runs past a clamped scale (and is drawn with the off-scale marker). */
export function isOffScale(value: number | null, scale: SlowestScale): boolean {
  return scale.clamped && isMeasured(value) && value > scale.max
}
