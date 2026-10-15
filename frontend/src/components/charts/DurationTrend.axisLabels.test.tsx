/**
 * Wave 2.6 fix round — the duration trend's axis labels.
 *
 *   R2-16  the day axis printed the ISO key ("2026-09-06") while the brush
 *          under the same chart, and every other frame on the page, said
 *          "Sep 6": one format, the kit's day label.
 *   R2-17  the duration axis printed its zero as "0ms" among "2.5s … 10.0s":
 *          every tick is printed in the axis's own unit.
 */
import { render } from '@testing-library/react'
import { cloneElement, type ReactElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { SeriesChart } from '@/lib/viz/contracts'
import DurationTrend, { durationAxis } from './DurationTrend'
import { durationBandPoints, durationTickLabel } from './durationBuckets'

const axes: Record<string, unknown>[] = []

vi.mock('recharts', () => {
  const axis = (which: string) => (props: Record<string, unknown>) => {
    axes.push({ ...props, __axis: which })
    return null
  }
  return {
    usePlotArea: () => ({ x: 40, y: 8, width: 400, height: 200 }),
    ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    ComposedChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    CartesianGrid: () => null,
    Legend: () => null,
    XAxis: axis('x'),
    YAxis: axis('y'),
    Tooltip: () => null,
    Line: () => null,
    Area: () => null,
  }
})

const series = (points: { x: string; y: number; n: number }[]): SeriesChart => ({
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: [{ key: 'all', label: 'All', points }],
})
const days = ['2026-09-05', '2026-09-06', '2026-09-07']
const band = durationBandPoints({
  p50: series(days.map((x) => ({ x, y: 2_000, n: 9 }))),
  p95: series(days.map((x, i) => ({ x, y: i === 1 ? 9_400 : 4_000, n: 9 }))),
})

function drawn() {
  axes.length = 0
  render(<DurationTrend model={band} title="Duration trend" />)
  const last = (which: string) => axes.filter((a) => a.__axis === which).slice(-1)[0] as Record<string, unknown>
  const x = last('x')
  const y = last('y')
  return { x, y }
}

/** The text one y tick draws, through the element the chart hands Recharts. */
function tickText(tick: unknown, value: number): string {
  const { container, unmount } = render(<svg>{cloneElement(tick as ReactElement<Record<string, unknown>>, { x: 0, y: 0, payload: { value } })}</svg>)
  const text = container.textContent ?? ''
  unmount()
  return text
}

describe('R2-16: the day axis prints the kit\'s day label', () => {
  it('"Sep 6", never "2026-09-06"', () => {
    const format = drawn().x.tickFormatter as ((value: string) => string) | undefined
    expect(format?.('2026-09-06')).toBe('Sep 6')
  })
})

describe('R2-17: every duration tick is printed in the axis\'s unit', () => {
  it('a seconds axis prints its zero as "0s", among "2.5s … 10.0s"', () => {
    const { y } = drawn()
    expect(y.ticks).toEqual([0, 2_500, 5_000, 7_500, 10_000])
    expect((y.ticks as number[]).map((value) => tickText(y.tick, value))).toEqual(['0s', '2.5s', '5.0s', '7.5s', '10.0s'])
  })

  it('durationTickLabel: a millisecond axis stays in ms, zero included', () => {
    const axis = durationAxis(400)
    expect(axis?.ticks.map((value) => durationTickLabel(value, axis))).toEqual(['0ms', '100ms', '200ms', '300ms', '400ms'])
  })

  it('durationTickLabel: a fractional millisecond step keeps its places, no tick rounds to a value it is not', () => {
    // formatDuration rounds a fractional ms value from 10 ms up (an interpolated p95); a 2.5 ms step must not.
    const axis = { domain: [0, 20] as [number, number], step: 2.5 }
    expect([0, 2.5, 10, 12.5].map((value) => durationTickLabel(value, axis))).toEqual(['0ms', '2.5ms', '10.0ms', '12.5ms'])
  })

  it('durationTickLabel: a seconds axis with sub-second steps prints seconds to the step\'s precision', () => {
    const half = durationAxis(1_873)
    expect(half?.ticks.map((value) => durationTickLabel(value, half))).toEqual(['0s', '0.5s', '1.0s', '1.5s', '2.0s'])
    const quarter = durationAxis(901)
    // 0..1000 in 250 ms steps reaches a second: printed in seconds, to the step's two places.
    expect(quarter?.ticks.map((value) => durationTickLabel(value, quarter))).toEqual(['0s', '0.25s', '0.50s', '0.75s', '1.00s'])
  })

  it('durationTickLabel: a minutes axis steps on the clock (2m, not 1m 40s / 4m 10s), with a zero in seconds', () => {
    // Regression (W02 baselines): the decimal ladder ticked a 250 s axis at 100 s steps (1m 40s, 3m 20s) and a
    // 16-minute one at 250 s steps (4m 10s, 8m 20s, 12m 30s).
    const label = (max: number) => {
      const axis = durationAxis(max)
      return axis?.ticks.map((value) => durationTickLabel(value, axis))
    }
    expect(label(250_000)).toEqual(['0s', '2m', '4m', '6m'])
    expect(label(1_000_000)).toEqual(['0s', '5m', '10m', '15m', '20m'])
    expect(label(70_000)).toEqual(['0s', '30s', '1m', '1m 30s'])
    expect(label(5_400_000)).toEqual(['0s', '30m', '1h', '1h 30m'])
  })
})
