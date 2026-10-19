/**
 * W29a UIX-02, the audit's guard: ONE colour per failure concept, on every
 * surface. For each concept the donut slice (`categorySliceStyles`), the
 * failure-group circle (`FAILURE_CATEGORY_PATTERNS[categoryKey(enum)]`) and the
 * kind badge (`failureKindColor(failureKindOf(enum))`, i.e. `FAILURE_KIND_DEFS`)
 * are the same token.
 */
import { describe, expect, it } from 'vitest'
import { FAILURE_KIND_DEFS, failureKindOf, kindDef } from '@/utils/failureKind'
import { categoryKey } from './failureGroups/failureGroups.model'
import { FAILURE_CATEGORY_PATTERNS, categorySliceStyles, failureCategoryColor, failureKindColor } from './failureTaxonomy'

const CONCEPTS = [
  // [server enum, the kind it rolls up to]
  ['PRODUCT_BUG', 'product'],
  ['INFRASTRUCTURE', 'infrastructure'],
  ['TEST_DATA', 'test_code'],
  ['UNKNOWN', 'unknown'],
] as const

describe('one colour per failure concept', () => {
  it.each(CONCEPTS)('%s: donut slice = failure group = kind badge = %s', (category, kind) => {
    const slice = categorySliceStyles([category]).get(category)?.color
    const group = FAILURE_CATEGORY_PATTERNS[categoryKey(category)].color
    const badge = failureKindColor(failureKindOf(category))
    expect(failureKindOf(category)).toBe(kind)
    expect(slice).toBe(group)
    expect(badge).toBe(group)
    expect(kindDef(kind).color).toBe(group)
    expect(failureCategoryColor(category)).toBe(group)
  })

  it('the three kinds that mean something are three different colours (product, infrastructure, test code)', () => {
    const colours = ['product', 'infrastructure', 'test_code'].map((k) => kindDef(k).color)
    expect(new Set(colours).size).toBe(3)
    expect(FAILURE_KIND_DEFS.map((d) => d.color)).toEqual(FAILURE_KIND_DEFS.map((d) => failureKindColor(d.id)))
  })
})
