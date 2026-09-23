/**
 * VIZ-402 — ranked, grouped and stacked bars. Every edge case in the story is
 * a case here: descending order, full-precision labels, a value axis that
 * starts at zero, the fixed status order, the absolute ↔ 100% toggle whose
 * segments sum to exactly 100.0, middle-truncated names, ties at the top-N
 * boundary, the diverging change chart and pagination past 50 bars.
 */
import { describe, expect, it } from 'vitest'
import { validateChartResponse } from './chartState'
import { validateChartSeries, type SeriesChart } from '@/lib/viz/contracts'
import {
  ELLIPSIS,
  MIN_BAR_ROW_HEIGHT,
  barPlotHeight,
  minRowHeight,
  MAX_BARS_PER_PAGE,
  MAX_BAR_LABEL,
  PERCENT_MODE_NOTE,
  TIE_CAP_EXTRA,
  barSeries,
  barsFromSeries,
  chartResponseFromFailureCategories,
  chartResponseFromTopFailing,
  middleTruncate,
  rankedModel,
  statusBarModel,
  statusBarSeries,
  statusRowsFromSeries,
  type BarInput,
} from './BarChart.model'

const bar = (label: string, value: number): BarInput => ({ key: label, label, value })
const sum = (values: number[]) => Number(values.reduce((a, b) => a + b, 0).toFixed(10))

describe('rankedModel', () => {
  const ITEMS = [bar('checkout', 12), bar('auth', 41), bar('search', 7), bar('billing', 23)]

  it('sorts descending and labels every bar with its full-precision count', () => {
    const model = rankedModel(ITEMS)
    expect(model.bars.map((b) => b.label)).toEqual(['auth', 'billing', 'checkout', 'search'])
    expect(model.bars.map((b) => b.value)).toEqual([41, 23, 12, 7])
    expect(model.bars.map((b) => b.valueLabel)).toEqual(['41', '23', '12', '7'])
  })

  it('labels a big count in full, never abbreviated', () => {
    const model = rankedModel([bar('a', 1_234_567)])
    expect(model.bars[0].valueLabel).toBe('1,234,567')
    expect(model.bars[0].valueLabel).not.toMatch(/[kKmM]/)
  })

  it('starts the value axis at zero', () => {
    for (const items of [ITEMS, [bar('a', 900), bar('b', 880)], [bar('only', 3)]]) {
      const model = rankedModel(items)
      expect(model.domain[0], JSON.stringify(items)).toBe(0)
      expect(model.domain[1]).toBeGreaterThanOrEqual(Math.max(...items.map((i) => i.value)))
      expect(model.diverging).toBe(false)
    }
  })

  it('breaks ties deterministically, by label', () => {
    const model = rankedModel([bar('zeta', 5), bar('alpha', 5), bar('mid', 9)])
    expect(model.bars.map((b) => b.label)).toEqual(['mid', 'alpha', 'zeta'])
  })

  describe('ties at the top-N boundary', () => {
    // 9 clear leaders, then five tests all tied on 5, then the rest.
    const items = [
      ...Array.from({ length: 9 }, (_, i) => bar(`lead-${i}`, 50 - i)),
      ...Array.from({ length: 5 }, (_, i) => bar(`tied-${i}`, 5)),
      bar('below', 1),
    ]

    it('includes the ties past top N and says so in the chart’s own text', () => {
      const model = rankedModel(items, { topN: 10 })
      expect(model.bars).toHaveLength(14) // 9 leaders + all 5 tied on the 10th place
      expect(model.ties).toBe(4)
      expect(model.bars.map((b) => b.label)).not.toContain('below')
      expect(model.bars.filter((b) => b.tied)).toHaveLength(5)
      const note = model.notes.join(' ')
      expect(note).toMatch(/tie/i)
      expect(note).toContain('10')
    })

    it('caps the ties at N + 5 and says it capped them', () => {
      const many = [
        ...Array.from({ length: 9 }, (_, i) => bar(`lead-${i}`, 50 - i)),
        ...Array.from({ length: 20 }, (_, i) => bar(`tied-${String(i).padStart(2, '0')}`, 5)),
      ]
      const model = rankedModel(many, { topN: 10 })
      expect(TIE_CAP_EXTRA).toBe(5)
      expect(model.bars).toHaveLength(15)
      expect(model.ties).toBe(5)
      const note = model.notes.join(' ')
      expect(note).toMatch(/tie/i)
      expect(note).toContain('15')
    })

    it('says nothing about ties when there are none', () => {
      const model = rankedModel(items.slice(0, 9), { topN: 10 })
      expect(model.ties).toBe(0)
      expect(model.notes.join(' ')).not.toMatch(/tie/i)
    })
  })

  describe('a change chart with negative values', () => {
    const change = [bar('up', 5), bar('down', -8), bar('flat', 0), bar('small', 2)]

    it('diverges around zero on a symmetric axis', () => {
      const model = rankedModel(change)
      expect(model.diverging).toBe(true)
      // Symmetric, and NICE on both sides: -10..10, not -8..8.
      expect(model.domain).toEqual([-10, 10])
      expect(model.domain[0]).toBeLessThan(0)
      expect(model.domain[0] + model.domain[1]).toBe(0)
      expect(model.bars.map((b) => b.value)).toEqual([5, 2, 0, -8])
      expect(model.bars.map((b) => b.valueLabel)).toEqual(['5', '2', '0', '-8'])
      expect(model.notes.join(' ')).toMatch(/zero/i)
    })

    it('keeps zero on the axis even when every change is negative', () => {
      const model = rankedModel([bar('a', -3), bar('b', -9)])
      expect(model.diverging).toBe(true)
      expect(model.domain).toEqual([-10, 10])
    })
  })

  describe('pagination past 50 bars', () => {
    const many = Array.from({ length: 120 }, (_, i) => bar(`test-${String(i).padStart(3, '0')}`, 1000 - i))

    it('draws at most 50 bars and states the total, leaving the position to the controls', () => {
      expect(MAX_BARS_PER_PAGE).toBe(50)
      const first = rankedModel(many)
      expect(first.bars).toHaveLength(50)
      expect(first.page).toBe(0)
      expect(first.pages).toBe(3)
      expect(first.total).toBe(120)
      // `page` / `pages` ARE the position; the footer draws it once, next to
      // the buttons it is the `aria-describedby` for.
      expect(first.notes.join(' ')).toContain('120 bars in all')
    })

    it('shows the next page from the same ordering', () => {
      const second = rankedModel(many, { page: 1 })
      expect(second.bars).toHaveLength(50)
      expect(second.bars[0].label).toBe('test-050')
      expect(second.page).toBe(1)
      expect(second.pages).toBe(3)
      const last = rankedModel(many, { page: 2 })
      expect(last.bars).toHaveLength(20)
      // A page past the end clamps rather than drawing nothing.
      expect(rankedModel(many, { page: 99 }).page).toBe(2)
    })

    it('says nothing about pages when everything fits on one', () => {
      const model = rankedModel(many.slice(0, 50))
      expect(model.pages).toBe(1)
      expect(model.notes.join(' ')).not.toMatch(/page/i)
    })
  })

  describe('long test names', () => {
    const long =
      'tests.integration.checkout.test_payment_gateway_declines_an_expired_card_and_retries_once'

    it('middle-truncates the drawn label and keeps the full name for the tooltip and the table', () => {
      const model = rankedModel([{ key: 'k', label: long, value: 3 }])
      const only = model.bars[0]
      expect(only.short).not.toBe(long)
      expect(only.short.length).toBeLessThanOrEqual(MAX_BAR_LABEL)
      expect(only.short).toContain(ELLIPSIS)
      expect(only.short.startsWith('tests.integration')).toBe(true)
      expect(only.short.endsWith('once')).toBe(true)
      // The full name is what the tooltip and the table read.
      expect(only.label).toBe(long)
      const table = barSeries(model) as SeriesChart
      expect(table.series[0].points[0].x).toBe(long)
    })

    it('leaves a short name alone', () => {
      expect(middleTruncate('auth')).toBe('auth')
      expect(middleTruncate('x'.repeat(MAX_BAR_LABEL))).toBe('x'.repeat(MAX_BAR_LABEL))
    })

    it('truncates in the MIDDLE, where two names differ least', () => {
      const a = middleTruncate(`${'same_prefix_'.repeat(3)}ending_alpha`)
      const b = middleTruncate(`${'same_prefix_'.repeat(3)}ending_beta`)
      expect(a).not.toBe(b)
      expect(a.indexOf(ELLIPSIS)).toBeGreaterThan(0)
      expect(a.indexOf(ELLIPSIS)).toBeLessThan(a.length - 1)
    })
  })
})

describe('statusBarModel', () => {
  const ROWS = [
    { key: 'checkout', label: 'checkout', counts: { passed: 80, failed: 10, broken: 5, skipped: 5 } },
    { key: 'auth', label: 'auth', counts: { passed: 45, failed: 5 } },
  ]

  it('stacks the statuses in the fixed order, whatever order the counts arrive in', () => {
    const model = statusBarModel([
      { key: 'a', label: 'a', counts: { skipped: 1, broken: 2, failed: 3, passed: 4, unknown: 5 } },
    ])
    expect(model.statuses).toEqual(['passed', 'failed', 'broken', 'skipped', 'unknown'])
    expect(model.bars[0].segments.map((s) => s.status)).toEqual([
      'passed',
      'failed',
      'broken',
      'skipped',
      'unknown',
    ])
  })

  it('leaves out a status nothing has, and keeps the order of the rest', () => {
    const model = statusBarModel(ROWS)
    expect(model.statuses).toEqual(['passed', 'failed', 'broken', 'skipped'])
  })

  it('keeps the bars in the order they arrived (a composition is not a ranking)', () => {
    expect(statusBarModel(ROWS).bars.map((b) => b.label)).toEqual(['checkout', 'auth'])
  })

  it('stacks absolute values on an axis that starts at zero', () => {
    const model = statusBarModel(ROWS, { layout: 'stacked', mode: 'absolute' })
    expect(model.domain).toEqual([0, 100])
    expect(model.bars[0].total).toBe(100)
    expect(model.bars[0].segments.map((s) => s.plotted)).toEqual([80, 10, 5, 5])
  })

  it('sizes a GROUPED axis by the largest single segment, still from zero', () => {
    const model = statusBarModel(ROWS, { layout: 'grouped', mode: 'absolute' })
    expect(model.domain).toEqual([0, 80])
    expect(model.domain[0]).toBe(0)
  })

  it('toggles to 100% stacked whose segments sum to exactly 100.0', () => {
    const rows = [
      { key: 'thirds', label: 'thirds', counts: { passed: 1, failed: 1, broken: 1 } },
      ...ROWS,
    ]
    const model = statusBarModel(rows, { layout: 'stacked', mode: 'percent' })
    expect(model.domain).toEqual([0, 100])
    for (const drawn of model.bars) {
      expect(sum(drawn.segments.map((s) => s.plotted)), drawn.label).toBe(100)
      expect(sum(drawn.segments.map((s) => s.percent)), drawn.label).toBe(100)
    }
    // Three equal thirds, and the skipped segment this bar does not have.
    expect(model.bars[0].segments.map((s) => s.plotted)).toEqual([33.3, 33.3, 33.4, 0])
    // The absolute values survive the toggle: the tooltip and the table read them.
    expect(model.bars[1].segments.map((s) => s.value)).toEqual([80, 10, 5, 5])
  })

  it('draws an all-zero bar as nothing rather than as 100% of something', () => {
    const model = statusBarModel([{ key: 'empty', label: 'empty', counts: {} }], { mode: 'percent' })
    expect(model.bars[0].total).toBe(0)
    expect(model.bars[0].segments.every((s) => s.plotted === 0)).toBe(true)
    expect(model.empty).toBe(true)
  })

  it('middle-truncates a long bar label and paginates past 50 bars', () => {
    const many = Array.from({ length: 60 }, (_, i) => ({
      key: `s${i}`,
      label: `suite_with_a_very_long_name_number_${String(i).padStart(3, '0')}`,
      counts: { passed: 1, failed: 1 },
    }))
    const model = statusBarModel(many)
    expect(model.bars).toHaveLength(MAX_BARS_PER_PAGE)
    expect(model.pages).toBe(2)
    expect(model.total).toBe(60)
    // The note states the SIZE of the set; the page position is drawn beside
    // the page buttons (`barPageLabel`), so it is never said twice.
    expect(model.notes.join(' ')).toContain('60 bars in all')
    expect(model.notes.join(' ')).not.toContain('Page 1 of 2')
    expect(model.bars[0].short).toContain(ELLIPSIS)
    expect(model.bars[0].label).toBe(many[0].label)
  })

  it('turns the model into a C3 series the frame table reads, FOLLOWING the mode', () => {
    const absolute = statusBarSeries(statusBarModel(ROWS))
    const checked = validateChartSeries(absolute)
    expect(checked.ok ? [] : checked.errors).toEqual([])
    const counts = absolute as SeriesChart
    expect(counts.series.map((s) => s.key)).toEqual(['passed', 'failed', 'broken', 'skipped'])
    expect(counts.series[0].points.map((p) => p.y)).toEqual([80, 45])

    // In 100% mode the PLOT shows shares, so the table shows shares: a table
    // that kept the counts would be answering a different question from the
    // chart beside it. `n` stays the true count either way.
    const percent = statusBarModel(ROWS, { mode: 'percent' })
    const shares = statusBarSeries(percent) as SeriesChart
    expect(validateChartSeries(shares).ok).toBe(true)
    expect(shares.series[0].points.map((p) => p.y)).toEqual(
      percent.bars.map((b) => b.segments[0].percent),
    )
    expect(shares.series[0].points.map((p) => p.n)).toEqual([80, 45])
    // …and the model says which side of the toggle is live, in words.
    expect(percent.notes).toContain(PERCENT_MODE_NOTE)
    expect(statusBarModel(ROWS).notes).not.toContain(PERCENT_MODE_NOTE)
  })
})

describe('adapters', () => {
  it('reads /analytics/top-failing', () => {
    const items = [
      { test_name: 'test_a', fail_count: 12, test_fingerprint: 'fp-a' },
      { test_name: 'test_b', fail_count: 30 },
    ]
    const response = chartResponseFromTopFailing({ items })
    const checked = validateChartResponse(response)
    expect(checked.ok ? [] : checked.errors).toEqual([])
    // Keyed by fingerprint (or by position), LABELLED by name: one adapter,
    // and the bars carry the display names the reader knows.
    expect(barsFromSeries(response.series)).toEqual([
      { key: 'fp-a', label: 'test_a', value: 12 },
      { key: 'test_b#1', label: 'test_b', value: 30 },
    ])
    expect(rankedModel(barsFromSeries(response.series)).bars.map((b) => b.label)).toEqual(['test_b', 'test_a'])
  })

  it('reads /analytics/failure-categories', () => {
    const items = [
      { category: 'PRODUCT_BUG', count: 9 },
      { category: 'INFRASTRUCTURE', count: 4 },
    ]
    const response = chartResponseFromFailureCategories({ items })
    const checked = validateChartResponse(response)
    expect(checked.ok ? [] : checked.errors).toEqual([])
    expect(barsFromSeries(response.series)).toEqual([
      { key: 'PRODUCT_BUG', label: 'PRODUCT_BUG', value: 9 },
      { key: 'INFRASTRUCTURE', label: 'INFRASTRUCTURE', value: 4 },
    ])
  })

  it('reads a chart-data series, preferring the axis display names over the keys', () => {
    const series: SeriesChart = {
      kind: 'series',
      dimensions: ['suite'],
      x_type: 'category',
      series: [
        {
          key: 'failures',
          label: 'Failures',
          points: [
            { x: 'checkout', y: 12, n: 12 },
            { x: 'auth', y: null, n: 0 },
            { x: 'search', y: 3, n: 3 },
          ],
        },
      ],
      x_labels: { checkout: 'Checkout' },
    }
    // A null point is no data, not a zero-height bar.
    expect(barsFromSeries(series)).toEqual([
      { key: 'checkout', label: 'Checkout', value: 12 },
      { key: 'search', label: 'search', value: 3 },
    ])
  })

  it('reads a two-dimension chart-data series as status rows', () => {
    const series: SeriesChart = {
      kind: 'series',
      dimensions: ['suite', 'status'],
      x_type: 'category',
      series: [
        { key: 'failed', label: 'Failed', points: [{ x: 'auth', y: 4, n: 4 }, { x: 'cart', y: 1, n: 1 }] },
        { key: 'passed', label: 'Passed', points: [{ x: 'auth', y: 40, n: 40 }, { x: 'cart', y: 9, n: 9 }] },
      ],
    }
    const rows = statusRowsFromSeries(series)
    expect(rows).toEqual([
      { key: 'auth', label: 'auth', counts: { failed: 4, passed: 40 } },
      { key: 'cart', label: 'cart', counts: { failed: 1, passed: 9 } },
    ])
    // The series arrived failed-first; the model still stacks passed first.
    expect(statusBarModel(rows).statuses).toEqual(['passed', 'failed'])
  })
})

// ── What the first Linux baselines showed ────────────────────────────────────

describe('the value axis ends on a nice number, with evenly spaced ticks', () => {
  // Rounded: 0.2 - 0.1 is not 0.1 in binary floating point.
  const evenlySpaced = (ticks: number[]) =>
    new Set(ticks.slice(1).map((tick, i) => (tick - ticks[i]).toFixed(9))).size === 1

  it('ranked: 41 is drawn on 0-50 by 10, not on "0 15 30 41"', () => {
    const model = rankedModel([bar('a', 41), bar('b', 33), bar('c', 4)])
    expect(model.domain).toEqual([0, 50])
    expect(model.ticks).toEqual([0, 10, 20, 30, 40, 50])
  })

  it.each([
    [31, [0, 10, 20, 30, 40]],
    [46, [0, 10, 20, 30, 40, 50]],
    [9, [0, 2, 4, 6, 8, 10]],
    [300, [0, 100, 200, 300]],
  ])('ranked: a maximum of %s gets ticks %j', (max, ticks) => {
    const model = rankedModel([bar('top', max), bar('low', 1)])
    expect(model.ticks).toEqual(ticks)
    expect(model.domain).toEqual([ticks[0], ticks[ticks.length - 1]])
  })

  it('never ends below the data, and starts at zero', () => {
    for (const values of [[1], [3, 2], [999, 1], [12_345, 7], [0.4, 0.2]]) {
      const model = rankedModel(values.map((value, i) => bar(`b${i}`, value)))
      expect(model.domain[0]).toBe(0)
      expect(model.domain[1]).toBeGreaterThanOrEqual(Math.max(...values))
      expect(model.ticks[model.ticks.length - 1]).toBe(model.domain[1])
      expect(evenlySpaced(model.ticks), JSON.stringify(model.ticks)).toBe(true)
    }
  })

  it('stacked: sized by the largest TOTAL, grouped by the largest segment, both nice', () => {
    const rows = [
      { key: 'checkout', label: 'checkout', counts: { passed: 180, failed: 22, broken: 8, skipped: 10 } },
      { key: 'auth', label: 'auth', counts: { passed: 140, failed: 6 } },
    ]
    const stacked = statusBarModel(rows, { layout: 'stacked' })
    expect(stacked.domain).toEqual([0, 250])
    expect(evenlySpaced(stacked.ticks)).toBe(true)
    const grouped = statusBarModel(rows, { layout: 'grouped' })
    expect(grouped.domain).toEqual([0, 200])
    expect(grouped.ticks).toEqual([0, 50, 100, 150, 200])
  })

  it('100% mode stays 0-100, ticked at 25', () => {
    const model = statusBarModel([{ key: 'a', label: 'a', counts: { passed: 3, failed: 1 } }], { mode: 'percent' })
    expect(model.domain).toEqual([0, 100])
    expect(model.ticks).toEqual([0, 25, 50, 75, 100])
  })
})

describe('the plot grows with the rows it draws', () => {
  it('gives a ranked or stacked row the measured 20 px a label needs', () => {
    expect(MIN_BAR_ROW_HEIGHT).toBe(20)
    expect(minRowHeight('ranked')).toBe(20)
    expect(minRowHeight('stacked', 4)).toBe(20)
  })

  it('gives a grouped row room for every status bar at 9 px or more', () => {
    // (4 x 9 + 3 x 4 gap) over the 80% of the band Recharts draws bars in.
    expect(minRowHeight('grouped', 4)).toBe(60)
    for (const statuses of [2, 3, 4, 5]) {
      const row = minRowHeight('grouped', statuses)
      const thickness = (row * 0.8 - (statuses - 1) * 4) / statuses
      expect(thickness, `${statuses} statuses`).toBeGreaterThanOrEqual(9)
    }
  })

  it("keeps the caller's height for a few bars, and grows past it for a 50-bar page", () => {
    expect(barPlotHeight(5, MIN_BAR_ROW_HEIGHT, 260, 68)).toBe(260)
    expect(barPlotHeight(50, MIN_BAR_ROW_HEIGHT, 260, 68)).toBe(1068)
    // Every row gets at least its minimum, whatever the page size.
    for (const rows of [1, 9, 13, 27, 50]) {
      const height = barPlotHeight(rows, MIN_BAR_ROW_HEIGHT, 260, 68)
      expect((height - 68) / rows).toBeGreaterThanOrEqual(MIN_BAR_ROW_HEIGHT)
    }
  })
})
