/**
 * VIZ-407 — the pure zoom. A zoom SLICES an already-built model; these pin
 * what a slice keeps from the whole window (axes, fold, colours, the trend
 * analysis) and what it re-derives over the days in view.
 */
import { describe, expect, it } from 'vitest'
import type { EnvelopeMeta, SeriesPoint } from '@/lib/viz/contracts'
import { analyzeTrend } from '@/lib/trendStats'
import { addUtcDays } from '../seriesAlignment'
import { bandNotice, durationBandPoints } from '../durationBuckets'
import { buildMultiSeriesModel, OTHER_KEY, type MultiSeriesInputSeries } from '../multiSeriesModel'
import { buildTimeSeriesModel, type TimeSeriesPoint } from '../timeSeriesModel'
import {
  CALENDAR_WORDS,
  PROMOTE_NOT_LATEST_REASON,
  PROMOTE_RELATIVE_REASON,
  RELATIVE_DAY_WORDS,
  clampRange,
  dayInWords,
  dayShort,
  durationBandMax,
  flaggedDaysText,
  isFullRange,
  markersOutsideRange,
  promoteDecision,
  rangeInWords,
  resolveZoom,
  sameRange,
  sliceDurationBand,
  sliceMultiSeriesModel,
  sliceTimeSeriesModel,
  sliceTrendAnalysis,
  zoomChangeLabel,
  zoomKeysFor,
  zoomNote,
  zoomOptionsOf,
  zoomResetLabel,
  zoomScopeLabel,
} from './zoomModel'

const D = (i: number) => addUtcDays('2026-03-01', i)
const point = (i: number, rate: number | null, n = 100): TimeSeriesPoint => ({
  x: D(i),
  rate,
  rateReason: rate === null ? 'no runs' : null,
  executions: rate === null ? 0 : n,
  n: rate === null ? 0 : n,
  partial: false,
})

const TEN = Array.from({ length: 10 }, (_, i) => point(i, 90 + (i % 3)))
const model10 = buildTimeSeriesModel({
  points: TEN,
  releases: [
    { id: 'a', name: 'R-early', date: D(1) },
    { id: 'b', name: 'R-late', date: D(8) },
  ],
})

describe('ranges', () => {
  it('clamps, orders and rounds a range; an empty axis has none', () => {
    expect(clampRange({ start: -3, end: 40 }, 10)).toEqual({ start: 0, end: 9 })
    expect(clampRange({ start: 6, end: 2 }, 10)).toEqual({ start: 2, end: 6 })
    expect(clampRange({ start: Number.NaN, end: 2.6 }, 10)).toEqual({ start: 0, end: 3 })
    expect(clampRange({ start: 0, end: 0 }, 0)).toBeNull()
  })

  it('a whole-axis range is no zoom', () => {
    expect(isFullRange({ start: 0, end: 9 }, 10)).toBe(true)
    expect(isFullRange({ start: 1, end: 9 }, 10)).toBe(false)
    expect(zoomKeysFor(model10.points.map((p) => p.x), { start: 0, end: 9 })).toBeNull()
    expect(zoomKeysFor(model10.points.map((p) => p.x), null)).toBeNull()
  })

  it('remembers day KEYS and resolves them on the current axis — or drops them', () => {
    const xs = model10.points.map((p) => p.x)
    const keys = zoomKeysFor(xs, { start: 2, end: 5 })
    expect(keys).toEqual({ from: D(2), to: D(5) })
    expect(resolveZoom(xs, keys)).toEqual({ start: 2, end: 5 })
    // The axis moved on by one day: the same days, at new positions.
    const nextDay = Array.from({ length: 10 }, (_, i) => D(i + 1))
    expect(resolveZoom(nextDay, keys)).toEqual({ start: 1, end: 4 })
    // A zoomed day that left the axis ends the zoom rather than re-pointing it.
    const later = Array.from({ length: 10 }, (_, i) => D(i + 3))
    expect(resolveZoom(later, keys)).toBeNull()
    // Covering the whole axis now: no zoom.
    expect(resolveZoom([D(2), D(3), D(4), D(5)], keys)).toBeNull()
    expect(resolveZoom(xs, { from: D(5), to: D(2) })).toBeNull()
    expect(resolveZoom(xs, null)).toBeNull()
  })

  it('compares ranges', () => {
    expect(sameRange(null, null)).toBe(true)
    expect(sameRange({ start: 1, end: 2 }, { start: 1, end: 2 })).toBe(true)
    expect(sameRange({ start: 1, end: 2 }, null)).toBe(false)
    expect(sameRange({ start: 1, end: 2 }, { start: 1, end: 3 })).toBe(false)
  })

  it('normalises the frame option', () => {
    expect(zoomOptionsOf(undefined)).toBeNull()
    expect(zoomOptionsOf(false)).toBeNull()
    expect(zoomOptionsOf(true)).toEqual({})
    const options = { applyAsWindow: { windowOptions: [7] } }
    expect(zoomOptionsOf(options)).toBe(options)
  })
})

describe('sliceTimeSeriesModel', () => {
  it('returns the model itself when unzoomed, and when there is nothing to slice', () => {
    expect(sliceTimeSeriesModel(model10, null)).toBe(model10)
    const empty = buildTimeSeriesModel({ points: [] })
    expect(sliceTimeSeriesModel(empty, { start: 0, end: 3 })).toBe(empty)
  })

  it('a range at the START has no preceding day', () => {
    const slice = sliceTimeSeriesModel(model10, { start: 0, end: 3 })
    expect(slice.points.map((p) => p.x)).toEqual([D(0), D(1), D(2), D(3)])
    expect(slice.precedingPoint).toBeNull()
  })

  it('a range at the END keeps the day before it as precedingPoint', () => {
    const slice = sliceTimeSeriesModel(model10, { start: 6, end: 9 })
    expect(slice.points.map((p) => p.x)).toEqual([D(6), D(7), D(8), D(9)])
    expect(slice.precedingPoint).toBe(model10.points[5])
  })

  it('an unzoomed model carries no precedingPoint at all', () => {
    expect(model10.precedingPoint).toBeUndefined()
  })

  it('keeps the whole window’s axes: a zoom changes the days, not the scale', () => {
    const zoomed = buildTimeSeriesModel({ points: TEN, zoomRateAxis: true })
    const slice = sliceTimeSeriesModel(zoomed, { start: 3, end: 4 })
    expect(slice.rateAxis).toBe(zoomed.rateAxis)
    expect(slice.executionsAxis).toBe(zoomed.executionsAxis)
  })

  it('takes markers outside the range off the plot and keeps the window count as it was', () => {
    const slice = sliceTimeSeriesModel(model10, { start: 5, end: 9 })
    expect(slice.markers.map((m) => m.x)).toEqual([D(8)])
    expect(slice.markersOutsideWindow).toBe(model10.markersOutsideWindow)
    const xs = model10.points.map((p) => p.x)
    expect(markersOutsideRange(model10.markers, xs, { start: 5, end: 9 }).map((m) => m.names[0])).toEqual(['R-early'])
    expect(markersOutsideRange(model10.markers, xs, null)).toEqual([])
  })

  it('one day: a dot, isolated, with its predecessor', () => {
    const slice = sliceTimeSeriesModel(model10, { start: 4, end: 4 })
    expect(slice.points).toHaveLength(1)
    expect(slice.singlePoint).toBe(true)
    expect(slice.isolated).toEqual([D(4)])
    expect(slice.precedingPoint).toBe(model10.points[3])
  })

  it('re-derives isolated points and gaps over the days in view', () => {
    // Day 3 has a measured neighbour on day 2 in the full model, so it is NOT
    // isolated there; zoomed to days 3..5 with day 4 a gap, it is — and a line
    // renderer would otherwise draw nothing for it.
    const pts = [point(0, 90), point(1, 91), point(2, 92), point(3, 93), point(4, null), point(5, null), point(6, 90)]
    const full = buildTimeSeriesModel({ points: pts })
    expect(full.isolated).toEqual([D(6)])
    const slice = sliceTimeSeriesModel(full, { start: 3, end: 5 })
    expect(slice.isolated).toEqual([D(3)])
    expect(slice.gaps).toBe(2)
    expect(slice.singlePoint).toBe(true)
  })

  it('keeps the still-filling day (and its in-progress count) only while it is in view', () => {
    const meta = { partial_day: D(9), includes_in_progress: 3 } as unknown as EnvelopeMeta
    const full = buildTimeSeriesModel({ points: TEN, meta })
    expect(sliceTimeSeriesModel(full, { start: 7, end: 9 })).toMatchObject({ partialDay: D(9), inProgressCount: 3 })
    expect(sliceTimeSeriesModel(full, { start: 0, end: 5 })).toMatchObject({ partialDay: null, inProgressCount: 0 })
  })
})

describe('sliceTrendAnalysis — the analysis is the WHOLE window’s', () => {
  // 35 days around 90-92 % and one collapse on day 28, a Sunday like days
  // 0, 7, 14 and 21 — the four-week baseline the anomaly rule needs.
  const pts = Array.from({ length: 35 }, (_, i) => point(i, i === 28 ? 60 : 90 + (i % 3)))
  const full = buildTimeSeriesModel({ points: pts })
  const analysis = analyzeTrend(full.points)
  const range = { start: 25, end: 34 }
  const slice = sliceTimeSeriesModel(full, range)
  const days = slice.points.map((p) => p.x)
  const zoomed = sliceTrendAnalysis(analysis, days)

  it('keeps a day’s anomaly and moving average identical, zoomed and unzoomed', () => {
    if (!analysis.available || !zoomed.available) throw new Error('fixture must be analysable')
    const anomaly = analysis.anomalies.find((a) => a.x === D(28))
    expect(anomaly).toBeDefined()
    expect(zoomed.anomalies).toEqual([anomaly])
    expect(zoomed.anomalies[0]).toBe(anomaly)
    const fullMa = analysis.movingAverage.find((p) => p.x === D(26))
    const zoomedMa = zoomed.movingAverage.find((p) => p.x === D(26))
    expect(zoomedMa).toBe(fullMa)
    expect(zoomed.fit.line.find((p) => p.x === D(30))).toBe(analysis.fit.line.find((p) => p.x === D(30)))
  })

  it('…which recomputing on the slice would NOT give (the wrong fix this guards against)', () => {
    const recomputed = analyzeTrend(slice.points)
    const flagged = recomputed.available ? recomputed.anomalies.map((a) => a.x) : []
    expect(flagged).not.toContain(D(28))
    const ma = recomputed.available ? recomputed.movingAverage.find((p) => p.x === D(26)) : undefined
    expect(ma).toBeUndefined()
  })

  it('cuts only the per-day values; the fit, the explanations and the period stay whole', () => {
    if (!analysis.available || !zoomed.available) throw new Error('fixture must be analysable')
    expect(zoomed.movingAverage.every((p) => days.includes(p.x))).toBe(true)
    expect(zoomed.fit.line.map((p) => p.x)).toEqual(days)
    expect(zoomed.fit.slopePerWeek).toBe(analysis.fit.slopePerWeek)
    expect(zoomed.explain).toBe(analysis.explain)
    expect(zoomed.period).toBe(analysis.period)
    expect(zoomed.windowDays).toBe(35)
  })

  it('passes an unavailable analysis through untouched', () => {
    const short = analyzeTrend(TEN.slice(0, 3))
    expect(sliceTrendAnalysis(short, [D(0)])).toBe(short)
  })
})

describe('sliceMultiSeriesModel — the fold and the colours are the whole window’s', () => {
  const RATE = { kind: 'rate', title: 'Pass rate %' } as const
  function line(key: string, n: (i: number) => number, days = 10): MultiSeriesInputSeries {
    return {
      key,
      label: key,
      points: Array.from({ length: days }, (_, i): SeriesPoint => {
        const size = n(i)
        return size === 0 ? { x: D(i), y: null, n: 0, measured: false, reason: 'no runs' } : { x: D(i), y: 80 + i, n: size }
      }),
    }
  }
  // Nine series: s0..s7 busy early, `late` quiet early and busy on days 8-9.
  // Over the whole window `late` is the smallest and folds into "Other"; over
  // days 8-9 alone it would be the biggest.
  const input = [
    ...Array.from({ length: 8 }, (_, k) => line(`s${k}`, (i) => (i < 8 ? 100 + k : 1))),
    line('late', (i) => (i < 8 ? 1 : 300)),
  ]
  const full = buildMultiSeriesModel({ series: input, metric: RATE })

  it('keeps every line, its key, colour slot, order and full-window volume', () => {
    const slice = sliceMultiSeriesModel(full, { start: 8, end: 9 })
    expect(slice.xs).toEqual([D(8), D(9)])
    expect(slice.lines.map((l) => [l.key, l.styleIndex, l.dash, l.volume])).toEqual(
      full.lines.map((l) => [l.key, l.styleIndex, l.dash, l.volume]),
    )
    expect(slice.fold).toBe(full.fold)
    expect(slice.yAxis).toBe(full.yAxis)
    expect(slice.lines.every((l) => l.points.length === 2)).toBe(true)
  })

  it('keeps "Other" in its reserved colour slot, not the slot of its position', () => {
    // The server folded: three lines, "Other" third — and in slot 7, always.
    const served = buildMultiSeriesModel({
      series: [line('a', () => 100), line('b', () => 90), { ...line(OTHER_KEY, () => 50), label: 'Other' }],
      metric: RATE,
    })
    const other = served.lines.find((l) => l.key === OTHER_KEY)
    expect(other?.styleIndex).toBe(7)
    const slice = sliceMultiSeriesModel(served, { start: 2, end: 4 })
    expect(slice.lines.map((l) => [l.key, l.styleIndex, l.dash])).toEqual(served.lines.map((l) => [l.key, l.styleIndex, l.dash]))
  })

  it('…where rebuilding from the zoomed days would reshuffle them (the wrong fix)', () => {
    expect(full.lines.map((l) => l.key)).toContain(OTHER_KEY)
    expect(full.lines.map((l) => l.key)).not.toContain('late')
    const rebuilt = buildMultiSeriesModel({
      series: input.map((s) => ({ ...s, points: s.points.slice(8) })),
      metric: RATE,
    })
    expect(rebuilt.lines.map((l) => l.key)).toContain('late')
  })

  it('re-derives each line’s label point, gaps and the partial day over the slice', () => {
    const gappy = buildMultiSeriesModel({
      series: [line('a', (i) => (i === 5 ? 0 : 100)), line('b', () => 100)],
      metric: RATE,
      meta: { partial_day: D(9), includes_in_progress: 2 } as unknown as EnvelopeMeta,
    })
    const early = sliceMultiSeriesModel(gappy, { start: 3, end: 5 })
    const a = early.lines.find((l) => l.key === 'a')
    expect(a?.gaps).toBe(1)
    expect(a?.last).toEqual({ index: 1, y: 84 })
    expect(early.gaps).toBe(1)
    expect(early.partialDay).toBeNull()
    expect(early.inProgressCount).toBe(0)
    const late = sliceMultiSeriesModel(gappy, { start: 7, end: 9 })
    expect(late.partialDay).toBe(D(9))
    expect(late.inProgressCount).toBe(2)
    expect(sliceMultiSeriesModel(gappy, null)).toBe(gappy)
  })

  it('keeps each line’s point on the day before the view, so the first day in view has a previous day (Wave 2.4 F4)', () => {
    const gappy = buildMultiSeriesModel({ series: [line('a', (i) => (i === 5 ? 0 : 100)), line('b', () => 100)], metric: RATE })
    const slice = sliceMultiSeriesModel(gappy, { start: 6, end: 9 })
    slice.lines.forEach((l) => expect(l.precedingPoint).toBe(gappy.lines.find((g) => g.key === l.key)?.points[5]))
    // Line a did not measure day 5: its predecessor is there, unmeasured — never skipped to day 4.
    expect(slice.lines.find((l) => l.key === 'a')?.precedingPoint?.y).toBeNull()
    // From the first day there is none; unzoomed, the field is absent.
    expect(sliceMultiSeriesModel(gappy, { start: 0, end: 3 }).lines.every((l) => l.precedingPoint === null)).toBe(true)
    expect(gappy.lines.every((l) => l.precedingPoint === undefined)).toBe(true)
  })
})

describe('sliceDurationBand — the built band, sliced', () => {
  const series = (points: SeriesPoint[]) => ({ kind: 'series' as const, dimensions: ['day'], x_type: 'time' as const, series: [{ key: 'v', label: 'v', points }] })
  const band = durationBandPoints({
    p50: series(Array.from({ length: 6 }, (_, i) => ({ x: D(i), y: 100, n: 10 }))),
    p95: series(Array.from({ length: 6 }, (_, i) => ({ x: D(i), y: i === 1 || i === 4 ? 50 : 300, n: 10 }))),
  })

  it('keeps each built point and re-counts the inverted days in view, in bandNotice’s words', () => {
    expect(band.inverted).toBe(2)
    const slice = sliceDurationBand(band, { start: 3, end: 5 })
    expect(slice.points).toHaveLength(3)
    slice.points.forEach((point, i) => expect(point).toBe(band.points[3 + i]))
    expect(slice.inverted).toBe(1)
    expect(slice.notice).toBe(bandNotice(1))
    expect(sliceDurationBand(band, { start: 2, end: 3 })).toMatchObject({ inverted: 0, notice: null })
    expect(sliceDurationBand(band, null)).toBe(band)
  })

  it('keeps the day before the view as precedingPoint (Wave 2.4 F4)', () => {
    expect(sliceDurationBand(band, { start: 3, end: 5 }).precedingPoint).toBe(band.points[2])
    expect(sliceDurationBand(band, { start: 0, end: 2 }).precedingPoint).toBeNull()
    expect(band.precedingPoint).toBeUndefined()
  })

  it('bandNotice keeps the builder’s sentence', () => {
    expect(bandNotice(0)).toBeNull()
    expect(bandNotice(1)).toBe('p95 was below p50 on 1 day; both are drawn as reported.')
    expect(bandNotice(2)).toBe(band.notice)
  })

  it('durationBandMax is the top of the WHOLE band: the larger percentile of any day, gaps skipped', () => {
    // p50 is the larger value on an inverted day, so the max must read `high`, not p95.
    const inverted = durationBandPoints({
      p50: series([{ x: D(0), y: 100, n: 5 }, { x: D(1), y: 900, n: 5 }, { x: D(2), y: null, n: 0 }]),
      p95: series([{ x: D(0), y: 300, n: 5 }, { x: D(1), y: 600, n: 5 }, { x: D(2), y: null, n: 0 }]),
    })
    expect(durationBandMax(inverted)).toBe(900)
    expect(durationBandMax(band)).toBe(300)
    const empty = durationBandPoints({
      p50: series([{ x: D(0), y: null, n: 0 }]),
      p95: series([{ x: D(0), y: null, n: 0 }]),
    })
    expect(durationBandMax(empty)).toBeUndefined()
  })
})

describe('flaggedDaysText — the strip’s count agrees with its explanation', () => {
  it('reads as before when unzoomed', () => {
    expect(flaggedDaysText('Flagged as unusual', 0)).toBe('No day flagged as unusual')
    expect(flaggedDaysText('Flagged as unusual', 1)).toBe('1 day flagged as unusual')
    expect(flaggedDaysText('Flagged as unusual', 3)).toBe('3 days flagged as unusual')
  })

  it('states both counts when zoomed', () => {
    expect(flaggedDaysText('Flagged as unusual', 2, 5)).toBe('2 days flagged as unusual in view (5 in the window)')
    expect(flaggedDaysText('Flagged as unusual', 0, 1)).toBe('No day flagged as unusual in view (1 in the window)')
  })

  it('the sliced analysis carries the window’s count; an unzoomed one does not', () => {
    const pts = Array.from({ length: 35 }, (_, i) => point(i, i === 28 ? 60 : 90 + (i % 3)))
    const analysis = analyzeTrend(buildTimeSeriesModel({ points: pts }).points)
    if (!analysis.available) throw new Error('fixture must be analysable')
    expect('zoomed' in analysis).toBe(false)
    const early = sliceTrendAnalysis(analysis, pts.slice(0, 10).map((p) => p.x))
    expect(early.zoomed).toEqual({ anomaliesInWindow: analysis.anomalies.length })
    expect(early.available && early.anomalies).toEqual([])
  })
})

describe('words', () => {
  it('names a UTC day from its key, never through the viewer’s zone', () => {
    expect(dayShort('2026-09-03')).toBe('Sep 3')
    expect(dayInWords('2026-09-03')).toBe('September 3, 2026')
    expect(dayShort('week-12')).toBe('week-12')
    expect(dayInWords('2026-13-40')).toBe('2026-13-40')
  })

  it('says a range the way a reader does', () => {
    expect(rangeInWords('2026-09-03', '2026-09-12')).toBe('Sep 3–12, 2026')
    expect(rangeInWords('2026-08-28', '2026-09-12')).toBe('Aug 28 – Sep 12, 2026')
    expect(rangeInWords('2025-12-28', '2026-01-03')).toBe('Dec 28, 2025 – Jan 3, 2026')
    expect(rangeInWords('2026-09-03', '2026-09-03')).toBe('Sep 3, 2026')
    expect(rangeInWords('a', 'b')).toBe('a–b')
    expect(RELATIVE_DAY_WORDS.range('3', '9')).toBe('days 3–9 since release start')
    expect(RELATIVE_DAY_WORDS.full('3')).toBe('Day 3 since release start')
  })

  it('announces, scopes and notes the zoom', () => {
    expect(zoomChangeLabel(CALENDAR_WORDS, D(2), D(5))).toBe('zoomed to Mar 3–6, 2026')
    expect(zoomResetLabel(30)).toBe('zoom reset, all 30 days shown')
    expect(zoomScopeLabel(CALENDAR_WORDS, D(2), D(5), undefined)).toBe('zoomed to Mar 3–6, 2026')
    expect(zoomScopeLabel(CALENDAR_WORDS, D(2), D(5), 'Project A')).toBe('Project A; zoomed to Mar 3–6, 2026')
    const note = zoomNote({ words: CALENDAR_WORDS, from: D(2), to: D(5), shown: 4, total: 30 })
    expect(note).toBe(
      'Zoomed to Mar 3–6, 2026: 4 of 30 days, not the page window. The summary, table and export show these days only.',
    )
    const full = zoomNote({ words: CALENDAR_WORDS, from: D(2), to: D(5), shown: 4, total: 30, trendAnalysis: true, markersOutside: 2 })
    expect(full).toContain('Trend statistics are computed over all 30 days.')
    expect(full).toContain('2 release markers are outside the zoom and listed in the table.')
    expect(zoomNote({ words: CALENDAR_WORDS, from: D(2), to: D(5), shown: 4, total: 30, markersOutside: 1 })).toContain(
      '1 release marker is outside',
    )
  })
})

describe('promoteDecision — only a window the page can show EXACTLY', () => {
  const xs = Array.from({ length: 30 }, (_, i) => D(i))
  const OPTIONS = [1, 7, 14, 30, 90]

  it('offers a range that ends on the latest day, as exactly its length', () => {
    expect(promoteDecision({ xs, range: { start: 23, end: 29 }, windowOptions: OPTIONS, currentWindowDays: 30 })).toEqual({
      enabled: true,
      days: 7,
    })
    expect(promoteDecision({ xs, range: { start: 29, end: 29 }, windowOptions: OPTIONS, currentWindowDays: 30 })).toEqual({
      enabled: true,
      days: 1,
    })
  })

  it('refuses a range that ends before the latest day, with the visible reason', () => {
    expect(promoteDecision({ xs, range: { start: 20, end: 26 }, windowOptions: OPTIONS, currentWindowDays: 30 })).toEqual({
      enabled: false,
      reason: PROMOTE_NOT_LATEST_REASON,
    })
  })

  it('refuses a length the page would SNAP to another, naming the lengths that work', () => {
    const decision = promoteDecision({ xs, range: { start: 18, end: 29 }, windowOptions: OPTIONS, currentWindowDays: 30 })
    expect(decision).toEqual({ enabled: false, reason: 'The page window can be 1, 7, 14, 30 or 90 days; this range is 12 days' })
  })

  it('refuses the window already in force, and an axis of relative days', () => {
    expect(promoteDecision({ xs, range: { start: 23, end: 29 }, windowOptions: OPTIONS, currentWindowDays: 7 })).toEqual({
      enabled: false,
      reason: 'The page window is already the last 7 days',
    })
    // One day is named as the filter bar and its chip name it (review F8), never "last 1 day".
    expect(promoteDecision({ xs, range: { start: 29, end: 29 }, windowOptions: OPTIONS, currentWindowDays: 1 })).toEqual({
      enabled: false,
      reason: 'The page window is already the last 24 hours',
    })
    expect(
      promoteDecision({ xs, range: { start: 23, end: 29 }, windowOptions: OPTIONS, currentWindowDays: 30, relative: true }),
    ).toEqual({ enabled: false, reason: PROMOTE_RELATIVE_REASON })
  })
})
