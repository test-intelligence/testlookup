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
 *
 * W29a UIX-02: the set itself is the app's ONE failure-category / kind map and
 * lives in `../failureTaxonomy` (with `taxonOf`, the kind triad and the slice
 * styles), at the kit's root so pages can import it without reaching this
 * folder (d3Confinement.ratchet). It is re-exported here for the failure-group
 * views, unchanged.
 */
import type { PatternSpec } from '../patterns'
import { FAILURE_CATEGORY_PATTERNS, type FailureCategoryKey } from '../failureTaxonomy'

export {
  FAILURE_CATEGORY_PATTERNS,
  INFRASTRUCTURE_DETAIL_DECALS,
  KIND_CATEGORY,
  categorySliceStyles,
  failureCategoryColor,
  failureKindColor,
  kindTaxon,
  taxonOf,
} from '../failureTaxonomy'
export type { FailureCategoryKey } from '../failureTaxonomy'

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
