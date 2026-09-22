import { render, screen } from '@testing-library/react'
import { BarChart3 } from 'lucide-react'
import { describe, expect, it } from 'vitest'
import MetricCard from './MetricCard'

describe('MetricCard', () => {
  it('renders title and metric value', () => {
    render(<MetricCard title="Pass Rate" metric={{ value: '98.2%' }} icon={<BarChart3 />} />)

    expect(screen.getByText('Pass Rate')).toBeInTheDocument()
    expect(screen.getByText('98.2%')).toBeInTheDocument()
  })

  it('shows placeholder when metric is missing', () => {
    render(<MetricCard title="Defects" icon={<BarChart3 />} />)

    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('renders loading skeleton and hides trend while loading', () => {
    render(
      <MetricCard
        title="Stability"
        metric={{ value: 100, trend: 12, trend_direction: 'up' }}
        icon={<BarChart3 />}
        loading
      />,
    )

    expect(screen.queryByText('100')).not.toBeInTheDocument()
    expect(screen.queryByText('12% vs prev period')).not.toBeInTheDocument()
  })

  it('renders absolute trend value regardless of sign', () => {
    render(
      <MetricCard
        title="Flaky"
        metric={{ value: 7, trend: -6, trend_direction: 'down' }}
        icon={<BarChart3 />}
      />,
    )

    expect(screen.getByText('6% vs prev period')).toBeInTheDocument()
  })

  // Narrow the trend row's parent without a non-null assertion (lint ratchet).
  function trendRowClass(text: string): string {
    const parent = screen.getByText(text).parentElement
    if (parent === null) throw new Error(`trend row for "${text}" has no parent element`)
    return parent.className
  }

  it('colors a rising trend green by default (up is good)', () => {
    render(
      <MetricCard
        title="Pass Rate"
        metric={{ value: '98%', trend: 3, trend_direction: 'up' }}
        icon={<BarChart3 />}
      />,
    )

    expect(trendRowClass('3% vs prev period')).toContain('--status-passed')
  })

  it('colors a falling trend green when positiveDirection is down (Failed/Flaky/Duration)', () => {
    // Audit 1.5: "Failed tests trending down" used to render red — the trend
    // color keyed on direction alone, not on whether the direction was good.
    render(
      <MetricCard
        title="Failed"
        metric={{ value: 4, trend: -20, trend_direction: 'down' }}
        icon={<BarChart3 />}
        positiveDirection="down"
      />,
    )

    expect(trendRowClass('20% vs prev period')).toContain('--status-passed')
  })

  it('colors a rising trend red when positiveDirection is down', () => {
    render(
      <MetricCard
        title="Failed"
        metric={{ value: 9, trend: 25, trend_direction: 'up' }}
        icon={<BarChart3 />}
        positiveDirection="down"
      />,
    )

    expect(trendRowClass('25% vs prev period')).toContain('--status-failed')
  })

  // Fix round B (a11y M1): direction and good/bad are WORDS, not only the
  // icon's shape and colour (WCAG 1.4.1 use of colour, 1.1.1 non-text).
  it('states the direction and whether it is better or worse in text; the icon is decoration', () => {
    const { container } = render(
      <MetricCard
        title="Failed"
        metric={{ value: 9, trend: 25, trend_direction: 'up' }}
        icon={<BarChart3 />}
        positiveDirection="down"
      />,
    )
    const row = container.querySelector('[data-metric-trend]') as HTMLElement
    expect(row.textContent).toBe('Up25% vs prev period(worse)')
    const svg = row.querySelector('svg') as SVGElement
    expect(svg).toHaveAttribute('aria-hidden', 'true')
    expect(svg.getAttribute('class')).toContain('shrink-0')
  })

  it('takes the caller change text, e.g. percentage points', () => {
    const { container } = render(
      <MetricCard
        title="Pass rate"
        metric={{ value: '91.2%', trend: 2.9, trend_direction: 'down', trend_text: '2.9 pp vs previous period' }}
        icon={<BarChart3 />}
      />,
    )
    expect(container.querySelector('[data-metric-trend]')?.textContent).toBe('Down2.9 pp vs previous period(worse)')
  })

  it('"none": a reason with no number, no direction word, no judgement', () => {
    const { container } = render(
      <MetricCard
        title="Broken"
        metric={{ value: 2, trend: null, trend_direction: 'none', trend_text: 'New: 0 in the previous period' }}
        icon={<BarChart3 />}
      />,
    )
    const row = container.querySelector('[data-metric-trend]') as HTMLElement
    expect(row.textContent).toBe('New: 0 in the previous period')
    expect(row.querySelector('svg')).toBeNull()
  })

  it('flat is "No change", never judged', () => {
    const { container } = render(
      <MetricCard title="Runs" metric={{ value: 5, trend: 0, trend_direction: 'flat' }} icon={<BarChart3 />} />,
    )
    expect(container.querySelector('[data-trend-direction]')?.textContent).toBe('No change')
    expect(container.querySelector('[data-trend-judgement]')).toBeNull()
  })

  it('does not announce itself as a live region (audit 1.5: no per-card aria-live)', () => {
    // A dashboard renders 6-8 cards polling via SWR; per-card role="status"
    // re-announced every value to screen readers on every refresh.
    const { container } = render(
      <MetricCard title="Pass Rate" metric={{ value: '98.2%' }} icon={<BarChart3 />} />,
    )

    expect(container.querySelector('[aria-live]')).toBeNull()
    expect(container.querySelector('[role="status"]')).toBeNull()
  })
})
