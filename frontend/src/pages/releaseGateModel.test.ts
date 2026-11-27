import { describe, expect, it } from 'vitest'
import {
  describeBandHold,
  describePassRateBands,
  describeVerdictThresholds,
  rulesNotChecked,
  verdictThresholds,
} from './releaseGateModel'

const frozen = (thresholds: Record<string, unknown>) => ({ policy_snapshot: { document: { thresholds } } })

describe('verdictThresholds (D-104)', () => {
  it('reads a stored decision from its frozen policy document', () => {
    expect(verdictThresholds(frozen({
      go_threshold: 20, no_go_threshold: 55, pass_rate_minimum: 90, pass_rate_hard_floor_factor: 0.7,
    }))).toEqual({ go: 20, noGo: 55, passMin: 90, floorPct: 90 * 0.7 })
  })

  it('reads a quick look from the top of its snapshot', () => {
    expect(verdictThresholds({ go_threshold: 20, no_go_threshold: 55, threshold: 90, no_go_floor_pct: 63 }))
      .toEqual({ go: 20, noGo: 55, passMin: 90, floorPct: 63 })
  })

  it('never fills a missing number in, and needs both halves of the floor', () => {
    expect(verdictThresholds(frozen({ pass_rate_minimum: 90 }))).toEqual({
      go: undefined, noGo: undefined, passMin: 90, floorPct: undefined,
    })
    expect(verdictThresholds(null)).toEqual({})
    expect(verdictThresholds({ threshold: '90' })).toEqual({
      go: undefined, noGo: undefined, passMin: undefined, floorPct: undefined,
    })
  })
})

describe('describeVerdictThresholds (D-104)', () => {
  it('says all four in the finding’s words, with 90 × 0.7 shown as 63', () => {
    expect(describeVerdictThresholds(frozen({
      go_threshold: 20, no_go_threshold: 55, pass_rate_minimum: 90, pass_rate_hard_floor_factor: 0.7,
    }))).toBe('Go under risk 20 and at 90% or more · No-Go at risk 55 or under 63%')
  })

  it('leaves each missing part out', () => {
    expect(describeVerdictThresholds({ no_go_floor_pct: 63 })).toBe('No-Go under 63%')
    expect(describeVerdictThresholds({ threshold: 92.5 })).toBe('Go at 92.5% or more')
    expect(describeVerdictThresholds({ go_threshold: 15, no_go_threshold: 40 })).toBe('Go under risk 15 · No-Go at risk 40')
  })

  it('is null when there is nothing to say', () => {
    expect(describeVerdictThresholds({ verdict_driver: 'composite_risk' })).toBeNull()
    expect(describeVerdictThresholds(undefined)).toBeNull()
  })
})

describe('pass-rate bands (review F1)', () => {
  const bands = { orange_min: 90, yellow_min: 95, green_min: 99 }

  it('the thresholds line ends with the bands that can floor the verdict', () => {
    expect(describeVerdictThresholds({ threshold: 90 }, bands))
      .toBe('Go at 90% or more · Pass-rate bands: No-Go under 90%, Conditional under 95%')
    expect(describePassRateBands(null)).toBeNull()
    expect(describePassRateBands({ orange_min: 80 })).toBe('Pass-rate bands: No-Go under 80%')
  })

  it('names the edge the run fell under', () => {
    expect(describeBandHold(92, 'orange', bands)).toBe('Held back by the pass-rate band: 92.0% is under 95% (Conditional).')
    expect(describeBandHold(85, 'red', bands)).toBe('Held back by the pass-rate band: 85.0% is under 90% (No-Go).')
    expect(describeBandHold(99.5, 'green', bands)).toBeNull()
    expect(describeBandHold(null, 'red', bands)).toBeNull()
    expect(describeBandHold(85, 'red', null)).toBeNull()
  })
})

describe('rulesNotChecked (D-142)', () => {
  it('lists the names, falling back to the type, skipping junk', () => {
    expect(rulesNotChecked({
      rules_not_checked: [
        { name: 'Flaky test limit', type: 'flaky_recurrence' },
        { type: 'perf_regression' },
        { name: '  ' },
        'nope',
      ],
    })).toEqual(['Flaky test limit', 'perf_regression'])
    expect(rulesNotChecked({})).toEqual([])
    expect(rulesNotChecked(null)).toEqual([])
  })
})
