import { describe, expect, it } from 'vitest'
import type {
  DecisionIntelligence,
  DecisionReportAttempt,
  DecisionReportVerification,
} from '@/services/runIntelligenceService'
import { deriveDecisionTrustState, describeDisagreement } from './decisionTrustState'

function report(status: 'passed' | 'failed' | 'pending' = 'passed'): DecisionIntelligence {
  return {
    schema_version: 1,
    status: 'complete',
    generated_at: '2026-11-21T10:00:00Z',
    metrics: {},
    failure_clusters: [],
    deep_findings: {},
    flaky_findings: [],
    test_health_findings: [],
    release_decision: { recommendation: 'NO_GO', risk_score: 70 },
    quality_review: {
      missing_or_failed_specialists: [],
      contradictions: [],
      gap_report: null,
      refined_report: null,
      requires_human_review: false,
    },
    source_stages: ['decision_report'],
    evidence_bundle_sha256: 'b'.repeat(64),
    verification: { status },
  } as DecisionIntelligence
}

const failedVerification: DecisionReportVerification = { status: 'failed' }
const passedVerification: DecisionReportVerification = { status: 'passed' }
const published: DecisionReportAttempt = {
  pipeline_run_id: 'p-1', status: 'published', verification_status: 'passed', at: '2026-11-21T10:01:00Z',
}

describe('deriveDecisionTrustState — the inconsistent state names its cause (D-154)', () => {
  it('report verified, latest verification failed, attempt missing: still shows the last verified report', () => {
    const retained = report('passed')
    const trust = deriveDecisionTrustState(retained, failedVerification, null)
    expect(trust.state).toBe('inconsistent')
    expect(trust.disagreement).toBe('The latest attempt failed verification; an earlier report passed.')
    expect(trust.earlierVerifiedReport).toBe(retained)
    // Not a verdict source: the gate, the Runs list and run_verdict stay in parity.
    expect(trust.displayReport).toBeNull()
  })

  it('report verified, latest verification failed, attempt published (mismatched): same message', () => {
    const trust = deriveDecisionTrustState(report('passed'), failedVerification, published)
    expect(trust.state).toBe('inconsistent')
    expect(trust.disagreement).toBe('The latest attempt failed verification; an earlier report passed.')
    expect(trust.earlierVerifiedReport).not.toBeNull()
  })

  it('an unverified report is never offered as the earlier verified one', () => {
    const trust = deriveDecisionTrustState(report('failed'), passedVerification, published)
    expect(trust.state).toBe('inconsistent')
    expect(trust.earlierVerifiedReport).toBeNull()
    expect(trust.disagreement).toBe(
      'The latest attempt is recorded as published, but its report failed verification.',
    )
  })

  it('agreeing states carry no disagreement', () => {
    const trust = deriveDecisionTrustState(report('passed'), passedVerification, published)
    expect(trust.state).toBe('verified')
    expect(trust.disagreement).toBeNull()
    expect(trust.earlierVerifiedReport).toBeNull()
  })

  it('falls back to listing each record when no specific cause applies', () => {
    expect(describeDisagreement(undefined, 'passed', null)).toBe(
      'The verification records disagree: report missing, latest verification passed, latest attempt missing.',
    )
  })
})
