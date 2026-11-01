/**
 * Status is never colour-only (VIZ-102 / VIZ-105): the status hues are only
 * 1.07–1.38:1 against each other, so every status mark carries its fixed
 * pattern, and its legend swatch carries the same.
 *
 * Pinned on the chart that draws stacked status bars on every page —
 * `BarChart variant="stacked"` (`StatusBarPlot`), which replaced the legacy
 * `TrendChart` (deleted in W25c with its status areas and dashed status lines,
 * which no kit chart draws). The status donut's patterns are pinned by
 * `DonutChart.test.tsx`.
 *
 * Recharts is replaced by thin SVG stand-ins that echo the props the
 * component passes (fill, legend content), so the test reads what the
 * component asked Recharts to draw — and the `<defs>` the component puts in
 * the chart are real DOM.
 */
import { render } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { ChartSeries, SeriesChart, VizStatus } from '@/lib/viz/contracts'
import BarChart from './BarChart'
import type { ChartResponse, ChartState } from './chartState'
import { STATUS_ENCODING } from './tokens'

vi.mock('recharts', () => {
  const chart =
    (name: string) =>
    ({ children }: { children?: ReactNode }) => (
      <svg data-chart={name}>
        <g>{children}</g>
      </svg>
    )
  return {
    // The recharts hooks Wave 2.4's tooltip content reads (`ChartTooltip`'s `usePlotArea`, the bars' `useXAxisScale`): listed so a mock that ever renders that content does not throw.
    usePlotArea: () => undefined,
    useXAxisScale: () => undefined,
    ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    BarChart: chart('bar'),
    // For the donut half of `BreakdownChart`, which `BarChart.tsx` also exports.
    PieChart: chart('pie'),
    Pie: ({ children }: { children?: ReactNode }) => <g data-pie="">{children}</g>,
    Label: () => null,
    CartesianGrid: () => null,
    XAxis: () => null,
    YAxis: () => null,
    ReferenceLine: () => null,
    LabelList: () => null,
    Tooltip: () => null,
    // The legend renders the component's own content (if any) outside the plot.
    Legend: ({ content }: { content?: ReactElement | ((props: object) => ReactNode) }) => (
      <foreignObject data-legend-host="">{typeof content === 'function' ? content({}) : (content ?? null)}</foreignObject>
    ),
    Bar: ({ dataKey, fill }: { dataKey: string; fill?: string }) => <rect data-key={dataKey} fill={fill} />,
    Cell: ({ fill }: { fill?: string }) => <path data-cell="" fill={fill} />,
  }
})

const DRAWN: readonly VizStatus[] = ['passed', 'failed', 'broken', 'skipped']

/** Results by suite and status, as `BarChart.test.tsx` builds them. */
function statusState(rows: [string, Record<string, number>][]): ChartState<ChartResponse> {
  const statuses = [...new Set(rows.flatMap(([, counts]) => Object.keys(counts)))]
  const series: SeriesChart = {
    kind: 'series',
    dimensions: ['suite', 'status'],
    x_type: 'category',
    series: statuses.map((status) => ({
      key: status,
      label: status,
      points: rows.map(([suite, counts]) => ({ x: suite, y: counts[status] ?? 0, n: counts[status] ?? 0 })),
    })),
  }
  return { status: 'ready', data: { meta: null, series: series as ChartSeries }, meta: null, revalidating: false }
}

const SUITES = statusState([
  ['checkout', { passed: 80, failed: 10, broken: 5, skipped: 5 }],
  ['auth', { passed: 45, failed: 5, broken: 2, skipped: 1 }],
])

const stacked = () => <BarChart title="Results by suite" state={SUITES} variant="stacked" animate={false} />

/** The `<pattern>` a `url(#id)` fill points at, or null. */
function patternOf(fill: string | null): Element | null {
  const id = /^url\(#(.+)\)$/.exec(fill ?? '')?.[1]
  if (!id) return null
  const node = document.getElementById(id)
  return node?.tagName.toLowerCase() === 'pattern' ? node : null
}

const expectStatusPattern = (fill: string | null, status: VizStatus) => {
  const pattern = patternOf(fill)
  expect(pattern, `${status}: fill ${fill} is not a pattern`).not.toBeNull()
  expect(pattern?.id.endsWith(STATUS_ENCODING[status].patternId), `${status}: ${pattern?.id}`).toBe(true)
}

describe('status patterns are drawn, not just declared (BarChart variant="stacked")', () => {
  it('stacked bars: every status segment is filled with its own status pattern, defined inside the chart\'s own svg', () => {
    const { container } = render(stacked())
    const svg = container.querySelector('svg[data-chart="bar"]') as SVGElement
    expect(svg).not.toBeNull()
    expect(Array.from(svg.querySelectorAll('rect[data-key]'), (rect) => rect.getAttribute('data-key'))).toEqual([...DRAWN])
    for (const status of DRAWN) {
      const fill = svg.querySelector(`rect[data-key="${status}"]`)?.getAttribute('fill') ?? null
      expectStatusPattern(fill, status)
      // The pattern lives in THIS chart's svg, not somewhere else on the page.
      const id = /^url\(#(.+)\)$/.exec(fill ?? '')?.[1] ?? ''
      expect(svg.querySelector(`pattern[id="${id}"]`), status).not.toBeNull()
    }
    // Four segments, four different patterns.
    expect(new Set(DRAWN.map((s) => svg.querySelector(`rect[data-key="${s}"]`)?.getAttribute('fill'))).size).toBe(4)
  })

  it('a status legend entry carries its label and the same pattern as its marks', () => {
    const { container } = render(stacked())
    const legend = container.querySelector('[data-chart-legend]') as HTMLElement
    expect(legend).not.toBeNull()
    expect(Array.from(legend.querySelectorAll('[data-legend-status]'), (entry) => entry.getAttribute('data-legend-status'))).toEqual([
      ...DRAWN,
    ])
    for (const status of DRAWN) {
      const entry = legend.querySelector(`[data-legend-status="${status}"]`) as HTMLElement
      expect(entry, status).not.toBeNull()
      expect(entry).toHaveTextContent(STATUS_ENCODING[status].label)
      const swatch = entry.querySelector('[data-legend-swatch]')
      expect(swatch?.getAttribute('fill')).toBe(container.querySelector(`rect[data-key="${status}"]`)?.getAttribute('fill'))
      expectStatusPattern(swatch?.getAttribute('fill') ?? null, status)
    }
  })

  it('two stacked charts on one page do not share (and so cannot clobber) pattern ids', () => {
    const { container } = render(
      <>
        {stacked()}
        {stacked()}
      </>,
    )
    const ids = Array.from(container.querySelectorAll('pattern'), (p) => p.id)
    // Both charts drew a pattern for each of their four statuses …
    expect(ids).toHaveLength(2 * DRAWN.length)
    // … and no id is used twice, so neither chart's `url(#…)` can resolve to the other's.
    expect(new Set(ids).size).toBe(ids.length)
  })
})
