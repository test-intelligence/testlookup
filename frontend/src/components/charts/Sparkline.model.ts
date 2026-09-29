/**
 * The sparkline's model (VIZ-104, kit gap K3) — the geometry and the words of
 * a KPI cell's small trend line, as pure data, so every rule is testable
 * without a DOM.
 *
 * A sparkline draws a REAL series or nothing. The 20 KPI glyphs this replaces
 * were mostly decoration — literal point strings, constant dots, a count
 * drawn as bars — and the rule the story sets is that a sparkline with no
 * real series is removed rather than drawn from invented points. So:
 *
 *   - the series is taken in the order given (OLDEST first, the latest value
 *     on the right). It is never sorted: the caller's order is the time order,
 *     and sorting by value would draw a different story;
 *   - `null` (or any non-finite number) is NOT MEASURED: the line breaks there
 *     and resumes at the next measured point. It is never joined across and
 *     never read as 0 — a day with no runs is a gap, not a zero pass rate;
 *   - fewer than 2 measured points is not a trend: the model is `null` and
 *     the component renders nothing, so the caller keeps its own caption;
 *   - a flat series (every value equal, and no domain to place it in) is a
 *     line across the MIDDLE, never a division by a zero span;
 *   - the accessible name carries the numbers the line shows — how many
 *     points, the latest, the lowest and the highest — so a screen-reader
 *     user gets the trend, not "image".
 *
 * Coordinates: x is a percentage of the width (0 at the oldest slot, 100 at
 * the newest), because the svg stretches to its cell with
 * `preserveAspectRatio="none"`; y is in pixels of the fixed height, so the
 * vertical inset is exact. The stroke is `non-scaling-stroke`, and the end dot
 * is a round element positioned from the same numbers, so neither is
 * stretched into an ellipse (Overview's `<circle>` was).
 */
import { formatNumber } from '@/utils/formatters'
import { CHART_VARS } from './tokens'

/** The default drawn height, in px (Overview's `h-7`). */
export const SPARKLINE_HEIGHT = 28
/**
 * The vertical inset, in px, above the highest and below the lowest point:
 * half the end dot, so the dot is never cut by the cell edge.
 */
export const SPARKLINE_INSET = 3

export type SparklineTone = 'good' | 'warn' | 'bad' | 'neutral' | 'accent'

/** Tone to colour, from the chart tokens only (`check:theme` forbids a literal here). */
export const SPARKLINE_TONE_COLOR: Record<SparklineTone, string> = {
  good: CHART_VARS.status.passed,
  warn: CHART_VARS.status.broken,
  bad: CHART_VARS.status.failed,
  neutral: CHART_VARS.neutral,
  accent: CHART_VARS.accent,
}

export type SparklineFormat = (value: number) => string

/** One decimal, grouped: 92.4, 1,284. */
export const defaultSparklineFormat: SparklineFormat = (value) => formatNumber(value, { maximumFractionDigits: 1 })

export interface SparklinePoint {
  /** The point's position in the input series (nulls included). */
  index: number
  /** The TRUE value, never clamped. */
  value: number
  /** 0-100, a percentage of the width. */
  x: number
  /** px from the top, within [inset, height - inset] (the drawn value is clamped to the domain). */
  y: number
}

export interface SparklineModel {
  /**
   * Runs of consecutive measured points. A `null` ends one run and the next
   * measured point starts another, so the line is never drawn across a gap.
   */
  runs: SparklinePoint[][]
  /** Measured points, in series order. */
  points: SparklinePoint[]
  /** The newest measured point: where the end dot sits and what "latest" says. */
  last: SparklinePoint
  /** How many values were not measured (`null` or non-finite). */
  missing: number
  min: number
  max: number
  height: number
}

export interface SparklineOptions {
  /** A fixed value range (a pass rate is `[0, 100]`); by default the series' own min and max. */
  domain?: readonly [number, number]
  height?: number
}

const measured = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v)

/** `null` for fewer than 2 measured points: nothing to draw, the caller keeps its caption. */
export function buildSparklineModel(
  series: readonly (number | null)[],
  { domain, height = SPARKLINE_HEIGHT }: SparklineOptions = {},
): SparklineModel | null {
  const values = series.filter(measured)
  if (values.length < 2) return null

  const min = Math.min(...values)
  const max = Math.max(...values)
  const [lo, hi] = domain && measured(domain[0]) && measured(domain[1]) ? domain : [min, max]
  const span = hi - lo
  const inset = Math.min(SPARKLINE_INSET, height / 2)
  const drawable = height - 2 * inset
  // A zero span (a flat series, or a degenerate domain) has no "higher" to
  // map to: the line goes through the middle rather than through NaN.
  const yOf = (v: number) => {
    if (!(span > 0)) return height / 2
    const t = (Math.min(hi, Math.max(lo, v)) - lo) / span
    return inset + (1 - t) * drawable
  }
  // Slots, not points, share the width: a gap keeps its place on the axis, so
  // the points either side of it are as far apart as the time between them.
  const last = series.length - 1
  const xOf = (i: number) => (i / last) * 100

  const runs: SparklinePoint[][] = []
  const points: SparklinePoint[] = []
  let run: SparklinePoint[] | null = null
  series.forEach((v, index) => {
    if (!measured(v)) {
      run = null
      return
    }
    const point = { index, value: v, x: xOf(index), y: yOf(v) }
    points.push(point)
    if (!run) {
      run = []
      runs.push(run)
    }
    run.push(point)
  })

  return {
    runs,
    points,
    last: points[points.length - 1],
    missing: series.length - values.length,
    min,
    max,
    height,
  }
}

const num = (n: number) => Number(n.toFixed(2))

/**
 * One svg path for all the runs: a `M … L …` subpath per run. A run of ONE
 * point (measured between two gaps) is a zero-length subpath, which a round
 * line cap draws as a dot the width of the stroke — the value stays visible
 * without being joined to its neighbours.
 */
export function sparklineLinePath(model: SparklineModel): string {
  return model.runs
    .map((run) => {
      const [first, ...rest] = run
      const head = `M ${num(first.x)} ${num(first.y)}`
      if (rest.length === 0) return `${head} L ${num(first.x)} ${num(first.y)}`
      return `${head} ${rest.map((p) => `L ${num(p.x)} ${num(p.y)}`).join(' ')}`
    })
    .join(' ')
}

/** The filled area under each run of two or more points, closed on the bottom edge. Gaps stay empty. */
export function sparklineAreaPath(model: SparklineModel): string {
  const bottom = num(model.height)
  return model.runs
    .filter((run) => run.length >= 2)
    .map((run) => {
      const line = run.map((p, i) => `${i === 0 ? 'M' : 'L'} ${num(p.x)} ${num(p.y)}`).join(' ')
      return `${line} L ${num(run[run.length - 1].x)} ${bottom} L ${num(run[0].x)} ${bottom} Z`
    })
    .join(' ')
}

/**
 * The accessible name: the caller's label, then the numbers the line shows.
 * "Pass rate: 14 points, latest 92.4%, min 81%, max 97.5%" — with ", 2 not
 * measured" when the series has gaps, so a gap is not read as a missing
 * sentence.
 */
export function sparklineLabel(label: string, model: SparklineModel, format: SparklineFormat = defaultSparklineFormat): string {
  const gaps = model.missing > 0 ? `, ${model.missing} not measured` : ''
  return (
    `${label}: ${model.points.length} points, latest ${format(model.last.value)}, ` +
    `min ${format(model.min)}, max ${format(model.max)}${gaps}`
  )
}
