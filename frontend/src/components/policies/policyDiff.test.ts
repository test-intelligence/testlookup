import { describe, expect, it } from 'vitest'

import {
  DEFAULT_HARD_CAPS, DEFAULT_KIND_RULES, DEFAULT_PASS_RATE_BANDS, DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS,
  type PolicyDocument, type PolicyRule,
} from '@/services/policyService'
import { POLICY_FIELD_LABELS, diffPolicyDocuments, sentenceLabel } from './policyDiff'

function doc(over: Partial<PolicyDocument> = {}): PolicyDocument {
  return {
    schema_version: 1,
    thresholds: { ...DEFAULT_THRESHOLDS },
    dimension_weights: { ...DEFAULT_WEIGHTS },
    rules: [],
    pass_rate_bands: { ...DEFAULT_PASS_RATE_BANDS },
    hard_caps: { ...DEFAULT_HARD_CAPS },
    kind_rules: { ...DEFAULT_KIND_RULES },
    ...over,
  }
}

const P0_RULE: PolicyRule = { id: 'r-p0', name: 'Block on P0', type: 'open_defect_limit', enabled: true, params: { max: 0, action: 'BLOCK' } }
const texts = (a: PolicyDocument, b: PolicyDocument) => diffPolicyDocuments(a, b).map((change) => change.text)

describe('diffPolicyDocuments', () => {
  it('identical documents differ in nothing', () => {
    expect(diffPolicyDocuments(doc(), doc())).toEqual([])
  })

  it('names a threshold by the editor\'s label, in sentence case', () => {
    const next = doc({ thresholds: { ...DEFAULT_THRESHOLDS, go_threshold: 25, pass_rate_hard_floor_factor: 0.8 } })
    expect(texts(doc(), next)).toEqual(['GO threshold 20 → 25', 'Hard floor factor 0.7 → 0.8'])
    expect(sentenceLabel(POLICY_FIELD_LABELS.thresholds.no_go_threshold.label)).toBe('NO_GO threshold')
    expect(sentenceLabel(POLICY_FIELD_LABELS.thresholds.pass_rate_minimum.label)).toBe('Pass rate minimum')
  })

  it('each other section names its setting by key', () => {
    const next = doc({
      pass_rate_bands: { ...DEFAULT_PASS_RATE_BANDS, yellow_min: 97 },
      hard_caps: { ...DEFAULT_HARD_CAPS, max_flaky_count: 5 },
      dimension_weights: { ...DEFAULT_WEIGHTS, user_impact: 0.3, blast_radius: 0.1 },
    })
    expect(texts(doc(), next)).toEqual([
      'Pass-rate band yellow_min 95 → 97',
      'Hard cap max_flaky_count 10 → 5',
      'Dimension weight user_impact 0.25 → 0.30',
      'Dimension weight blast_radius 0.15 → 0.10',
    ])
  })

  it('a section missing from the older document reads its values as none', () => {
    const older = doc()
    delete older.hard_caps
    expect(texts(older, doc())).toEqual([
      'Hard cap max_p0_defects none → 0',
      'Hard cap max_flaky_count none → 10',
      'Hard cap max_new_failures_24h none → 20',
    ])
  })

  it('failure-kind weighting: on / off, a budget added, removed and changed', () => {
    const on = doc({ kind_rules: { enabled: true, infrastructure: { max_failures: 5, downgrade_to: 'CONDITIONAL_GO' } } })
    expect(texts(doc(), on)).toEqual(['Failure-kind weighting off → on', 'Failure-kind budget infrastructure added'])
    expect(texts(on, doc())).toEqual(['Failure-kind weighting on → off', 'Failure-kind budget infrastructure removed'])
    const tighter = doc({ kind_rules: { enabled: true, infrastructure: { max_failures: 3, downgrade_to: 'CONDITIONAL_GO' } } })
    expect(texts(on, tighter)).toEqual(['Failure-kind budget infrastructure max_failures 5 → 3'])
  })

  it('a rule is matched by id: added, removed, toggled, re-parametrised, renamed', () => {
    const withRule = doc({ rules: [P0_RULE] })
    expect(texts(doc(), withRule)).toEqual(['Rule "Block on P0" added'])
    expect(texts(withRule, doc())).toEqual(['Rule "Block on P0" removed'])
    expect(texts(withRule, doc({ rules: [{ ...P0_RULE, enabled: false }] }))).toEqual(['Rule "Block on P0" enabled → disabled'])
    expect(texts(withRule, doc({ rules: [{ ...P0_RULE, params: { max: 1, action: 'BLOCK' } }] }))).toEqual([
      'Rule "Block on P0" params changed (max 0 → 1)',
    ])
    expect(texts(withRule, doc({ rules: [{ ...P0_RULE, name: 'Block on P0/P1' }] }))).toEqual([
      'Rule "Block on P0" renamed to "Block on P0/P1"',
    ])
    // Reordering the same rules is not a change.
    const other: PolicyRule = { ...P0_RULE, id: 'r-flaky', name: 'Flaky' }
    expect(diffPolicyDocuments(doc({ rules: [P0_RULE, other] }), doc({ rules: [other, P0_RULE] }))).toEqual([])
  })

  it('two real versions: the GO threshold moved from 20 to 25', () => {
    const v4 = doc({ pass_rate_bands: { orange_min: 92, yellow_min: 97, green_min: 99 } })
    const v5 = { ...v4, thresholds: { ...v4.thresholds, go_threshold: 25 } }
    const [change] = diffPolicyDocuments(v4, v5)
    expect(change).toEqual({
      section: 'thresholds', key: 'go_threshold', label: 'GO threshold', before: '20', after: '25',
      text: 'GO threshold 20 → 25',
    })
  })
})
