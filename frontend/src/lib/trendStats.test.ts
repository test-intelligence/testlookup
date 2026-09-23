/**
 * VIZ-405 — `trendStats`. Every expected number in the GOLDEN blocks was worked
 * out by hand (the arithmetic is in the comment beside it), not read back from
 * the implementation. The PROPERTY blocks pin the algebra a trend has to obey
 * whatever the data: a shifted series keeps its slope, re-weighting every day
 * by the same factor changes nothing, reversing time negates the slope.
 */
import { describe, expect, it } from 'vitest'
import {
  ANOMALY_MAD_FLOOR_PTS,
  ANOMALY_MAD_THRESHOLD,
  ANOMALY_MIN_EXECUTIONS,
  ANOMALY_RULE,
  FLAT_SLOPE_PTS_PER_WEEK,
  INSUFFICIENT_DATA_REASON,
  NOT_DAILY_REASON,
  PERIOD_MIN_DAYS_WITH_RUNS,
  PERIOD_MIN_EXECUTIONS,
  formatTrendFit,
  TREND_MAX_DAYS,
  TREND_MIN_DAYS_WITH_RUNS,
  analyzeTrend,
  detectAnomalies,
  movingAverage,
  periodOverPeriod,
  periodTakeaway,
  trendFrameTakeaway,
  trendRowsForDay,
  trendTakeaway,
  weightedTrend,
  type TrendInputPoint,
} from './trendStats'

const DAY_MS = 86_400_000
const START = Date.UTC(2026, 2, 1) // 2026-03-01
const D = (i: number) => new Date(START + i * DAY_MS).toISOString().slice(0, 10)

/** The fit, or a failed test — never a silent `undefined`. */
const fitted = (points: readonly TrendInputPoint[]) => {
  const fit = weightedTrend(points)
  if (!fit) throw new Error('expected a trend fit')
  return fit
}

/** `[rate, n]` per consecutive UTC day from 2026-03-01; `rate: null` is a gap. */
const series = (spec: readonly (readonly [number | null, number])[]): TrendInputPoint[] =>
  spec.map(([rate, n], i) => ({ x: D(i), rate, n }))

// ── Fixture G: ten days, two gaps (one of them a skip-only day that still
//    carries a count), unequal volumes. ─────────────────────────────────────
const G = series([
  [90, 100], // t0
  [80, 100], // t1
  [null, 40], // t2  gap: 40 executions, every one skipped — NOT a 0 %
  [100, 50], // t3
  [70, 50], // t4
  [90, 100], // t5
  [60, 10], // t6
  [95, 100], // t7
  [null, 25], // t8  gap again
  [85, 100], // t9
])

describe('movingAverage — golden', () => {
  it('pools each day with the six before it, weighted by executions, skipping gaps', () => {
    // t6: window t0..t6, runs on t0 t1 t3 t4 t5 t6
    //     Σn = 100+100+50+50+100+10 = 410
    //     Σ rate·n = 9000+8000+5000+3500+9000+600 = 35100 → 35100/410 = 85.6097561
    // t7: window t1..t7, runs on t1 t3 t4 t5 t6 t7
    //     Σ rate·n = 8000+5000+3500+9000+600+9500 = 35600 → 35600/410 = 86.8292683
    // t8: a gap — no average is drawn ON a day nobody ran anything
    // t9: window t3..t9, runs on t3 t4 t5 t6 t7 t9
    //     Σ rate·n = 5000+3500+9000+600+9500+8500 = 36100 → 36100/410 = 88.0487805
    // t0..t5: the 7-day window reaches before the series starts — no average.
    const ma = movingAverage(G)
    expect(ma.map((p) => p.x)).toEqual([D(6), D(7), D(9)])
    expect(ma[0].value).toBeCloseTo(35100 / 410, 9)
    expect(ma[1].value).toBeCloseTo(35600 / 410, 9)
    expect(ma[2].value).toBeCloseTo(36100 / 410, 9)
    expect(ma.map((p) => [p.days, p.executions])).toEqual([
      [6, 410],
      [6, 410],
      [6, 410],
    ])
  })

  it('never lets a gap in as a zero, even a gap that carries an execution count', () => {
    // If t2 (null, 40 skipped) were read as 0 % at weight 40, t6 would be
    // 35100 / 450 = 78.0 — this pins the 85.6 instead.
    expect(movingAverage(G)[0].value).not.toBeCloseTo(35100 / 450, 3)
    expect(movingAverage(G)[0].value).toBeCloseTo(85.6097561, 6)
  })

  it('needs at least 4 of the 7 days to have runs — gap days do not count toward that', () => {
    // t7, t8, t9 each see only three days with runs in their window.
    const sparse = series([
      [90, 10],
      [90, 10],
      [90, 10],
      [null, 0],
      [null, 0],
      [null, 0],
      [null, 0],
      [80, 10],
      [80, 10],
      [80, 10],
    ])
    expect(movingAverage(sparse)).toEqual([])
  })
})

// ── Fixture W: six steady days at 90 % and one 2-test day at 0 %. ───────────
const W = series([
  [90, 100],
  [90, 100],
  [90, 100],
  [90, 100],
  [90, 100],
  [90, 100],
  [0, 2],
])

describe('weightedTrend — golden', () => {
  it('weights each day by its executions, so a 2-test day barely moves the line', () => {
    // Σw = 602, Σwt = 1500 + 12 = 1512, Σwt² = 5500 + 72 = 5572,
    // Σwy = 54000, Σwty = 135000.
    // slope = (Σw·Σwty − Σwt·Σwy) / (Σw·Σwt² − (Σwt)²)
    //       = (81 270 000 − 81 648 000) / (3 354 344 − 2 286 144)
    //       = −378 000 / 1 068 200 = −0.3538663 per day → −2.4770642 per week.
    // Unweighted, the same data gives −1890/196 = −9.64 per day (−67.5 per week).
    const fit = fitted(W)
    expect(fit.slopePerDay).toBeCloseTo(-378_000 / 1_068_200, 9)
    expect(fit.slopePerWeek).toBeCloseTo((-378_000 / 1_068_200) * 7, 9)
    expect(fit.mean).toBeCloseTo(54_000 / 602, 9)
    expect(Math.abs(fit.slopePerWeek)).toBeLessThan(3)
    // …and what little it moves is no larger than the slope's own standard
    // error, so no direction is claimed from one 2-test day.
    // Residuals r = y − (ȳ + b(t − t̄)); s² = Σ n·r² / (7 − 2); SE = √(s² / Σ n(t − t̄)²).
    const tBar = 1512 / 602
    const residual = (y: number, t: number) => y - (54_000 / 602 + fit.slopePerDay * (t - tBar))
    const ssr = [0, 1, 2, 3, 4, 5].reduce((sum, t) => sum + 100 * residual(90, t) ** 2, 0) + 2 * residual(0, 6) ** 2
    const sxx = 5572 - 1512 ** 2 / 602
    expect(fit.slopeStandardErrorPerWeek).toBeCloseTo(Math.sqrt(ssr / 5 / sxx) * 7, 9)
    expect(fit.slopeStandardErrorPerWeek).toBeGreaterThan(Math.abs(fit.slopePerWeek))
    expect(fit.direction).toBe('flat')
    expect(fit.flatBecause).toBe('within-standard-error')
  })

  it('draws the fitted line across the days it was fitted on, through the weighted means', () => {
    const fit = fitted(W)
    // t̄ = 1512/602; the line passes through (t̄, ȳ).
    const tBar = 1512 / 602
    expect(fit.line.map((p) => p.x)).toEqual([D(0), D(1), D(2), D(3), D(4), D(5), D(6)])
    expect(fit.line[0].value).toBeCloseTo(54_000 / 602 + fit.slopePerDay * (0 - tBar), 9)
    expect(fit.line[6].value).toBeCloseTo(54_000 / 602 + fit.slopePerDay * (6 - tBar), 9)
  })

  it('uses calendar days, not array positions: a gap day still takes up a day', () => {
    // 80 on t0, then a gap, then 82, 84 … on t2..t7: exactly +1 pt a day on the
    // calendar (80 + t). Read by position the line would be +2 a step.
    const withGap: TrendInputPoint[] = [
      { x: D(0), rate: 80, n: 10 },
      { x: D(1), rate: null, n: 0 },
      ...[2, 3, 4, 5, 6, 7].map((t) => ({ x: D(t), rate: 80 + t, n: 10 })),
    ]
    expect(fitted(withGap).slopePerWeek).toBeCloseTo(7, 9)
  })
})

/**
 * 29 days from 2026-03-01 (a Sunday) at `other` %, except the four earlier
 * Sundays t0, t7, t14, t21 at `rates`, and the fifth Sunday t28 at `last`.
 * 100 executions a day.
 */
const sundays = (rates: readonly [number, number, number, number], last: number | null, other = 93): TrendInputPoint[] =>
  series(
    Array.from({ length: 29 }, (_, t) => {
      if (t === 28) return [last, last === null ? 60 : 100] as const
      return [t % 7 === 0 ? rates[t / 7] : other, 100] as const
    }),
  )

// ── Fixture A: four ordinary Sundays, then a collapse on the fifth. ────────
const A = sundays([92, 94, 93, 95], 80)

describe('detectAnomalies — golden', () => {
  it('flags a day more than 3 MADs below the median of the same weekday in the 4 weeks before it', () => {
    // t28's baseline: Sundays t0 t7 t14 t21 = [92 94 93 95] → median 93.5.
    // |dev| = [1.5 0.5 0.5 1.5] → sorted [0.5 0.5 1.5 1.5] → MAD 1.
    // 93.5 − 80 = 13.5 > 3 × 1, and 80 < 92 (every baseline day) → flagged, 13.5 MADs below.
    // t14 (93): Sundays [94 92] → median 93, MAD 1 → 0 below, not flagged.
    const { anomalies, judgedDays } = detectAnomalies(A)
    expect(anomalies).toHaveLength(1)
    expect(anomalies[0]).toMatchObject({ x: D(28), rate: 80, weekday: 'Sunday', median: 93.5, mad: 1, baselineDays: 4 })
    expect(anomalies[0].deviations).toBeCloseTo(13.5, 9)
    // t14..t28 have at least 2 same-weekday days with runs behind them; t0..t13 do not.
    expect(judgedDays).toBe(15)
  })

  it('does NOT flag a day exactly 3 MADs below — the rule is "more than"', () => {
    // Sundays [100 90 80 70] → median 85, |dev| [15 5 5 15] → MAD 10.
    // 85 − 55 = 30 = 3 × 10 → not flagged; 54.9 is → flagged.
    expect(detectAnomalies(sundays([100, 90, 80, 70], 55, 85)).anomalies).toEqual([])
    expect(detectAnomalies(sundays([100, 90, 80, 70], 54.9, 85)).anomalies.map((a) => a.x)).toEqual([D(28)])
  })

  it('handles MAD = 0 without dividing by zero or flagging every dip (MAD floored at 1 pt)', () => {
    const steady = (last: number) => sundays([100, 100, 100, 100], last, 100)
    // Baseline all 100 → MAD 0. Floored at 1: a 0.5-pt dip is 0.5 MADs, not ∞.
    expect(detectAnomalies(steady(99.5)).anomalies).toEqual([])
    // A 4-pt drop is 4 floored MADs → flagged, with a finite score.
    const flagged = detectAnomalies(steady(96)).anomalies
    expect(flagged).toHaveLength(1)
    expect(flagged[0]).toMatchObject({ x: D(28), median: 100, mad: 0, effectiveMad: ANOMALY_MAD_FLOOR_PTS })
    expect(flagged[0].deviations).toBeCloseTo(4, 9)
    expect(Number.isFinite(flagged[0].deviations)).toBe(true)
    // A perfectly constant series flags nothing.
    expect(detectAnomalies(steady(100)).anomalies).toEqual([])
  })

  it('reads a gap as missing, never as a 0 % day to flag', () => {
    expect(detectAnomalies(sundays([92, 94, 93, 95], null)).anomalies).toEqual([])
  })

  it('states its threshold as a constant the text uses', () => {
    expect(ANOMALY_MAD_THRESHOLD).toBe(3)
  })
})

// ── Fixture P: two full weeks; the recent one is worse. ─────────────────────
const P = series([
  [90, 100],
  [90, 100],
  [90, 100],
  [null, 30],
  [90, 100],
  [90, 100],
  [90, 100], // previous 7: 6 days with runs, 600 executions, pooled 90
  [80, 100],
  [80, 100],
  [80, 100],
  [80, 100],
  [80, 100],
  [80, 100],
  [100, 50], // last 7: (80·600 + 100·50) / 650 = 53000/650 = 81.5384615
])

describe('periodOverPeriod — golden', () => {
  it('compares the last 7 days with the previous 7, pooled over executions', () => {
    const period = periodOverPeriod(P)
    expect(period.measurable).toBe(true)
    if (!period.measurable) return
    expect(period.last).toMatchObject({ from: D(7), to: D(13), executions: 650, daysWithRuns: 7 })
    expect(period.last.rate).toBeCloseTo(53_000 / 650, 9)
    expect(period.previous).toMatchObject({ from: D(0), to: D(6), executions: 600, daysWithRuns: 6 })
    expect(period.previous.rate).toBeCloseTo(90, 9)
    expect(period.deltaPts).toBeCloseTo(53_000 / 650 - 90, 9)
    // An unweighted mean of the last week would be 82.86 — not what is reported.
    expect(period.last.rate).not.toBeCloseTo(580 / 7, 2)
  })

  it('reads as one plain sentence', () => {
    expect(periodTakeaway(periodOverPeriod(P))).toBe('Last 7 days 81.5% vs 90.0% the previous 7 (down 8.5 pts; 650 vs 600 executions)')
  })

  it('is not measurable on fewer than 14 days, and says why', () => {
    const period = periodOverPeriod(P.slice(1))
    expect(period.measurable).toBe(false)
    if (period.measurable) return
    expect(period.reason).toMatch(/14 days/)
    expect(periodTakeaway(period)).toBeNull()
  })

  it('leaves the still-filling day out: the last 7 end on the last COMPLETE day', () => {
    // Mark t13 partial: the last 7 become t6..t12 and the previous 7 would start
    // before the series — so nothing is claimed.
    const partial = P.map((p, i) => (i === 13 ? { ...p, partial: true } : p))
    expect(periodOverPeriod(partial).measurable).toBe(false)
    // …and a 15th, still-filling day does not shift the comparison at all.
    const extra = [...P, { x: D(14), rate: 10, n: 3, partial: true }]
    const shifted = periodOverPeriod(extra)
    expect(shifted.measurable && shifted.last.to).toBe(D(13))
  })

  it('is computed even when the long-window overlays are not (a recent incident must show)', () => {
    // 14 days, only 6 with runs: too few for overlays, enough to compare weeks.
    const thin = series([
      [95, 100], [null, 0], [95, 100], [null, 0], [95, 100], [null, 0], [null, 0],
      [70, 100], [null, 0], [70, 100], [null, 0], [70, 100], [null, 0], [null, 0],
    ])
    const analysis = analyzeTrend(thin)
    expect(analysis.available).toBe(false)
    expect(analysis.period.measurable).toBe(true)
    expect(periodTakeaway(analysis.period)).toBe('Last 7 days 70.0% vs 95.0% the previous 7 (down 25.0 pts; 300 vs 300 executions)')
  })

  it('says "no change" rather than "up 0.0"', () => {
    const same = series(Array.from({ length: 14 }, () => [88, 20] as const))
    expect(periodTakeaway(periodOverPeriod(same))).toBe('Last 7 days 88.0% vs 88.0% the previous 7 (no change; 140 vs 140 executions)')
  })
})

// ── The takeaway sentence ───────────────────────────────────────────────────

/** 30 days, gaps on t5, t12 and t19, exactly on the line 95 + slope·t/7. */
const linear = (perWeek: number, base = 95): TrendInputPoint[] =>
  Array.from({ length: 30 }, (_, t) =>
    [5, 12, 19].includes(t)
      ? { x: D(t), rate: null, n: 0 }
      : { x: D(t), rate: base + (perWeek * t) / 7, n: 40 + ((t * 37) % 200) },
  )

describe('trendTakeaway — golden', () => {
  it('reads the story’s sentence for a series falling 0.8 pts a week', () => {
    const analysis = analyzeTrend(linear(-0.8))
    expect(analysis.available).toBe(true)
    expect(trendTakeaway(analysis)).toBe('Pass rate is falling 0.8 pts per week (30 days, 27 days with runs)')
  })

  it('reads "rising" for a rising series', () => {
    expect(trendTakeaway(analyzeTrend(linear(1.5, 80)))).toBe(
      'Pass rate is rising 1.5 pts per week (30 days, 27 days with runs)',
    )
  })

  it('makes no direction claim inside the flat band, and states the band', () => {
    const text = trendTakeaway(analyzeTrend(linear(0.2)))
    expect(text).toBe('Pass rate is flat: under 0.25 pts per week either way (30 days, 27 days with runs)')
    expect(text).not.toMatch(/rising|falling|improving|declining/)
    expect(FLAT_SLOPE_PTS_PER_WEEK).toBe(0.25)
    // Just outside the band, a direction IS stated.
    expect(trendTakeaway(analyzeTrend(linear(0.3)))).toMatch(/^Pass rate is rising 0\.3 pts per week/)
  })

  it('never uses causal wording', () => {
    for (const perWeek of [-0.8, 0, 1.5]) {
      const analysis = analyzeTrend(linear(perWeek))
      const words = [
        trendTakeaway(analysis),
        periodTakeaway(analysis.period),
        ...(analysis.available ? Object.values(analysis.explain) : []),
      ].join(' ')
      expect(words).not.toMatch(/because|due to|caused|driven by|as a result|improv|declin|regress/i)
    }
  })
})

// ── Properties ──────────────────────────────────────────────────────────────

/** A deterministic pseudo-random series (LCG), with gaps, for the property tests. */
function pseudoSeries(seed: number, length = 45): TrendInputPoint[] {
  let state = seed >>> 0
  const next = () => {
    state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0
    return state / 2 ** 32
  }
  return Array.from({ length }, (_, t) => {
    const gap = next() < 0.15
    const rate = 60 + next() * 40
    const n = 1 + Math.floor(next() * 400)
    return gap ? { x: D(t), rate: null, n: 0 } : { x: D(t), rate, n }
  })
}

const SEEDS = [1, 7, 42, 2026, 90210]

describe('weightedTrend — properties', () => {
  it('adding a constant shifts the mean by that constant and leaves the slope alone', () => {
    for (const seed of SEEDS) {
      const base = pseudoSeries(seed)
      const shifted = base.map((p) => (p.rate === null ? p : { ...p, rate: p.rate + 7.5 }))
      const a = fitted(base)
      const b = fitted(shifted)
      expect(b.slopePerDay).toBeCloseTo(a.slopePerDay, 9)
      expect(b.mean).toBeCloseTo(a.mean + 7.5, 9)
    }
  })

  it('scaling every weight by the same factor does not change the slope', () => {
    for (const seed of SEEDS) {
      const base = pseudoSeries(seed)
      const scaled = base.map((p) => ({ ...p, n: p.n * 13 }))
      expect(fitted(scaled).slopePerDay).toBeCloseTo(fitted(base).slopePerDay, 9)
    }
  })

  it('reversing time negates the slope', () => {
    for (const seed of SEEDS) {
      const base = pseudoSeries(seed)
      // Same days, values in reverse order.
      const reversed = base.map((p, i) => ({ ...base[base.length - 1 - i], x: p.x }))
      expect(fitted(reversed).slopePerDay).toBeCloseTo(-fitted(base).slopePerDay, 9)
    }
  })

  it('a flat series has slope exactly 0 and makes no claim', () => {
    for (const level of [0, 42.7, 93.3, 100]) {
      const flat = pseudoSeries(3).map((p) => (p.rate === null ? p : { ...p, rate: level }))
      const fit = fitted(flat)
      expect(fit.slopePerWeek).toBe(0)
      expect(fit.direction).toBe('flat')
      expect(trendTakeaway(analyzeTrend(flat))).toMatch(/^Pass rate is flat/)
    }
  })

  it('the day-weighted slope is not the unweighted one (weighting is real)', () => {
    // Guard against a property suite that would also pass with every n = 1.
    const base = pseudoSeries(42)
    const unit = base.map((p) => ({ ...p, n: p.rate === null ? 0 : 1 }))
    expect(fitted(unit).slopePerDay).not.toBeCloseTo(fitted(base).slopePerDay, 3)
  })
})

// ── The minimum-sample rule and the other refusals ─────────────────────────

describe('analyzeTrend — minimum sample', () => {
  const days = (withRuns: number, span = 10): TrendInputPoint[] =>
    Array.from({ length: span }, (_, t) => (t < withRuns ? { x: D(t), rate: 90 - t, n: 20 } : { x: D(t), rate: null, n: 0 }))

  it(`refuses overlays below ${TREND_MIN_DAYS_WITH_RUNS} days with runs, with the story’s reason`, () => {
    const analysis = analyzeTrend(days(6))
    expect(analysis.available).toBe(false)
    if (analysis.available) return
    expect(analysis.reason).toBe(INSUFFICIENT_DATA_REASON)
    expect(INSUFFICIENT_DATA_REASON).toBe('Needs at least 7 days with runs')
    expect(analysis.daysWithRuns).toBe(6)
    expect(trendTakeaway(analysis)).toBeNull()
  })

  it('offers them at exactly 7', () => {
    const analysis = analyzeTrend(days(7))
    expect(analysis.available).toBe(true)
  })

  it('does not count the still-filling day toward the 7', () => {
    const points = days(7).map((p, i) => (i === 6 ? { ...p, partial: true } : p))
    const analysis = analyzeTrend(points)
    expect(analysis.available).toBe(false)
    expect(analysis.daysWithRuns).toBe(6)
  })

  it('does not count a gap that carries executions', () => {
    const points = days(6).map((p, i) => (i === 8 ? { ...p, rate: null, n: 50 } : p))
    expect(analyzeTrend(points).available).toBe(false)
  })

  it(`refuses a series longer than ${TREND_MAX_DAYS} days, and non-day buckets`, () => {
    const long = Array.from({ length: TREND_MAX_DAYS + 1 }, (_, t) => ({ x: D(t), rate: 90, n: 10 }))
    const tooLong = analyzeTrend(long)
    expect(tooLong.available).toBe(false)
    if (!tooLong.available) expect(tooLong.reason).toMatch(/366/)
    const hourly = Array.from({ length: 10 }, (_, t) => ({ x: `2026-03-01T0${t}:00:00Z`, rate: 90, n: 10 }))
    const notDaily = analyzeTrend(hourly)
    expect(notDaily.available).toBe(false)
    if (!notDaily.available) expect(notDaily.reason).toBe(NOT_DAILY_REASON)
  })

  it('an empty series is simply unavailable', () => {
    const empty = analyzeTrend([])
    expect(empty.available).toBe(false)
    expect(empty.period.measurable).toBe(false)
  })
})

// ── What a reader is told about each statistic ─────────────────────────────

describe('explanations and per-day rows', () => {
  it('every statistic states its method, window and sample size', () => {
    const analysis = analyzeTrend(G)
    expect(analysis.available).toBe(true)
    if (!analysis.available) return
    const { movingAverage: ma, trendLine, anomalies, period } = analysis.explain
    expect(ma).toMatch(/weighted by .*executions/i)
    expect(ma).toMatch(/7 calendar days/)
    // Days with runs t0 t1 t3 t4 t5 t6 t7 t9: 100+100+50+50+100+10+100+100 = 610.
    expect(ma).toMatch(/Sample: 3 days averaged from 8 days with runs \(610 executions\)/)
    expect(trendLine).toMatch(/least-squares/i)
    expect(trendLine).toMatch(/Window: 2026-03-01 to 2026-03-10 \(10 days, 8 with runs, 610 executions\)/)
    expect(trendLine).toMatch(/flat if under 0\.25 or within its standard error/)
    expect(anomalies).toMatch(/more than 3 MADs \(floored at 1 pt\) below the median of the same weekday in the previous 4 weeks/)
    // Ten days: no day has 2 same-weekday days behind it, so none is judged — and that is said.
    expect(anomalies).toMatch(/Sample: 0 days judged, 0 flagged/)
    expect(period).toMatch(/14 days/)
  })

  it('names the still-filling day it left out', () => {
    const points = [...G, { x: D(10), rate: 20, n: 4, partial: true }]
    const analysis = analyzeTrend(points)
    expect(analysis.available && analysis.explain.trendLine).toMatch(/2026-03-11 is still filling and is left out/)
  })

  it('gives a flagged day the rule that flagged it, and shown overlays their values', () => {
    const analysis = analyzeTrend(A)
    const rows = trendRowsForDay(analysis, D(28), { movingAverage: true, trendLine: true })
    const flagged = rows.find((row) => row.key === 'anomaly')
    expect(flagged?.value).toMatch(/80\.0% is 13\.5 MADs below 93\.5%, the median of the 4 previous Sundays/)
    expect(flagged?.value).toContain(ANOMALY_RULE)
    expect(rows.find((row) => row.key === 'movingAverage')?.label).toBe('7-day moving average')
    expect(rows.find((row) => row.key === 'trendLine')?.label).toBe('Trend line')
    expect(rows.find((row) => row.key === 'trendLine')?.value).toMatch(/^\d+\.\d% \(fit\)$/)
    // Overlays that are OFF contribute no row; the anomaly is always stated.
    const bare = trendRowsForDay(analysis, D(28), { movingAverage: false, trendLine: false })
    expect(bare.map((row) => row.key)).toEqual(['anomaly'])
    expect(trendRowsForDay(analysis, D(1), { movingAverage: false, trendLine: false })).toEqual([])
  })
})

// ── Fix round B: the reviewers' series ─────────────────────────────────────

/** Consecutive UTC days from `start`; `n` is one number or one per day. */
const from = (start: string, rates: readonly (number | null)[], n: number | readonly number[]): TrendInputPoint[] => {
  const at = Date.parse(`${start}T00:00:00Z`)
  return rates.map((rate, i) => ({
    x: new Date(at + i * DAY_MS).toISOString().slice(0, 10),
    rate,
    n: typeof n === 'number' ? (rate === null ? 0 : n) : n[i],
  }))
}

/** 2026-03-02 is a Monday. 8 weeks: weekdays 96 % on 400 executions, weekends 88 % on 60. */
const WEEKEND_RATES = Array.from({ length: 56 }, (_, i) => (i % 7 >= 5 ? 88 : 96))
const WEEKEND_N = Array.from({ length: 56 }, (_, i) => (i % 7 >= 5 ? 60 : 400))
const weekend = from('2026-03-02', WEEKEND_RATES, WEEKEND_N)
/** 28 days alternating 90 / 80 on 100 executions. */
const alternating = from('2026-03-02', Array.from({ length: 28 }, (_, i) => (i % 2 ? 80 : 90)), 100)

describe('detectAnomalies — a weekly pattern is not an anomaly (M1)', () => {
  it('flags NOTHING on a suite at 96 % on weekdays and 88 % at weekends (was 16 of 52 judged days)', () => {
    const { anomalies, judgedDays } = detectAnomalies(weekend)
    expect(anomalies).toEqual([])
    // Days 14..55 each have at least 2 earlier same-weekday days with runs.
    expect(judgedDays).toBe(42)
  })

  it('flags nothing on the same pattern with deterministic ±0.5-pt jitter (not only on exact values)', () => {
    const jitter = (i: number) => (((i * 37) % 11) - 5) / 10
    const noisy = weekend.map((p, i) => ({ ...p, rate: (p.rate as number) + jitter(i) }))
    expect(detectAnomalies(noisy).anomalies).toEqual([])
  })

  it('flags NOTHING on an 80 / 90 alternating series (was 12 of 24)', () => {
    // A same-weekday baseline of an alternating series is itself mixed (lag 7
    // is odd): an 80 day sees [90, 80, 90] — median 90, MAD 0. It is NOT
    // flagged because it is not below every day of its baseline: 80 was seen.
    const { anomalies, judgedDays } = detectAnomalies(alternating)
    expect(anomalies).toEqual([])
    expect(judgedDays).toBe(14)
  })

  it('still flags a genuine multi-day incident, every day of its first week', () => {
    // 28 days at 95 % then six at 70 %: each incident day is compared with the
    // same weekday of the four weeks before, all 95 % — 25 MADs (MAD 0 → 1 pt).
    const incident = from('2026-03-02', [...Array(28).fill(95), ...Array(6).fill(70)], 200)
    const flagged = detectAnomalies(incident).anomalies
    expect(flagged.map((a) => a.x)).toEqual(incident.slice(28).map((p) => p.x))
    expect(flagged.every((a) => a.median === 95 && a.deviations === 25 && a.baselineDays === 4)).toBe(true)
  })

  it('with only 10 days of history, judges and flags the incident days that have 2 same-weekday days behind them', () => {
    const short = from('2026-03-02', [...Array(10).fill(95), ...Array(6).fill(70)], 200)
    const { anomalies, judgedDays } = detectAnomalies(short)
    // Days 14 and 15 (2026-03-16, -17) have days 7 and 0 / 8 and 1 behind them.
    expect(anomalies.map((a) => a.x)).toEqual(['2026-03-16', '2026-03-17'])
    expect(judgedDays).toBe(2)
  })

  it('does not flag a day at a level its weekday already reached in the baseline', () => {
    // 2026-03-16 (80) is below Mondays [90, 90] and flagged. 2026-03-30 (80
    // again) sees Mondays [90, 80, 90, 90]: median 90, MAD 0, 10 MADs below —
    // but 80 is no lower than every baseline day, so it is a level this
    // weekday has already reached, not an unusual one.
    const rates = Array.from({ length: 29 }, (_, i) => (i === 14 || i === 28 ? 80 : 90))
    expect(detectAnomalies(from('2026-03-02', rates, 100)).anomalies.map((a) => a.x)).toEqual(['2026-03-16'])
  })

  it(`skips a day under ${ANOMALY_MIN_EXECUTIONS} executions — neither judged nor used in a baseline (M2)`, () => {
    // 21 days at 95 % on 500, then a 1-execution day at 0 %: was flagged at 95 MADs.
    const oneExecution = from('2026-03-02', [...Array(21).fill(95), 0], [...Array(21).fill(500), 1])
    const result = detectAnomalies(oneExecution)
    expect(result.anomalies).toEqual([])
    expect(result.judgedDays).toBe(7)
    // At the minimum it IS judged (and flagged).
    const atMinimum = from('2026-03-02', [...Array(21).fill(95), 0], [...Array(21).fill(500), ANOMALY_MIN_EXECUTIONS])
    expect(detectAnomalies(atMinimum).anomalies.map((a) => a.x)).toEqual(['2026-03-23'])
    // A thin day is not a baseline either: two 1-execution Mondays leave the third Monday unjudged.
    const thinBaseline = from('2026-03-02', [...Array(15).fill(95)], [1, ...Array(6).fill(500), 1, ...Array(7).fill(500)])
    expect(detectAnomalies(thinBaseline).judgedDays).toBe(0)
  })

  it('states the rule — same weekday, lower than each, the execution minimum — in its text', () => {
    expect(ANOMALY_RULE).toBe(
      'Rule: more than 3 MADs below the median of the same weekday in the previous 4 weeks, and below each of them; days under 50 executions are not judged.',
    )
    const analysis = analyzeTrend(from('2026-03-02', [...Array(28).fill(95), 70], 200))
    if (!analysis.available) throw new Error('expected an analysis')
    expect(analysis.explain.anomalies).toMatch(/same weekday in the previous 4 weeks/)
    expect(analysis.explain.anomalies).toMatch(/under 50 executions/)
    const rows = trendRowsForDay(analysis, '2026-03-30', { movingAverage: false, trendLine: false })
    expect(rows[0].value).toBe(
      `70.0% is 25.0 MADs below 95.0%, the median of the 4 previous Mondays (MAD 0.0 pts, floored at 1 pt; 200 executions). ${ANOMALY_RULE}`,
    )
  })
})

describe('periodOverPeriod — a minimum sample per week (M2)', () => {
  it('refuses a week of one 1-execution day, and says what it had', () => {
    // Was "Last 7 days 0.0% vs 95.0% the previous 7 (down 95.0 pts)".
    const thin = from('2026-03-01', [...Array(7).fill(95), null, null, null, null, null, null, 0], [...Array(7).fill(500), 0, 0, 0, 0, 0, 0, 1])
    const period = periodOverPeriod(thin)
    expect(period.measurable).toBe(false)
    if (period.measurable) return
    expect(period.reason).toBe(
      `The last 7 days have 1 execution on 1 day with runs; each week needs at least ${PERIOD_MIN_EXECUTIONS} executions on ${PERIOD_MIN_DAYS_WITH_RUNS} days with runs`,
    )
    expect(periodTakeaway(period)).toBeNull()
  })

  it(`is measurable at exactly ${PERIOD_MIN_EXECUTIONS} executions on ${PERIOD_MIN_DAYS_WITH_RUNS} days, and not one below either`, () => {
    const week = (n: readonly number[]) => from('2026-03-01', Array(14).fill(90), [...Array(7).fill(100), ...n])
    expect(periodOverPeriod(week([34, 33, 33, 0, 0, 0, 0])).measurable).toBe(true)
    expect(periodOverPeriod(week([34, 33, 32, 0, 0, 0, 0])).measurable).toBe(false)
    expect(periodOverPeriod(week([50, 50, 0, 0, 0, 0, 0])).measurable).toBe(false)
  })

  it('states the sample in the sentence', () => {
    expect(periodTakeaway(periodOverPeriod(P))).toBe(
      'Last 7 days 81.5% vs 90.0% the previous 7 (down 8.5 pts; 650 vs 600 executions)',
    )
  })
})

describe('the smaller correctness fixes (3)', () => {
  it('shows a fitted value clamped to 0-100 and marked "(fit)" — a step series fits 102.3 % on its last day', () => {
    const step = from('2026-03-01', [...Array(15).fill(90), ...Array(15).fill(100)], 100)
    const analysis = analyzeTrend(step)
    if (!analysis.available) throw new Error('expected an analysis')
    expect(Math.max(...analysis.fit.line.map((p) => p.value))).toBeGreaterThan(100)
    const last = analysis.fit.line[analysis.fit.line.length - 1].x
    const row = trendRowsForDay(analysis, last, { movingAverage: false, trendLine: true })[0]
    expect(row).toEqual({ key: 'trendLine', label: 'Trend line', value: '100.0% (fit)' })
    expect(formatTrendFit(-3)).toBe('0.0% (fit)')
    expect(formatTrendFit(92.64)).toBe('92.6% (fit)')
  })

  it('counts the same days in the takeaway as the fit used when the still-filling day is left out', () => {
    const points = from('2026-03-01', Array.from({ length: 30 }, (_, i) => 95 - i * 0.2), 100)
    points[29] = { ...points[29], partial: true }
    const analysis = analyzeTrend(points)
    expect(trendTakeaway(analysis)).toBe('Pass rate is falling 1.4 pts per week (29 days, 29 days with runs)')
    expect(analysis.available && analysis.explain.trendLine).toMatch(/2026-03-01 to 2026-03-29 \(29 days, 29 with runs/)
  })

  it('refuses weekly buckets: "daily" means consecutive one-day steps', () => {
    const weekly = Array.from({ length: 12 }, (_, i) => ({ x: D(i * 7), rate: 90 + i * 0.5, n: 500 }))
    const analysis = analyzeTrend(weekly)
    expect(analysis.available).toBe(false)
    if (!analysis.available) expect(analysis.reason).toBe(NOT_DAILY_REASON)
    expect(analysis.period.measurable).toBe(false)
    // A missing day inside an otherwise daily series is refused too.
    const holed = from('2026-03-01', Array(10).fill(90), 50).filter((_, i) => i !== 4)
    expect(analyzeTrend(holed).available).toBe(false)
  })

  it('reports an 80 / 90 alternating series as flat: its -0.27 pts/week slope is under its own standard error', () => {
    // The least-squares slope IS -0.268 pts/week (the series starts high and
    // ends low), but its standard error is 0.848 pts/week: saying "falling"
    // would state noise as a direction.
    const fit = fitted(alternating)
    expect(fit.slopePerWeek).toBeCloseTo(-0.2682, 4)
    expect(fit.slopeStandardErrorPerWeek).toBeCloseTo(0.8481, 4)
    expect(fit.direction).toBe('flat')
    expect(fit.flatBecause).toBe('within-standard-error')
    expect(trendTakeaway(analyzeTrend(alternating))).toBe(
      'Pass rate is flat: its slope, 0.3 pts per week, is within its standard error of 0.8 (28 days, 28 days with runs)',
    )
    // The gallery-shaped case is NOT flattened: -0.83 pts/week at a standard error of 0.44 is a direction.
    const falling = fitted(linear(-0.8))
    expect(falling.direction).toBe('falling')
    expect(falling.flatBecause).toBeNull()
  })

  it('keeps every explanation short enough to be read as a description (under 240 characters)', () => {
    const analysis = analyzeTrend(from('2026-03-01', Array.from({ length: 30 }, (_, i) => 95 - (i % 5)), 1234))
    if (!analysis.available) throw new Error('expected an analysis')
    for (const text of Object.values(analysis.explain)) expect(text.length).toBeLessThanOrEqual(240)
  })
})

describe('trendFrameTakeaway — what the ChartFrame says', () => {
  const on = { movingAverage: true, trendLine: false }
  const off = { movingAverage: false, trendLine: false }
  // 30 days falling 0.8 a week, so the last two weeks are complete and comparable.
  const analysis = analyzeTrend(linear(-0.8))

  it('uses the module sentence while an overlay is on, with the period line beside it', () => {
    const text = trendFrameTakeaway(analysis, on, 'Caller takeaway')
    expect(text).toMatch(/^Pass rate is falling 0\.8 pts per week \(30 days, 27 days with runs\) · Last 7 days /)
  })

  it('keeps the caller’s takeaway while every overlay is off — the period line still shows', () => {
    const text = trendFrameTakeaway(analysis, off, 'Caller takeaway')
    expect(text).toMatch(/^Caller takeaway · Last 7 days \d+\.\d% vs \d+\.\d% the previous 7/)
    expect(trendFrameTakeaway(analysis, off, undefined)).toMatch(/^Last 7 days /)
  })

  it('shows the period line even when the overlays are unavailable', () => {
    const thin = series([
      [95, 100], [null, 0], [95, 100], [null, 0], [95, 100], [null, 0], [null, 0],
      [70, 100], [null, 0], [70, 100], [null, 0], [70, 100], [null, 0], [null, 0],
    ])
    expect(trendFrameTakeaway(analyzeTrend(thin), on, undefined)).toBe(
      'Last 7 days 70.0% vs 95.0% the previous 7 (down 25.0 pts; 300 vs 300 executions)',
    )
  })

  it('says nothing new when nothing is measurable', () => {
    expect(trendFrameTakeaway(analyzeTrend(G), off, 'Kept')).toBe('Kept')
    expect(trendFrameTakeaway(analyzeTrend([]), on, undefined)).toBeUndefined()
  })
})
