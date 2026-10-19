/**
 * W29a UIX-02: the failure-kind triad reads the ONE failure-category map, so a
 * kind badge and the category views agree; the kind lookups are unchanged.
 */
import { describe, expect, it } from 'vitest'
import { failureKindColor } from '@/components/charts/failureTaxonomy'
import { CHART_VARS } from '@/components/charts/tokens'
import { FAILURE_KIND_DEFS, failureKindOf, kindDef } from './failureKind'

describe('FAILURE_KIND_DEFS', () => {
  it('each colour is failureKindColor(id), and none is an old --kind-* alias', () => {
    for (const def of FAILURE_KIND_DEFS) {
      expect(def.color, def.id).toBe(failureKindColor(def.id))
      expect(def.color, def.id).not.toContain('--kind-')
    }
    expect(FAILURE_KIND_DEFS.map((d) => [d.id, d.color])).toEqual([
      ['product', CHART_VARS.series[0]],
      ['test_code', CHART_VARS.series[2]],
      ['infrastructure', CHART_VARS.series[1]],
      ['unknown', CHART_VARS.neutral],
    ])
  })

  it('keeps its order, labels and fallback', () => {
    expect(FAILURE_KIND_DEFS.map((d) => d.label)).toEqual(['Product', 'Test code', 'Infrastructure', 'Unknown'])
    expect(kindDef('nonsense').id).toBe('unknown')
    expect(kindDef(null).id).toBe('unknown')
  })
})

describe('failureKindOf (unchanged)', () => {
  it.each([
    ['PRODUCT_BUG', 'product'], ['INFRASTRUCTURE', 'infrastructure'], ['TEST_DATA', 'test_code'],
    ['AUTOMATION_DEFECT', 'test_code'], ['FLAKY', 'test_code'], ['UNKNOWN', 'unknown'],
    [' product_bug ', 'product'], [null, 'unknown'], ['SOMETHING_ELSE', 'unknown'],
  ])('%j → %s', (category, kind) => {
    expect(failureKindOf(category)).toBe(kind)
  })
})
