/**
 * Nice axis scales — a domain that ends ON a tick, and ticks that are round
 * numbers an equal distance apart.
 *
 * Two defects the first Linux baselines showed, and neither a unit test nor a
 * text assertion could see:
 *
 *   - a value axis that ENDS AT THE DATA MAXIMUM ("0 15 30 41"): the last
 *     interval is shorter than the others, and the eye reads an evenly spaced
 *     grid as the scale;
 *   - a zoomed rate axis whose ticks sat at 93.33 and 96.67 and were PRINTED
 *     as 93 and 96 — labels that name the wrong value, on ticks that then look
 *     unevenly spaced.
 *
 * Both are fixed the same way: the chart is handed the ticks, not the domain
 * alone, and every tick is a multiple of a step from the 1-2-2.5-5 ladder, so
 * its label needs no rounding at the precision `tickDecimals` gives it.
 *
 * Pure, and free of `@/` value imports: `timeSeriesModel` imports this, and
 * the Playwright specs import `timeSeriesModel` in plain Node.
 */

/** Step mantissas for a primary axis: 1, 2, 2.5, 5 per decade. */
export const DECADE_STEPS = [1, 2, 2.5, 5] as const
/**
 * A finer ladder for a SECONDARY axis whose interval count is fixed by the
 * primary one (so its ticks land on the same grid lines): with only four
 * intervals to spend, 1-2-2.5-5 can waste up to half the axis.
 */
export const FINE_STEPS = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8] as const

export interface NiceScale {
  /** Starts and ends ON a tick. */
  domain: [number, number]
  /** Evenly spaced, `step` apart, first and last equal to the domain. */
  ticks: number[]
  step: number
}

export interface NiceOptions {
  /** Aim for at most this many intervals between `min` and `max`. */
  intervals: number
  /** Only whole-number steps (a count has no half-run tick). */
  integer?: boolean
  steps?: readonly number[]
}

/** Decimal places a step needs to be printed exactly: 2.5 -> 1, 0.25 -> 2, 50 -> 0. */
export function tickDecimals(step: number): number {
  for (let places = 0; places <= 10; places++) {
    const scaled = step * 10 ** places
    if (Math.abs(scaled - Math.round(scaled)) < 1e-9 * Math.max(1, scaled)) return places
  }
  return 10
}

/** Floating-point noise off a computed tick: 0.1 * 3 is 0.30000000000000004. */
const clean = (value: number) => Number(value.toPrecision(12))

/** The smallest ladder step at or above `rough`. */
export function niceStep(rough: number, { integer = false, steps = DECADE_STEPS }: Omit<NiceOptions, 'intervals'> = {}): number {
  if (!Number.isFinite(rough) || rough <= 0) return 1
  let exponent = Math.floor(Math.log10(rough))
  if (integer) exponent = Math.max(0, exponent)
  // Bounded: the next decade's first step is always >= rough.
  for (let e = exponent; e <= exponent + 2; e++) {
    const base = 10 ** e
    for (const mantissa of steps) {
      const step = clean(mantissa * base)
      if (integer && !Number.isInteger(step)) continue
      if (step >= rough * (1 - 1e-12)) return step
    }
  }
  return clean(10 ** (exponent + 3))
}

/** Ticks from `lo` to `hi`, `step` apart, each printed-exact at `tickDecimals(step)`. */
function ticksOf(lo: number, hi: number, step: number): number[] {
  const count = Math.round((hi - lo) / step)
  const places = tickDecimals(step)
  return Array.from({ length: count + 1 }, (_, i) => Number((lo + i * step).toFixed(places)))
}

/**
 * A nice scale covering `[min, max]`: the step is the smallest ladder step
 * that fits the span in `intervals`, and the domain is widened OUTWARD to the
 * nearest multiples of it — never inward, so no value is ever off the axis.
 */
export function niceScale(min: number, max: number, { intervals, integer = false, steps = DECADE_STEPS }: NiceOptions): NiceScale {
  const low = Math.min(min, max)
  const high = Math.max(min, max)
  const span = high - low
  const step = niceStep(span > 0 ? span / intervals : Math.max(Math.abs(high), 1) / intervals, { integer, steps })
  const lo = clean(Math.floor(low / step + 1e-9) * step)
  let hi = clean(Math.ceil(high / step - 1e-9) * step)
  if (hi <= lo) hi = clean(lo + step)
  return { domain: [lo, hi], ticks: ticksOf(lo, hi, step), step }
}

/**
 * A value axis for counts (or any non-negative quantity) that STARTS AT ZERO:
 * a bar encodes value as length, so the baseline is not negotiable. An
 * all-zero or empty set gets `[0, 1]`.
 */
export function zeroBasedScale(max: number, { intervals = 5, integer = false }: { intervals?: number; integer?: boolean } = {}): NiceScale {
  if (!Number.isFinite(max) || max <= 0) return { domain: [0, 1], ticks: [0, 1], step: 1 }
  return niceScale(0, max, { intervals, integer })
}

/** Clock steps for a duration axis that reaches a minute, ms: 15 s up to a day. */
const CLOCK_STEPS_MS = [
  15e3, 30e3, 60e3, 120e3, 300e3, 600e3, 900e3, 1_800e3, 3_600e3, 7_200e3, 10_800e3, 21_600e3, 43_200e3, 86_400e3,
] as const

/**
 * A zero-based DURATION axis in ms. Below a minute it is `zeroBasedScale`
 * (0.5 s, 2.5 s, 25 s read fine); from a minute up the step is a clock step
 * (30 s, 1 m, 2 m, 5 m, 15 m, 1 h ...). The decimal ladder put ticks at
 * 1m 40s / 3m 20s and 4m 10s / 8m 20s (100 s and 250 s steps).
 */
export function durationScale(maxMs: number, { intervals = 4 }: { intervals?: number } = {}): NiceScale {
  if (!Number.isFinite(maxMs) || maxMs < 60_000) return zeroBasedScale(maxMs, { intervals })
  const rough = maxMs / intervals
  const day = CLOCK_STEPS_MS[CLOCK_STEPS_MS.length - 1]
  const step = CLOCK_STEPS_MS.find((s) => s >= rough) ?? Math.ceil(rough / day) * day
  const hi = Math.ceil(maxMs / step - 1e-9) * step
  return { domain: [0, hi], ticks: ticksOf(0, hi, step), step }
}

/**
 * A change axis, SYMMETRIC about zero so "down 8" and "up 8" are the same
 * length — and nice on both sides, so the ticks read -10, -5, 0, 5, 10 rather
 * than -9, -4.5, 0, 4.5, 9.
 */
export function symmetricScale(extreme: number, { intervalsPerSide = 3, integer = false }: { intervalsPerSide?: number; integer?: boolean } = {}): NiceScale {
  const side = zeroBasedScale(Math.abs(extreme), { intervals: intervalsPerSide, integer })
  const hi = side.domain[1]
  const negatives = side.ticks.filter((tick) => tick > 0).map((tick) => -tick).reverse()
  return { domain: [-hi, hi], ticks: [...negatives, ...side.ticks], step: side.step }
}

/**
 * A secondary axis from zero whose interval count is FIXED — the primary
 * axis's — so its ticks sit on the grid lines the primary axis draws. A
 * right-hand scale whose ticks float between the grid lines reads as a second,
 * misaligned grid.
 */
export function alignedZeroBasedScale(max: number, intervals: number, { integer = false }: { integer?: boolean } = {}): NiceScale {
  const n = Math.max(1, Math.round(intervals))
  const top = Number.isFinite(max) && max > 0 ? max : 1
  const step = niceStep(top / n, { integer, steps: FINE_STEPS })
  return { domain: [0, clean(step * n)], ticks: ticksOf(0, clean(step * n), step), step }
}
