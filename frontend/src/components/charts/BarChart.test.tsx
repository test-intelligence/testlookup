/**
 * VIZ-402 — the bars as they are DRAWN, and the registry choosing between a
 * donut and a ranked bar. Recharts is replaced by stand-ins that echo the
 * props the component passes, so the test reads the axis domain, the stacking
 * and the series order the component actually asked for.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { cloneElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { ChartSeries, SeriesChart } from '@/lib/viz/contracts'
import BarChart, { BreakdownChart, RankedBarPlot, RankedTooltip, StatusTooltip, barMark, rankedShareWhole, rankedWhole } from './BarChart'
import { OTHER_KEY } from './multiSeriesModel'
import { rankedModel, statusBarModel } from './BarChart.model'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import { ChartFrameContext } from './chartFrameContext'
import type { ChartResponse, ChartState } from './chartState'
import { tooltipText } from './tooltip'
import { readTooltip } from './tooltipTestUtils'

/** Every `<Tooltip>` a plot asked Recharts for, so its `content` can be rendered as Recharts would. */
const tips = vi.hoisted(() => ({ props: [] as Record<string, unknown>[] }))

vi.mock('recharts', () => ({
  // `ChartTooltip`'s column geometry; bars place their tooltip by `useXAxisScale` instead.
  usePlotArea: () => undefined,
  // The value axis's scale, for the tooltip's mark: 100 px + 2 px per unit.
  useXAxisScale: () => (value: unknown) => 100 + 2 * (value as number),
  ResponsiveContainer: ({ children, height }: { children?: ReactNode; height?: number }) => (
    <div data-container-height={height}>{children}</div>
  ),
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
  XAxis: ({ type, domain, ticks, label }: { type?: string; domain?: unknown; ticks?: unknown; label?: { value?: string } }) => (
    <g
      data-xaxis={type}
      data-domain={Array.isArray(domain) ? domain.join(',') : ''}
      data-ticks={Array.isArray(ticks) ? ticks.join(',') : ''}
      data-axis-title={label?.value ?? ''}
    />
  ),
  YAxis: ({ type, dataKey }: { type?: string; dataKey?: string }) => <g data-yaxis={type} data-datakey={dataKey} />,
  CartesianGrid: () => null,
  ReferenceLine: ({ x }: { x?: number }) => <line data-reference-x={String(x)} />,
  Tooltip: (props: Record<string, unknown>) => {
    tips.props.push(props)
    return null
  },
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
    // From zero to a NICE end past the data: 0-50, not 0-41.
    expect(plot.getAttribute('data-bar-domain')).toBe('0,50')
    expect(plot.getAttribute('data-bar-diverging')).toBe('false')
    // Horizontal: the category axis is the y one.
    expect(container.querySelector('[data-chart="bar"]')?.getAttribute('data-layout')).toBe('vertical')
    expect(container.querySelector('[data-yaxis]')?.getAttribute('data-yaxis')).toBe('category')
    // …and the value axis really carries the zero-based domain AND its ticks:
    // given a domain alone, Recharts ended the axis on the data max.
    const axis = container.querySelector('[data-xaxis="number"]')
    expect(axis?.getAttribute('data-domain')).toBe('0,50')
    expect(axis?.getAttribute('data-ticks')).toBe('0,10,20,30,40,50')
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
    expect(plot.getAttribute('data-bar-domain')).toBe('-10,10')
    expect(container.querySelector('[data-reference-x="0"]')).not.toBeNull()
    expect(screen.getByText(/diverge from a zero baseline/i)).toBeInTheDocument()
  })

  it('titles the value axis for what it measures: a change chart is "Change", a ranking "Count"', () => {
    const change = categorySeries([
      ['up', 5],
      ['down', -8],
    ])
    const axisTitle = (container: HTMLElement) => container.querySelector('[data-xaxis="number"]')?.getAttribute('data-axis-title')
    const diverging = render(<BarChart title="Change" state={ready(change)} variant="ranked" animate={false} />)
    expect(axisTitle(diverging.container)).toBe('Change')
    // The table view heads its value column with the same words as the axis
    // (it said "Value" under a "Count" or "Change" axis).
    fireEvent.click(within(diverging.container).getByRole('button', { name: 'View as table' }))
    expect(within(diverging.container).getByRole('columnheader', { name: 'Change' })).toBeInTheDocument()
    diverging.unmount()

    const ranking = render(<BarChart title="Top failing tests" state={ready(RANKED)} variant="ranked" animate={false} />)
    expect(axisTitle(ranking.container)).toBe('Count')
    ranking.unmount()

    // A caller that knows better still names it.
    const named = render(
      <BarChart title="Change" state={ready(change)} variant="ranked" valueAxisLabel="Failures, week on week" animate={false} />,
    )
    expect(axisTitle(named.container)).toBe('Failures, week on week')
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

  it('a bar on page 2 states its share of EVERY page — not of the page it is on (Wave 2.4 T1)', () => {
    tips.props.length = 0
    // 120 bars, 1000 down to 881: 112,860 in all; page 2 alone holds 46,275.
    const many: [string, number][] = Array.from({ length: 120 }, (_, i) => [`test-${String(i).padStart(3, '0')}`, 1000 - i])
    const { container } = render(
      <BarChart title="Top failing tests" state={ready(categorySeries(many))} variant="ranked" valueAxisLabel="Failures" animate={false} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Next bars' }))
    expect(container.querySelector('[data-bar-chart="ranked"]')?.getAttribute('data-bar-page')).toBe('1')
    const content = tips.props[tips.props.length - 1].content as ReactElement<{ whole?: number | null }>
    expect(content.props.whole).toBe(112_860)
    // The first bar of page 2, test-050 (950): 950 / 112,860 = 0.8 %, where page 2 alone would say 2.1 %.
    const surface = container.querySelector('[data-bar-chart="ranked"]') as HTMLElement
    fireEvent.keyDown(surface, { key: 'Home' })
    expect(tooltipText(readTooltip(container.querySelector('[data-chart-readout]') as HTMLElement))).toBe(
      'test-050. Failures: 950. Share of total: 0.8%',
    )
  })

  it('states NO share of total when the server returned only the top N of M, with no "Other" (Wave 2.4 F1)', () => {
    tips.props.length = 0
    // The top 3 of 400: the three bars sum to 60 of an unknown total.
    const top3 = categorySeries([
      ['checkout', 30],
      ['auth', 20],
      ['search', 10],
    ])
    const meta = {
      schema_version: 1,
      scope: { projects: [], releases: [], suites: [], window: { from: '2026-09-01', to: '2026-09-07', days: 7, timezone: 'UTC' as const } },
      totals: { matched_runs: 42, total_runs: 50, matched_executions: 1260, total_executions: 1500 },
      pass_rate_basis: 'executions' as const,
      ignored_filters: [],
      truncated: true,
      truncated_total: 400,
      measured: true,
      reason: null,
      includes_in_progress: 0,
      partial_day: null,
      generated_at: '2026-09-08T00:00:00Z',
      as_of: '2026-09-08T00:00:00Z',
    }
    const truncated: ChartState<ChartResponse> = { status: 'truncated', data: { meta, series: top3 }, meta, shown: 3, total: 400, revalidating: false }
    const { container } = render(<BarChart title="Top failing tests" state={truncated} variant="ranked" valueAxisLabel="Failures" animate={false} />)
    const content = tips.props[tips.props.length - 1].content as ReactElement<{ whole?: number | null }>
    expect(content.props.whole).toBeNull()
    fireEvent.keyDown(container.querySelector('[data-bar-chart="ranked"]') as HTMLElement, { key: 'Home' })
    const readout = tooltipText(readTooltip(container.querySelector('[data-chart-readout]') as HTMLElement))
    // The value still; no share of a whole the chart does not have.
    expect(readout).toBe('checkout. Failures: 30')
    // The same bars, not truncated, do state it.
    expect(rankedShareWhole([{ key: 'a', value: 30 }, { key: 'b', value: 20 }], false)).toBe(50)
    // Truncated but carrying the "Other" roll-up: the bars ARE the whole again.
    expect(rankedShareWhole([{ key: 'a', value: 30 }, { key: OTHER_KEY, value: 70 }], true)).toBe(100)
    expect(rankedShareWhole([{ key: 'a', value: 30 }], true)).toBeNull()
  })

  it('states the exact value and its share of every bar on every page (VIZ-601)', () => {
    const model = rankedModel([
      { key: 'a', label: 'checkout', value: 30 },
      { key: 'b', label: 'auth', value: 10 },
    ])
    const { container } = render(
      <RankedTooltip active payload={[{ payload: model.bars[0] }]} valueTitle="Failures" whole={rankedWhole([{ value: 30 }, { value: 10 }, { value: 60 }])} />,
    )
    expect(readTooltip(container.querySelector('[data-chart-tooltip]') as HTMLElement)).toEqual({
      title: 'checkout',
      rows: [
        { kind: 'value', label: 'Failures', value: '30' },
        { kind: 'share', label: 'Share of total', value: '30.0%' },
      ],
    })
  })

  it('states no share on a diverging (change) chart: a sum of rises and falls is not a whole', () => {
    expect(rankedWhole([{ value: 5 }, { value: -3 }])).toBeNull()
    expect(rankedWhole([{ value: 0 }])).toBeNull()
    expect(rankedWhole([{ value: 2 }, { value: 6 }])).toBe(8)
  })

  it('pointer and keyboard read the SAME content for every bar (VIZ-601)', () => {
    tips.props = []
    const { container } = render(
      <ChartAnnouncerProvider>
        <BarChart
          title="Top failing"
          state={ready(categorySeries([['checkout', 30], ['auth', 10]]))}
          variant="ranked"
          valueAxisLabel="Failures"
          animate={false}
        />
      </ChartAnnouncerProvider>,
    )
    const content = tips.props[tips.props.length - 1].content as ReactElement<Record<string, unknown>>
    const surface = container.querySelector('[data-bar-chart="ranked"]') as HTMLElement
    const model = rankedModel([
      { key: 'checkout', label: 'checkout', value: 30 },
      { key: 'auth', label: 'auth', value: 10 },
    ])
    model.bars.forEach((drawn, index) => {
      fireEvent.keyDown(surface, { key: index === 0 ? 'Home' : 'ArrowRight' })
      const heard = document.querySelector('[data-chart-announcer="assertive"]')?.textContent ?? ''
      const readout = readTooltip(container.querySelector('[data-chart-readout]') as HTMLElement)
      const hover = render(cloneElement(content, { active: true, payload: [{ payload: drawn }] }))
      const pointed = readTooltip(hover.container.querySelector('[data-chart-tooltip]') as HTMLElement)
      hover.unmount()
      expect(heard).toBe(`Top failing: ${tooltipText(pointed)}`)
      expect(readout).toEqual(pointed)
    })
    expect(tooltipText(readTooltip(container.querySelector('[data-chart-readout]') as HTMLElement))).toBe(
      'auth. Failures: 10. Share of total: 25.0%',
    )
  })

  it('keeps the tooltip beside the bar: the mark runs from zero to the bar end, over the bar', () => {
    const scale = (value: number) => 100 + 2 * value
    expect(barMark([30], 16, 50, scale)).toEqual({ left: 100, top: 42, width: 60, height: 16 })
    // A negative (diverging) bar runs LEFT of zero.
    expect(barMark([-10], 16, 50, scale)).toEqual({ left: 80, top: 42, width: 20, height: 16 })
    // No geometry yet: no mark, and no placement guessed.
    expect(barMark([30], 16, undefined, scale)).toBeNull()
    expect(barMark([30], 16, 50, undefined)).toBeNull()
  })

  it('renders a hostile test name as literal text in the tooltip, the readout and the table', () => {
    const hostile = '<img src=x onerror="window.__xss=1">'
    tips.props = []
    const { container } = render(
      <ChartAnnouncerProvider>
        <BarChart title="Hostile" state={ready(categorySeries([[hostile, 3], ['benign', 1]]))} variant="ranked" animate={false} />
      </ChartAnnouncerProvider>,
    )
    const surface = container.querySelector('[data-bar-chart="ranked"]') as HTMLElement
    fireEvent.keyDown(surface, { key: 'Home' })
    expect(container.querySelector('[data-chart-readout] .chart-tooltip-title')?.textContent).toBe(hostile)
    const content = tips.props[tips.props.length - 1].content as ReactElement<Record<string, unknown>>
    const model = rankedModel([{ key: hostile, label: hostile, value: 3 }])
    const hover = render(cloneElement(content, { active: true, payload: [{ payload: model.bars[0] }] }))
    expect(hover.container.querySelector('[data-chart-tooltip] .chart-tooltip-title')?.textContent).toBe(hostile)
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    expect(screen.getByRole('table').textContent).toContain(hostile)
    expect(document.querySelector('img')).toBeNull()
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
  })

  it('grows the plot for a 50-bar page instead of squeezing it into a 5-bar height', () => {
    const many: [string, number][] = Array.from({ length: 60 }, (_, i) => [`suite-${i}`, 300 - i * 2])
    const { container } = render(
      <BarChart title="Many" state={ready(categorySeries(many))} variant="ranked" height={260} animate={false} />,
    )
    // 50 rows x 20 px + 68 px of margins and value axis.
    expect(container.querySelector('[data-bar-chart]')?.getAttribute('data-bar-plot-height')).toBe('1068')
    expect(container.querySelector('[data-container-height]')?.getAttribute('data-container-height')).toBe('1068')
  })

  it('keeps the requested height when the rows fit in it', () => {
    const { container } = render(<BarChart title="Few" state={ready(RANKED)} variant="ranked" height={260} animate={false} />)
    expect(container.querySelector('[data-container-height]')?.getAttribute('data-container-height')).toBe('260')
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

  it('gives a grouped row room for each status bar, growing the plot', () => {
    const { container } = render(
      <BarChart title="Results by suite" state={ready(SUITES)} variant="grouped" height={100} animate={false} />,
    )
    // 2 rows x 60 px (four statuses at 9 px + gaps) + 68 px + a 32 px legend.
    expect(container.querySelector('[data-bar-chart]')?.getAttribute('data-bar-plot-height')).toBe('220')
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

  it('names the centre total by what the slices count, not "executions"', () => {
    render(<BreakdownChart title="Failures by category" state={ready(categories(4))} animate={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    // 10 + 11 + 12 + 13 failures, in four failure categories.
    expect(document.querySelector('[data-donut-table-total]')?.textContent).toBe('Total 46 failures')
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

describe('BarChart — full screen (VIZ-608)', () => {
  it('grows the plot to the frame body in full screen, and not otherwise', () => {
    const plot = (fullscreen: boolean) => {
      const { container, unmount } = render(
        <ChartFrameContext.Provider value={{ fullscreen, bodyHeight: fullscreen ? 900 : null, portalContainer: null }}>
          {/* The plot as the frame's body holds it: the frame provides this context there. */}
          <RankedBarPlot
            title="Top failing"
            model={rankedModel([
              { key: 'a', label: 'a', value: 3 },
              { key: 'b', label: 'b', value: 1 },
            ])}
            height={260}
            animate={false}
          />
        </ChartFrameContext.Provider>,
      )
      const height = container.querySelector('[data-container-height]')?.getAttribute('data-container-height')
      const shown = (container.querySelector('[data-chart-presentation]') as HTMLElement | null)?.style.height ?? null
      unmount()
      return { height, shown }
    }
    // Outside full screen: the page height, and no scaling wrapper at all.
    expect(plot(false)).toEqual({ height: '260', shown: null })
    // Full screen: 900 px on screen, LAID OUT at 900 × 11/15 = 660 and scaled
    // up by 15/11, so the 11 px names read at 15 px in the space reserved for
    // them (Wave 2.4 review A5).
    expect(plot(true)).toEqual({ height: '660', shown: '900px' })
  })
})

describe('BarChart — the status tooltip (VIZ-601)', () => {
  const rows: [string, Record<string, number>][] = [
    ['checkout', { passed: 80, failed: 15, broken: 5 }],
    ['auth', { passed: 45, failed: 5 }],
  ]

  it('lists every segment with its TRUE count and share of the bar, then the bar total as n', () => {
    const model = statusBarModel(
      [{ key: 'checkout', label: 'checkout', counts: { passed: 80, failed: 15, broken: 5 } }],
      { layout: 'stacked', mode: 'percent' },
    )
    const row = { key: 'checkout', label: 'checkout', short: 'checkout', total: 100, bar: model.bars[0] }
    const { container } = render(<StatusTooltip active payload={[{ payload: row }]} layout="stacked" />)
    const tip = readTooltip(container.querySelector('[data-chart-tooltip]') as HTMLElement)
    // 100% mode still says the COUNT: flipping the toggle never changes what a bar holds.
    expect(tooltipText(tip)).toBe(
      'checkout. Passed: 80 (80.0% of bar). Failed: 15 (15.0% of bar). Broken: 5 (5.0% of bar). Samples: 100',
    )
  })

  it('pointer and keyboard read the SAME content for every bar, in either mode', () => {
    for (const initialMode of ['absolute', 'percent'] as const) {
      tips.props = []
      const { container, unmount } = render(
        <ChartAnnouncerProvider>
          <BarChart title="Results" state={ready(statusSeries(rows))} variant="stacked" initialMode={initialMode} animate={false} />
        </ChartAnnouncerProvider>,
      )
      const content = tips.props[tips.props.length - 1].content as ReactElement<Record<string, unknown>>
      const model = statusBarModel(
        rows.map(([suite, counts]) => ({ key: suite, label: suite, counts })),
        { layout: 'stacked', mode: initialMode },
      )
      const surface = container.querySelector('[data-bar-chart="stacked"]') as HTMLElement
      model.bars.forEach((bar, index) => {
        fireEvent.keyDown(surface, { key: index === 0 ? 'Home' : 'ArrowRight' })
        const heard = document.querySelector('[data-chart-announcer="assertive"]')?.textContent ?? ''
        const row = { key: bar.key, label: bar.label, short: bar.short, total: bar.total, bar }
        const hover = render(cloneElement(content, { active: true, payload: [{ payload: row }] }))
        const pointed = readTooltip(hover.container.querySelector('[data-chart-tooltip]') as HTMLElement)
        hover.unmount()
        expect(heard).toBe(`Results: ${tooltipText(pointed)}`)
        expect(readTooltip(container.querySelector('[data-chart-readout]') as HTMLElement)).toEqual(pointed)
      })
      unmount()
    }
  })
})
