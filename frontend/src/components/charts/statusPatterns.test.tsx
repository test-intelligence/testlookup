/**
 * Status is never colour-only (VIZ-102 / VIZ-105): the status hues are only
 * 1.07–1.38:1 against each other, so every status mark carries its fixed
 * pattern (fills) or dash (lines), and its legend swatch carries the same.
 *
 * Recharts is replaced by thin SVG stand-ins that echo the props the
 * components pass (fill, stroke dash, legend content), so the test reads what
 * the component asked Recharts to draw — and the `<defs>` the component puts in
 * the chart are real DOM.
 */
import { render } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { VIZ_STATUSES, type VizStatus } from '@/lib/viz/contracts'
import TrendChart from './TrendChart'
import DefectDonut from './DefectDonut'
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
    LineChart: chart('line'),
    AreaChart: chart('area'),
    BarChart: chart('bar'),
    PieChart: chart('pie'),
    CartesianGrid: () => null,
    XAxis: () => null,
    YAxis: () => null,
    Tooltip: () => null,
    // The legend renders the component's own content (if any) outside the plot.
    Legend: ({ content }: { content?: ReactElement | ((props: object) => ReactNode) }) => (
      <foreignObject data-legend-host="">{typeof content === 'function' ? content({}) : (content ?? null)}</foreignObject>
    ),
    Bar: ({ dataKey, fill }: { dataKey: string; fill?: string }) => <rect data-key={dataKey} fill={fill} />,
    Area: ({ dataKey, fill }: { dataKey: string; fill?: string }) => <path data-key={dataKey} fill={fill} />,
    Line: ({ dataKey, strokeDasharray }: { dataKey: string; strokeDasharray?: string }) => (
      <path data-key={dataKey} data-dash={strokeDasharray ?? 'solid'} />
    ),
    Pie: ({ children }: { children?: ReactNode }) => <g data-pie="">{children}</g>,
    Cell: ({ fill }: { fill?: string }) => <path data-cell="" fill={fill} />,
  }
})

const POINT = { date: '2026-09-01', passed: 5, failed: 2, skipped: 1, broken: 1, total: 9, pass_rate: 55.6 }

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

describe('status patterns are drawn, not just declared', () => {
  it('stacked bars: every status segment is filled with its own status pattern', () => {
    const { container } = render(<TrendChart data={[POINT]} type="bar" />)
    for (const status of ['passed', 'failed', 'skipped', 'broken'] as const) {
      expectStatusPattern(container.querySelector(`rect[data-key="${status}"]`)?.getAttribute('fill') ?? null, status)
    }
    // One pattern per drawn status, all defined inside the chart's own svg.
    const svg = container.querySelector('svg[data-chart="bar"]') as SVGElement
    for (const status of VIZ_STATUSES.filter((s) => s !== 'unknown')) {
      expect(svg.querySelector(`pattern[id$="${STATUS_ENCODING[status].patternId}"]`), status).not.toBeNull()
    }
  })

  it('a status legend entry carries the same pattern as its marks', () => {
    const { container } = render(<TrendChart data={[POINT]} type="bar" />)
    const legend = container.querySelector('[data-chart-legend]') as HTMLElement
    expect(legend).not.toBeNull()
    for (const status of ['passed', 'failed', 'skipped', 'broken'] as const) {
      const entry = legend.querySelector(`[data-legend-status="${status}"]`) as HTMLElement
      expect(entry, status).not.toBeNull()
      expect(entry).toHaveTextContent(STATUS_ENCODING[status].label)
      const swatch = entry.querySelector('[data-legend-swatch]')
      expect(swatch?.getAttribute('fill')).toBe(container.querySelector(`rect[data-key="${status}"]`)?.getAttribute('fill'))
      expectStatusPattern(swatch?.getAttribute('fill') ?? null, status)
    }
  })

  it('areas: the passed area is filled with the passed pattern', () => {
    const { container } = render(<TrendChart data={[POINT]} type="area" />)
    expectStatusPattern(container.querySelector('path[data-key="passed"]')?.getAttribute('fill') ?? null, 'passed')
  })

  it('lines: each status line has its own dash, and the legend draws that dash', () => {
    const { container } = render(<TrendChart data={[POINT]} type="line" />)
    const dashes = (['passed', 'failed', 'skipped'] as const).map(
      (status) => container.querySelector(`path[data-key="${status}"]`)?.getAttribute('data-dash'),
    )
    expect(new Set(dashes).size).toBe(3)
    const legend = container.querySelector('[data-chart-legend]') as HTMLElement
    for (const [i, status] of (['passed', 'failed', 'skipped'] as const).entries()) {
      const line = legend.querySelector(`[data-legend-status="${status}"] [data-legend-swatch]`)
      expect(line?.getAttribute('stroke-dasharray') ?? 'solid', status).toBe(dashes[i])
    }
  })

  it('donut: every slice is patterned; P1 and P2 carry the failed and broken status patterns', () => {
    const { container } = render(<DefectDonut data={[3, 7, 12, 5]} />)
    const fills = Array.from(container.querySelectorAll('path[data-cell]'), (cell) => cell.getAttribute('fill'))
    expect(fills).toHaveLength(4)
    for (const fill of fills) expect(patternOf(fill), `${fill}`).not.toBeNull()
    expect(new Set(fills).size).toBe(4)
    expectStatusPattern(fills[0], 'failed')
    expectStatusPattern(fills[1], 'broken')
    const legend = container.querySelector('[data-chart-legend]') as HTMLElement
    expect(legend.querySelectorAll('[data-legend-swatch]')).toHaveLength(4)
    expect(legend.querySelector('[data-legend-swatch]')?.getAttribute('fill')).toBe(fills[0])
  })

  it('two charts on one page do not share (and so cannot clobber) pattern ids', () => {
    const { container } = render(
      <>
        <TrendChart data={[POINT]} type="bar" />
        <TrendChart data={[POINT]} type="bar" />
      </>,
    )
    const ids = Array.from(container.querySelectorAll('pattern'), (p) => p.id)
    expect(ids.length).toBeGreaterThan(0)
    expect(new Set(ids).size).toBe(ids.length)
  })
})
