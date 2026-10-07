/**
 * The Runs table's AI-verdict column (UX redesign P4, owner decision D2: the
 * `/intelligence` list retired into a column of the runs list).
 *
 * WHERE THE VERDICT COMES FROM. The retired list (`IntelligenceHubPage`) never
 * showed a per-run AI verdict: its rows were `GET /runs` (the same rows as
 * /runs), its one "AI" column was the run's pass rate (US-15.1 removed the
 * "AI confidence" label it wore), and a row opened the run's Run Intelligence
 * report. That report is where a run's AI verdict lives:
 * `GET /api/v1/runs/{id}/intelligence` — the release recommendation (GO /
 * CONDITIONAL_GO / NO_GO) and the human-review envelope (E8.3). There is no
 * batch form of it (nor of `/release-readiness/{id}`, nor any list endpoint
 * that carries a recommendation), so the column asks once per run ON SCREEN
 * (a table page is at most 25), never for a run still in flight, at most
 * `MAX_IN_FLIGHT` at a time, each answer cached by SWR.
 *
 * WHAT IT SAYS. The recommendation the run's Analysis tab shows, chosen the
 * same way (`RunIntelligencePage`): a verified decision report's
 * recommendation wins; when a report envelope exists but none of it is
 * displayable, there is no verdict; otherwise the persisted release decision.
 * No recommendation is "—", with the reason in the cell's title — never a
 * verdict made up from the pass rate.
 */
import useSWR from 'swr'
import { deriveDecisionTrustState } from '@/components/ai/decisionTrustState'
import { getData } from '@/services/http'
import type { RunIntelligence } from '@/services/runIntelligenceService'
import { isRunInProgress } from '@/utils/runPassRate'

export type AiRecommendation = 'GO' | 'CONDITIONAL_GO' | 'NO_GO'

/** Why a run has no AI verdict (the "—" cell's title). */
export type NoVerdictReason =
  /** Still running: nothing has been analysed yet (no request is made). */
  | 'in-progress'
  /** No AI analysis exists for the run. */
  | 'not-analysed'
  /** Analysed, but the analysis made no release recommendation. */
  | 'no-recommendation'
  /** The decision report exists but did not pass verification. */
  | 'not-verified'
  /** The request failed: unknown, not "none". */
  | 'unavailable'

export type AiVerdict =
  | {
      recommendation: AiRecommendation
      /** The E8.3 review state of the AI report, when a person has not accepted it. */
      review: 'draft' | 'rejected' | null
    }
  | { recommendation: null; reason: NoVerdictReason }

const RECOMMENDATIONS: readonly string[] = ['GO', 'CONDITIONAL_GO', 'NO_GO']

const asRecommendation = (value: unknown): AiRecommendation | null =>
  typeof value === 'string' && RECOMMENDATIONS.includes(value) ? (value as AiRecommendation) : null

/**
 * A run's AI verdict from its Run Intelligence payload, chosen as the run's
 * Analysis tab chooses it. Tolerant of a partial payload (an older API, a
 * cached snapshot): anything it cannot read is "no verdict", never a guess.
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
  'no-recommendation': 'No AI verdict: the analysis made no release recommendation',
  'not-verified': 'No AI verdict: the decision report did not pass verification',
  unavailable: 'AI verdict unavailable: the request failed',
}

// ── Fetching ───────────────────────────────────────────────────────────────

/**
 * Verdict requests in flight at once. The endpoint computes a snapshot on a
 * cache miss (a Postgres + Mongo aggregation on a small per-worker pool), so a
 * page of 25 runs is asked a few at a time, not all at once.
 */
export const MAX_IN_FLIGHT = 3

let inFlight = 0
const waiting: Array<() => void> = []

/** Run `task` when fewer than `MAX_IN_FLIGHT` verdict requests are in flight. */
function limited<T>(task: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const start = () => {
      inFlight += 1
      task()
        .then(resolve, reject)
        .finally(() => {
          inFlight -= 1
          waiting.shift()?.()
        })
    }
    if (inFlight < MAX_IN_FLIGHT) start()
    else waiting.push(start)
  })
}

/** One run's verdict: its Run Intelligence report, reduced to the column's value. */
export function fetchAiVerdict(runId: string): Promise<AiVerdict> {
  return limited(() =>
    getData<Partial<RunIntelligence>>(`/api/v1/runs/${encodeURIComponent(runId)}/intelligence`, {
      // A row's cell renders its own "unavailable": 25 toasts would bury the page.
      suppressToast: true,
    }),
  ).then(aiVerdictOf)
}

/**
 * The AI verdict of one run on screen. A run still in flight is not asked
 * (it has nothing analysed yet); its verdict is "—".
 */
export function useRunAiVerdict(run: { id: string; status?: string | null }): {
  verdict: AiVerdict | undefined
  isLoading: boolean
} {
  const inProgress = isRunInProgress(run.status)
  const { data, error, isLoading } = useSWR<AiVerdict>(
    inProgress ? null : ['run-ai-verdict', run.id],
    () => fetchAiVerdict(run.id),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  if (inProgress) return { verdict: { recommendation: null, reason: 'in-progress' }, isLoading: false }
  if (error && !data) return { verdict: { recommendation: null, reason: 'unavailable' }, isLoading: false }
  return { verdict: data, isLoading }
}
