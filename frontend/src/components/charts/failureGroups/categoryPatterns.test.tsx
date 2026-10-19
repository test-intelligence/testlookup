/**
 * The failure-category pattern set (VIZ-504): seven categories, seven shapes,
 * so a category is never told by colour alone.
 */
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderPatterns } from '../patterns'
import { CHART_VARS } from '../tokens'
import {
  categoryPatternId,
  categoryPatternSpecs,
  categorySliceStyles,
  FAILURE_CATEGORY_PATTERNS,
  failureCategoryColor,
  failureKindColor,
  INFRASTRUCTURE_DETAIL_DECALS,
  KIND_CATEGORY,
  taxonOf,
} from './categoryPatterns'
import { FAILURE_CATEGORY_KEYS } from './failureGroups.model'

describe('failure-category patterns', () => {
  it('cover exactly the categories the model draws', () => {
    expect(Object.keys(FAILURE_CATEGORY_PATTERNS).sort()).toEqual([...FAILURE_CATEGORY_KEYS].sort())
  })

  it('every category has its own shape and its own colour', () => {
    const decals = Object.values(FAILURE_CATEGORY_PATTERNS).map((p) => p.decal)
    expect(new Set(decals).size).toBe(decals.length)
    const colors = Object.values(FAILURE_CATEGORY_PATTERNS).map((p) => p.color)
    expect(new Set(colors).size).toBe(colors.length)
    for (const color of colors) expect(color).toMatch(/^var\(--/)
    expect(FAILURE_CATEGORY_PATTERNS.unknown.color).toBe(CHART_VARS.neutral)
  })

  it('renders one <pattern> per category with the prefixed id; vertical and grid are upright', () => {
    const specs = categoryPatternSpecs('cpX', ['product_bug', 'infrastructure', 'unknown', 'other'])
    expect(specs.map((s) => s.id)).toEqual([
      categoryPatternId('cpX', 'product_bug'),
      'cpX-category-infrastructure',
      'cpX-category-unknown',
      'cpX-category-other',
    ])
    const { container } = render(
      <svg>
        <defs>{renderPatterns(specs)}</defs>
      </svg>,
    )
    const patterns = Array.from(container.querySelectorAll('pattern'))
    expect(patterns.map((p) => p.getAttribute('data-chart-pattern'))).toEqual(['diagonal', 'vertical', 'grid', 'solid'])
    expect(patterns.map((p) => p.getAttribute('patternTransform'))).toEqual(['rotate(45)', null, null, null])
    // diagonal / vertical: fill + one stripe; grid: fill + two bars; solid: just the fill.
    expect(patterns.map((p) => p.querySelectorAll('rect').length)).toEqual([2, 2, 3, 1])
  })
})

// W29a UIX-02: the set is the app's ONE failure-category / kind map
// (components/charts/failureTaxonomy, re-exported here).
describe('taxonOf: every vocabulary onto the seven keys', () => {
  it.each([
    // the unknown words
    [null, 'unknown'], [undefined, 'unknown'], ['', 'unknown'], ['  ', 'unknown'], ['UNKNOWN', 'unknown'],
    ['unclassified', 'unknown'], ['Uncategorised', 'unknown'], ['uncategorized', 'unknown'], ['none', 'unknown'],
    // an exact key, any spelling
    ['PRODUCT_BUG', 'product_bug'], ['product bug', 'product_bug'], ['INFRASTRUCTURE', 'infrastructure'],
    ['TEST_DATA', 'test_data'], ['test-data', 'test_data'], ['AUTOMATION_DEFECT', 'automation_defect'],
    ['FLAKY', 'flaky'], ['other', 'other'],
    // the rules, first match wins
    ['Flakiness', 'flaky'], ['intermittent', 'flaky'],
    ['automation', 'automation_defect'], ['script error', 'automation_defect'],
    ['LOCATOR', 'test_data'], ['fixture missing', 'test_data'], ['Test code / data', 'test_data'], ['data', 'test_data'],
    ['TIMEOUT', 'infrastructure'], ['timed out', 'infrastructure'], ['NETWORK', 'infrastructure'], ['HTTP', 'infrastructure'],
    ['502 from the proxy', 'infrastructure'], ['Infra / runner', 'infrastructure'], ['CI', 'infrastructure'], ['env', 'infrastructure'],
    ['ASSERTION', 'product_bug'], ['product regression', 'product_bug'],
    // anything else
    ['cosmic rays', 'other'],
  ])('taxonOf(%j) is %s', (raw, key) => {
    expect(taxonOf(raw)).toBe(key)
  })
})

describe('the kind triad and the colour helpers', () => {
  it('KIND_CATEGORY puts test code on the test-data colour', () => {
    expect(KIND_CATEGORY).toEqual({ product: 'product_bug', test_code: 'test_data', infrastructure: 'infrastructure', unknown: 'unknown' })
  })

  it('failureCategoryColor / failureKindColor return the map colour, unknown → muted', () => {
    expect(failureCategoryColor('ASSERTION')).toBe(FAILURE_CATEGORY_PATTERNS.product_bug.color)
    expect(failureCategoryColor(null)).toBe(CHART_VARS.neutral)
    expect(failureKindColor('product')).toBe(CHART_VARS.series[0])
    expect(failureKindColor('test_code')).toBe(CHART_VARS.series[2])
    expect(failureKindColor('infrastructure')).toBe(CHART_VARS.series[1])
    expect(failureKindColor('unknown')).toBe(CHART_VARS.neutral)
    expect(failureKindColor('nonsense')).toBe(CHART_VARS.neutral)
    expect(failureKindColor(undefined)).toBe(CHART_VARS.neutral)
  })

  it('the three infrastructure detail rows share a colour and differ by shape', () => {
    expect(new Set(Object.values(INFRASTRUCTURE_DETAIL_DECALS)).size).toBe(3)
  })
})

describe('categorySliceStyles: colour by category, never by rank', () => {
  it('the colour of a key does not depend on its position', () => {
    const forward = categorySliceStyles(['ASSERTION', 'TIMEOUT', 'LOCATOR'])
    const backward = categorySliceStyles(['LOCATOR', 'TIMEOUT', 'ASSERTION'])
    for (const key of ['ASSERTION', 'TIMEOUT', 'LOCATOR']) {
      expect(forward.get(key)?.color, key).toBe(backward.get(key)?.color)
    }
    expect(forward.get('ASSERTION')).toEqual({ color: CHART_VARS.series[0], decal: 'diagonal' })
    expect(forward.get('TIMEOUT')).toEqual({ color: CHART_VARS.series[1], decal: 'vertical' })
    expect(forward.get('LOCATOR')).toEqual({ color: CHART_VARS.series[2], decal: 'dashes' })
  })

  it('a second key of one category keeps the colour and takes the first unused decal', () => {
    const styles = categorySliceStyles(['TEST_CODE', 'DATA', 'ASSERTION'])
    expect(styles.get('TEST_CODE')).toEqual({ color: CHART_VARS.series[2], decal: 'dashes' })
    // 'dashes' is taken, so the second test-data slice draws the first decal not yet used: diagonal.
    expect(styles.get('DATA')).toEqual({ color: CHART_VARS.series[2], decal: 'diagonal' })
    expect(styles.get('DATA')?.decal).not.toBe(styles.get('TEST_CODE')?.decal)
  })
})
