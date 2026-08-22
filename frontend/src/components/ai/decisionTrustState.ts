import type {
  DecisionIntelligence,
  DecisionReportAttempt,
  DecisionReportVerification,
} from '@/services/runIntelligenceService'
import { readAnalysisCoverage } from './analysisCoverage'

export type DecisionTrustState =
  | 'verified'
  /** Failures the pipeline never analysed. Comes from gap_report, the only
   *  thing that actually measures analysis coverage. */
  | 'verified_gaps'
  /** A specialist stage was missing/failed, or the persisted payload was
   *  truncated. Distinct from 'verified_gaps': this one used to be LABELLED
   *  as analysis gaps, which is a different claim about a different thing. */
  | 'verified_degraded'
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
    // Analysis gaps outrank specialist degradation: a release decision resting
    // on failures nobody analysed is the more serious claim, and it is the one
    // the reader could not previously see at all.
    const coverage = readAnalysisCoverage(report.quality_review?.gap_report)
    const state: DecisionTrustState = coverage?.hasAnalysisGaps
      ? 'verified_gaps'
      : report.status === 'degraded'
        ? 'verified_degraded'
        : 'verified'
    return { state, displayReport: report, allowsLocalDecision: false }
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
