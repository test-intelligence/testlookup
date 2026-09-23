/**
 * VIZ-404 fix round A — the model half of the review findings. Each block
 * names the finding it pins; each was written against the pre-fix model and
 * failed there.
 */
import { describe, expect, it } from 'vitest'
import type { EnvelopeMeta, SeriesPoint } from '@/lib/viz/contracts'
import { SVG_POINT_LIMIT } from './timeSeriesModel'
import { addUtcDays } from './seriesAlignment'
import {
  KEPT_BEFORE_OTHER,
  MIN_PLOT_WIDTH_WITH_LABELS,
  OTHER_KEY,
  PARTIAL_MARK,
  SERIES_DASHES,
  buildMultiSeriesModel,
  comparabilityFromMeta,
  placeDirectLabels,
  tipContentAt,
  tipText,
  type MultiSeriesInputSeries,
} from './multiSeriesModel'

const RATE = { kind: 'rate', title: 'Pass rate %' } as const
const COUNT = { kind: 'count', title: 'Executions' } as const
const pct = (v: number) => `${v.toFixed(1)}%`
const day = (i: number, from = '2026-03-01') => addUtcDays(from, i)

function line(key: string, ys: (number | null)[], n: number | number[] = 100, from = '2026-03-01'): MultiSeriesInputSeries {
  return {
    key,
    label: key,
    points: ys.map((y, i): SeriesPoint => {
      const size = Array.isArray(n) ? n[i] : n
      return y === null ? { x: day(i, from), y: null, n: 0, measured: false, reason: 'no sample' } : { x: day(i, from), y, n: size }
    }),
  }
}

const meta = (extra: Record<string, unknown>) => extra as unknown as EnvelopeMeta

// ── The comparability contract agreed with the backend (C2 meta.comparability) ──

describe('comparabilityFromMeta reads meta.comparability', () => {
  it('reads { comparable, reason, reason_code } from the envelope', () => {
    const read = comparabilityFromMeta(
      meta({ comparability: { comparable: false, reason: 'release/2.4 ran 2 suites main did not', reason_code: 'different_suites' } }),
    )
    expect(read).toEqual({ comparable: false, reason: 'release/2.4 ran 2 suites main did not', reasonCode: 'different_suites' })
  })

  it('a comparable envelope, or one without the object, is comparable', () => {
    expect(comparabilityFromMeta(meta({ comparability: { comparable: true, reason: null, reason_code: null } })).comparable).toBe(true)
    expect(comparabilityFromMeta(meta({})).comparable).toBe(true)
    expect(comparabilityFromMeta(null).comparable).toBe(true)
  })

  it('never reads the names the API does not send (meta.comparable / comparable_reason)', () => {
    expect(comparabilityFromMeta(meta({ comparable: false, comparable_reason: 'x' })).comparable).toBe(true)
  })

  it('a blank reason is no reason, and the banner says the API did not say why', () => {
    const model = buildMultiSeriesModel({
      series: [line('a', [1]), line('b', [2])],
      metric: RATE,
      meta: meta({ comparability: { comparable: false, reason: '  ', reason_code: 'x' } }),
    })
    expect(model.banner).toMatch(/the API did not say why/)
  })

  it('an explicit comparability still takes precedence over the envelope', () => {
    const model = buildMultiSeriesModel({
      series: [line('a', [1]), line('b', [2])],
      metric: RATE,
      meta: meta({ comparability: { comparable: false, reason: 'from the API', reason_code: 'x' } }),
      comparability: { comparable: true, reason: null },
    })
    expect(model.banner).toBeNull()
  })
})

// ── m4: dash patterns ─────────────────────────────────────────────────────────

describe('dash patterns (m4)', () => {
  const onSegments = (dash: string | undefined) =>
    dash === undefined ? [] : dash.split(/\s+/).map(Number).filter((_, i) => i % 2 === 0)

  it('at most ONE pattern mixes a long dash with dots: dash-dot and dash-dot-dot read the same', () => {
    const dashDotFamily = SERIES_DASHES.filter((dash) => {
      const on = onSegments(dash)
      return on.some((seg) => seg >= 6) && on.some((seg) => seg <= 3)
    })
    expect(dashDotFamily, dashDotFamily.join(' | ')).toHaveLength(1)
  })

  it('every pattern repeats within 32 px, so a 32 px legend swatch shows a whole period', () => {
    for (const dash of SERIES_DASHES) {
      const period = dash === undefined ? 0 : dash.split(/\s+/).map(Number).reduce((a, b) => a + b, 0)
      expect(period, String(dash)).toBeLessThanOrEqual(32)
    }
  })
})

// ── M6: the direct-label gutter must not crush the plot ───────────────────────

describe('placeDirectLabels checks the plot width too (M6)', () => {
  it('reports fits=false when the plot, gutter and all, would be narrower than the minimum', () => {
    expect(MIN_PLOT_WIDTH_WITH_LABELS).toBeGreaterThanOrEqual(180)
    const requests = [{ key: 'a', y: 50 }]
    expect(placeDirectLabels(requests, { top: 0, bottom: 200, plotWidth: 56 }).fits).toBe(false)
    expect(placeDirectLabels(requests, { top: 0, bottom: 200, plotWidth: MIN_PLOT_WIDTH_WITH_LABELS - 1 }).fits).toBe(false)
    expect(placeDirectLabels(requests, { top: 0, bottom: 200, plotWidth: MIN_PLOT_WIDTH_WITH_LABELS }).fits).toBe(true)
    // Without a width it is the vertical check alone, as before.
    expect(placeDirectLabels(requests, { top: 0, bottom: 200 }).fits).toBe(true)
  })
})

// ── Correctness: the 366-day crop and the aligned caption ─────────────────────

describe('the SVG crop is worded by alignment', () => {
  const ys = Array.from({ length: 400 }, (_, i) => i)

  it('a calendar axis keeps the LATEST days and says so', () => {
    const model = buildMultiSeriesModel({ series: [line('a', ys, 1, '2025-01-01')], metric: COUNT })
    expect(model.xs[model.xs.length - 1]).toBe(day(399, '2025-01-01'))
    expect(model.cappedNote).toBe(`Showing the latest ${SVG_POINT_LIMIT} of 400 days.`)
  })

  it('an aligned axis keeps the FIRST days since release start, and says so — never "latest"', () => {
    const model = buildMultiSeriesModel({ series: [line('r1', ys, 1, '2025-01-01')], metric: COUNT, alignment: 'release-start' })
    expect(model.xs[model.xs.length - 1]).toBe(String(SVG_POINT_LIMIT - 1))
    expect(model.cappedNote).toBe(`Showing the first ${SVG_POINT_LIMIT} of 400 days since release start.`)
  })
})

describe('the aligned caption says what day 0 really is', () => {
  const r1 = line('r1', [null, 70, 75], [0, 10, 10], '2026-02-02')
  const r2 = line('r2', [80, 81], 10, '2026-02-20')

  it('with no start dates, day 0 is each release\'s first day WITH RUNS — not "its own first day"', () => {
    const model = buildMultiSeriesModel({ series: [r1, r2], metric: RATE, alignment: 'release-start' })
    expect(model.caption).toMatch(/first day with runs/)
    expect(model.caption).not.toMatch(/own first day/)
    expect(model.lines[0].points[0].date).toBe('2026-02-03')
  })

  it('with every start date given, day 0 is the start date, and the caption says so', () => {
    const model = buildMultiSeriesModel({
      series: [r1, r2],
      metric: RATE,
      alignment: 'release-start',
      starts: { r1: '2026-02-02', r2: '2026-02-20' },
    })
    expect(model.caption).toMatch(/start date/)
    expect(model.caption).not.toMatch(/first day with runs/)
    expect(model.lines[0].points[0].date).toBe('2026-02-02')
  })

  it('with some start dates given, it says both', () => {
    const model = buildMultiSeriesModel({ series: [r1, r2], metric: RATE, alignment: 'release-start', starts: { r1: '2026-02-02' } })
    expect(model.caption).toMatch(/start date/)
    expect(model.caption).toMatch(/first day with runs/)
  })
})

// ── Correctness: "Other" for a rate ───────────────────────────────────────────

describe('a client-folded RATE "Other" is the merged-count rate, Σ(y·n)/Σn', () => {
  // Ten suites, two days. The three smallest by volume fold.
  const ten = Array.from({ length: 10 }, (_, i) => line(`s${i}`, [100 - i, i === 9 ? null : 50], 10 * (10 - i)))

  it('pools the folded rates by their execution counts — never averages them', () => {
    const model = buildMultiSeriesModel({ series: ten, metric: RATE })
    const other = model.lines.find((l) => l.other)
    if (!other) throw new Error('no Other')
    // Folded: s7 (y 93, n 30), s8 (92, 20), s9 (91, 10) on day 0.
    expect(other.points[0].y).toBeCloseTo((93 * 30 + 92 * 20 + 91 * 10) / 60, 10)
    // Day 1: s9 is unmeasured, so only s7 and s8 pool.
    expect(other.points[1].y).toBeCloseTo((50 * 30 + 50 * 20) / 50, 10)
    expect(other.points[0].reason).toBeNull()
  })

  it('a day where no folded series measured anything is a gap with a reason', () => {
    // Nine suites: the two smallest (s7, s8) fold, and neither measured day 0.
    const series = Array.from({ length: 9 }, (_, i) => line(`s${i}`, [i >= 7 ? null : 90, 90], i >= 7 ? 9 - i : 100 - i))
    const model = buildMultiSeriesModel({ series, metric: RATE })
    const other = model.lines.find((l) => l.other)
    expect(other?.points[0].y).toBeNull()
    expect(other?.points[0].reason).toBeTruthy()
    expect(other?.points[1].y).toBe(90)
  })

  it('a server "__other__" that must be folded again keeps its values, pooled by its counts, and the notice counts both folds', () => {
    const eight = Array.from({ length: 8 }, (_, i) => line(`a${i}`, [90, 91], 100 - i))
    const serverOther: MultiSeriesInputSeries = {
      key: OTHER_KEY,
      label: 'Other',
      points: [
        { x: day(0), y: 42, n: 5000 },
        { x: day(1), y: 43, n: 5000 },
      ],
    }
    const model = buildMultiSeriesModel({
      series: [...eight, serverOther],
      metric: RATE,
      seriesNoun: 'suites',
      meta: meta({ truncated_axes: { series: { dimension: 'suite', kept: 8, total: 20 } } }),
    })
    const other = model.lines.find((l) => l.other)
    if (!other) throw new Error('no Other')
    // a7 (y 90, n 93) is folded here into the server's Other (42 over 5000).
    expect(other.points[0].y).toBeCloseTo((42 * 5000 + 90 * 93) / 5093, 10)
    expect(other.points[1].y).toBeCloseTo((43 * 5000 + 91 * 93) / 5093, 10)
    expect(model.fold).toMatchObject({ folded: 13, total: 20 })
    expect(model.foldNotice).toMatch(/^20 suites: the 7 with the most executions are drawn, and the other 13 are folded into "Other"/)
  })

  it('a server "__other__" with no truncated_axes still says a further fold happened here', () => {
    const eight = Array.from({ length: 8 }, (_, i) => line(`a${i}`, [90], 100 - i))
    const serverOther: MultiSeriesInputSeries = { key: OTHER_KEY, label: 'Other', points: [{ x: day(0), y: 42, n: 5000 }] }
    const model = buildMultiSeriesModel({ series: [...eight, serverOther], metric: RATE, seriesNoun: 'suites' })
    expect(model.lines.find((l) => l.other)?.points[0].y).not.toBeNull()
    expect(model.foldNotice).toMatch(/server/)
    expect(model.foldNotice).toMatch(/1 more suite is folded in here/)
  })
})

// ── Correctness: the notice's words ───────────────────────────────────────────

describe('the fold notice', () => {
  it('is singular for one folded series ("the other 1 is folded")', () => {
    // The server kept 7 of 8 and sent the eighth as "Other".
    const seven = Array.from({ length: 7 }, (_, i) => line(`s${i}`, [i], 100 - i))
    const serverOther: MultiSeriesInputSeries = { key: OTHER_KEY, label: 'Other', points: [{ x: day(0), y: 3, n: 3 }] }
    const model = buildMultiSeriesModel({
      series: [...seven, serverOther],
      metric: COUNT,
      seriesNoun: 'suites',
      meta: meta({ truncated_axes: { series: { dimension: 'suite', kept: 7, total: 8 } } }),
    })
    expect(model.foldNotice).toBe('8 suites: the 7 with the most executions are drawn, and the other 1 is folded into "Other".')
  })

  it('states the tie-break when the last kept place is a tie on executions', () => {
    // k1 and k2 both ran 100: only one of them can be the 7th.
    const series = [
      ...Array.from({ length: 6 }, (_, i) => line(`top${i}`, [1], 1000 - i)),
      line('k2', [1], 100),
      line('k1', [1], 100),
      line('small', [1], 5),
    ]
    const model = buildMultiSeriesModel({ series, metric: COUNT, seriesNoun: 'suites' })
    const kept = model.lines.filter((l) => !l.other).map((l) => l.key)
    expect(kept).toHaveLength(KEPT_BEFORE_OTHER)
    expect(kept).toContain('k1')
    expect(kept).not.toContain('k2')
    expect(model.foldNotice).toMatch(/tie/)
    expect(model.foldNotice).toMatch(/k1/)
    expect(model.foldNotice).toMatch(/k2/)
    expect(model.foldNotice).toMatch(/sorts first/)
  })

  it('says nothing about a tie when there is none at the cut', () => {
    const nine = Array.from({ length: 9 }, (_, i) => line(`s${i}`, [i], 100 - i))
    expect(buildMultiSeriesModel({ series: nine, metric: COUNT }).foldNotice).not.toMatch(/tie/)
  })
})

// ── Correctness: meta.partial_day ─────────────────────────────────────────────

describe('the still-filling day (meta.partial_day)', () => {
  const series = [line('payments', [97, 96, 40]), line('cart', [90, 91, 10])]
  const partial = meta({ partial_day: day(2), includes_in_progress: 3 })

  it('marks the partial day\'s points, and names it on the model', () => {
    const model = buildMultiSeriesModel({ series, metric: RATE, meta: partial })
    expect(model.partialDay).toBe(day(2))
    expect(model.inProgressCount).toBe(3)
    expect(model.lines[0].points.map((p) => p.partial)).toEqual([false, false, true])
  })

  it('never anchors a direct label on the still-filling day: the label names the last COMPLETE day', () => {
    const model = buildMultiSeriesModel({ series, metric: RATE, meta: partial })
    expect(model.lines[0].last).toEqual({ index: 1, y: 96 })
    expect(model.lines[1].last).toEqual({ index: 1, y: 91 })
  })

  it('the tooltip and the cursor say the day is still filling', () => {
    const model = buildMultiSeriesModel({ series, metric: RATE, meta: partial })
    const tip = tipContentAt(model, 2, { format: pct })
    expect(tip.rows.every((row) => row.partial)).toBe(true)
    expect(tipText(model, tip)).toContain(PARTIAL_MARK)
    expect(tipText(model, tipContentAt(model, 1, { format: pct }))).not.toContain(PARTIAL_MARK)
  })

  it('a partial_day that is not on the axis marks nothing', () => {
    const model = buildMultiSeriesModel({ series, metric: RATE, meta: meta({ partial_day: '2027-01-01' }) })
    expect(model.partialDay).toBeNull()
    expect(model.lines[0].last).toEqual({ index: 2, y: 40 })
  })

  it('an aligned axis marks the release day that falls on the partial day', () => {
    const r1 = line('r1', [70, 71, 72], 10, '2026-02-02')
    const r2 = line('r2', [80, 81], 10, '2026-03-10')
    const model = buildMultiSeriesModel({
      series: [r1, r2],
      metric: RATE,
      alignment: 'release-start',
      meta: meta({ partial_day: '2026-03-11' }),
    })
    expect(model.lines[1].points.map((p) => p.partial)).toEqual([false, true, false])
    expect(model.lines[0].points.every((p) => !p.partial)).toBe(true)
    expect(model.lines[1].last).toEqual({ index: 0, y: 80 })
  })
})
