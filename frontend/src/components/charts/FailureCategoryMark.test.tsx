/**
 * W29a UIX-02: a failure category as a MARK — one prefixed pattern in the
 * taxon's colour and decal, decorative, with `kind` winning over `category`.
 */
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import FailureCategoryMark from './FailureCategoryMark'
import { FAILURE_CATEGORY_PATTERNS } from './failureTaxonomy'
import { CHART_VARS } from './tokens'

function mark(ui: ReactElement) {
  const { container } = render(ui)
  const svg = container.querySelector('svg') as SVGSVGElement
  const patterns = svg.querySelectorAll('pattern')
  return { svg, patterns, pattern: patterns[0] }
}

describe('FailureCategoryMark', () => {
  it('draws ONE pattern in the taxon colour and decal, filling a rounded 10 px square, aria-hidden', () => {
    const { svg, patterns, pattern } = mark(<FailureCategoryMark category="ASSERTION" />)
    expect(svg).toHaveAttribute('aria-hidden', 'true')
    expect(svg).toHaveAttribute('width', '10')
    expect(svg).toHaveAttribute('data-failure-category', 'product_bug')
    expect(patterns).toHaveLength(1)
    expect(pattern).toHaveAttribute('data-chart-pattern', FAILURE_CATEGORY_PATTERNS.product_bug.decal)
    expect(pattern.querySelector('rect')).toHaveAttribute('fill', CHART_VARS.series[0])
    const square = svg.querySelector(':scope > rect') as SVGRectElement
    expect(square).toHaveAttribute('rx', '2')
    expect(square.getAttribute('fill')).toBe(`url(#${pattern.getAttribute('id')})`)
  })

  it('kind wins over category', () => {
    const { svg, pattern } = mark(<FailureCategoryMark category="ASSERTION" kind="infrastructure" />)
    expect(svg).toHaveAttribute('data-failure-category', 'infrastructure')
    expect(pattern.querySelector('rect')).toHaveAttribute('fill', CHART_VARS.series[1])
    expect(pattern).toHaveAttribute('data-chart-pattern', 'vertical')
  })

  it('takes a decal of its own (one colour, three infrastructure shapes) and a size', () => {
    const { svg, pattern } = mark(<FailureCategoryMark category="TIMEOUT" decal="dashes" size={8} />)
    expect(svg).toHaveAttribute('width', '8')
    expect(svg).toHaveAttribute('data-decal', 'dashes')
    expect(pattern).toHaveAttribute('data-chart-pattern', 'dashes')
    expect(pattern.querySelector('rect')).toHaveAttribute('fill', CHART_VARS.series[1])
  })

  it('an unclassified failure is the muted neutral; two marks never share a pattern id', () => {
    const { container } = render(
      <>
        <FailureCategoryMark category={null} />
        <FailureCategoryMark kind="unknown" />
      </>,
    )
    const patterns = Array.from(container.querySelectorAll('pattern'))
    expect(patterns.map((p) => p.querySelector('rect')?.getAttribute('fill'))).toEqual([CHART_VARS.neutral, CHART_VARS.neutral])
    expect(new Set(patterns.map((p) => p.id)).size).toBe(2)
  })
})
