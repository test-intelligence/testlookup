/**
 * VIZ-404 — the multi-series model. Every edge case in the story is asserted on
 * the model the plot, the tooltip, the cursor and the table all read.
 */
import { describe, expect, it } from 'vitest'
import type { EnvelopeMeta, SeriesPoint } from '@/lib/viz/contracts'
import { SVG_POINT_LIMIT } from './timeSeriesModel'
import { addUtcDays } from './seriesAlignment'
import {
  ABSOLUTE_X_TITLE,
  ALIGNED_X_TITLE,
  DIRECT_LABEL_HEIGHT,
  HIDDEN_SUFFIX,
  KEPT_BEFORE_OTHER,
  MAX_DRAWN_SERIES,
  NOT_REPORTED_REASON,
  OTHER_KEY,
  OTHER_LABEL,
  OTHER_RATE_REASON,
  SERIES_DASHES,
  buildMultiSeriesModel,
  comparabilityFromMeta,
  directLabelText,
  isolateHidden,
  multiSeriesInputFromChartData,
  multiSeriesToChartSeries,
  placeDirectLabels,
  tipContentAt,
  tipText,
  toggleHidden,
  visibilityAnnouncement,
  type MultiSeriesInputSeries,
} from './multiSeriesModel'

const pct = (v: number) => `${v.toFixed(1)}%`
const RATE = { kind: 'rate', title: 'Pass rate %' } as const
const COUNT = { kind: 'count', title: 'Executions' } as const

const day = (i: number) => addUtcDays('2026-03-01', i)
function line(key: string, values: (number | null)[], n: number | ((i: number) => number) = 100, label = key): MultiSeriesInputSeries {
  return {
    key,
    label,
    points: values.map((y, i): SeriesPoint => {
      const size = typeof n === 'function' ? n(i) : n
      return y === null ? { x: day(i), y: null, n: 0, measured: false, reason: 'no evaluated executions in this bucket' } : { x: day(i), y, n: size }
    }),
  }
}

const THREE = [
  line('payments', [97, 96, 98, 97]),
  line('cart', [90, 91, 89, 92]),
  // Day 1: search is the HIGHEST, so a sorted tooltip is not the input order.
  line('search', [84, 99, 86, 87]),
]

const meta = (extra: Partial<EnvelopeMeta> & Record<string, unknown>) => extra as unknown as EnvelopeMeta

describe('buildMultiSeriesModel — lines', () => {
  it('draws one line per series, in input order, each with its own colour slot AND dash', () => {
    const model = buildMultiSeriesModel({ series: THREE, metric: RATE })
    expect(model.lines.map((l) => l.key)).toEqual(['payments', 'cart', 'search'])
    expect(model.lines.map((l) => l.styleIndex)).toEqual([0, 1, 2])
    expect(new Set(model.lines.map((l) => l.dash)).size).toBe(3)
    expect(model.xTitle).toBe(ABSOLUTE_X_TITLE)
    expect(model.yAxis).toEqual({ domain: [0, 100], ticks: [0, 25, 50, 75, 100] })
  })

  it('never varies by colour alone: all eight slots have distinct dash patterns', () => {
    expect(SERIES_DASHES).toHaveLength(MAX_DRAWN_SERIES)
    expect(new Set(SERIES_DASHES).size).toBe(MAX_DRAWN_SERIES)
  })

  it('a count axis starts at zero and reaches the largest value', () => {
    const model = buildMultiSeriesModel({ series: [line('a', [3, 41, 12])], metric: COUNT })
    expect(model.yAxis.domain[0]).toBe(0)
    expect(model.yAxis.domain[1]).toBeGreaterThanOrEqual(41)
  })
})

describe('gaps, not zeros', () => {
  it('an unmeasured day is null with the server reason, and a day a series never sent is null too', () => {
    const partial: MultiSeriesInputSeries = {
      key: 'search',
      label: 'search',
      // Day 1 missing entirely, day 2 measured:false.
      points: [
        { x: day(0), y: 88, n: 10 },
        { x: day(2), y: null, n: 0, measured: false, reason: 'every test was skipped' },
        { x: day(3), y: 90, n: 10 },
      ],
    }
    const model = buildMultiSeriesModel({ series: [line('cart', [90, 91, 89, 92]), partial], metric: RATE })
    const search = model.lines[1]
    expect(search.points.map((p) => p.y)).toEqual([88, null, null, 90])
    expect(search.points[1].reason).toBe(NOT_REPORTED_REASON)
    expect(search.points[2].reason).toBe('every test was skipped')
    expect(search.gaps).toBe(2)
    expect(model.gaps).toBe(2)
    // …and nothing anywhere was turned into a zero.
    expect(model.lines.flatMap((l) => l.points).some((p) => p.y === 0)).toBe(false)
  })

  it('keeps a day NOBODY reported on the axis, as a gap', () => {
    const a: MultiSeriesInputSeries = { key: 'a', label: 'a', points: [{ x: day(0), y: 1, n: 1 }, { x: day(3), y: 2, n: 1 }] }
    const model = buildMultiSeriesModel({ series: [a], metric: COUNT })
    expect(model.xs).toEqual([day(0), day(1), day(2), day(3)])
    expect(model.lines[0].points.map((p) => p.y)).toEqual([1, null, null, 2])
  })

  it('marks a measured point with no measured neighbour, so it is drawn as a dot', () => {
    const model = buildMultiSeriesModel({ series: [line('a', [null, 5, null, 6, 7])], metric: COUNT })
    expect(model.lines[0].isolated).toBe(true)
    expect(model.lines[0].last).toEqual({ index: 4, y: 7 })
  })
})

describe('more than 8 series: top 7 by volume + "Other"', () => {
  // Twelve suites. `huge-value` has the LARGEST values and the SMALLEST volume:
  // ranking by value would keep it; ranking by volume folds it.
  const twelve = Array.from({ length: 12 }, (_, i) =>
    i === 11 ? line('huge-value', [900, 900, 900], 1) : line(`suite-${String(i).padStart(2, '0')}`, [10 + i, 20 + i, 30 + i], 1000 - i * 10),
  )

  it('keeps the 7 with the most executions (n), not the largest values, and folds the rest', () => {
    const model = buildMultiSeriesModel({ series: twelve, metric: COUNT, seriesNoun: 'suites' })
    expect(model.lines).toHaveLength(MAX_DRAWN_SERIES)
    expect(model.lines.slice(0, KEPT_BEFORE_OTHER).map((l) => l.key)).toEqual(
      ['00', '01', '02', '03', '04', '05', '06'].map((k) => `suite-${k}`),
    )
    expect(model.lines.map((l) => l.key)).not.toContain('huge-value')
    const other = model.lines[7]
    expect(other).toMatchObject({ key: OTHER_KEY, label: OTHER_LABEL, other: true, styleIndex: 7 })
    expect(model.fold).toMatchObject({ folded: 5, total: 12, source: 'client', clientFolded: 5 })
    expect(model.foldNotice).toMatch(/12 suites/)
    expect(model.foldNotice).toMatch(/the 7 with the most executions/)
    expect(model.foldNotice).toMatch(/other 5 are folded into "Other"/)
  })

  it('a COUNT "Other" is the sum of what it folds', () => {
    const model = buildMultiSeriesModel({ series: twelve, metric: COUNT })
    // suites 7..10 and huge-value on day 0: 17 + 18 + 19 + 20 + 900.
    expect(model.lines[7].points[0].y).toBe(17 + 18 + 19 + 20 + 900)
  })

  it('a RATE "Other" is NEVER a plain average: it is the merged-count rate, pooled by n', () => {
    const model = buildMultiSeriesModel({ series: twelve.map((s) => ({ ...s })), metric: RATE })
    const other = model.lines[7]
    // Folded on day 0: suite-07..10 (17..20, n 930..900) and huge-value (900, n 1).
    const folded = [
      [17, 930],
      [18, 920],
      [19, 910],
      [20, 900],
      [900, 1],
    ]
    const pooled = folded.reduce((sum, [y, n]) => sum + y * n, 0) / folded.reduce((sum, [, n]) => sum + n, 0)
    const mean = folded.reduce((sum, [y]) => sum + y, 0) / folded.length
    expect(other.points[0].y).toBeCloseTo(pooled, 10)
    expect(other.points[0].y).not.toBeCloseTo(mean, 0)
  })

  it('a RATE "Other" with measured values but no counts to pool them by is a gap with the reason', () => {
    const noCounts = twelve.map((s) => ({ ...s, points: s.points.map((p) => ({ ...p, n: /^suite-0[0-6]$/.test(s.key) ? p.n : 0 })) }))
    const model = buildMultiSeriesModel({ series: noCounts, metric: RATE })
    expect(model.lines[7].points.every((p) => p.y === null && p.reason === OTHER_RATE_REASON)).toBe(true)
  })

  it('a RATE "Other" from the server is drawn as sent (recomputed from counts there), last, with the counts from truncated_axes', () => {
    const kept = Array.from({ length: 7 }, (_, i) => line(`s${i}`, [90 - i, 91 - i], 500 - i))
    // The server's recomputed rate: NOT the mean of anything on this side.
    const serverOther: MultiSeriesInputSeries = { key: OTHER_KEY, label: 'Other', points: [{ x: day(0), y: 1, n: 100 }, { x: day(1), y: 2, n: 100 }] }
    const model = buildMultiSeriesModel({
      series: [serverOther, ...kept],
      metric: RATE,
      seriesNoun: 'suites',
      meta: meta({ truncated_axes: { series: { dimension: 'suite', kept: 7, total: 12 } } }),
    })
    expect(model.lines).toHaveLength(8)
    expect(model.lines[7]).toMatchObject({ key: OTHER_KEY, styleIndex: 7 })
    expect(model.lines[7].points.map((p) => p.y)).toEqual([1, 2])
    expect(model.fold).toMatchObject({ folded: 5, total: 12, source: 'server', clientFolded: 0 })
    expect(model.foldNotice).toMatch(/12 suites.*other 5/)
  })

  it('exactly 8 series are all drawn, with no "Other" and no notice', () => {
    const eight = Array.from({ length: 8 }, (_, i) => line(`s${i}`, [i], 10))
    const model = buildMultiSeriesModel({ series: eight, metric: COUNT })
    expect(model.lines).toHaveLength(8)
    expect(model.lines.some((l) => l.other)).toBe(false)
    expect(model.foldNotice).toBeNull()
  })
})

describe('the shared tooltip', () => {
  const withGap = [...THREE, line('auth', [null, 70, 71, 72])]

  it('lists every series for the day, SORTED DESCENDING, with an unmeasured one last as "—" + reason', () => {
    const model = buildMultiSeriesModel({ series: withGap, metric: RATE })
    const tip = tipContentAt(model, 0, { format: pct })
    expect(tip.title).toBe(day(0))
    expect(tip.rows.map((r) => r.label)).toEqual(['payments', 'cart', 'search', 'auth'])
    expect(tip.rows.map((r) => r.value)).toEqual(['97.0%', '90.0%', '84.0%', '—'])
    expect(tip.rows[3].reason).toBe('no evaluated executions in this bucket')
    // Day 1 reorders by value, not by input.
    const next = tipContentAt(model, 1, { format: pct })
    expect(next.rows.map((r) => r.key)).toEqual(['search', 'payments', 'cart', 'auth'])
    expect(next.rows.map((r) => r.y)).toEqual([99, 96, 91, 70])
  })

  it('sorts by value, whatever the input order', () => {
    const model = buildMultiSeriesModel({ series: [line('low', [1]), line('high', [3]), line('mid', [2])], metric: COUNT })
    expect(tipContentAt(model, 0, { format: String }).rows.map((r) => r.key)).toEqual(['high', 'mid', 'low'])
  })

  it('leaves hidden series out, and says how many', () => {
    const model = buildMultiSeriesModel({ series: THREE, metric: RATE })
    const tip = tipContentAt(model, 0, { format: pct, hidden: new Set(['cart']) })
    expect(tip.rows.map((r) => r.key)).toEqual(['payments', 'search'])
    expect(tip.hiddenCount).toBe(1)
    expect(tipText(model, tip)).toBe(`${day(0)}: payments 97.0%, search 84.0%; 1 hidden`)
  })

  it('reads the same in words, reason included, for the keyboard cursor', () => {
    const model = buildMultiSeriesModel({ series: withGap, metric: RATE })
    expect(tipText(model, tipContentAt(model, 0, { format: pct }))).toBe(
      `${day(0)}: payments 97.0%, cart 90.0%, search 84.0%, auth — (no evaluated executions in this bucket)`,
    )
  })
})

describe('placeDirectLabels', () => {
  const overlap = (ys: number[]) => {
    const sorted = [...ys].sort((a, b) => a - b)
    return sorted.some((y, i) => i > 0 && y - sorted[i - 1] < DIRECT_LABEL_HEIGHT - 1e-9)
  }

  it('nudges labels whose lines end at the same height apart, so none overlaps', () => {
    const placed = placeDirectLabels(
      [
        { key: 'a', y: 100 },
        { key: 'b', y: 101 },
        { key: 'c', y: 102 },
        { key: 'd', y: 180 },
      ],
      { top: 10, bottom: 200 },
    )
    expect(placed.fits).toBe(true)
    expect(placed.labels).toHaveLength(4)
    expect(overlap(placed.labels.map((l) => l.y))).toBe(false)
    // Each still points at where its own line ends.
    expect(placed.labels.find((l) => l.key === 'b')?.target).toBe(101)
  })

  it('stays inside the plot: a pile at the bottom is pushed back up', () => {
    const placed = placeDirectLabels(
      [
        { key: 'a', y: 199 },
        { key: 'b', y: 199 },
        { key: 'c', y: 199 },
      ],
      { top: 10, bottom: 200 },
    )
    expect(placed.fits).toBe(true)
    for (const label of placed.labels) {
      expect(label.y + DIRECT_LABEL_HEIGHT / 2).toBeLessThanOrEqual(200 + 1e-9)
      expect(label.y - DIRECT_LABEL_HEIGHT / 2).toBeGreaterThanOrEqual(10 - 1e-9)
    }
    expect(overlap(placed.labels.map((l) => l.y))).toBe(false)
  })

  it('drops to legend-only when they cannot fit without overlapping', () => {
    const requests = Array.from({ length: 8 }, (_, i) => ({ key: `s${i}`, y: 20 }))
    expect(placeDirectLabels(requests, { top: 0, bottom: 8 * DIRECT_LABEL_HEIGHT - 1 })).toEqual({ fits: false, labels: [] })
    expect(placeDirectLabels(requests, { top: 0, bottom: 8 * DIRECT_LABEL_HEIGHT }).fits).toBe(true)
  })

  it('shortens a long name for the gutter', () => {
    expect(directLabelText('payments')).toBe('payments')
    expect(directLabelText('checkout-service-long')).toBe('checkout-se…')
  })
})

describe('legend: hide, isolate, announce', () => {
  const model = buildMultiSeriesModel({ series: THREE, metric: RATE })
  const keys = model.lines.map((l) => l.key)

  it('toggles one series and isolates another (and isolating again shows all)', () => {
    const hidden = toggleHidden(new Set(), 'cart')
    expect([...hidden]).toEqual(['cart'])
    expect([...toggleHidden(hidden, 'cart')]).toEqual([])
    const alone = isolateHidden(new Set(), 'search', keys)
    expect([...alone].sort()).toEqual(['cart', 'payments'])
    expect([...isolateHidden(alone, 'search', keys)]).toEqual([])
  })

  it('words the change for the page announcer', () => {
    expect(visibilityAnnouncement(model, new Set(['cart']), { kind: 'hidden', key: 'cart' })).toBe('cart hidden, 2 of 3 series shown')
    expect(visibilityAnnouncement(model, new Set(), { kind: 'shown', key: 'cart' })).toBe('cart shown, 3 of 3 series shown')
    expect(visibilityAnnouncement(model, new Set(['cart', 'payments']), { kind: 'isolated', key: 'search' })).toBe(
      'only search shown, 1 of 3 series shown',
    )
    expect(visibilityAnnouncement(model, new Set(), { kind: 'all-shown' })).toBe('all 3 series shown')
  })

  it('the table KEEPS a hidden series and marks it, rather than dropping it', () => {
    const chart = multiSeriesToChartSeries(model, new Set(['cart']))
    expect(chart.series.map((s) => s.label)).toEqual(['payments', `cart${HIDDEN_SUFFIX}`, 'search'])
    expect(chart.series[1].points.map((p) => p.y)).toEqual([90, 91, 89, 92])
  })

  it('the table carries a gap as a gap, with its reason', () => {
    const gappy = buildMultiSeriesModel({ series: [line('a', [1, null])], metric: COUNT })
    expect(multiSeriesToChartSeries(gappy).series[0].points[1]).toMatchObject({ y: null, measured: false, reason: expect.any(String) })
  })
})

describe('comparable:false', () => {
  it('reads a not-comparable envelope into a banner with the reason — the lines are still drawn', () => {
    const model = buildMultiSeriesModel({
      series: THREE,
      metric: RATE,
      meta: meta({ comparability: { comparable: false, reason: 'R2 runs 4 suites that R1 did not', reason_code: 'different_suites' } }),
    })
    expect(model.banner).toBe('Not directly comparable: R2 runs 4 suites that R1 did not. The comparison is still shown.')
    expect(model.lines).toHaveLength(3)
  })

  it('an explicit comparability overrides the meta, and a missing reason still says so', () => {
    expect(comparabilityFromMeta(meta({}))).toEqual({ comparable: true, reason: null })
    const model = buildMultiSeriesModel({ series: THREE, metric: RATE, comparability: { comparable: false, reason: null } })
    expect(model.banner).toMatch(/the API did not say why/)
    expect(buildMultiSeriesModel({ series: THREE, metric: RATE }).banner).toBeNull()
  })
})

describe('release-over-release alignment', () => {
  const r1: MultiSeriesInputSeries = {
    key: 'r1',
    label: 'R1',
    points: [0, 1, 2].map((i) => ({ x: addUtcDays('2026-03-02', i), y: 90 + i, n: 40 })),
  }
  const r2: MultiSeriesInputSeries = {
    key: 'r2',
    label: 'R2',
    points: [0, 1, 2, 3].map((i) => ({ x: addUtcDays('2026-04-10', i), y: 80 + i, n: 40 })),
  }

  it('puts both releases on "days since release start", both from day 0', () => {
    const model = buildMultiSeriesModel({ series: [r1, r2], metric: RATE, alignment: 'release-start' })
    expect(model.xTitle).toBe(ALIGNED_X_TITLE)
    expect(model.xs).toEqual(['0', '1', '2', '3'])
    expect(model.lines[0].points[0]).toMatchObject({ x: '0', y: 90, date: '2026-03-02' })
    expect(model.lines[1].points[0]).toMatchObject({ x: '0', y: 80, date: '2026-04-10' })
    expect(model.lines[0].points[3].y).toBeNull()
  })

  it('the table rows name the relative day AND the absolute dates; the tooltip does too', () => {
    const model = buildMultiSeriesModel({ series: [r1, r2], metric: RATE, alignment: 'release-start' })
    const chart = multiSeriesToChartSeries(model)
    expect(chart.x_type).toBe('category')
    expect(chart.x_labels?.['1']).toBe('Day 1 (R1 2026-03-03; R2 2026-04-11)')
    const tip = tipContentAt(model, 1, { format: pct })
    expect(tip.title).toBe('Day 1')
    expect(tipText(model, tip)).toBe('Day 1: R1 (2026-03-03) 91.0%, R2 (2026-04-11) 81.0%')
  })
})

describe('the SVG point budget: 8 × 366', () => {
  it('never draws more than 366 days per line, and says how many were dropped', () => {
    const long = { key: 'a', label: 'a', points: Array.from({ length: 400 }, (_, i) => ({ x: day(i), y: i, n: 1 })) }
    const model = buildMultiSeriesModel({ series: [long], metric: COUNT })
    expect(model.xs).toHaveLength(SVG_POINT_LIMIT)
    expect(model.xs[model.xs.length - 1]).toBe(day(399))
    expect(model.capped).toEqual({ shown: SVG_POINT_LIMIT, total: 400 })
  })

  it('12 series over 400 days draw at most 8 × 366 points', () => {
    const many = Array.from({ length: 12 }, (_, s) => ({
      key: `s${s}`,
      label: `s${s}`,
      points: Array.from({ length: 400 }, (_, i) => ({ x: day(i), y: i, n: 1 + s })),
    }))
    const model = buildMultiSeriesModel({ series: many, metric: COUNT })
    expect(model.lines.reduce((sum, l) => sum + l.points.length, 0)).toBeLessThanOrEqual(8 * 366)
  })
})

describe('multiSeriesInputFromChartData', () => {
  it('reads chart-data grouped by day × series, naming the server "Other"', () => {
    const input = multiSeriesInputFromChartData({
      kind: 'series',
      dimensions: ['day', 'suite'],
      x_type: 'time',
      series: [
        { key: 'cart', label: 'Cart', points: [{ x: day(0), y: 1, n: 1 }] },
        { key: OTHER_KEY, label: 'other', points: [{ x: day(0), y: 2, n: 2 }] },
      ],
    })
    expect(input.map((s) => s.label)).toEqual(['Cart', OTHER_LABEL])
  })
})
