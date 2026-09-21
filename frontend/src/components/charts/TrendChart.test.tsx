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
  // The series marks echo `isAnimationActive` so the `animate` prop is observable.
  Line: ({ isAnimationActive }: { isAnimationActive?: boolean }) => (
    <div data-testid="series-mark" data-animate={String(isAnimationActive)} />
  ),
  Area: ({ isAnimationActive }: { isAnimationActive?: boolean }) => (
    <div data-testid="series-mark" data-animate={String(isAnimationActive)} />
  ),
  Bar: ({ isAnimationActive }: { isAnimationActive?: boolean }) => (
    <div data-testid="series-mark" data-animate={String(isAnimationActive)} />
  ),
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

  // line: passed / failed / skipped / pass_rate · area: total / passed ·
  // bar: passed / failed / skipped / broken
  const SERIES_PER_TYPE = [['line', 4], ['area', 2], ['bar', 4]] as const

  it.each(SERIES_PER_TYPE)('leaves the %s chart on the Recharts animation default', (type, series) => {
    // `undefined` is what lets Recharts resolve its own default ('auto'), so
    // adding the prop changed nothing for the call sites that do not pass it.
    render(<TrendChart data={data} type={type} />)
    const marks = screen.getAllByTestId('series-mark')
    expect(marks).toHaveLength(series)
    for (const mark of marks) expect(mark).toHaveAttribute('data-animate', 'undefined')
  })

  it.each(SERIES_PER_TYPE)('forwards animate={false} to every %s series', (type, series) => {
    render(<TrendChart data={data} type={type} animate={false} />)
    const marks = screen.getAllByTestId('series-mark')
    expect(marks).toHaveLength(series)
    for (const mark of marks) expect(mark).toHaveAttribute('data-animate', 'false')
  })
})
