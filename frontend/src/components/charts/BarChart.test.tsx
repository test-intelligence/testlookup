/**
 * VIZ-402 — the bars as they are DRAWN, and the registry choosing between a
 * donut and a ranked bar. Recharts is replaced by stand-ins that echo the
 * props the component passes, so the test reads the axis domain, the stacking
 * and the series order the component actually asked for.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { ChartSeries, SeriesChart } from '@/lib/viz/contracts'
import BarChart, { BreakdownChart, RankedTooltip } from './BarChart'
import { rankedModel } from './BarChart.model'
import type { ChartResponse, ChartState } from './chartState'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  BarChart: ({ children, data, layout }: { children?: ReactNode; data?: { short?: string }[]; layout?: string }) => (
    <svg data-chart="bar" data-layout={layout} data-rows={(data ?? []).map((row) => row.short ?? '').join('|')}>
      <g>{children}</g>
    </svg>
  ),
  Bar: ({ children, dataKey, stackId, fill }: { children?: ReactNode; dataKey?: string; stackId?: string; fill?: string }) => (
    <g data-bar={dataKey} data-stack={stackId ?? ''} fill={fill}>
      {children}
    </g>
  ),
  Cell: ({ fill }: { fill?: string }) => <path data-cell="" fill={fill} />,
  LabelList: ({ dataKey }: { dataKey?: string }) => <text data-labellist={dataKey} />,
  XAxis: ({ type, domain }: { type?: string; domain?: unknown }) => (
    <g data-xaxis={type} data-domain={Array.isArray(domain) ? domain.join(',') : ''} />
  ),
  YAxis: ({ type, dataKey }: { type?: string; dataKey?: string }) => <g data-yaxis={type} data-datakey={dataKey} />,
  CartesianGrid: () => null,
  ReferenceLine: ({ x }: { x?: number }) => <line data-reference-x={String(x)} />,
  Tooltip: () => null,
  Legend: ({ content }: { content?: () => ReactNode }) => <div>{typeof content === 'function' ? content() : null}</div>,
  // For the donut half of BreakdownChart.
  PieChart: ({ children }: { children?: ReactNode }) => (
    <svg data-chart="pie">
      <g>{children}</g>
    </svg>
  ),
  Pie: ({ children }: { children?: ReactNode }) => <g data-pie="">{children}</g>,
  Label: () => null,
}))

function categorySeries(values: [string, number][], dimension = 'test'): ChartSeries {
  const chart: SeriesChart = {
    kind: 'series',
    dimensions: [dimension],
    x_type: 'category',
    series: [
      {
        key: 'failures',
        label: 'Failures',
        points: values.map(([x, y]) => ({ x, y, n: Math.abs(y) })),
      },
    ],
  }
  return chart
}

function statusSeries(rows: [string, Record<string, number>][]): ChartSeries {
  const statuses = [...new Set(rows.flatMap(([, counts]) => Object.keys(counts)))]
  const chart: SeriesChart = {
    kind: 'series',
    dimensions: ['suite', 'status'],
    x_type: 'category',
    series: statuses.map((status) => ({
      key: status,
      label: status,
      points: rows.map(([suite, counts]) => ({ x: suite, y: counts[status] ?? 0, n: counts[status] ?? 0 })),
    })),
  }
  return chart
}

const ready = (series: ChartSeries): ChartState<ChartResponse> => ({
  status: 'ready',
  data: { meta: null, series },
  meta: null,
  revalidating: false,
})

const RANKED = categorySeries([
  ['checkout', 12],
  ['auth', 41],
  ['search', 7],
  ['billing', 23],
])

describe('BarChart — ranked', () => {
  it('draws horizontal bars, sorted descending, on a value axis that starts at zero', () => {
    const { container } = render(<BarChart title="Top failing tests" state={ready(RANKED)} variant="ranked" animate={false} />)
    const plot = container.querySelector('[data-bar-chart="ranked"]') as HTMLElement
    expect(plot.getAttribute('data-bar-values')).toBe('41,23,12,7')
    expect(plot.getAttribute('data-bar-domain')).toBe('0,41')
    expect(plot.getAttribute('data-bar-diverging')).toBe('false')
    // Horizontal: the category axis is the y one.
    expect(container.querySelector('[data-chart="bar"]')?.getAttribute('data-layout')).toBe('vertical')
    expect(container.querySelector('[data-yaxis]')?.getAttribute('data-yaxis')).toBe('category')
    // …and the value axis really carries the zero-based domain.
    expect(container.querySelector('[data-xaxis="number"]')?.getAttribute('data-domain')).toBe('0,41')
    // Every bar is labelled with its full-precision value.
    expect(container.querySelector('[data-labellist]')?.getAttribute('data-labellist')).toBe('valueLabel')
  })

  it('diverges around a drawn zero baseline when a change is negative', () => {
    const change = categorySeries([
      ['up', 5],
      ['down', -8],
      ['small', 2],
    ])
    const { container } = render(<BarChart title="Change" state={ready(change)} variant="ranked" animate={false} />)
    const plot = container.querySelector('[data-bar-chart="ranked"]') as HTMLElement
    expect(plot.getAttribute('data-bar-diverging')).toBe('true')
    expect(plot.getAttribute('data-bar-domain')).toBe('-8,8')
    expect(container.querySelector('[data-reference-x="0"]')).not.toBeNull()
    expect(screen.getByText(/diverge from a zero baseline/i)).toBeInTheDocument()
  })

  it('states the ties it admitted past the top-N cut', () => {
    const items: [string, number][] = [
      ...Array.from({ length: 9 }, (_, i) => [`lead-${i}`, 50 - i] as [string, number]),
      ...Array.from({ length: 4 }, (_, i) => [`tied-${i}`, 5] as [string, number]),
      ['below', 1],
    ]
    const { container } = render(
      <BarChart title="Top failing tests" state={ready(categorySeries(items))} variant="ranked" topN={10} animate={false} />,
    )
    const note = within(container.querySelector('[data-chart-footer]') as HTMLElement).getByText(/tied with the 10th/i)
    expect(note).toBeInTheDocument()
    expect(container.querySelector('[data-bar-chart="ranked"]')?.getAttribute('data-bar-total')).toBe('13')
  })

  it('paginates past 50 bars, stating the page and the total, and turns the page', () => {
    const many: [string, number][] = Array.from({ length: 120 }, (_, i) => [`test-${String(i).padStart(3, '0')}`, 1000 - i])
    const { container } = render(
      <BarChart title="Top failing tests" state={ready(categorySeries(many))} variant="ranked" animate={false} />,
    )
    const footer = container.querySelector('[data-chart-footer]') as HTMLElement
    expect(within(footer).getByText('120 bars in all')).toBeInTheDocument()
    // The page position is said ONCE, beside the buttons it describes.
    expect(within(footer).getByText('Page 1 of 3')).toBeInTheDocument()
    expect(container.querySelector('[data-bar-chart="ranked"]')?.getAttribute('data-bar-page')).toBe('0')

    fireEvent.click(screen.getByRole('button', { name: 'Next bars' }))
    expect(within(container.querySelector('[data-chart-footer]') as HTMLElement).getByText('Page 2 of 3')).toBeInTheDocument()
    expect(container.querySelector('[data-bar-chart="ranked"]')?.getAttribute('data-bar-page')).toBe('1')
  })

  it('shows the full name in the tooltip when the axis had to truncate it', () => {
    const long = 'tests.integration.checkout.test_payment_gateway_declines_an_expired_card_and_retries_once'
    const model = rankedModel([{ key: 'k', label: long, value: 3 }])
    const { container } = render(<RankedTooltip active payload={[{ payload: model.bars[0] }]} />)
    expect(container.textContent).toContain(long)
    expect(container.querySelector('img')).toBeNull()
  })

  it('hands an empty ranking over to the filtered-empty state', () => {
    const { container } = render(<BarChart title="Top failing tests" state={ready(categorySeries([]))} variant="ranked" />)
    expect(container.querySelector('[data-chart-frame]')?.getAttribute('data-chart-state')).toBe('filtered-empty')
    expect(container.querySelector('[data-bar-chart]')).toBeNull()
  })
})

describe('BarChart — stacked and grouped by status', () => {
  const SUITES = statusSeries([
    ['checkout', { passed: 80, failed: 10, broken: 5, skipped: 5 }],
    ['auth', { passed: 45, failed: 5, broken: 0, skipped: 0 }],
  ])

  it('stacks the statuses in the fixed order', () => {
    const { container } = render(<BarChart title="Results by suite" state={ready(SUITES)} variant="stacked" animate={false} />)
    const bars = Array.from(container.querySelectorAll('[data-bar]'))
    expect(bars.map((bar) => bar.getAttribute('data-bar'))).toEqual(['passed', 'failed', 'broken', 'skipped'])
    for (const bar of bars) expect(bar.getAttribute('data-stack')).toBe('stack')
    expect(container.querySelector('[data-bar-chart]')?.getAttribute('data-bar-domain')).toBe('0,100')
  })

  it('groups without a stack id, on an axis sized by the largest segment', () => {
    const { container } = render(<BarChart title="Results by suite" state={ready(SUITES)} variant="grouped" animate={false} />)
    for (const bar of container.querySelectorAll('[data-bar]')) expect(bar.getAttribute('data-stack')).toBe('')
    expect(container.querySelector('[data-bar-chart="grouped"]')?.getAttribute('data-bar-domain')).toBe('0,80')
  })

  it('toggles between absolute counts and 100%', () => {
    const { container } = render(<BarChart title="Results by suite" state={ready(SUITES)} variant="stacked" animate={false} />)
    expect(container.querySelector('[data-bar-chart]')?.getAttribute('data-bar-mode')).toBe('absolute')

    const toggle = screen.getByRole('button', { name: 'Show 100%' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(toggle)

    const plot = container.querySelector('[data-bar-chart]') as HTMLElement
    expect(plot.getAttribute('data-bar-mode')).toBe('percent')
    expect(plot.getAttribute('data-bar-domain')).toBe('0,100')
    expect(screen.getByRole('button', { name: 'Show counts' })).toHaveAttribute('aria-pressed', 'true')
    // The status order does not change with the mode.
    expect(Array.from(container.querySelectorAll('[data-bar]'), (bar) => bar.getAttribute('data-bar'))).toEqual([
      'passed',
      'failed',
      'broken',
      'skipped',
    ])
  })

  it('makes the table view FOLLOW the absolute / 100% toggle', () => {
    render(<BarChart title="Results by suite" state={ready(SUITES)} variant="stacked" animate={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    const read = () =>
      Array.from(document.querySelectorAll('tbody tr'), (row) =>
        Array.from(row.children, (cell) => cell.textContent),
      )
    expect(read()).toEqual([
      ['checkout', '80', '10', '5', '5'],
      ['auth', '45', '5', '0', '0'],
    ])

    // The toggle used to exist only in the drawing: the plot showed shares
    // and the table went on showing counts, so the two views of one chart
    // answered different questions.
    fireEvent.click(screen.getByRole('button', { name: 'Show 100%' }))
    expect(read()).toEqual([
      ['checkout', '80.0%', '10.0%', '5.0%', '5.0%'],
      ['auth', '90.0%', '10.0%', '0.0%', '0.0%'],
    ])
  })
})

describe('BreakdownChart — the registry picks the chart type', () => {
  const categories = (n: number) =>
    categorySeries(
      Array.from({ length: n }, (_, i) => [`category-${i}`, 10 + i] as [string, number]),
      'failure_category',
    )

  it('draws a donut for five categories or fewer', () => {
    const { container } = render(<BreakdownChart title="Failures by category" state={ready(categories(4))} animate={false} />)
    expect(container.querySelector('[data-chart-choice]')?.getAttribute('data-chart-choice')).toBe('donut')
    expect(container.querySelector('[data-chart-offers-pie]')?.getAttribute('data-chart-offers-pie')).toBe('true')
    expect(container.querySelector('[data-donut]')).not.toBeNull()
  })

  it('draws a ranked bar past five categories, and offers no pie — the caller does not decide', () => {
    const { container } = render(
      // The caller asks for a donut. The registry says no.
      <BreakdownChart title="Failures by category" state={ready(categories(6))} preferred="donut" animate={false} />,
    )
    expect(container.querySelector('[data-chart-choice]')?.getAttribute('data-chart-choice')).toBe('ranked-bar')
    expect(container.querySelector('[data-chart-offers-pie]')?.getAttribute('data-chart-offers-pie')).toBe('false')
    expect(container.querySelector('[data-donut]')).toBeNull()
    expect(container.querySelector('[data-bar-chart="ranked"]')).not.toBeNull()
  })
})
