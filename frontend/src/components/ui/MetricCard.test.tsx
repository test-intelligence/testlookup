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
