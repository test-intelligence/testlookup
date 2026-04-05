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
})
