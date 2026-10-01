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

  it('durationTickLabel: a seconds axis with sub-second steps prints seconds to the step\'s precision', () => {
    const half = durationAxis(1_873)
    expect(half?.ticks.map((value) => durationTickLabel(value, half))).toEqual(['0s', '0.5s', '1.0s', '1.5s', '2.0s'])
    const quarter = durationAxis(901)
    // 0..1000 in 250 ms steps reaches a second: printed in seconds, to the step's two places.
    expect(quarter?.ticks.map((value) => durationTickLabel(value, quarter))).toEqual(['0s', '0.25s', '0.50s', '0.75s', '1.00s'])
  })

  it('durationTickLabel: a minutes axis keeps the kit\'s minutes, with a zero in seconds', () => {
    const axis = durationAxis(250_000)
    const labels = axis?.ticks.map((value) => durationTickLabel(value, axis)) ?? []
    expect(labels[0]).toBe('0s')
    expect(labels.slice(1).every((label) => /^\d+m \d+s$/.test(label))).toBe(true)
  })
})
