import { describe, expect, it } from 'vitest'
import { parseEmailAvailability, parseTrendSummary, type TrendSummary } from './trends'

/** A complete, valid `/analytics/trend-summary` body (the §1.3 shape). */
function valid(): TrendSummary {
  return {
    window_days: 14,
    today: '2026-09-18',
    measured: true,
    reason: null,
    days_with_runs: 10,
    empty_days: 4,
    active_day_share: 0.7143,
    executions: { total: 120, passed: 100, failed: 10, broken: 5, skipped: 5, evaluated: 115 },
    pass_rate: 87,
    variance: { stddev_pp: 4.12, stability: 86.27 },
    gap: { silent_days: 4, start: '2026-09-15', end: '2026-09-18', ongoing: true, expected_runs_missed: 4 },
    last_run_day: '2026-09-14',
    previous_run_day: '2026-09-13',
    untagged_share: 0,
    confidence: {
      score: 71,
      dimensions: [
        { id: 'data_coverage', label: 'Data coverage', score: 71.43, weight: 0.4 },
        { id: 'sample_size', label: 'Sample size', score: 100, weight: 0.25 },
        { id: 'variance_stability', label: 'Variance stability', score: 86.27, weight: 0.2 },
        { id: 'tag_quality', label: 'Tag quality', score: 100, weight: 0.15 },
      ],
      verdict: 'HEALTHY',
      band: 'High',
    },
  }
}

describe('parseTrendSummary (TRND-06, the §0 defensive guard)', () => {
  it.each<[string, unknown]>([
    ['undefined', undefined],
    ['null', null],
    ['an empty object', {}],
    ['a string', 'HEALTHY'],
    ['an array', [valid()]],
    ['a wrong-typed confidence', { ...valid(), confidence: 'x' }],
    ['dimensions that are not a list', { ...valid(), confidence: { ...valid().confidence, dimensions: {} } }],
    ['a dimension without a score', { ...valid(), confidence: { ...valid().confidence, dimensions: [{ id: 'x', label: 'X', weight: 1 }] } }],
    ['a string score', { ...valid(), confidence: { ...valid().confidence, score: '71' } }],
    ['executions missing a count', { ...valid(), executions: { total: 1, passed: 1, failed: 0, broken: 0, skipped: 0 } }],
    ['a NaN pass rate', { ...valid(), pass_rate: Number.NaN }],
    ['a gap that is not an object', { ...valid(), gap: 'paused' }],
    ['a gap with a string length', { ...valid(), gap: { ...valid().gap, silent_days: '4' } }],
    ['no variance', { ...valid(), variance: undefined }],
    ['a numeric verdict', { ...valid(), confidence: { ...valid().confidence, verdict: 3 } }],
  ])('%s → null', (_name, raw) => {
    expect(parseTrendSummary(raw)).toBeNull()
  })

  it('a valid body is returned whole', () => {
    expect(parseTrendSummary(valid())).toEqual(valid())
  })

  it('null pass rate, null stddev, no gap and no run days are "not measured", kept as null (never 0)', () => {
    const empty = {
      ...valid(),
      measured: false,
      reason: 'No executions in this window.',
      pass_rate: null,
      variance: { stddev_pp: null, stability: 0 },
      gap: null,
      last_run_day: null,
      previous_run_day: null,
    }
    const parsed = parseTrendSummary(empty)
    expect(parsed).not.toBeNull()
    expect(parsed?.pass_rate).toBeNull()
    expect(parsed?.variance.stddev_pp).toBeNull()
    expect(parsed?.gap).toBeNull()
    expect(parsed?.reason).toBe('No executions in this window.')
  })

  it('an absent gap reads as no gap; unknown extra keys are ignored', () => {
    const { gap: _gap, ...rest } = valid()
    const parsed = parseTrendSummary({ ...rest, extra: 'new key' })
    expect(parsed?.gap).toBeNull()
    expect(parsed).not.toHaveProperty('extra')
  })
})

describe('parseEmailAvailability', () => {
  it.each<[unknown]>([[undefined], [null], [{}], [{ available: 'yes' }], ['ok']])('%j → null', (raw) => {
    expect(parseEmailAvailability(raw)).toBeNull()
  })

  it('keeps the reason text and code', () => {
    expect(parseEmailAvailability({ available: false, code: 'not_configured', reason: 'Email is not configured.' })).toEqual({
      available: false,
      code: 'not_configured',
      reason: 'Email is not configured.',
    })
    expect(parseEmailAvailability({ available: true })).toEqual({ available: true, code: null, reason: null })
  })
})
