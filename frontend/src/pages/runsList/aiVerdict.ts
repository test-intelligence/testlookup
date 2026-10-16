/**
 * The Runs table's AI-verdict column (UX redesign P4, owner decision D2: the
 * `/intelligence` list retired into a column of the runs list).
 *
 * WHERE THE VERDICT COMES FROM (FAIL-04). One request for the runs on screen:
 * `GET /api/v1/runs/ai-verdicts?run_id=…` (≤ 25 ids, `hooks/useRunListInsights`)
 * reads each run's STORED intelligence snapshot and returns, per run, the slim
 * `verdict_inputs` — the same keys as a run-intelligence payload, only the
 * ones `aiVerdictOf` reads, each object's JS truthiness kept (a truthy
 * non-object stands in as `[]`) — plus the review state. The list never
 * computes a verdict: a run with no snapshot reads "—", with the reason
 * (`not-analysed`, or `not-summarised` when a finished analysis exists but its
 * summary is not built yet; the worker rebuilds it after every pipeline).
 *
 * WHAT IT SAYS. The recommendation the run's Analysis tab shows, chosen the
 * same way (`RunIntelligencePage`): a verified decision report's
 * recommendation wins; when a report envelope exists but none of it is
 * displayable, there is no verdict; otherwise the persisted release decision.
 * ONE reducer (`aiVerdictOf`) runs over the full payload (the Analysis tab)
 * and over the slim one (this column), so the two agree by construction; the
 * server's `run_verdict.ai_recommendation_of` (the `?recommendation=` filter)
 * is the same rule, pinned by the shared case table (`aiVerdict.test.ts`).
 * No recommendation is "—", with the reason in the cell's title — never a
 * verdict made up from the pass rate.
 */
import { deriveDecisionTrustState } from '@/components/ai/decisionTrustState'
import type { RunIntelligence } from '@/services/runIntelligenceService'
import type { RunVerdictItem } from '@/types/runs'

export type AiRecommendation = 'GO' | 'CONDITIONAL_GO' | 'NO_GO'

/** Why a run has no AI verdict (the "—" cell's title). */
export type NoVerdictReason =
  /** Still running: nothing has been analysed yet (no request is made). */
  | 'in-progress'
  /** No AI analysis exists for the run. */
  | 'not-analysed'
  /** Analysed (a finished pipeline exists), but its summary is not built: the list reads stored state only. */
  | 'not-summarised'
  /** Analysed, but the analysis made no release recommendation. */
  | 'no-recommendation'
  /** The decision report exists but did not pass verification. */
  | 'not-verified'
  /** The request failed, or the run is not available to the reader: unknown, not "none". */
  | 'unavailable'

export type AiVerdict =
  | {
      recommendation: AiRecommendation
      /** The E8.3 review state of the AI report, when a person has not accepted it. */
      review: 'draft' | 'rejected' | null
    }
  | {
      recommendation: null
      reason: NoVerdictReason
      /** A title more precise than the reason's default (an id the batch answered as unavailable). */
      title?: string
    }

const RECOMMENDATIONS: readonly string[] = ['GO', 'CONDITIONAL_GO', 'NO_GO']

const asRecommendation = (value: unknown): AiRecommendation | null =>
  typeof value === 'string' && RECOMMENDATIONS.includes(value) ? (value as AiRecommendation) : null

/**
 * A run's AI verdict from its Run Intelligence payload, chosen as the run's
 * Analysis tab chooses it. Tolerant of a partial payload (an older API, a
 * cached snapshot, the batch's slim `verdict_inputs`): anything it cannot read
 * is "no verdict", never a guess.
 */
export function aiVerdictOf(intel: Partial<RunIntelligence> | null | undefined): AiVerdict {
  if (!intel || typeof intel !== 'object') return { recommendation: null, reason: 'not-analysed' }
  const summary = intel.structured_summary ?? null
  const report = summary?.decision_intelligence ?? null
  const verification = summary?.decision_report_verification ?? null
  const attempt = summary?.latest_decision_attempt ?? null
  const hasReportEnvelope = Boolean(report || verification || attempt)
  const trust = deriveDecisionTrustState(report, verification, attempt)
  const fromReport = trust.displayReport ? asRecommendation(trust.displayReport.release_decision?.recommendation) : null
  const recommendation = fromReport ?? (hasReportEnvelope ? null : asRecommendation(intel.release_decision?.recommendation))
  if (recommendation) {
    const state = intel.review?.state
    return {
      recommendation,
      review: state === 'pending_review' ? 'draft' : state === 'rejected' ? 'rejected' : null,
    }
  }
  if (hasReportEnvelope) return { recommendation: null, reason: 'not-verified' }
  const analysed = Boolean(intel.intelligence_available || intel.release_decision || (intel.top_analyses?.length ?? 0) > 0)
  return { recommendation: null, reason: analysed ? 'no-recommendation' : 'not-analysed' }
}

/** The pill's words. */
export const RECOMMENDATION_LABEL: Record<AiRecommendation, string> = {
  GO: 'Go',
  CONDITIONAL_GO: 'Conditional',
  NO_GO: 'No-Go',
}

/** The "—" cell's title. */
export const NO_VERDICT_TITLE: Record<NoVerdictReason, string> = {
  'in-progress': 'No AI verdict: the run is still in progress',
  'not-analysed': 'No AI verdict: this run has not been analysed',
  'not-summarised':
    "No AI verdict on this list yet: the run was analysed but its summary is not built. Open the run's analysis to build it.",
  'no-recommendation': 'No AI verdict: the analysis made no release recommendation',
  'not-verified': 'No AI verdict: the decision report did not pass verification',
  unavailable: 'AI verdict unavailable: the request failed',
}

/** The title of a run the batch answered as unavailable (unknown or not accessible: one answer). */
export const UNAVAILABLE_RUN_TITLE = 'AI verdict unavailable: the run is not available to you'

/**
 * One run's verdict from its `GET /runs/ai-verdicts` item. `unavailable`: the
 * id came back in the batch's `unavailable` list. No item (and not listed
 * unavailable) is a failed answer, never "not analysed".
 */
export function aiVerdictFromBatch(item: RunVerdictItem | undefined, unavailable: boolean): AiVerdict {
  if (unavailable) return { recommendation: null, reason: 'unavailable', title: UNAVAILABLE_RUN_TITLE }
  if (!item) return { recommendation: null, reason: 'unavailable' }
  if (!item.snapshot) return { recommendation: null, reason: item.analysed ? 'not-summarised' : 'not-analysed' }
  // The slim payload has a run-intelligence payload's keys (contract §1.2);
  // the review state is attached to it the way the per-run read attaches it.
  const inputs = (item.verdict_inputs ?? {}) as Partial<RunIntelligence>
  return aiVerdictOf({ ...inputs, review: item.review as RunIntelligence['review'] })
}
