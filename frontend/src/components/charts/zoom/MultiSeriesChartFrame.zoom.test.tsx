/**
 * VIZ-407 on the multi-series frame: a zoom slices the model the frame was
 * given, so the legend's hidden series, the "top 7 + Other" fold and every
 * line's colour survive it; the table shows the zoomed days; a release-aligned
 * axis speaks in relative days and is never offered as the page window.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import { ANNOUNCE_DEBOUNCE_MS, ChartAnnouncerProvider } from '../ChartAnnouncer'
import type { ChartState } from '../chartState'
import MultiSeriesChartFrame from '../MultiSeriesChartFrame'
import { HIDDEN_SUFFIX, OTHER_KEY, buildMultiSeriesModel, type MultiSeriesInputSeries, type MultiSeriesModel } from '../multiSeriesModel'
import { addUtcDays } from '../seriesAlignment'
import { RESET_ZOOM_LABEL } from './ChartRangeBrush'
import { PROMOTE_RELATIVE_REASON, type ChartZoomOptions } from './zoomModel'

const captured = vi.hoisted(() => ({ lines: [] as Record<string, unknown>[] }))

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  LineChart: ({ children }: { children: ReactNode }) => <svg data-testid="line-chart">{children}</svg>,
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  ReferenceLine: () => null,
  Tooltip: () => null,
  Line: (props: Record<string, unknown>) => {
    captured.lines.push(props)
    return <g data-testid="series-line" data-name={String(props.name)} />
  },
  usePlotArea: () => ({ x: 60, y: 16, width: 400, height: 200 }),
  useChartWidth: () => 600,
  useXAxisScale: () => undefined,
  useYAxisScale: () => (value: unknown) => 16 + (1 - (value as number) / 100) * 200,
}))

const READY: ChartState<unknown> = { status: 'ready', data: null, meta: null, revalidating: false }
const RATE = { kind: 'rate', title: 'Pass rate %' } as const
const D = (i: number) => addUtcDays('2026-03-01', i)

function suite(key: string, n: (i: number) => number, days = 10): MultiSeriesInputSeries {
  return {
    key,
    label: key,
    points: Array.from({ length: days }, (_, i) => {
      const size = n(i)
      return size === 0 ? { x: D(i), y: null, n: 0, measured: false, reason: 'no runs' } : { x: D(i), y: 80 + i, n: size }
    }),
  }
}

/** The Line props of the LAST render, one per drawn series. */
function lastLines(): Record<string, unknown>[] {
  const names = new Set<string>()
  const out: Record<string, unknown>[] = []
  for (let i = captured.lines.length - 1; i >= 0; i--) {
    const name = String(captured.lines[i].name)
    if (names.has(name)) break
    names.add(name)
    out.unshift(captured.lines[i])
  }
  return out
}

function mount(model: MultiSeriesModel, zoom: boolean | ChartZoomOptions = true) {
  return render(
    <ChartAnnouncerProvider>
      <MultiSeriesChartFrame title="Pass rate by suite" state={READY} model={model} headingLevel={2} animate={false} zoom={zoom} />
    </ChartAnnouncerProvider>,
  )
}

const startHandle = () => screen.getByRole('slider', { name: 'Start of zoom range' })
function zoomToLast(n: number) {
  fireEvent.keyDown(startHandle(), { key: 'End' })
  for (let i = 1; i < n; i++) fireEvent.keyDown(startHandle(), { key: 'ArrowLeft' })
}

beforeEach(() => {
  captured.lines = []
  useTimeWindowStore.setState({ days: 30 })
})
afterEach(() => vi.useRealTimers())

describe('MultiSeriesChartFrame — VIZ-407 zoom', () => {
  const three = buildMultiSeriesModel({
    series: [suite('payments', () => 100), suite('cart', () => 90), suite('search', () => 80)],
    metric: RATE,
    seriesNoun: 'suites',
  })

  it('draws no brush unless asked', () => {
    mount(three, false)
    expect(screen.queryByRole('slider')).toBeNull()
  })

  // Baseline review B: a line chart places its days as POINTS, edge to edge.
  it('hands the brush a point scale, and a sparkline of the shown lines only', () => {
    const { container } = mount(three)
    expect(container.querySelector('[data-chart-brush-track]')).toHaveAttribute('data-chart-brush-scale', 'point')
    const moves = () => container.querySelector('[data-chart-brush-spark]')?.getAttribute('d')?.match(/M/g)?.length ?? 0
    const before = moves()
    expect(before).toBeGreaterThanOrEqual(3)
    fireEvent.click(container.querySelector('[data-legend-series="cart"]') as HTMLElement)
    expect(moves()).toBeLessThan(before)
  })

  it('a hidden series stays hidden through a zoom and its reset', () => {
    const { container } = mount(three)
    fireEvent.click(container.querySelector('[data-legend-series="cart"]') as HTMLButtonElement)
    zoomToLast(3)
    expect(lastLines().map((l) => l.name)).toEqual(['payments', 'search'])
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    const table = screen.getByRole('table', { name: /data table/i })
    expect(within(table).getAllByRole('columnheader').map((th) => th.textContent)).toContain(`cart${HIDDEN_SUFFIX}`)
    // The table is the zoomed days, and says so.
    expect(within(table).getAllByRole('rowheader').map((th) => th.textContent)).toEqual([D(7), D(8), D(9)])
    expect(container.querySelector('[data-chart-zoom-table-note]')).toHaveTextContent('Zoomed to Mar 8–10, 2026')
    fireEvent.click(screen.getByRole('button', { name: RESET_ZOOM_LABEL }))
    expect(lastLines().map((l) => l.name)).toEqual(['payments', 'search'])
    expect(container.querySelector('[data-legend-series="cart"]')).toHaveAttribute('aria-pressed', 'false')
  })

  it('keeps the fold and every colour: the zoom never re-ranks the series on the zoomed days', () => {
    // `late` is small over the whole window (folded into Other) and the
    // biggest on days 8-9 alone.
    const nine = buildMultiSeriesModel({
      series: [
        ...Array.from({ length: 8 }, (_, k) => suite(`s${k}`, (i) => (i < 8 ? 100 + k : 1))),
        suite('late', (i) => (i < 8 ? 1 : 300)),
      ],
      metric: RATE,
    })
    const { container } = mount(nine)
    const before = lastLines().map((l) => [l.name, l.stroke, l.strokeDasharray])
    zoomToLast(2)
    expect(lastLines().map((l) => [l.name, l.stroke, l.strokeDasharray])).toEqual(before)
    expect(container.querySelector('[data-legend-series="late"]')).toBeNull()
    expect(container.querySelector(`[data-legend-series="${OTHER_KEY}"]`)).not.toBeNull()
  })

  it('announces the zoom, then the legend, each in its own words', () => {
    vi.useFakeTimers()
    const { container } = mount(three)
    const polite = () => container.querySelector('[data-chart-announcer="polite"]')?.textContent
    zoomToLast(3)
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS + 10)
    })
    expect(polite()).toBe('Pass rate by suite chart: zoomed to Mar 8–10, 2026')
    fireEvent.click(container.querySelector('[data-legend-series="cart"]') as HTMLButtonElement)
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS + 10)
    })
    expect(polite()).toBe('Pass rate by suite chart: cart hidden, 2 of 3 series shown')
    fireEvent.keyDown(startHandle(), { key: 'ArrowLeft' })
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DEBOUNCE_MS + 10)
    })
    expect(polite()).toBe('Pass rate by suite chart: zoomed to Mar 7–10, 2026')
  })

  it('on a release-aligned axis: relative days, and never the page window', () => {
    const aligned = buildMultiSeriesModel({
      series: [suite('R1', () => 100, 6), suite('R2', () => 100, 6)],
      metric: RATE,
      alignment: 'release-start',
    })
    mount(aligned, { applyAsWindow: { windowOptions: [1, 7, 14, 30, 90] } })
    expect(screen.getByRole('slider', { name: 'End of zoom range' })).toHaveAttribute('aria-valuetext', 'Day 5 since release start')
    zoomToLast(1)
    const apply = screen.getByRole('button', { name: 'Apply as time filter' })
    expect(apply).toHaveAttribute('aria-disabled', 'true')
    expect(apply).toHaveAccessibleDescription(PROMOTE_RELATIVE_REASON)
  })
})
