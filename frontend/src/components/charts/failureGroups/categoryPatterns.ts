/**
 * The failure-category pattern set (VIZ-504): what a failure group's circle
 * and its table swatch are filled with, so a category is never told by colour
 * alone (it is also in the readout, the table and the panel as words).
 *
 * One FIXED colour and shape per category, whatever the rank or the other
 * categories on screen, so "product bug" looks the same in every view and on
 * every day. `flaky` takes the flaky marker's colour and `unknown` the muted
 * neutral (an unclassified failure is not a category to draw attention to); the
 * rest take series colours. Drawn through `patterns.tsx`'s `renderPatterns`,
 * which knows every shape (`PatternDecal`).
 */
import type { PatternDecal, PatternSpec } from '../patterns'
import { CHART_VARS } from '../tokens'

export const FAILURE_CATEGORY_PATTERNS = {
  product_bug: { color: CHART_VARS.series[0], decal: 'diagonal' },
  infrastructure: { color: CHART_VARS.series[1], decal: 'vertical' },
  test_data: { color: CHART_VARS.series[2], decal: 'dashes' },
  automation_defect: { color: CHART_VARS.series[3], decal: 'crosshatch' },
  flaky: { color: CHART_VARS.flaky, decal: 'dots' },
  unknown: { color: CHART_VARS.neutral, decal: 'grid' },
  other: { color: CHART_VARS.series[5], decal: 'solid' },
} as const satisfies Readonly<Record<string, { color: string; decal: PatternDecal }>>

export type FailureCategoryKey = keyof typeof FAILURE_CATEGORY_PATTERNS

/** The pattern id of a failure category under `prefix` (a `useChartPatternPrefix()`). */
export const categoryPatternId = (prefix: string, category: FailureCategoryKey) => `${prefix}-category-${category}`

/** One pattern per failure category, under `prefix`, for `renderPatterns`. */
export function categoryPatternSpecs(prefix: string, categories: readonly FailureCategoryKey[]): PatternSpec[] {
  return categories.map((category) => ({
    id: categoryPatternId(prefix, category),
    color: FAILURE_CATEGORY_PATTERNS[category].color,
    decal: FAILURE_CATEGORY_PATTERNS[category].decal,
  }))
}
