/**
 * EXEC-04b / TRND-09: the execution-metrics shapes as chart models and
 * states. The server's fixed histogram buckets are drawn bucket for bucket
 * (labelled edges, the open top bucket as the overflow); a `measured: false`
 * block is a not-measured state with the server's reason; a change the
 * server could not compute is "—", never "no change".
 */
import { describe, expect, it } from 'vitest'
import type { DurationHistogram, ExecutionMetrics } from '@/types/executionMetrics'
import {
  changeText,
  changeTitle,
  changeTone,
  executionState,
  histogramFromServer,
  histogramTakeaway,
  percentileText,
  serverHistogramCaption,
  SERVER_TOO_OLD_REASON,
  slowestFromServer,
  slowTestName,
  suiteRuleText,
} from './ExecutionTimeSection.model'

/** The contract's 15 fixed buckets (edges 0, 10, 25 … 300000 ms, the last open-ended), zero-filled. */
const EDGES = [0, 10, 25, 50, 100, 250, 500, 1_000, 2_500, 5_000, 10_000, 30_000, 60_000, 300_000]
function histogram(counts: Record<number, number>, extra: Partial<DurationHistogram> = {}): DurationHistogram {
  const buckets = EDGES.map((lower, i) => ({ lower_ms: lower, upper_ms: i + 1 < EDGES.length ? EDGES[i + 1] : null, count: counts[lower] ?? 0 }))
  const n = buckets.reduce((sum, b) => sum + b.count, 0)
  return { measured: true, reason: null, n, buckets, p50_ms: 180, p95_ms: 2_400, ...extra }
}

describe('histogramFromServer', () => {
  it('draws every fixed bucket in order, its edges labelled, the open top bucket as the overflow', () => {
    const model = histogramFromServer(histogram({ 0: 40, 100: 7, 300_000: 1 }))
    expect(model.buckets).toHaveLength(14)
    expect(model.buckets.map((b) => b.label).slice(0, 3)).toEqual(['0ms – 10ms', '10ms – 25ms', '25ms – 50ms'])
    expect(model.buckets[model.buckets.length - 1]).toMatchObject({ from: 300_000, to: null, count: 1, overflow: true, label: '≥ 5m 0s' })
    expect(model.buckets.filter((b) => b.overflow)).toHaveLength(1)
    expect(model.buckets.find((b) => b.from === 100)?.count).toBe(7)
    // Zero-filled buckets stay on the axis: two scopes compare bucket for bucket.
    expect(model.buckets.find((b) => b.from === 10)?.count).toBe(0)
    expect(model).toMatchObject({ counted: 48, total: 48, excluded: 0, excludedStatement: null })
  })

  it('never claims more placed than the bars hold: the placed count is the buckets’ own sum', () => {
    const model = histogramFromServer({ ...histogram({ 0: 3 }), n: 5 })
    expect(model.counted).toBe(3)
    expect(model.total).toBe(5)
  })

  it('nothing counted: no bars at all (the chart says nothing carried a duration)', () => {
    expect(histogramFromServer(histogram({})).buckets).toEqual([])
  })

  it('says what the buckets are, and the takeaway names the window p50 / p95 and n', () => {
    const h = histogram({ 0: 40 })
    expect(serverHistogramCaption(h)).toMatch(/the last bucket counts everything from 5m 0s up\.$/)
    expect(histogramTakeaway(h)).toBe('p50 180ms · p95 2.4s over 40 test executions')
    expect(histogramTakeaway({ ...h, measured: false, n: 0 })).toBeUndefined()
  })
})

describe('executionState', () => {
  const base = { measured: true, reason: null } as unknown as ExecutionMetrics

  it('loading until the read answers; drawn once it does', () => {
    expect(executionState({ data: undefined, error: undefined, isLoading: true }).status).toBe('loading')
    expect(executionState({ data: base, error: undefined, isLoading: false }).status).toBe('ready')
  })

  it('a measured:false answer (the denied or empty shape) is not-measured with the server’s reason, as text', () => {
    const state = executionState({ data: { ...base, measured: false, reason: 'no run in this scope in the window' }, error: undefined, isLoading: false })
    expect(state).toEqual({ status: 'not-measured', reason: 'no run in this scope in the window', meta: null })
  })

  it('a block of its own: measured:false speaks for that frame; a block the server does not send says so', () => {
    const data = { ...base, duration_histogram: { measured: false, reason: 'no test execution in the window carried a duration' } } as unknown as ExecutionMetrics
    expect(executionState({ data, error: undefined, isLoading: false }, (d) => d.duration_histogram)).toMatchObject({
      status: 'not-measured',
      reason: 'no test execution in the window carried a duration',
    })
    expect(executionState({ data: base, error: undefined, isLoading: false }, (d) => d.duration_histogram)).toMatchObject({
      status: 'not-measured',
      reason: SERVER_TOO_OLD_REASON,
    })
  })

  it('errors are classified (403 forbidden; a 500 an error with Retry)', () => {
    const retry = () => undefined
    expect(executionState({ data: undefined, error: { response: { status: 403, headers: {} } }, isLoading: false }).status).toBe('forbidden')
    const failed = executionState({ data: undefined, error: { response: { status: 500, headers: {} } }, isLoading: false, retry })
    expect(failed).toMatchObject({ status: 'error', error: { kind: 'server' }, retry })
  })
})

describe('figures and words', () => {
  it('a percentile that was not measured is "not reported", never 0', () => {
    expect(percentileText({ n: 0, p50_ms: null, p95_ms: null, measured: false, reason: 'x' }, 'p50_ms')).toBe('not reported')
    expect(percentileText({ n: 4, p50_ms: 5_000, p95_ms: 6_100, measured: true, reason: null }, 'p95_ms')).toBe('6.1s')
  })

  it('a change: signed, toned (slower is worse), "—" when the server could not compare', () => {
    expect([changeText(12.5), changeTone(12.5)]).toEqual(['+12.5%', 'worse'])
    expect([changeText(-3), changeTone(-3)]).toEqual(['−3.0%', 'better'])
    expect([changeText(0), changeTone(0)]).toEqual(['no change', 'none'])
    expect([changeText(null), changeTone(null)]).toEqual(['—', 'none'])
    expect(changeTitle(12.5, 'previous 14 days')).toBe('12.5% slower than the previous 14 days')
    expect(changeTitle(null, 'previous 14 days')).toBe('Not compared: no figure for the previous 14 days')
  })

  it('a slow test is named test (suite); an unnamed one by its fingerprint, never blank', () => {
    expect(slowTestName({ test_name: 'test_pay', suite_name: 'checkout', test_fingerprint: 'abcdef0123' })).toBe('test_pay (checkout)')
    expect(slowTestName({ test_name: null, suite_name: null, test_fingerprint: 'abcdef0123' })).toBe('Unnamed test abcdef01')
  })

  it('the slowest tests keep the server order and their executions as the run count', () => {
    const model = slowestFromServer(
      [
        { test_fingerprint: 'a', test_name: 'slow', suite_name: null, n: 6, p50_ms: 1_700, p95_ms: 40_000, max_ms: 41_000 },
        { test_fingerprint: 'b', test_name: 'unknown', suite_name: null, n: 2, p50_ms: null, p95_ms: null, max_ms: null },
      ],
      20,
    )
    expect(model.rows).toEqual([
      { name: 'slow', p95: 40_000, runs: 6 },
      { name: 'unknown', p95: null, runs: 2 },
    ])
  })

  it('the regression rule in words, from the server’s own constants', () => {
    expect(suiteRuleText({ recent_runs: 5, baseline_runs: 20, ratio: 1.5, same_branch: true })).toBe(
      'A suite is listed when the median of its last 5 runs is at least 1.5× the median of the 20 runs before them, on the same branch.',
    )
  })
})
