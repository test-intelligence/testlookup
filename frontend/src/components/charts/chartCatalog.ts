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
 * Pure: no React, no DOM, no colours. The rounding rule itself lives in the
 * leaf `percents.ts` (re-exported here), so a kit module that only rounds does
 * not bring the registry with it.
 */

export { largestRemainderPercents } from './percents'

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
