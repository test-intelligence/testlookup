/**
 * The ONE failure-category / failure-kind map of the app (W29a UIX-02).
 *
 * One FIXED colour and shape per category, whatever the rank or the other
 * categories on screen, so "product bug" looks the same in every view and on
 * every day (VIZ-504's set, unchanged): `flaky` takes the flaky marker's colour
 * and `unknown` the muted neutral (an unclassified failure is not a category to
 * draw attention to); the rest take series colours.
 *
 * - `taxonOf` folds every vocabulary onto the seven keys: the server enum
 *   (`PRODUCT_BUG`, `TEST_DATA`, …), the Failures card's display buckets
 *   (`Infra / runner`, `Test code / data`), the legacy strings (`ASSERTION`,
 *   `TIMEOUT`, `LOCATOR`, `NETWORK`).
 * - The kind triad reads it through `KIND_CATEGORY` (test code takes the
 *   test-data colour), so a kind badge, the Failures category card and the
 *   category donut agree.
 * - A category is a coloured MARK (`FailureCategoryMark`, a slice, a swatch, a
 *   tint, a border) beside NEUTRAL text: series colours are >= 3:1 marks, not
 *   4.5:1 text.
 *
 * Lives at the chart kit's root, NOT in `failureGroups/`: pages import it, and
 * `failureGroups/d3Confinement.ratchet.test.ts` keeps every page's static
 * closure out of that folder (d3 is behind it). `failureGroups/categoryPatterns`
 * re-exports the set for the failure-group views. Tokens only (rule 6a).
 */
import type { FailureKind } from '@/utils/failureKind'
import type { PatternDecal } from './patterns'
import { CHART_VARS } from './tokens'

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

const UNKNOWN_WORDS = new Set(['', 'unknown', 'unclassified', 'uncategorised', 'uncategorized', 'none'])
const TAXON_RULES: readonly (readonly [RegExp, FailureCategoryKey])[] = [
  [/flak|intermittent/, 'flaky'],
  [/automation|script/, 'automation_defect'],
  [/test.?data|locator|fixture|test.?code|\bdata\b/, 'test_data'],
  [/infra|timeout|timed.?out|network|http|\b5\d\d\b|runner|\bci\b|env/, 'infrastructure'],
  [/product|assert/, 'product_bug'],
]

const own = (map: object, key: string): boolean => Object.prototype.hasOwnProperty.call(map, key)

/**
 * The category key of any failure-category string. Trimmed, case-insensitive,
 * `_` / `-` / space equivalent; the first rule that matches wins; anything
 * else → `other` (drawn as other; its own words are still printed).
 */
export function taxonOf(raw: string | null | undefined): FailureCategoryKey {
  const text = (raw ?? '').trim().toLowerCase()
  const key = text.replace(/[\s-]+/g, '_')
  if (UNKNOWN_WORDS.has(key)) return 'unknown'
  if (own(FAILURE_CATEGORY_PATTERNS, key)) return key as FailureCategoryKey
  // `_` / `-` / space are one separator; the rules read it as a space so `\b` works.
  const words = text.replace(/[_\s-]+/g, ' ')
  for (const [pattern, taxon] of TAXON_RULES) {
    if (pattern.test(words)) return taxon
  }
  return 'other'
}

/** The failure-kind triad on the category map (test code takes the test-data colour). */
export const KIND_CATEGORY: Readonly<Record<FailureKind, FailureCategoryKey>> = {
  product: 'product_bug',
  test_code: 'test_data',
  infrastructure: 'infrastructure',
  unknown: 'unknown',
}

/** The category key of a failure kind; an unknown kind → `unknown`. */
export function kindTaxon(kind: string | null | undefined): FailureCategoryKey {
  const k = (kind ?? '').trim().toLowerCase()
  return own(KIND_CATEGORY, k) ? KIND_CATEGORY[k as FailureKind] : 'unknown'
}

/** The colour token of any failure-category string (one of seven constants). */
export function failureCategoryColor(raw: string | null | undefined): string {
  return FAILURE_CATEGORY_PATTERNS[taxonOf(raw)].color
}

/** The colour token of a failure kind; an unknown kind → the unknown colour. */
export function failureKindColor(kind: string | null | undefined): string {
  return FAILURE_CATEGORY_PATTERNS[kindTaxon(kind)].color
}

/** The Failures card's three infrastructure rows: one colour, three shapes. */
export const INFRASTRUCTURE_DETAIL_DECALS: Readonly<Record<'runner' | 'timeout' | 'network', PatternDecal>> = {
  runner: 'vertical',
  timeout: 'dashes',
  network: 'dots',
}

const DECAL_ORDER: readonly PatternDecal[] = ['diagonal', 'vertical', 'dashes', 'crosshatch', 'dots', 'grid', 'solid']

/**
 * One chart's slice styles. A slice's COLOUR is always its taxon's (never its
 * rank's); its decal is the taxon's for the first slice of that taxon, and a
 * later slice of a taxon already drawn takes the first decal of DECAL_ORDER
 * not yet used in this chart (deterministic in slice order), so two slices of
 * one colour are still told apart.
 */
export function categorySliceStyles(rawKeys: readonly string[]): Map<string, { color: string; decal: PatternDecal }> {
  const out = new Map<string, { color: string; decal: PatternDecal }>()
  const seenTaxa = new Set<FailureCategoryKey>()
  const usedDecals = new Set<PatternDecal>()
  for (const raw of rawKeys) {
    if (out.has(raw)) continue
    const taxon = taxonOf(raw)
    const set = FAILURE_CATEGORY_PATTERNS[taxon]
    let decal: PatternDecal = set.decal
    if (seenTaxa.has(taxon)) decal = DECAL_ORDER.find((d) => !usedDecals.has(d)) ?? set.decal
    seenTaxa.add(taxon)
    usedDecals.add(decal)
    out.set(raw, { color: set.color, decal })
  }
  return out
}
