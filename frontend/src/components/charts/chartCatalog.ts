/**
 * The chart CATALOGUE (VIZ-401 / VIZ-402) — two things every chart in Epic 4
 * shares, in one place so neither can drift.
 *
 *  1. `chooseChart` — the registry that picks the chart TYPE for a request.
 *     "Wrong tool for the job" is a rule, not a caller's choice: a part-to-
 *     whole breakdown of more than `MAX_PIE_CATEGORIES` categories is drawn as
 *     a ranked horizontal bar and is offered NO pie option at all, however the
 *     caller asked. A `preferred` type is a preference, and the registry
 *     overrules it; that is the whole point of having a registry.
 *
 *  2. `largestRemainderPercents` — the rounding rule for every part-to-whole
 *     chart here (the donut's slice percents, and a 100% stacked bar's
 *     segments). Rounding each share on its own gives 33.3 × 3 = 99.9 and
 *     16.7 × 6 = 100.2: a reader who adds the labels up finds the chart is
 *     lying. The largest-remainder (Hamilton) method hands out the tenths that
 *     are owed, so the drawn percents sum to exactly 100.0.
 *
 * Pure: no React, no DOM, no colours.
 */

/** Every chart type this catalogue can pick. */
export const CATALOG_CHART_TYPES = ['donut', 'ranked-bar', 'grouped-bar', 'stacked-bar', 'diverging-bar'] as const
export type CatalogChartType = (typeof CATALOG_CHART_TYPES)[number]

/**
 * The most categories a part-to-whole chart may have. Five is the status
 * vocabulary (passed, failed, broken, skipped, unknown) and it is also about
 * as many arcs as anyone can compare by angle; past it the honest chart is a
 * ranked bar.
 */
export const MAX_PIE_CATEGORIES = 5

export interface ChartRequest {
  /**
   * What the reader wants to see:
   *   part-to-whole  shares of one total (status split, failure categories)
   *   ranking        biggest first (top failing tests)
   *   composition    each category broken into series (results by suite)
   *   change         a delta that may be negative
   */
  intent: 'part-to-whole' | 'ranking' | 'composition' | 'change'
  /** Distinct categories in the data. */
  categories: number
  /** Series per category; 1 (or absent) means none. */
  seriesPerCategory?: number
  /** True when any value is below zero. */
  hasNegatives?: boolean
  /** What the CALLER would like. A preference only — the rules above win. */
  preferred?: CatalogChartType
}

export interface ChartChoice {
  type: CatalogChartType
  /** Types the reader may switch to. Never contains a type a rule ruled out. */
  alternatives: CatalogChartType[]
  /** Why this type, in words the chart can show. */
  reason: string
}

/**
 * The chart type for `request`. The caller's `preferred` is honoured only when
 * it is among the types the rules allow.
 */
export function chooseChart(request: ChartRequest): ChartChoice {
  const { intent, categories, seriesPerCategory = 1, hasNegatives = false, preferred } = request

  const decide = (): ChartChoice => {
    // A negative value has no share of a whole, and it needs a baseline to
    // hang below: it is always a diverging bar around zero.
    if (hasNegatives) {
      return {
        type: 'diverging-bar',
        alternatives: [],
        reason: 'Some values are below zero, so the bars diverge from a zero baseline.',
      }
    }
    if (intent === 'composition' || seriesPerCategory > 1) {
      return {
        type: 'stacked-bar',
        alternatives: ['grouped-bar', 'ranked-bar'],
        reason: 'Each category is broken into series, so the bars stack in the fixed status order.',
      }
    }
    if (intent === 'part-to-whole') {
      if (categories > MAX_PIE_CATEGORIES) {
        return {
          type: 'ranked-bar',
          alternatives: ['stacked-bar'],
          reason:
            `${categories} categories cannot be compared by angle: past ${MAX_PIE_CATEGORIES} ` +
            'a part-to-whole chart is ranked as horizontal bars instead.',
        }
      }
      return {
        type: 'donut',
        alternatives: ['ranked-bar'],
        reason: `${categories} categories of one total, in fixed order, with the total in the centre.`,
      }
    }
    return {
      type: 'ranked-bar',
      alternatives: categories <= MAX_PIE_CATEGORIES ? ['donut'] : [],
      reason: 'A ranking: horizontal bars, biggest first, from a zero baseline.',
    }
  }

  const choice = decide()
  if (preferred && preferred !== choice.type && choice.alternatives.includes(preferred)) {
    return {
      ...choice,
      type: preferred,
      alternatives: [choice.type, ...choice.alternatives.filter((type) => type !== preferred)],
    }
  }
  return choice
}

/** Whether a pie/donut may be offered for `request` at all. */
export function offersPie(request: ChartRequest): boolean {
  const choice = chooseChart(request)
  return choice.type === 'donut' || choice.alternatives.includes('donut')
}

/**
 * `values` as percentages of their total, rounded to `decimals` places by the
 * largest-remainder method, so the result sums to exactly 100 whenever
 * anything was measured at all. A total of zero (or nothing at all) gives
 * zeros — never NaN, and never a share of nothing.
 *
 * Ties are paid from the BACK: three equal thirds read 33.3, 33.3, 33.4.
 */
export function largestRemainderPercents(values: readonly number[], decimals = 1): number[] {
  const scale = 10 ** decimals
  const units = 100 * scale
  // A NEGATIVE value has no share of a whole. Passed through it makes the
  // total smaller than the positives that make it up, so the shares run past
  // 100% and the negative one draws a bar pointing out of the stack. It is 0%
  // here, exactly as the donut drops a negative bucket rather than drawing it.
  const shares = values.map((value) => (Number.isFinite(value) && value > 0 ? value : 0))
  const total = shares.reduce((sum, value) => sum + value, 0)
  if (!(total > 0)) return values.map(() => 0)

  const exact = shares.map((value) => (value / total) * units)
  const floors = exact.map((value) => Math.floor(value))
  let owed = units - floors.reduce((sum, value) => sum + value, 0)

  // Biggest remainder first; on a tie the LATER index is paid first.
  const order = exact
    .map((value, index) => ({ index, remainder: value - floors[index] }))
    .sort((a, b) => b.remainder - a.remainder || b.index - a.index)

  for (const { index } of order) {
    if (owed <= 0) break
    floors[index] += 1
    owed -= 1
  }
  return floors.map((value) => value / scale)
}
