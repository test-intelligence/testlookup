/**
 * A failure category (or kind) as a MARK (W29a UIX-02): a small 2 px-rounded
 * square filled with the category's fixed colour and decal from the one map
 * (`failureTaxonomy.ts`), drawn BESIDE the category's words — which stay
 * neutral text (`--color-text` / `--color-text-secondary`): a series colour is
 * a >= 3:1 mark, not 4.5:1 text.
 *
 * Decorative (`aria-hidden`): the words beside it carry the meaning. Its
 * pattern id is per instance (`useChartPatternPrefix`), so two marks never
 * share an id. At the kit's root (not in `failureGroups/`), so a page can
 * import it without reaching d3's folder.
 */
import { patternFill, renderPatterns, useChartPatternPrefix, type PatternDecal } from './patterns'
import { FAILURE_CATEGORY_PATTERNS, failureCategoryColor, failureKindColor, kindTaxon, taxonOf } from './failureTaxonomy'

export interface FailureCategoryMarkProps {
  /** Any failure-category string (server enum, display bucket, legacy word). */
  category?: string | null
  /** A failure kind (product / test_code / infrastructure / unknown); wins over `category`. */
  kind?: string | null
  /** A decal other than the taxon's own (the Failures card's three infrastructure rows). */
  decal?: PatternDecal
  /** Edge length in px (default 10). */
  size?: number
  className?: string
}

export default function FailureCategoryMark({ category, kind, decal, size = 10, className }: FailureCategoryMarkProps) {
  const prefix = useChartPatternPrefix()
  const taxon = kind ? kindTaxon(kind) : taxonOf(category)
  const color = kind ? failureKindColor(kind) : failureCategoryColor(category)
  const shape = decal ?? FAILURE_CATEGORY_PATTERNS[taxon].decal
  const id = `${prefix}-category-mark`
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className={['inline-block shrink-0 align-middle', className].filter(Boolean).join(' ')}
      data-failure-category={taxon}
      data-decal={shape}
    >
      <defs>{renderPatterns([{ id, color, decal: shape }])}</defs>
      <rect width={size} height={size} rx={2} ry={2} fill={patternFill(id)} />
    </svg>
  )
}
