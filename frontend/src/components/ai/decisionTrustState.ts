import type {
  DecisionIntelligence,
  DecisionReportAttempt,
  DecisionReportVerification,
} from '@/services/runIntelligenceService'

export type DecisionTrustState =
  | 'verified'
  | 'verified_gaps'
  | 'rejected_stale'
  | 'rejected_empty'
  | 'pending'
  | 'inconsistent'

export interface DerivedDecisionTrust {
  state: DecisionTrustState
  displayReport: DecisionIntelligence | null
  allowsLocalDecision: boolean
}

export function deriveDecisionTrustState(
  report: DecisionIntelligence | null | undefined,
  latestVerification: DecisionReportVerification | null | undefined,
  latestAttempt: DecisionReportAttempt | null | undefined,
): DerivedDecisionTrust {
  const reportVerified = report?.verification?.status === 'passed'
  const publishedAgrees = latestAttempt?.status === 'published'
    && latestAttempt.verification_status === 'passed'
    && latestVerification?.status === 'passed'
  const rejectionAgrees = latestAttempt?.status === 'rejected'
    && latestAttempt.verification_status === 'failed'
    && latestVerification?.status === 'failed'

  if (report && reportVerified && publishedAgrees) {
    return {
      state: report.status === 'degraded' ? 'verified_gaps' : 'verified',
      displayReport: report,
      allowsLocalDecision: false,
    }
  }
  if (report && reportVerified && rejectionAgrees) {
    return { state: 'rejected_stale', displayReport: report, allowsLocalDecision: false }
  }
  if (rejectionAgrees) {
    return { state: 'rejected_empty', displayReport: null, allowsLocalDecision: false }
  }
  if (!latestAttempt && (
    latestVerification?.status === 'pending' || report?.verification?.status === 'pending'
  )) {
    return { state: 'pending', displayReport: null, allowsLocalDecision: false }
  }
  return { state: 'inconsistent', displayReport: null, allowsLocalDecision: false }
}
