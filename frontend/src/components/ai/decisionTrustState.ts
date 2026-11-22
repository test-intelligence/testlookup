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
  /** The report a verdict may rest on. Null unless the three records agree:
   *  the gate, the Runs list and the server's run_verdict all read this. */
  displayReport: DecisionIntelligence | null
  allowsLocalDecision: boolean
  /** 'inconsistent' only: which record disagrees, in plain words (D-154). */
  disagreement: string | null
  /** 'inconsistent' only: a report that itself passed verification, shown for
   *  reference the way 'rejected_stale' shows one. Never a verdict source. */
  earlierVerifiedReport: DecisionIntelligence | null
}

type RecordStatus = string | null | undefined

function statusWord(status: RecordStatus): string {
  return status ? status : 'missing'
}

/** Name the record that disagrees, instead of "missing or inconsistent". */
export function describeDisagreement(
  reportStatus: RecordStatus,
  latestVerificationStatus: RecordStatus,
  latestAttempt: DecisionReportAttempt | null | undefined,
): string {
  const attemptFailed = latestAttempt?.status === 'rejected'
    || latestAttempt?.verification_status === 'failed'
  if (reportStatus === 'passed' && (latestVerificationStatus === 'failed' || attemptFailed)) {
    return 'The latest attempt failed verification; an earlier report passed.'
  }
  if (reportStatus === 'passed' && !latestAttempt) {
    return 'The report passed verification, but no record of the attempt that published it was found.'
  }
  if (reportStatus === 'passed') {
    return `The report passed verification, but the latest verification record is ${statusWord(latestVerificationStatus)}.`
  }
  if (latestAttempt?.status === 'published' && reportStatus === 'failed') {
    return 'The latest attempt is recorded as published, but its report failed verification.'
  }
  if (latestAttempt?.status === 'published' && !reportStatus) {
    return 'The latest attempt is recorded as published, but its report is missing.'
  }
  return `The verification records disagree: report ${statusWord(reportStatus)}, `
    + `latest verification ${statusWord(latestVerificationStatus)}, `
    + `latest attempt ${latestAttempt ? `${latestAttempt.status} (${latestAttempt.verification_status})` : 'missing'}.`
}

const NO_DISAGREEMENT = { disagreement: null, earlierVerifiedReport: null } as const

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
    return { state, displayReport: report, allowsLocalDecision: false, ...NO_DISAGREEMENT }
  }
  if (report && reportVerified && rejectionAgrees) {
    return { state: 'rejected_stale', displayReport: report, allowsLocalDecision: false, ...NO_DISAGREEMENT }
  }
  if (rejectionAgrees) {
    return { state: 'rejected_empty', displayReport: null, allowsLocalDecision: false, ...NO_DISAGREEMENT }
  }
  if (!latestAttempt && (
    latestVerification?.status === 'pending' || report?.verification?.status === 'pending'
  )) {
    return { state: 'pending', displayReport: null, allowsLocalDecision: false, ...NO_DISAGREEMENT }
  }
  return {
    state: 'inconsistent',
    displayReport: null,
    allowsLocalDecision: false,
    disagreement: describeDisagreement(
      report?.verification?.status, latestVerification?.status, latestAttempt,
    ),
    earlierVerifiedReport: report && reportVerified ? report : null,
  }
}
