/**
 * Wave 2.6 fix round — the multi-series renderer's x axis and line-end labels.
 *
 *   R2-5   the release-aligned axis is ticked from day 0 in even steps, thinned
 *          evenly from the start; a line that ends before the axis does has
 *          its leader drawn from ITS last point, not from the plot's edge; the
 *          days left off the axis are named under the plot.
 *   R2-16  the calendar axis prints the kit's day label ("Mar 1"), as the
 *          brush under it and every other frame do — never the ISO key.
 *
 * Geometry is a stand-in point scale (jsdom lays nothing out); what is pinned
 * is what reaches Recharts and the DOM.
 */
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChartAnnouncerProvider } from './ChartAnnouncer'
import type { ChartState } from './chartState'
import MultiSeriesChartFrame from './MultiSeriesChartFrame'
import { addUtcDays } from './seriesAlignment'
import { LABEL_ROW, buildMultiSeriesModel, type MultiSeriesInputSeries, type MultiSeriesModel } from './multiSeriesModel'

const captured = vi.hoisted(() => ({ xAxis: null as Record<string, unknown> | null, xs: [] as string[] }))
const plot = { x: 60, y: 16, width: 400, height: 200 }

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  LineChart: ({ children }: { children: ReactNode }) => <svg data-testid="line-chart">{children}</svg>,
  CartesianGrid: () => null,
  XAxis: (props: Record<string, unknown>) => {
    captured.xAxis = props
    return null
  },
  YAxis: () => null,
  ReferenceLine: () => null,
  Tooltip: () => null,
  Line: () => null,
  usePlotArea: () => plot,
  useChartWidth: () => 600,
  // A point scale over the model's days: the first on the plot's left edge, the last on its right.
  useXAxisScale: () => (value: unknown) => {
    const index = captured.xs.indexOf(String(value))
    return index < 0 ? undefined : plot.x + (index * plot.width) / Math.max(1, captured.xs.length - 1)
  },
  useYAxisScale: () => (value: unknown) => plot.y + (1 - (value as number) / 100) * plot.height,
}))

const READY: ChartState<unknown> = { status: 'ready', data: null, meta: null, revalidating: false }
const RATE = { kind: 'rate', title: 'Pass rate %' } as const
const END = '2026-09-18'

/** Returned to the window's end, measured for its first `length` days (the API's shape). */
function release(key: string, startedDaysAgo: number, length: number): MultiSeriesInputSeries {
  const start = addUtcDays(END, -startedDaysAgo)
  return {
    key,
    label: key,
    points: Array.from({ length: startedDaysAgo + 1 }, (_, day) => {
      const x = addUtcDays(start, day)
      return day < length ? { x, y: 90 - day, n: 40 } : { x, y: null, n: 0, measured: false, reason: 'no runs' }
    }),
  }
}

function mount(model: MultiSeriesModel) {
  captured.xs = model.xs
  return render(
    <ChartAnnouncerProvider>
      <MultiSeriesChartFrame title="Pass rate by release" state={READY} model={model} headingLevel={3} animate={false} />
    </ChartAnnouncerProvider>,
  )
}

const leaderOf = (container: HTMLElement, key: string) =>
  (container.querySelector(`[data-direct-label="${key}"] [data-direct-label-leader]`)?.getAttribute('points') ?? '')
    .trim()
    .split(/\s+/)
    .map((pair) => pair.split(',').map(Number))

const GATE = [release('2026.09', 10, 10), release('2026.08', 40, 26), release('2026.07', 70, 20)]
const aligned = buildMultiSeriesModel({ series: GATE, metric: RATE, alignment: 'release-start', seriesNoun: 'releases' })

beforeEach(() => {
  captured.xAxis = null
})

describe('R2-5: the release-aligned axis', () => {
  it('hands Recharts the model\'s ticks, thinned evenly from day 0', () => {
    mount(aligned)
    expect(captured.xAxis?.ticks).toEqual(['0', '5', '10', '15', '20', '25'])
    expect(captured.xAxis?.interval).toBe('equidistantPreserveStart')
  })

  it('names the days it left off, under the plot', () => {
    const { container } = mount(aligned)
    expect(container.querySelector('[data-chart-trim-note]')?.textContent).toBe(aligned.trimNote)
  })

  it('leads each label from its line\'s own last point', () => {
    const { container } = mount(aligned)
    const edge = plot.x + plot.width
    const step = plot.width / (aligned.xs.length - 1)
    for (const line of aligned.lines) {
      const points = leaderOf(container, line.key)
      const lastX = plot.x + (line.last?.index ?? 0) * step
      expect(points, line.key).toHaveLength(3)
      // Clear of the line's end by the same gap the gutter keeps from the edge…
      expect(points[0][0], line.key).toBeCloseTo(lastX + LABEL_ROW.leaderStart, 6)
      // …flat at its height to the gutter, then one step to the label's row.
      expect(points[1]).toEqual([edge + LABEL_ROW.leaderTurn, points[0][1]])
      expect(points[2][0]).toBe(edge + LABEL_ROW.leaderEnd)
    }
    // 2026.09 ends on day 9 of 0..25: its leader starts 16 days left of the edge, not at it.
    expect(leaderOf(container, '2026.09')[0][0]).toBeLessThan(edge - 100)
  })

  it('a line that ends on the last day keeps the leader it always had', () => {
    const { container } = mount(aligned)
    const edge = plot.x + plot.width
    expect(leaderOf(container, '2026.08')[0][0]).toBe(edge + LABEL_ROW.leaderStart)
  })
})

describe('R2-16: the calendar axis prints the kit\'s day label', () => {
  const calendar = buildMultiSeriesModel({ series: GATE.slice(0, 2), metric: RATE })

  it('formats each tick as "Sep 6", never "2026-09-06"', () => {
    mount(calendar)
    const format = captured.xAxis?.tickFormatter as ((value: string) => string) | undefined
    expect(format?.('2026-09-06')).toBe('Sep 6')
    expect(captured.xAxis?.ticks).toBeUndefined()
  })

  it('the aligned axis prints its days as they are', () => {
    mount(aligned)
    const format = captured.xAxis?.tickFormatter as ((value: string) => string) | undefined
    expect(format === undefined || format('5') === '5').toBe(true)
  })
})
