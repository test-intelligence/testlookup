/**
 * VIZ-406 — the pure half of duration analysis: log-spaced bucket edges with an
 * overflow bucket, the excluded-count statement, and the p50/p95 band.
 */
import { describe, expect, it } from 'vitest'
import type { SeriesChart } from '@/lib/viz/contracts'
import {
  BUCKET_STEPS,
  MAX_FINITE_BUCKETS,
  MIN_FINITE_BUCKETS,
  UNDERFLOW_PREFIX,
  bandToChartSeries,
  buildDurationHistogram,
  durationBandPoints,
  excludedStatement,
  histogramToChartSeries,
  logBucketEdges,
  rankSlowestTests,
  slowestToChartSeries,
} from './durationBuckets'

const series = (points: SeriesChart['series'][number]['points']): SeriesChart => ({
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: [{ key: 'duration', label: 'duration', points }],
})

describe('logBucketEdges', () => {
  it('uses 1-2-5 steps per decade, so every edge is a round duration', () => {
    expect(BUCKET_STEPS).toEqual([1, 2, 5])
    const edges = logBucketEdges([1, 3, 40, 900])
    for (const edge of edges) {
      const mantissa = Number(edge.toExponential().split('e')[0])
      expect(BUCKET_STEPS).toContain(mantissa)
    }
  })

  it('opens at or below the smallest value, including a sub-millisecond one', () => {
    const edges = logBucketEdges([0.4, 3, 40, 900])
    expect(edges[0]).toBe(0.2)
    expect(edges[0]).toBeLessThanOrEqual(0.4)
  })

  it('rises monotonically', () => {
    const edges = logBucketEdges([0.4, 3, 40, 900])
    for (let i = 1; i < edges.length; i++) expect(edges[i]).toBeGreaterThan(edges[i - 1])
  })

  it('closes just above the bulk, not above one extreme outlier', () => {
    const bulk = Array.from({ length: 99 }, (_, i) => 10 + i * 5) // 10 ms … 500 ms
    const edges = logBucketEdges([...bulk, 3_600_000]) // one hour-long test
    // The top edge answers for the bulk; the outlier lands in the overflow bucket.
    expect(edges[edges.length - 1]).toBeLessThanOrEqual(1000)
  })

  it('never returns fewer than the minimum number of buckets, even for identical values', () => {
    const edges = logBucketEdges([100, 100, 100])
    expect(edges.length - 1).toBeGreaterThanOrEqual(MIN_FINITE_BUCKETS)
  })

  it('caps the ladder so a 12-decade spread does not become 36 bars', () => {
    const edges = logBucketEdges([0.001, 1_000_000_000])
    expect(edges.length - 1).toBeLessThanOrEqual(MAX_FINITE_BUCKETS)
  })

  it('has no edges at all when nothing is measurable', () => {
    expect(logBucketEdges([])).toEqual([])
    expect(logBucketEdges([0, -1, null, undefined, Number.NaN])).toEqual([])
  })
})

describe('buildDurationHistogram', () => {
  it('counts a sub-millisecond value into the bucket its edges promise', () => {
    const histogram = buildDurationHistogram([0.4, 3, 40, 900])
    const first = histogram.buckets[0]
    expect(first.from).toBe(0.2)
    expect(first.to).toBe(0.5)
    expect(first.count).toBe(1)
    expect(first.label).toBe('0.2ms – 0.5ms')
  })

  it('puts one extreme outlier in the overflow bucket instead of flattening the rest', () => {
    const bulk = Array.from({ length: 99 }, (_, i) => 10 + i * 5)
    const histogram = buildDurationHistogram([...bulk, 3_600_000])
    const overflow = histogram.buckets[histogram.buckets.length - 1]
    expect(overflow.overflow).toBe(true)
    expect(overflow.to).toBeNull()
    expect(overflow.count).toBe(1)
    expect(overflow.label.startsWith('≥')).toBe(true)
    // The bulk is still spread over several bars, not crushed into one.
    const populated = histogram.buckets.filter((b) => !b.overflow && b.count > 0)
    expect(populated.length).toBeGreaterThan(1)
  })

  it('excludes missing and zero durations AND counts them', () => {
    const histogram = buildDurationHistogram([null, undefined, Number.NaN, 0, -5, 100, 200])
    expect(histogram.counted).toBe(2)
    expect(histogram.excluded).toBe(5)
    expect(histogram.missing).toBe(4)
    expect(histogram.zero).toBe(1)
    expect(histogram.total).toBe(7)
  })

  it('states the excluded count in the chart’s own words', () => {
    const histogram = buildDurationHistogram([...Array(214).fill(null), 100])
    expect(histogram.excludedStatement).toBe('214 executions without duration')
  })

  it('every counted value lands in exactly one bucket', () => {
    const values = [0.4, 0.9, 3, 12, 40, 99, 900, 3_600_000]
    const histogram = buildDurationHistogram(values)
    const summed = histogram.buckets.reduce((total, bucket) => total + bucket.count, 0)
    expect(summed).toBe(histogram.counted)
    expect(histogram.counted).toBe(values.length)
  })

  it('is empty, not zero-filled, when nothing has a duration', () => {
    const histogram = buildDurationHistogram([null, 0])
    expect(histogram.buckets).toEqual([])
    expect(histogram.excludedStatement).toBe('2 executions without duration, including 1 recorded as 0ms')
  })
})

describe('excludedStatement', () => {
  it('says nothing when nothing was excluded', () => {
    expect(excludedStatement({ missing: 0, zero: 0 })).toBeNull()
  })

  it('reads as one execution in the singular', () => {
    expect(excludedStatement({ missing: 1, zero: 0 })).toBe('1 execution without duration')
  })

  it('breaks out zero-duration executions, which are excluded for a different reason', () => {
    expect(excludedStatement({ missing: 200, zero: 14 })).toBe(
      '214 executions without duration, including 14 recorded as 0ms',
    )
  })
})

describe('durationBandPoints', () => {
  it('shades between p50 and p95', () => {
    const band = durationBandPoints({
      p50: series([
        { x: '2026-03-01', y: 100, n: 10 },
        { x: '2026-03-02', y: 120, n: 10 },
      ]),
      p95: series([
        { x: '2026-03-01', y: 400, n: 10 },
        { x: '2026-03-02', y: 500, n: 10 },
      ]),
    })
    expect(band.points.map((p) => [p.low, p.high])).toEqual([
      [100, 400],
      [120, 500],
    ])
    expect(band.inverted).toBe(0)
    expect(band.notice).toBeNull()
  })

  it('draws the band as reported when the data says p95 is BELOW p50, and says so', () => {
    const band = durationBandPoints({
      p50: series([{ x: '2026-03-01', y: 400, n: 10 }]),
      p95: series([{ x: '2026-03-01', y: 100, n: 10 }]),
    })
    // The series keep their own values: nothing is swapped or clamped.
    expect(band.points[0].p50).toBe(400)
    expect(band.points[0].p95).toBe(100)
    // The shaded band still has a low and a high, so it is drawable.
    expect(band.points[0].low).toBe(100)
    expect(band.points[0].high).toBe(400)
    expect(band.points[0].inverted).toBe(true)
    expect(band.inverted).toBe(1)
    expect(band.notice).toContain('1 day')
  })

  it('leaves an unmeasured bucket a gap on both lines and in the band', () => {
    const band = durationBandPoints({
      p50: series([
        { x: '2026-03-01', y: 100, n: 10 },
        { x: '2026-03-02', y: null, n: 0, measured: false, reason: 'no execution in this bucket carries a duration' },
      ]),
      p95: series([
        { x: '2026-03-01', y: 400, n: 10 },
        { x: '2026-03-02', y: null, n: 0, measured: false, reason: 'no execution in this bucket carries a duration' },
      ]),
    })
    expect(band.points[1].p50).toBeNull()
    expect(band.points[1].p95).toBeNull()
    expect(band.points[1].low).toBeNull()
    expect(band.points[1].high).toBeNull()
    expect(band.points[1].reason).toContain('no execution')
  })

  it('unions the two x axes so a bucket only one percentile reported is still drawn', () => {
    const band = durationBandPoints({
      p50: series([{ x: '2026-03-02', y: 100, n: 1 }]),
      p95: series([
        { x: '2026-03-01', y: 400, n: 1 },
        { x: '2026-03-02', y: 500, n: 1 },
      ]),
    })
    expect(band.points.map((p) => p.x)).toEqual(['2026-03-01', '2026-03-02'])
    expect(band.points[0].p50).toBeNull()
    expect(band.points[0].low).toBeNull()
  })
})

describe('rankSlowestTests', () => {
  const rows = [
    { name: 'a', p95: 100, runs: 3 },
    { name: 'b', p95: 900, runs: 7 },
    { name: 'c', p95: null, runs: 2 },
    { name: 'd', p95: 500, runs: 1 },
  ]

  it('ranks by p95 descending and keeps the run count beside each', () => {
    const ranked = rankSlowestTests(rows)
    expect(ranked.rows.map((r) => r.name)).toEqual(['b', 'd', 'a', 'c'])
    expect(ranked.rows[0].runs).toBe(7)
  })

  it('sinks an unmeasured p95 to the bottom rather than ranking it as zero', () => {
    const ranked = rankSlowestTests(rows)
    expect(ranked.rows[ranked.rows.length - 1].name).toBe('c')
    expect(ranked.rows[ranked.rows.length - 1].p95).toBeNull()
  })

  it('shows the 20 slowest by default and reports how many there really were', () => {
    const many = Array.from({ length: 50 }, (_, i) => ({ name: `t${i}`, p95: i, runs: 1 }))
    const ranked = rankSlowestTests(many)
    expect(ranked.rows).toHaveLength(20)
    expect(ranked.shown).toBe(20)
    expect(ranked.total).toBe(50)
    expect(ranked.truncated).toBe(true)
  })
})

describe('the C3 series each duration chart hands its frame', () => {
  it('turns the histogram into one bar series keyed by the bucket LABEL', () => {
    const chart = histogramToChartSeries(buildDurationHistogram([0.4, 3, 12, 3_600_000]))
    expect(chart.x_type).toBe('category')
    expect(chart.series[0].points[0].x).toBe('0.2ms – 0.5ms')
    const total = chart.series[0].points.reduce((sum, point) => sum + (point.y ?? 0), 0)
    expect(total).toBe(4)
  })

  it('keeps an unmeasured percentile a null WITH its reason, never a zero', () => {
    const chart = bandToChartSeries(
      durationBandPoints({
        p50: series([
          { x: '2026-03-01', y: null, n: 0, measured: false, reason: 'no execution in this bucket carries a duration' },
        ]),
        p95: series([
          { x: '2026-03-01', y: null, n: 0, measured: false, reason: 'no execution in this bucket carries a duration' },
        ]),
      }),
    )
    const point = chart.series[0].points[0]
    expect(point.y).toBeNull()
    expect(point.measured).toBe(false)
    expect(point.reason).toContain('no execution')
  })

  it('carries the run count of each slow test as its sample size', () => {
    const chart = slowestToChartSeries(rankSlowestTests([{ name: 'cart', p95: 900, runs: 40 }]))
    expect(chart.series[0].points[0]).toMatchObject({ x: 'cart', y: 900, n: 40 })
    expect(chart.series[1].points[0].y).toBe(40)
  })

  it('marks a test with no measured p95 as unmeasured rather than ranking it at zero', () => {
    const chart = slowestToChartSeries(rankSlowestTests([{ name: 'quiet', p95: null, runs: 2 }]))
    expect(chart.series[0].points[0].y).toBeNull()
    expect(chart.series[0].points[0].measured).toBe(false)
  })
})

// ── fix round B ──────────────────────────────────────────────────────────────

describe('fix round B · 7 the ladder has no hole under its first edge', () => {
  it('counts a duration below the first edge in a bucket whose label INCLUDES it', () => {
    const model = buildDurationHistogram([0.0004, 1, 2, 5])
    // Every counted value must lie inside the bucket that counts it. That is
    // the invariant the old comment CLAIMED and the placement loop broke:
    // `edges[0]` is the last ladder edge <= the smallest value, and when the
    // smallest value is under the ladder's own floor there is no such edge.
    const underflow = model.buckets.filter((bucket) => bucket.underflow)
    expect(underflow).toHaveLength(1)
    expect(underflow[0].from).toBe(0)
    expect(underflow[0].to).toBe(model.buckets[1].from)
    expect(underflow[0].count).toBe(1)
    expect(underflow[0].label).toBe(`${UNDERFLOW_PREFIX}0.001ms`)
    // …and it is the FIRST bucket: the axis stays sorted.
    expect(model.buckets[0]).toBe(underflow[0])
    // The bucket that used to swallow it now counts only what it names.
    const mislabelled = model.buckets.find((bucket) => bucket.label === '0.001ms – 0.002ms')
    expect(mislabelled?.count ?? 0).toBe(0)
    // Nothing is lost and nothing is double counted.
    expect(model.buckets.reduce((sum, bucket) => sum + bucket.count, 0)).toBe(model.counted)
  })

  it('adds no underflow bucket when every value is on the ladder', () => {
    const model = buildDurationHistogram([1, 2, 5, 40])
    expect(model.buckets.some((bucket) => bucket.underflow)).toBe(false)
  })

  it('keeps every counted value inside the range its bucket names', () => {
    const values = [0.0004, 0.0009, 1, 2, 5, 40, 3_600_000]
    const model = buildDurationHistogram(values)
    for (const value of values) {
      const bucket = model.buckets.find((b) => value >= b.from && (b.to === null || value < b.to))
      expect(bucket, `${value} has no bucket`).toBeDefined()
    }
  })
})

describe('fix round B · 7 the band series carries its real sample size', () => {
  it('does not declare n: 0 on a measured percentile', () => {
    const band = durationBandPoints({
      p50: series([{ x: '2026-03-01', y: 100, n: 37 }]),
      p95: series([{ x: '2026-03-01', y: 400, n: 41 }]),
    })
    expect(band.points[0].n50).toBe(37)
    expect(band.points[0].n95).toBe(41)
    const chart = bandToChartSeries(band)
    expect(chart.series[0].points[0].n).toBe(37)
    expect(chart.series[1].points[0].n).toBe(41)
  })
})
