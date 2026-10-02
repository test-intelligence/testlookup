/**
 * Wave 2.6 fix round (R2-5) — the release-aligned axis ends where the data
 * does, and it is ticked from day 0 in even steps.
 *
 * The gate's "Pass rate by release" asks `chart-data` for 90 days. The API
 * answers every release over the WHOLE window (a release that started 70 days
 * ago has 71 returned days, most of them unmeasured), so the aligned axis ran
 * 0..70 while the longest measured run ended on day 25: 60 % of the plot was
 * empty, the lines were squeezed into its left third, and the line-end labels
 * sat at day 70, 130-200 px from the lines they named. Recharts' own tick
 * thinning then printed 1, 5, 10, 16, 22... under a caption that says day 0
 * is each release's start.
 */
import { describe, expect, it } from 'vitest'
import type { SeriesPoint } from '@/lib/viz/contracts'
import { addUtcDays } from './seriesAlignment'
import { buildMultiSeriesModel, type MultiSeriesInputSeries } from './multiSeriesModel'

const RATE = { kind: 'rate', title: 'Pass rate %' } as const
const WINDOW_END = '2026-09-18'

/**
 * A release as the API returns it for a 90-day window: every day from its
 * start to the window's end, measured for its first `length` days and
 * unmeasured after — the production fixture's shape.
 */
function release(key: string, startedDaysAgo: number, length: number): MultiSeriesInputSeries {
  const start = addUtcDays(WINDOW_END, -startedDaysAgo)
  const points: SeriesPoint[] = Array.from({ length: startedDaysAgo + 1 }, (_, day) => {
    const x = addUtcDays(start, day)
    return day < length ? { x, y: 80 + (day % 5), n: 40 } : { x, y: null, n: 0, measured: false, reason: 'no runs' }
  })
  return { key, label: key, points }
}

// The gate fixture: 2026.09 (10 days ago, 10 measured), 2026.08 (40 ago, 26), 2026.07 (70 ago, 20).
const GATE = [release('2026.09', 10, 10), release('2026.08', 40, 26), release('2026.07', 70, 20)]

describe('the aligned axis ends at the last day any release measured', () => {
  it('runs 0..25, not 0..70', () => {
    const model = buildMultiSeriesModel({ series: GATE, metric: RATE, alignment: 'release-start' })
    expect(model.xs[0]).toBe('0')
    expect(model.xs[model.xs.length - 1]).toBe('25')
    expect(model.lines.every((line) => line.points.length === 26)).toBe(true)
  })

  it('says so, and says what was left off', () => {
    const model = buildMultiSeriesModel({ series: GATE, metric: RATE, alignment: 'release-start' })
    expect(model.trimNote).toBe('The axis ends at day 25, the last day any release has a measured value; days 26–70 have none.')
  })

  it('every line still ends where it ends: the label names its last MEASURED day', () => {
    const model = buildMultiSeriesModel({ series: GATE, metric: RATE, alignment: 'release-start' })
    expect(model.lines.map((line) => line.last?.index)).toEqual([9, 25, 19])
  })

  it('a release whose range is longer than the axis is not "past its range" on the axis', () => {
    const model = buildMultiSeriesModel({ series: GATE, metric: RATE, alignment: 'release-start' })
    // 2026.09 has only 11 returned days (0..10): days 11..25 really are past its range.
    expect(model.rangeNote).toBe('2026.09 has 11 days; days 11–25 are past its range.')
  })

  it('nothing is trimmed when the last axis day is measured', () => {
    const full = [release('A', 10, 11), release('B', 5, 3)]
    const model = buildMultiSeriesModel({ series: full, metric: RATE, alignment: 'release-start' })
    expect(model.xs).toHaveLength(11)
    expect(model.trimNote).toBeNull()
  })

  it('a calendar axis is never trimmed: its days are the page window', () => {
    const model = buildMultiSeriesModel({ series: GATE, metric: RATE })
    expect(model.xs).toHaveLength(71)
    expect(model.trimNote).toBeNull()
  })

  it('a still-filling value counts as measured: the axis keeps its day', () => {
    const series = [release('A', 10, 4), { ...release('B', 10, 2) }]
    // B measures day 6 too, on the partial day.
    const b = series[1]
    const points = b.points.map((p, i) => (i === 6 ? { ...p, y: 50, n: 3, measured: true, reason: null } : p))
    const model = buildMultiSeriesModel({
      series: [series[0], { ...b, points }],
      metric: RATE,
      alignment: 'release-start',
      meta: { partial_day: points[6].x } as never,
    })
    expect(model.xs[model.xs.length - 1]).toBe('6')
  })
})

describe('the aligned axis is ticked from day 0 in even integer steps', () => {
  it('0, 5, 10, 15, 20, 25 for the gate', () => {
    const model = buildMultiSeriesModel({ series: GATE, metric: RATE, alignment: 'release-start' })
    expect(model.xTicks).toEqual(['0', '5', '10', '15', '20', '25'])
  })

  it('every day on a short axis', () => {
    const model = buildMultiSeriesModel({ series: [release('A', 3, 4), release('B', 2, 3)], metric: RATE, alignment: 'release-start' })
    expect(model.xTicks).toEqual(['0', '1', '2', '3'])
  })

  it('each tick is a day on the axis, one step apart, starting at 0', () => {
    for (const length of [2, 8, 13, 31, 64, 120, 366]) {
      const model = buildMultiSeriesModel({ series: [release('A', length, length), release('B', 3, 3)], metric: RATE, alignment: 'release-start' })
      const ticks = (model.xTicks ?? []).map(Number)
      expect(ticks[0]).toBe(0)
      const step = ticks[1] - ticks[0]
      expect(Number.isInteger(step) && step >= 1).toBe(true)
      expect(ticks.every((tick, i) => tick === i * step)).toBe(true)
      expect(ticks.every((tick) => model.xs.includes(String(tick)))).toBe(true)
      // Never more than eight intervals before Recharts thins them.
      expect(ticks.length).toBeLessThanOrEqual(9)
    }
  })

  it('a calendar axis keeps the kit default (no ticks of its own)', () => {
    const model = buildMultiSeriesModel({ series: GATE, metric: RATE })
    expect(model.xTicks).toBeNull()
  })
})
