/**
 * VIZ-407 on the p50/p95 duration trend: an opt-in zoom that slices the BUILT
 * band (never rebuilds it from the zoomed days), with the plot, the table, the
 * summary and the figure's p95-below-p50 notice all speaking of the days in view.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SeriesChart, SeriesPoint } from '@/lib/viz/contracts'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import DurationChartFrame from '../DurationChartFrame'
import { durationBandPoints } from '../durationBuckets'
import { addUtcDays } from '../seriesAlignment'
import { RESET_ZOOM_LABEL } from './ChartRangeBrush'

const captured = vi.hoisted(() => ({ chartData: [] as { x?: string }[], yDomain: undefined as unknown, yTicks: undefined as unknown }))

vi.mock('recharts', () => {
  const pass = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    usePlotArea: () => ({ x: 40, y: 8, width: 400, height: 200 }),
    ResponsiveContainer: pass,
    ComposedChart: ({ children, data }: { children?: ReactNode; data?: { x?: string }[] }) => {
      if (data) captured.chartData = data
      return <div data-testid="chart">{children}</div>
    },
    CartesianGrid: () => null,
    Legend: () => null,
    XAxis: () => null,
    YAxis: ({ domain, ticks }: { domain?: unknown; ticks?: unknown }) => {
      captured.yDomain = domain
      captured.yTicks = ticks
      return null
    },
    Tooltip: () => null,
    Line: () => null,
    Area: () => null,
  }
})

const D = (i: number) => addUtcDays('2026-03-01', i)
const series = (points: SeriesPoint[]): SeriesChart => ({ kind: 'series', dimensions: ['day'], x_type: 'time', series: [{ key: 'v', label: 'v', points }] })
// Ten days; p95 is below p50 on days 1 and 2 only.
const band = durationBandPoints({
  p50: series(Array.from({ length: 10 }, (_, i) => ({ x: D(i), y: 100, n: 10 }))),
  p95: series(Array.from({ length: 10 }, (_, i) => ({ x: D(i), y: i === 1 || i === 2 ? 80 : 400, n: 10 }))),
})
const READY = { status: 'ready' as const, data: {}, meta: null, revalidating: false }

function mount(zoom?: boolean) {
  return render(<DurationChartFrame kind="trend" band={band} title="Duration p50/p95" headingLevel={3} state={READY} zoom={zoom} />)
}
const startHandle = () => screen.getByRole('slider', { name: 'Start of zoom range' })

beforeEach(() => {
  captured.chartData = []
  captured.yDomain = undefined
  captured.yTicks = undefined
  useTimeWindowStore.setState({ days: 30 })
})

describe('DurationChartFrame trend — VIZ-407 zoom', () => {
  it('draws no brush unless asked', () => {
    mount()
    expect(screen.queryByRole('slider')).toBeNull()
    expect(band.inverted).toBe(2)
  })

  it('zooms the plot and the table to the days in view, and says so', () => {
    const { container } = mount(true)
    fireEvent.keyDown(startHandle(), { key: 'PageUp' }) // days 7..9
    expect(captured.chartData.map((row) => row.x)).toEqual([D(7), D(8), D(9)])
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    const table = screen.getByRole('table', { name: /data table/i })
    expect(within(table).getAllByRole('rowheader').map((th) => th.textContent)).toEqual([D(7), D(8), D(9)])
    expect(container.querySelector('[data-chart-zoom-note]')).toHaveTextContent('Zoomed to Mar 8–10, 2026: 3 of 10 days')
    expect(container.querySelector('[data-chart-summary]')?.textContent).toContain('zoomed to Mar 8–10, 2026')
  })

  it('states the inverted days IN VIEW, in the builder’s own words — and none when none are', () => {
    const { container } = mount(true)
    const figure = () => container.querySelector('[data-chart="duration-trend"]') as HTMLElement
    expect(figure()).toHaveTextContent('p95 was below p50 on 2 days; both are drawn as reported.')
    fireEvent.keyDown(startHandle(), { key: 'ArrowRight' })
    fireEvent.keyDown(startHandle(), { key: 'ArrowRight' }) // days 2..9: one inverted day
    expect(figure()).toHaveTextContent('p95 was below p50 on 1 day; both are drawn as reported.')
    fireEvent.keyDown(startHandle(), { key: 'ArrowRight' }) // days 3..9: none
    expect(figure()).not.toHaveTextContent('p95 was below p50')
    expect(figure()).not.toHaveAttribute('data-inverted')
    fireEvent.click(screen.getByRole('button', { name: RESET_ZOOM_LABEL }))
    expect(figure()).toHaveTextContent('p95 was below p50 on 2 days')
  })

  it('keeps EXACTLY the whole window’s duration axis while zoomed — a non-round maximum included (Wave 2.4 F6)', () => {
    // A band whose tallest day (p95 = 1,873 ms on day 1) is OUTSIDE the zoom,
    // so a slice fitted to its own data would redraw a 400 ms week as the top
    // — and an axis fixed at [0, 1873] would end on the maximum, with ticks
    // the unzoomed chart never showed.
    const tall = durationBandPoints({
      p50: series(Array.from({ length: 10 }, (_, i) => ({ x: D(i), y: 100, n: 10 }))),
      p95: series(Array.from({ length: 10 }, (_, i) => ({ x: D(i), y: i === 1 ? 1873 : 400, n: 10 }))),
    })
    render(<DurationChartFrame kind="trend" band={tall} title="Duration p50/p95" headingLevel={3} state={READY} zoom />)
    const unzoomed = { domain: captured.yDomain, ticks: captured.yTicks }
    expect(unzoomed).toEqual({ domain: [0, 2000], ticks: [0, 500, 1000, 1500, 2000] })
    fireEvent.keyDown(startHandle(), { key: 'PageUp' }) // days 7..9, all at 400 ms
    expect(captured.chartData.map((row) => row.x)).toEqual([D(7), D(8), D(9)])
    expect({ domain: captured.yDomain, ticks: captured.yTicks }).toEqual(unzoomed)
    fireEvent.click(screen.getByRole('button', { name: RESET_ZOOM_LABEL }))
    expect({ domain: captured.yDomain, ticks: captured.yTicks }).toEqual(unzoomed)
  })
})
