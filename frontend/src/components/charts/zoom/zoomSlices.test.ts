/**
 * The zoom slicers at their edges: a model with no days (the range has
 * nothing to clamp to) and a line shorter than the axis. The main behaviour
 * is in `zoomModel.test.ts`.
 */
import { describe, expect, it } from 'vitest'
import type { SeriesPoint } from '@/lib/viz/contracts'
import { durationBandPoints } from '../durationBuckets'
import { buildMultiSeriesModel, type MultiSeriesModel } from '../multiSeriesModel'
import { addUtcDays } from '../seriesAlignment'
import { sliceDurationBand, sliceMultiSeriesModel } from './zoomSlices'

const D = (i: number) => addUtcDays('2026-09-01', i)
const RATE = { kind: 'rate', title: 'Pass rate %' } as const
const daySeries = (points: SeriesPoint[]) => ({
  kind: 'series' as const,
  dimensions: ['day'],
  x_type: 'time' as const,
  series: [{ key: 'v', label: 'v', points }],
})

describe('a zoom over a model with no days', () => {
  it('the duration band is returned as it is (nothing to slice), not a crash', () => {
    const empty = durationBandPoints({ p50: daySeries([]), p95: daySeries([]) })
    expect(empty.points).toHaveLength(0)
    expect(sliceDurationBand(empty, { start: 2, end: 5 })).toBe(empty)
  })

  it('the multi-series model is returned as it is', () => {
    const empty = buildMultiSeriesModel({ series: [], metric: RATE })
    expect(empty.xs).toHaveLength(0)
    expect(sliceMultiSeriesModel(empty, { start: 0, end: 3 })).toBe(empty)
  })
})

describe('a line with fewer points than the axis', () => {
  it('has NO previous day (null), never `undefined` — which means "not zoomed"', () => {
    const full = buildMultiSeriesModel({
      series: [
        { key: 'a', label: 'a', points: Array.from({ length: 6 }, (_, i): SeriesPoint => ({ x: D(i), y: 90, n: 3 })) },
        { key: 'b', label: 'b', points: Array.from({ length: 6 }, (_, i): SeriesPoint => ({ x: D(i), y: 80, n: 3 })) },
      ],
      metric: RATE,
    })
    // A model handed over with line b cut short (3 of the axis's 6 days).
    const ragged: MultiSeriesModel = {
      ...full,
      lines: full.lines.map((line) => (line.key === 'b' ? { ...line, points: line.points.slice(0, 3) } : line)),
    }
    const slice = sliceMultiSeriesModel(ragged, { start: 4, end: 5 })
    const a = slice.lines.find((line) => line.key === 'a')
    const b = slice.lines.find((line) => line.key === 'b')
    expect(a?.precedingPoint).toBe(full.lines.find((line) => line.key === 'a')?.points[3])
    expect(b?.precedingPoint).toBeNull()
    expect(b?.points).toHaveLength(0)
  })
})
