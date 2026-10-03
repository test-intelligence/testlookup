/**
 * The failure-category pattern set (VIZ-504): seven categories, seven shapes,
 * so a category is never told by colour alone.
 */
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderPatterns } from '../patterns'
import { CHART_VARS } from '../tokens'
import { categoryPatternId, categoryPatternSpecs, FAILURE_CATEGORY_PATTERNS } from './categoryPatterns'
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
