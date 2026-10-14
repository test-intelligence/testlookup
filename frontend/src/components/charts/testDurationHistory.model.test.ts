/**
 * EXEC-04: one test's duration across runs — oldest first, gaps kept as gaps,
 * the ratio against the baseline (else the window p50), and the outlier rule.
 */
import { describe, expect, it } from 'vitest'
import type { DurationBaseline, TestDurationStats } from '@/types/executionMetrics'
import {
  OUTLIER_RATIO,
  buildDurationHistoryModel,
  durationHistoryTipContent,
  ratioText,
  type DurationHistoryInputPoint,
} from './testDurationHistory.model'
import { tooltipText } from './tooltip'

const point = (n: number, duration_ms: number | null, extra: Partial<DurationHistoryInputPoint> = {}): DurationHistoryInputPoint => ({
  run_id: `run-${n}`,
  run_label: `Run #${n}`,
  build_number: String(n),
  run_seq: n,
  status: 'PASSED',
  duration_ms,
  created_at: `2026-10-${String(n).padStart(2, '0')}T10:00:00Z`,
  ...extra,
})

/** Newest first, as the API sends it: runs 6 (newest) .. 1. */
const HISTORY = [point(6, 900), point(5, 3600, { status: 'FAILED' }), point(4, null), point(3, 880), point(2, 920), point(1, 910)]
const BASELINE: DurationBaseline = { mean_ms: 900, stddev_ms: 40, p95_ms: 980, sample_count: 14, last_observed_ms: 900, last_observed_at: null }
const STATS: TestDurationStats = { window_days: 30, n: 14, p50_ms: 905, p95_ms: 1800, max_ms: 3600, measured: true, reason: null }

describe('buildDurationHistoryModel', () => {
  it('orders the runs oldest first and keeps a run without a duration as a gap', () => {
    const model = buildDurationHistoryModel({ history: HISTORY, durationStats: STATS, baseline: BASELINE })
    expect(model.points.map((p) => p.label)).toEqual(['Run #1', 'Run #2', 'Run #3', 'Run #4', 'Run #5', 'Run #6'])
    expect(model.points[3].durationMs).toBeNull()
    expect(model.points[3].ratio).toBeNull()
    expect(model.measured).toBe(5)
  })

  it('measures each run against the baseline mean and rings a 4x run as an outlier', () => {
    const model = buildDurationHistoryModel({ history: HISTORY, durationStats: STATS, baseline: BASELINE })
    expect(model.reference).toBe('baseline')
    const slow = model.points[4]
    expect(slow.ratio).toBe(4)
    expect(slow.outlier).toBe(true)
    expect(model.outliers).toBe(1)
    expect(model.points.filter((p) => p.outlier)).toHaveLength(1)
    expect(ratioText(slow, model.reference)).toBe('4× baseline')
  })

  it('the band is mean ± 1σ (never below zero), and the axis reaches it', () => {
    const model = buildDurationHistoryModel({ history: [point(1, 50)], baseline: { ...BASELINE, mean_ms: 30, stddev_ms: 50 } })
    expect(model.baseline).toMatchObject({ mean: 30, low: 0, high: 80, samples: 14 })
    expect(model.yMax).toBe(80)
  })

  it('falls back to the window p50 when the test has no baseline', () => {
    const model = buildDurationHistoryModel({ history: HISTORY, durationStats: STATS, baseline: null })
    expect(model.baseline).toBeNull()
    expect(model.reference).toBe('p50')
    expect(model.points[4].ratio).toBeCloseTo(3.98, 2)
    expect(model.points[4].outlier).toBe(true)
  })

  it('no reference at all: no ratio and no outlier claimed', () => {
    const model = buildDurationHistoryModel({ history: HISTORY })
    expect(model.reference).toBeNull()
    expect(model.points.every((p) => p.ratio === null && !p.outlier)).toBe(true)
    expect(model.p50).toBeNull()
    expect(model.statsReason).toBeNull()
  })

  it('unmeasured window stats draw no reference lines and carry the reason', () => {
    const model = buildDurationHistoryModel({
      history: HISTORY,
      durationStats: { ...STATS, measured: false, reason: 'no execution in the window carried a duration', p50_ms: null, p95_ms: null },
    })
    expect(model.p50).toBeNull()
    expect(model.p95).toBeNull()
    expect(model.statsReason).toBe('no execution in the window carried a duration')
  })

  it('the axis covers the slowest run and the p95 line', () => {
    const model = buildDurationHistoryModel({ history: [point(1, 100)], durationStats: { ...STATS, p95_ms: 5000 } })
    expect(model.yMax).toBe(5000)
  })

  it(`the outlier threshold is ${OUTLIER_RATIO}x, inclusive`, () => {
    const model = buildDurationHistoryModel({ history: [point(2, 1800), point(1, 1799)], baseline: BASELINE })
    expect(model.points.map((p) => p.outlier)).toEqual([false, true])
  })
})

describe('durationHistoryTipContent', () => {
  it('says "not reported" for a gap, never 0 ms', () => {
    const model = buildDurationHistoryModel({ history: HISTORY, baseline: BASELINE })
    const text = tooltipText(durationHistoryTipContent(model, 3))
    expect(text).toContain('Run #4')
    expect(text).toContain('Duration: not reported')
    expect(text).not.toMatch(/0ms/)
  })

  it('states the ratio, the phases and the worker when the run carried them', () => {
    const model = buildDurationHistoryModel({
      history: [point(1, 3600, { setup_ms: 120, call_ms: 3400, teardown_ms: 80, worker_id: 'gw1' })],
      baseline: BASELINE,
    })
    const text = tooltipText(durationHistoryTipContent(model, 0))
    expect(text).toContain('Duration: 3.6s')
    expect(text).toContain('Against: 4× baseline')
    expect(text).toContain('setup 120ms · test 3.4s · teardown 80ms')
    expect(text).toContain('Worker: gw1')
    expect(text).toMatch(/an outlier/)
  })
})
