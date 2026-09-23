/**
 * VIZ-403 — the pure half of the time-series chart. Every edge case the story
 * lists is a test here, so the rules survive a rewrite of the renderer.
 */
import { describe, expect, it } from 'vitest'
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import {
  AXIS_NOT_ZERO_LABEL,
  SVG_POINT_LIMIT,
  UTC_AXIS_CAPTION,
  buildTimeSeriesModel,
  localDayRange,
  pickRenderer,
  rateAxisDomain,
  releaseMarkers,
  releaseMarkerRows,
  timeSeriesFromChartData,
  timeSeriesFromTrends,
  timeSeriesToChartSeries,
  utcTodayNote,
} from './timeSeriesModel'

const meta = (over: Partial<EnvelopeMeta> = {}): EnvelopeMeta =>
  ({
    schema_version: 1,
    scope: {
      projects: [],
      releases: [],
      suites: [],
      window: { from: '2026-03-01', to: '2026-03-05', days: 5, timezone: 'UTC' },
    },
    totals: { matched_runs: 3, total_runs: 3, matched_executions: 30, total_executions: 30 },
    pass_rate_basis: 'executions',
    ignored_filters: [],
    truncated: false,
    truncated_total: null,
    measured: true,
    reason: null,
    includes_in_progress: 0,
    partial_day: null,
    generated_at: '2026-03-05T10:00:00Z',
    as_of: '2026-03-05T10:00:00Z',
    ...over,
  }) as EnvelopeMeta

const series = (key: string, points: SeriesChart['series'][number]['points']): SeriesChart => ({
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: [{ key, label: key, points }],
})

describe('timeSeriesFromTrends', () => {
  it('makes a day with no runs a GAP, never a zero pass rate', () => {
    const points = timeSeriesFromTrends([
      { date: '2026-03-01', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
      // A zero-run day: /metrics/trends zero-fills it and reports pass_rate 0.
      { date: '2026-03-02', passed: 0, failed: 0, skipped: 0, broken: 0, total: 0, pass_rate: 0 },
      { date: '2026-03-03', passed: 8, failed: 2, skipped: 0, broken: 0, total: 10, pass_rate: 80 },
    ])
    expect(points.map((p) => p.rate)).toEqual([90, null, 80])
    // The executions bar is a real zero — only the RATE is unknown.
    expect(points.map((p) => p.executions)).toEqual([10, 0, 10])
    expect(points[1].rateReason).toMatch(/no evaluated executions/i)
  })

  it('treats a day of nothing but skips as unmeasured, not 0%', () => {
    const points = timeSeriesFromTrends([
      { date: '2026-03-01', passed: 0, failed: 0, skipped: 7, broken: 0, total: 7, pass_rate: 0 },
    ])
    expect(points[0].rate).toBeNull()
    expect(points[0].executions).toBe(7)
  })

  it('fills the calendar so a missing day is still a bucket, and still a gap', () => {
    const points = timeSeriesFromTrends(
      [
        { date: '2026-03-01', passed: 10, failed: 0, skipped: 0, broken: 0, total: 10, pass_rate: 100 },
        { date: '2026-03-04', passed: 10, failed: 0, skipped: 0, broken: 0, total: 10, pass_rate: 100 },
      ],
      { from: '2026-03-01', to: '2026-03-04' },
    )
    expect(points.map((p) => p.x)).toEqual(['2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04'])
    expect(points.map((p) => p.rate)).toEqual([100, null, null, 100])
    expect(points[1].executions).toBeNull()
  })
})

describe('timeSeriesFromChartData', () => {
  it('reads y:null / measured:false as a gap and keeps the server reason', () => {
    const points = timeSeriesFromChartData({
      rate: series('pass_rate', [
        { x: '2026-03-01', y: 91.5, n: 20 },
        { x: '2026-03-02', y: null, n: 0, measured: false, reason: 'no evaluated executions in this bucket' },
      ]),
      executions: series('executions', [
        { x: '2026-03-01', y: 20, n: 20 },
        { x: '2026-03-02', y: 0, n: 0 },
      ]),
    })
    expect(points.map((p) => p.rate)).toEqual([91.5, null])
    expect(points[1].rateReason).toBe('no evaluated executions in this bucket')
    expect(points.map((p) => p.executions)).toEqual([20, 0])
  })

  it('unions the x axes of the two series so neither can drop a bucket', () => {
    const points = timeSeriesFromChartData({
      rate: series('pass_rate', [{ x: '2026-03-02', y: 80, n: 5 }]),
      executions: series('executions', [
        { x: '2026-03-01', y: 4, n: 4 },
        { x: '2026-03-02', y: 5, n: 5 },
      ]),
    })
    expect(points.map((p) => p.x)).toEqual(['2026-03-01', '2026-03-02'])
    expect(points[0].rate).toBeNull()
  })
})

describe('rateAxisDomain', () => {
  const rates = (values: (number | null)[]) => values.map((rate, i) => ({ x: `d${i}`, rate }))

  it('starts at zero when it is not zoomed', () => {
    expect(rateAxisDomain(rates([92, 96, 98]), { zoom: false })).toEqual({
      domain: [0, 100],
      startsAtZero: true,
      label: null,
    })
  })

  it('zooms to the data and then SAYS the axis does not start at 0', () => {
    const axis = rateAxisDomain(rates([92, 96, 98]), { zoom: true })
    expect(axis.domain[0]).toBeGreaterThan(0)
    expect(axis.domain[1]).toBeLessThanOrEqual(100)
    expect(axis.startsAtZero).toBe(false)
    expect(axis.label).toBe(AXIS_NOT_ZERO_LABEL)
  })

  it('claims no indicator when the zoomed domain happens to reach zero', () => {
    const axis = rateAxisDomain(rates([0, 50, 100]), { zoom: true })
    expect(axis.domain).toEqual([0, 100])
    expect(axis.startsAtZero).toBe(true)
    expect(axis.label).toBeNull()
  })

  it('falls back to the full axis when nothing is measured', () => {
    expect(rateAxisDomain(rates([null, null]), { zoom: true }).domain).toEqual([0, 100])
  })
})

describe('releaseMarkers', () => {
  const buckets = ['2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04']

  it('puts a release exactly on a bucket edge in the bucket it OPENS', () => {
    const { markers } = releaseMarkers(
      [{ id: 'r1', name: '1.4.0', date: '2026-03-04T00:00:00Z' }],
      buckets,
    )
    expect(markers).toHaveLength(1)
    expect(markers[0].x).toBe('2026-03-04')
    expect(markers[0].names).toEqual(['1.4.0'])
  })

  it('puts the last instant of a day in that day, not the next', () => {
    const { markers } = releaseMarkers(
      [{ id: 'r1', name: '1.3.0', date: '2026-03-03T23:59:59.999Z' }],
      buckets,
    )
    expect(markers[0].x).toBe('2026-03-03')
  })

  it('collapses two releases on one day into one marker naming both', () => {
    const { markers } = releaseMarkers(
      [
        { id: 'r1', name: '1.4.0', date: '2026-03-02T09:00:00Z' },
        { id: 'r2', name: '1.4.1', date: '2026-03-02T21:00:00Z' },
      ],
      buckets,
    )
    expect(markers).toHaveLength(1)
    expect(markers[0].names).toEqual(['1.4.0', '1.4.1'])
  })

  it('drops a release outside the window and COUNTS it rather than hiding it', () => {
    const { markers, outsideWindow } = releaseMarkers(
      [
        { id: 'r0', name: '1.2.0', date: '2026-02-27T12:00:00Z' },
        { id: 'r9', name: '2.0.0', date: '2026-03-09T12:00:00Z' },
        { id: 'r1', name: '1.4.0', date: '2026-03-02T09:00:00Z' },
      ],
      buckets,
    )
    expect(markers.map((m) => m.x)).toEqual(['2026-03-02'])
    expect(outsideWindow).toBe(2)
  })

  it('ignores a release with no usable date', () => {
    const { markers, outsideWindow } = releaseMarkers(
      [{ id: 'r1', name: 'unreleased', date: null }],
      buckets,
    )
    expect(markers).toEqual([])
    expect(outsideWindow).toBe(0)
  })

  it('lists the same markers as table rows', () => {
    const { markers } = releaseMarkers(
      [
        { id: 'r1', name: '1.4.0', date: '2026-03-02T09:00:00Z' },
        { id: 'r2', name: '1.5.0', date: '2026-03-04T09:00:00Z' },
      ],
      buckets,
    )
    expect(releaseMarkerRows(markers)).toEqual([
      { day: '2026-03-02', names: '1.4.0' },
      { day: '2026-03-04', names: '1.5.0' },
    ])
  })
})

describe('localDayRange', () => {
  it('names the local instants a UTC day covers', () => {
    const range = localDayRange('2026-03-10', { timeZone: 'Asia/Kolkata', locale: 'en-US' })
    // UTC+05:30 for the whole day.
    expect(range.start).toContain('05:30')
    expect(range.end).toContain('05:29')
    expect(range.offsetChanged).toBe(false)
  })

  it('survives a DST boundary: the two ends of one UTC day have different offsets', () => {
    // America/New_York moves EST → EDT at 02:00 local on 2026-03-08.
    const range = localDayRange('2026-03-08', { timeZone: 'America/New_York', locale: 'en-US' })
    expect(range.offsetChanged).toBe(true)
    expect(range.start).toContain('Mar 7')
    expect(range.start).toContain('19:00')
    expect(range.end).toContain('Mar 8')
    expect(range.end).toContain('19:59')
  })
})

describe('utcTodayNote', () => {
  it('explains an empty "today" to a viewer whose local date is already ahead', () => {
    const note = utcTodayNote({
      latest: { x: '2026-03-05', rate: null },
      now: new Date('2026-03-05T12:00:00Z'),
      timeZone: 'Pacific/Auckland', // UTC+13
    })
    expect(note).toContain('2026-03-05')
    expect(note).toContain('2026-03-06')
  })

  it('says nothing when the newest UTC day has data', () => {
    expect(
      utcTodayNote({
        latest: { x: '2026-03-05', rate: 91 },
        now: new Date('2026-03-05T12:00:00Z'),
        timeZone: 'Pacific/Auckland',
      }),
    ).toBeNull()
  })

  it('says nothing for a viewer whose local date matches the UTC date', () => {
    expect(
      utcTodayNote({
        latest: { x: '2026-03-05', rate: null },
        now: new Date('2026-03-05T12:00:00Z'),
        timeZone: 'UTC',
      }),
    ).toBeNull()
  })
})

describe('pickRenderer', () => {
  it('draws SVG up to the SVG point limit and hands anything larger to ECharts', () => {
    expect(SVG_POINT_LIMIT).toBe(366)
    expect(pickRenderer(1)).toBe('svg')
    expect(pickRenderer(366)).toBe('svg')
    expect(pickRenderer(367)).toBe('echarts')
  })
})

describe('buildTimeSeriesModel', () => {
  const points = [
    { date: '2026-03-01', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
    { date: '2026-03-02', passed: 0, failed: 0, skipped: 0, broken: 0, total: 0, pass_rate: 0 },
    { date: '2026-03-03', passed: 8, failed: 2, skipped: 0, broken: 0, total: 10, pass_rate: 80 },
  ]

  it('carries the UTC caption, so a reader knows the buckets are not local days', () => {
    const model = buildTimeSeriesModel({ points: timeSeriesFromTrends(points) })
    expect(model.caption).toBe(UTC_AXIS_CAPTION)
    expect(model.caption).toContain('UTC')
  })

  it('marks the partial day the server named, and only that one', () => {
    const model = buildTimeSeriesModel({
      points: timeSeriesFromTrends(points),
      meta: meta({ partial_day: '2026-03-03', includes_in_progress: 2 }),
    })
    expect(model.partialDay).toBe('2026-03-03')
    expect(model.points.map((p) => p.partial)).toEqual([false, false, true])
    expect(model.inProgressCount).toBe(2)
  })

  it('reports a single measured point so the renderer can draw a dot instead of a line', () => {
    const model = buildTimeSeriesModel({
      points: timeSeriesFromTrends([points[0]]),
    })
    expect(model.singlePoint).toBe(true)
    expect(model.isolated).toEqual(['2026-03-01'])
  })

  it('treats a measured day between two gaps as isolated, so it is not invisible', () => {
    const model = buildTimeSeriesModel({
      points: timeSeriesFromTrends([
        { date: '2026-03-01', passed: 0, failed: 0, skipped: 3, broken: 0, total: 3, pass_rate: 0 },
        { date: '2026-03-02', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
        { date: '2026-03-03', passed: 0, failed: 0, skipped: 3, broken: 0, total: 3, pass_rate: 0 },
      ]),
    })
    expect(model.singlePoint).toBe(true)
    expect(model.isolated).toEqual(['2026-03-02'])
  })

  it('counts the gaps rather than letting them read as zeros', () => {
    const model = buildTimeSeriesModel({ points: timeSeriesFromTrends(points) })
    expect(model.gaps).toBe(1)
    expect(model.points[1].rate).toBeNull()
  })

  it('places release markers on their buckets', () => {
    const model = buildTimeSeriesModel({
      points: timeSeriesFromTrends(points),
      releases: [{ id: 'r1', name: '1.4.0', date: '2026-03-02T00:00:00Z' }],
    })
    expect(model.markers.map((m) => m.x)).toEqual(['2026-03-02'])
  })

  it('picks the ECharts renderer past the SVG point limit', () => {
    const many = Array.from({ length: SVG_POINT_LIMIT + 1 }, (_, i) => ({
      x: `p${i}`,
      rate: 90,
      rateReason: null,
      executions: 1,
      n: 1,
      partial: false,
    }))
    expect(buildTimeSeriesModel({ points: many }).renderer).toBe('echarts')
  })
})

describe('timeSeriesToChartSeries', () => {
  it('hands the frame the SAME values the plot draws, gaps included', () => {
    const model = buildTimeSeriesModel({
      points: timeSeriesFromTrends([
        { date: '2026-03-01', passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 },
        { date: '2026-03-02', passed: 0, failed: 0, skipped: 0, broken: 0, total: 0, pass_rate: 0 },
      ]),
    })
    const chart = timeSeriesToChartSeries(model)
    expect(chart.kind).toBe('series')
    expect(chart.x_type).toBe('time')
    const rate = chart.series.find((s) => s.key === 'pass_rate')
    expect(rate?.points.map((p) => p.y)).toEqual([90, null])
    expect(rate?.points[1].measured).toBe(false)
    expect(rate?.points[1].reason).toBeTruthy()
    const executions = chart.series.find((s) => s.key === 'executions')
    expect(executions?.points.map((p) => p.y)).toEqual([10, 0])
  })
})

// ── fix round B ──────────────────────────────────────────────────────────────

describe('fix round B · 7 utcTodayNote and the viewer behind UTC', () => {
  const latest = { x: '2026-09-22', rate: null }

  it('never tells a viewer BEHIND UTC their date is "already" earlier', () => {
    // Niue is UTC-11: at 04:00 UTC on the 22nd their calendar still reads the
    // 21st. "already 2026-09-21" is a sentence about being ahead, and it is false.
    const note = utcTodayNote({ latest, now: new Date('2026-09-22T04:00:00Z'), timeZone: 'Pacific/Niue' })
    expect(note).not.toMatch(/already/)
    expect(note).toMatch(/still 2026-09-21/)
  })

  it('still says "already" to a viewer AHEAD of UTC', () => {
    // Kiritimati is UTC+14: at 11:30 UTC their calendar has already turned over.
    const note = utcTodayNote({ latest, now: new Date('2026-09-22T11:30:00Z'), timeZone: 'Pacific/Kiritimati' })
    expect(note).toMatch(/already 2026-09-23/)
  })

  it('says nothing when the calendar of the viewer agrees with UTC', () => {
    expect(utcTodayNote({ latest, now: new Date('2026-09-22T12:00:00Z'), timeZone: 'UTC' })).toBeNull()
  })
})

describe('fix round B · 7 a release with an unreadable date is counted, not dropped', () => {
  it('counts an unparseable date alongside the ones outside the window', () => {
    const buckets = ['2026-03-01', '2026-03-02']
    const placed = releaseMarkers(
      [
        { id: 'a', name: 'ok', date: '2026-03-01' },
        { id: 'b', name: 'outside', date: '2025-01-01' },
        { id: 'c', name: 'garbage', date: 'not-a-date' },
        // `null` is "not released yet" — a different thing, and still ignored.
        { id: 'd', name: 'unreleased', date: null },
      ],
      buckets,
    )
    expect(placed.markers.map((m) => m.x)).toEqual(['2026-03-01'])
    expect(placed.outsideWindow).toBe(2)
  })
})
