/**
 * The chart-type registry and the rounding rule every part-to-whole chart in
 * the catalogue shares (VIZ-401 / VIZ-402).
 */
import { describe, expect, it } from 'vitest'
import {
  MAX_PIE_CATEGORIES,
  chooseChart,
  largestRemainderPercents,
  offersPie,
  type ChartRequest,
} from './chartCatalog'

const sum = (values: number[]) => Number(values.reduce((a, b) => a + b, 0).toFixed(10))

describe('largestRemainderPercents', () => {
  /**
   * Table test. `naive` is what `Math.round(v / total * 1000) / 10` gives —
   * kept in the table so each row says WHY it is here: every row whose naive
   * sum is not 100 is a row the largest-remainder rule exists for.
   */
  const CASES: { name: string; values: number[]; expected: number[] }[] = [
    { name: 'the story: 880 / 60 / 20 / 40', values: [880, 60, 20, 40], expected: [88, 6, 2, 4] },
    // 33.333… three times: naive gives 33.3 × 3 = 99.9. The extra tenth goes to
    // the LAST of the tied slices, which is the worked example in the story.
    { name: 'thirds (33.3 / 33.3 / 33.4)', values: [1, 1, 1], expected: [33.3, 33.3, 33.4] },
    // naive 16.7 × 6 = 100.2; four tenths are owed, and ties pay out from the back
    { name: 'sixths', values: [1, 1, 1, 1, 1, 1], expected: [16.6, 16.6, 16.7, 16.7, 16.7, 16.7] },
    // naive 14.3 × 7 = 100.1
    { name: 'sevenths', values: [1, 1, 1, 1, 1, 1, 1], expected: [14.2, 14.3, 14.3, 14.3, 14.3, 14.3, 14.3] },
    // a bigger remainder outranks a tie: 1/7 (.857) is paid before the three 2/7s (.714)
    { name: 'mixed remainders', values: [1, 2, 2, 2], expected: [14.3, 28.5, 28.6, 28.6] },
    { name: 'one status only is a full ring', values: [17], expected: [100] },
    { name: 'a zero bucket stays zero', values: [1, 0], expected: [100, 0] },
    { name: 'a slice under 2%', values: [990, 8, 2], expected: [99, 0.8, 0.2] },
    { name: 'nothing at all', values: [], expected: [] },
    { name: 'all zero: zeros, never NaN', values: [0, 0, 0], expected: [0, 0, 0] },
  ]

  for (const { name, values, expected } of CASES) {
    it(`${name}`, () => {
      const percents = largestRemainderPercents(values)
      expect(percents).toEqual(expected)
      if (values.some((v) => v > 0)) expect(sum(percents)).toBe(100)
    })
  }

  it('sums to exactly 100.0 where naive per-slice rounding does not', () => {
    for (const values of [[1, 1, 1], [1, 1, 1, 1, 1, 1], [1, 1, 1, 1, 1, 1, 1], [1, 2, 2, 2]]) {
      const total = values.reduce((a, b) => a + b, 0)
      const naive = sum(values.map((v) => Math.round((v / total) * 1000) / 10))
      const exact = sum(largestRemainderPercents(values))
      expect(exact, `${values}`).toBe(100)
      // The row earns its place: naive rounding really does miss 100 here.
      expect(naive, `${values}: naive rounding already sums to 100`).not.toBe(100)
    }
  })

  it('never hands a bucket a bigger share than a bigger bucket', () => {
    const percents = largestRemainderPercents([500, 300, 200])
    expect(percents[0]).toBeGreaterThan(percents[1])
    expect(percents[1]).toBeGreaterThan(percents[2])
  })
})

describe('chooseChart — the registry, not the caller, picks the chart type', () => {
  const partToWhole = (categories: number, extra: Partial<ChartRequest> = {}): ChartRequest => ({
    intent: 'part-to-whole',
    categories,
    ...extra,
  })

  it('draws a donut for a part-to-whole breakdown at or under the category limit', () => {
    expect(MAX_PIE_CATEGORIES).toBe(5)
    for (const n of [1, 2, 4, 5]) {
      const choice = chooseChart(partToWhole(n))
      expect(choice.type, `${n} categories`).toBe('donut')
      expect(choice.alternatives).toContain('ranked-bar')
      expect(offersPie(partToWhole(n))).toBe(true)
    }
  })

  it('renders a ranked horizontal bar, and offers NO pie, past 5 categories', () => {
    const choice = chooseChart(partToWhole(6))
    expect(choice.type).toBe('ranked-bar')
    expect(choice.alternatives).not.toContain('donut')
    expect(offersPie(partToWhole(6))).toBe(false)
    // The chart says why in its own words, with both numbers in it.
    expect(choice.reason).toContain('6')
    expect(choice.reason).toContain('5')
  })

  it('overrides a caller that asked for a donut: the rule belongs to the registry', () => {
    const asked = partToWhole(9, { preferred: 'donut' })
    expect(chooseChart(asked).type).toBe('ranked-bar')
    expect(offersPie(asked)).toBe(false)
    // …and honours the preference when the rule allows it.
    expect(chooseChart(partToWhole(3, { preferred: 'donut' })).type).toBe('donut')
  })

  it('stacks a composition and never offers a pie for one', () => {
    const request: ChartRequest = { intent: 'composition', categories: 4, seriesPerCategory: 4 }
    const choice = chooseChart(request)
    expect(choice.type).toBe('stacked-bar')
    expect(choice.alternatives).toContain('grouped-bar')
    expect(offersPie(request)).toBe(false)
  })

  it('diverges a change chart that has negative values', () => {
    const request: ChartRequest = { intent: 'change', categories: 6, hasNegatives: true }
    const choice = chooseChart(request)
    expect(choice.type).toBe('diverging-bar')
    expect(choice.alternatives).not.toContain('donut')
    expect(choice.reason).toMatch(/zero/i)
  })

  it('ranks a ranking', () => {
    expect(chooseChart({ intent: 'ranking', categories: 10 }).type).toBe('ranked-bar')
  })
})
