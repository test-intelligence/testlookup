/**
 * W23d AGT-15 — `MetricSpark` (contract §4.4 G-D): the kit's Sparkline with
 * a unit-formatted caption; fewer than 2 measured points → the caption only;
 * the accessible name starts with "{label} for {service}"; the tone is the
 * kit's own (no new colour).
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import MetricSpark from './MetricSpark'
import { SPARKLINE_TONE_COLOR } from './Sparkline.model'

const base = { label: 'p95 latency', service: 'checkout', unit: 'seconds' }

describe('MetricSpark', () => {
  it('draws the series with an accessible name that starts with the label and formats values in the unit', () => {
    render(<MetricSpark {...base} values={[0.2, 0.21, null, 0.62, 0.6]} windowMean={0.61} baselineMean={0.205} />)
    const img = screen.getByRole('img')
    expect(img.getAttribute('aria-label')).toBe(
      'p95 latency for checkout: 4 points, latest 600 ms, min 200 ms, max 620 ms, 1 not measured',
    )
    expect(img).toHaveStyle({ height: '24px' })
    expect(img.className).toContain('w-[120px]')
    expect(screen.getByText('610 ms vs 205 ms before')).toBeInTheDocument()
  })

  it('fewer than 2 measured points: the caption only (no line is invented)', () => {
    render(<MetricSpark {...base} values={[null, 0.62]} windowMean={0.62} baselineMean={null} />)
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText('620 ms in the failure window')).toBeInTheDocument()
  })

  it.each([
    ['bytes', 300 * 1024 * 1024, 200 * 1024 * 1024, '300.0 MB vs 200.0 MB before'],
    ['ratio', 0.42, 0.21, '42.0% vs 21.0% before'],
    ['per_second', 3.5, 0.25, '3.5/s vs 0.25/s before'],
    ['count', 1200, 900, '1,200 vs 900 before'],
  ])('formats the %s caption', (unit, windowMean, baselineMean, caption) => {
    render(<MetricSpark {...base} unit={unit} values={[1, 2, 3]} windowMean={windowMean} baselineMean={baselineMean} />)
    expect(screen.getByText(caption)).toBeInTheDocument()
  })

  it('the warn tone is the kit\'s colour; neutral by default', () => {
    const { container, rerender } = render(<MetricSpark {...base} values={[1, 2]} windowMean={2} baselineMean={1} />)
    const stroke = () => container.querySelector('[data-part="line"]')?.getAttribute('stroke')
    expect(stroke()).toBe(SPARKLINE_TONE_COLOR.neutral)
    rerender(<MetricSpark {...base} values={[1, 2]} windowMean={2} baselineMean={1} tone="warn" />)
    expect(stroke()).toBe(SPARKLINE_TONE_COLOR.warn)
  })
})
