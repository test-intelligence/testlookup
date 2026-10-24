import { describe, expect, it } from 'vitest'

import { policyBands, type PolicySummary } from '@/services/policyService'
import {
  bandsSource,
  bandsTitle,
  decidedBy,
  describeDowngrade,
  goLineLabel,
  liveChip,
  noGoZoneLabel,
  passRateTone,
  readinessBands,
  versusGoLine,
} from './passRateBands'

const SUMMARY_POLICY = {
  id: 'p-1',
  name: 'Payments gate',
  version: 4,
  level: 'project',
  bands: { orange_min: 92, yellow_min: 97, green_min: 99 },
}

function effective(over: Partial<PolicySummary> = {}): PolicySummary {
  return {
    id: 'p-2',
    project_id: 'proj-1',
    version: 3,
    name: 'Checkout gate',
    description: null,
    rules: {
      schema_version: 1,
      thresholds: { go_threshold: 20, no_go_threshold: 55, pass_rate_minimum: 90, pass_rate_hard_floor_factor: 0.7 },
      dimension_weights: {
        user_impact: 0.25, env_sensitivity: 0.1, reproducibility: 0.15, regression_likely: 0.2,
        hist_recurrence: 0.1, blast_radius: 0.15, diagnosis_conf: 0.05,
      },
      rules: [],
      pass_rate_bands: { orange_min: 85, yellow_min: 93, green_min: 98 },
    },
    is_active: true,
    is_draft: false,
    created_by: 'u-1',
    activated_by: 'u-1',
    activated_at: '2026-10-02T00:00:00Z',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: null,
    ...over,
  }
}

describe('readinessBands', () => {
  it('no policy reads the default bands, labelled', () => {
    for (const none of [undefined, null]) {
      expect(readinessBands(none)).toEqual({
        goMin: 95, noGoBelow: 90, greenMin: 99, policyName: null, version: null, level: 'default',
      })
    }
  })

  it('reads the summary policy', () => {
    expect(readinessBands(SUMMARY_POLICY)).toEqual({
      goMin: 97, noGoBelow: 92, greenMin: 99, policyName: 'Payments gate', version: 4, level: 'project',
    })
    expect(readinessBands({ ...SUMMARY_POLICY, level: 'system' }).level).toBe('system')
  })

  it('reads the effective policy (a project row, or the system default)', () => {
    expect(readinessBands(effective())).toEqual({
      goMin: 93, noGoBelow: 85, greenMin: 98, policyName: 'Checkout gate', version: 3, level: 'project',
    })
    expect(readinessBands(effective({ project_id: null })).level).toBe('system')
  })

  it('a band missing from the policy reads its default, per key', () => {
    const partial = effective()
    partial.rules = { ...partial.rules, pass_rate_bands: { yellow_min: 96 } as never }
    expect(readinessBands(partial)).toMatchObject({ goMin: 96, noGoBelow: 90, greenMin: 99 })
    const none = effective()
    none.rules = { ...none.rules, pass_rate_bands: undefined }
    expect(readinessBands(none)).toMatchObject({ goMin: 95, noGoBelow: 90, greenMin: 99 })
    expect(policyBands(null)).toEqual({ orange_min: 90, yellow_min: 95, green_min: 99 })
    expect(policyBands({ rules: { pass_rate_bands: { orange_min: Number.NaN } } })).toEqual({
      orange_min: 90, yellow_min: 95, green_min: 99,
    })
  })
})

describe('the labels', () => {
  const policy = readinessBands(SUMMARY_POLICY)
  const defaults = readinessBands(null)

  it('names the lines and their source', () => {
    expect(goLineLabel(defaults)).toBe('GO ≥ 95%')
    expect(noGoZoneLabel(defaults)).toBe('No-Go < 90%')
    expect(goLineLabel(policy)).toBe('GO ≥ 97%')
    expect(noGoZoneLabel(policy)).toBe('No-Go < 92%')
    expect(bandsSource(defaults)).toBe('default bands')
    expect(bandsSource(policy)).toBe('Payments gate v4')
    expect(bandsTitle(defaults)).toBe('Bands: default')
    expect(bandsTitle(policy)).toBe('Bands: Payments gate v4')
    expect(goLineLabel(readinessBands({ ...SUMMARY_POLICY, bands: { ...SUMMARY_POLICY.bands, yellow_min: 97.5 } }))).toBe('GO ≥ 97.5%')
  })

  it('versusGoLine: at, below and above', () => {
    expect(versusGoLine(95, defaults)).toBe('at the GO line (95%, default bands)')
    expect(versusGoLine(95.3, defaults)).toBe('at the GO line (95%, default bands)')
    expect(versusGoLine(94, policy)).toBe('3 pp below the GO line (97%, Payments gate v4)')
    expect(versusGoLine(97, defaults)).toBe('2 pp above the GO line (95%, default bands)')
    expect(versusGoLine(88, defaults)).toBe('7 pp below the GO line (95%, default bands)')
  })

  it('liveChip: nothing at or above the GO line, then below it, then the No-Go zone', () => {
    expect(liveChip(96, defaults)).toBeNull()
    expect(liveChip(95, defaults)).toBeNull()
    expect(liveChip(93, defaults)).toBe('below the GO line (95%)')
    expect(liveChip(90, defaults)).toBe('below the GO line (95%)')
    expect(liveChip(85, defaults)).toBe('in the No-Go zone (< 90%)')
    expect(liveChip(93, policy)).toBe('below the GO line (97%)')
    expect(liveChip(91, policy)).toBe('in the No-Go zone (< 92%)')
    expect([passRateTone(96, defaults), passRateTone(93, defaults), passRateTone(85, defaults)]).toEqual([
      'success', 'warning', 'danger',
    ])
  })
})

describe('describeDowngrade', () => {
  it('words the three hard caps', () => {
    expect(describeDowngrade('p0_defects:2>0')).toBe('Open critical (P0) defects: 2, cap 0')
    expect(describeDowngrade('flaky:12>10')).toBe('Flaky tests: 12, cap 10')
    expect(describeDowngrade('new_failures_24h:25>20')).toBe('New failures in 24 h: 25, cap 20')
  })

  it('shows an unknown token verbatim', () => {
    expect(describeDowngrade('coverage:40>30')).toBe('coverage:40>30')
    expect(describeDowngrade('something else')).toBe('something else')
  })
})

describe('decidedBy', () => {
  it('names the policy and its bands', () => {
    expect(decidedBy(readinessBands({ ...SUMMARY_POLICY, bands: { orange_min: 90, yellow_min: 95, green_min: 99 } }))).toBe(
      'Decided by Payments gate v4 (project policy): Go at 95% or more, Conditional from 90%, No-Go below 90%.',
    )
    expect(decidedBy(readinessBands({ ...SUMMARY_POLICY, level: 'system' }))).toBe(
      'Decided by Payments gate v4 (system default policy): Go at 97% or more, Conditional from 92%, No-Go below 92%.',
    )
  })

  it('says when no policy is published, and what rule decides instead', () => {
    expect(decidedBy(readinessBands(null))).toBe(
      'Decided by the built-in rule (no release-gate policy is published for this project or the deployment): ' +
        'Go at 95% or more with no open defects and at most 5 flaky tests, Conditional from 85% with at most 5 open defects, otherwise No-Go.',
    )
  })
})
