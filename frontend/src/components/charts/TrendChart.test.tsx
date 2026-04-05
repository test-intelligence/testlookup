import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import TrendChart from './TrendChart'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  LineChart: ({ children }: { children: ReactNode }) => <div data-testid="line-chart">{children}</div>,
  AreaChart: ({ children }: { children: ReactNode }) => <div data-testid="area-chart">{children}</div>,
  BarChart: ({ children }: { children: ReactNode }) => <div data-testid="bar-chart">{children}</div>,
  CartesianGrid: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
  Legend: () => <div />,
  Line: () => <div />,
  Area: () => <div />,
  Bar: () => <div />,
}))

const data = [
  {
    date: '2026-03-01',
    passed: 20,
    failed: 2,
    skipped: 1,
    broken: 0,
    total: 23,
    pass_rate: 86.9,
  },
]

describe('TrendChart', () => {
  it('renders line chart by default', () => {
    render(<TrendChart data={data} />)
    expect(screen.getByTestId('line-chart')).toBeInTheDocument()
  })

  it('renders area chart when requested', () => {
    render(<TrendChart data={data} type="area" />)
    expect(screen.getByTestId('area-chart')).toBeInTheDocument()
  })

  it('renders bar chart when requested', () => {
    render(<TrendChart data={data} type="bar" />)
    expect(screen.getByTestId('bar-chart')).toBeInTheDocument()
  })
})
