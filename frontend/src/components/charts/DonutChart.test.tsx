/**
 * VIZ-401 — the donut as it is DRAWN: fixed slice order, the total in the
 * centre, a count-and-percent label per slice, a tiny slice whose label moved
 * to the legend, and an all-zero breakdown that hands over to the frame's
 * filtered-empty state instead of drawing a ring of nothing.
 *
 * Recharts is replaced by stand-ins that echo the props the component passes
 * (and CALL its label renderers, as Recharts does), so the test reads what the
 * component asked Recharts to draw.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { ChartSeries, SeriesChart } from '@/lib/viz/contracts'
import DonutChart, { DonutPlot, DonutTooltip, handOverWhenEmpty } from './DonutChart'
import { statusDonutModel } from './DonutChart.model'
import type { ChartResponse, ChartState } from './chartState'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  PieChart: ({ children }: { children?: ReactNode }) => (
    <svg data-chart="pie">
      <g>{children}</g>
    </svg>
  ),
  Pie: ({
    children,
    data,
    label,
    dataKey,
    isAnimationActive,
  }: {
    children?: ReactNode
    data?: { name: string }[]
    label?: ((props: object) => ReactNode) | boolean
    dataKey?: string
    isAnimationActive?: boolean
  }) => (
    <g data-pie="" data-datakey={dataKey} data-animate={String(isAnimationActive)}>
      {children}
      {typeof label === 'function' &&
        (data ?? []).map((_, index) => (
          <g key={index}>{label({ cx: 100, cy: 100, midAngle: 45, outerRadius: 80, index })}</g>
        ))}
    </g>
  ),
  Cell: ({ fill }: { fill?: string }) => <path data-cell="" fill={fill} />,
  Label: ({ content }: { content?: (props: object) => ReactNode }) => (
    <g data-pie-label="">{typeof content === 'function' ? content({ viewBox: { cx: 100, cy: 100 } }) : null}</g>
  ),
  Tooltip: () => null,
  Legend: ({ content }: { content?: () => ReactNode }) => (
    <div data-legend-host="">{typeof content === 'function' ? content() : null}</div>
  ),
}))

const STORY = { passed: 880, failed: 60, broken: 20, skipped: 40 }

function statusSeries(counts: Record<string, number>): ChartSeries {
  const chart: SeriesChart = {
    kind: 'series',
    dimensions: ['status'],
    x_type: 'category',
    series: [
      {
        key: 'executions',
        label: 'Executions',
        points: Object.entries(counts).map(([status, value]) => ({ x: status, y: value, n: value })),
      },
    ],
  }
  return chart
}

const readyState = (counts: Record<string, number>): ChartState<ChartResponse> => ({
  status: 'ready',
  data: { meta: null, series: statusSeries(counts) },
  meta: null,
  revalidating: false,
})

describe('DonutPlot', () => {
  it('draws one slice per status, in the fixed order, with the total in the centre', () => {
    const { container } = render(<DonutPlot title="Test donut" model={statusDonutModel(STORY)} animate={false} centreCaption="executions" />)
    expect(container.querySelectorAll('[data-cell]')).toHaveLength(4)
    expect(container.querySelector('[data-donut]')?.getAttribute('data-donut-total')).toBe('1000')
    const centre = container.querySelector('[data-donut-centre]')
    expect(centre?.textContent).toContain('1,000')
    expect(centre?.textContent).toContain('executions')
    // The PADDED arc is what is drawn; the values stay true (see the model tests).
    expect(container.querySelector('[data-pie]')?.getAttribute('data-datakey')).toBe('arc')
  })

  it('labels every slice with its count and percent', () => {
    const { container } = render(<DonutPlot title="Test donut" model={statusDonutModel(STORY)} animate={false} />)
    const labels = Array.from(container.querySelectorAll('[data-donut-slice-label]'), (node) => node.textContent)
    expect(labels).toEqual(['880 (88.0%)', '60 (6.0%)', '20 (2.0%)', '40 (4.0%)'])
  })

  it('moves a sub-2% slice’s label off the arc and into the legend, with its true value', () => {
    const { container } = render(<DonutPlot title="Test donut" model={statusDonutModel({ passed: 9_950, failed: 50 })} animate={false} />)
    const drawn = Array.from(container.querySelectorAll('[data-donut-slice-label]'), (node) =>
      node.getAttribute('data-donut-slice-label'),
    )
    expect(drawn).toEqual(['passed'])
    const legend = container.querySelector('[data-chart-legend]') as HTMLElement
    expect(legend).toHaveTextContent('Failed 50 (0.5%)')
    expect(container.querySelector('[data-donut]')?.getAttribute('data-donut-legend-only')).toBe('1')
    // …and the slice is still drawn.
    expect(container.querySelectorAll('[data-cell]')).toHaveLength(2)
  })

  it('draws one status as a full ring, still labelled', () => {
    const { container } = render(<DonutPlot title="Test donut" model={statusDonutModel({ passed: 412 })} animate={false} />)
    expect(container.querySelector('[data-donut]')?.getAttribute('data-donut-full-ring')).toBe('true')
    expect(container.querySelector('[data-donut-slice-label]')?.textContent).toBe('412 (100.0%)')
  })

  it('gives every slice a patterned fill, so status is never colour alone', () => {
    const { container } = render(<DonutPlot title="Test donut" model={statusDonutModel(STORY)} animate={false} />)
    const fills = Array.from(container.querySelectorAll('[data-cell]'), (cell) => cell.getAttribute('fill'))
    expect(new Set(fills).size).toBe(4)
    for (const fill of fills) {
      const id = /^url\(#(.+)\)$/.exec(fill ?? '')?.[1]
      expect(id, `${fill}`).toBeTruthy()
      expect(document.getElementById(id as string)?.tagName.toLowerCase()).toBe('pattern')
    }
  })

  it('shows the caller’s empty text rather than a ring of nothing', () => {
    render(<DonutPlot title="Test donut" model={statusDonutModel({})} emptyText="No defect data" />)
    expect(screen.getByText('No defect data')).toBeInTheDocument()
  })
})

describe('DonutTooltip', () => {
  it('shows the true value of a padded slice, as text', () => {
    const model = statusDonutModel({ passed: 9_950, failed: 50 })
    const { container } = render(<DonutTooltip active payload={[{ payload: { slice: model.slices[1] } }]} />)
    expect(container.textContent).toBe('Failed 50 (0.5%)')
    // A label is text: nothing in it is parsed as markup.
    expect(container.querySelector('img')).toBeNull()
  })

  it('renders nothing when nothing is hovered', () => {
    const { container } = render(<DonutTooltip />)
    expect(container.innerHTML).toBe('')
  })
})

describe('DonutChart (inside its frame)', () => {
  it('mounts inside a ChartFrame and offers the table view of the plotted values', () => {
    render(<DonutChart title="Status distribution" state={readyState(STORY)} animate={false} />)
    const frame = document.querySelector('[data-chart-frame]') as HTMLElement
    expect(frame).not.toBeNull()
    expect(frame.getAttribute('data-chart-state')).toBe('ready')
    expect(screen.getByRole('heading', { level: 3, name: 'Status distribution' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    const rows = Array.from(document.querySelectorAll('tbody tr'), (row) =>
      Array.from(row.children, (cell) => cell.textContent),
    )
    expect(rows).toEqual([
      // Count AND share: the ring draws both on every arc, so a table with
      // only the counts is a lesser view of the same chart. Two units, two
      // formatters (`SeriesFormat`).
      ['Passed', '880', '88.0%'],
      ['Failed', '60', '6.0%'],
      ['Broken', '20', '2.0%'],
      ['Skipped', '40', '4.0%'],
    ])
  })

  it('hands an all-zero breakdown over to the filtered-empty state', () => {
    render(
      <DonutChart
        title="Status distribution"
        state={readyState({ passed: 0, failed: 0, broken: 0, skipped: 0 })}
        animate={false}
      />,
    )
    const frame = document.querySelector('[data-chart-frame]') as HTMLElement
    expect(frame.getAttribute('data-chart-state')).toBe('filtered-empty')
    expect(screen.getByText('No data matches the current filters')).toBeInTheDocument()
    expect(document.querySelector('[data-donut]')).toBeNull()
  })

  it('leaves a state that is not drawn alone', () => {
    const loading: ChartState<ChartResponse> = { status: 'loading' }
    expect(handOverWhenEmpty(loading, true)).toBe(loading)
  })
})
