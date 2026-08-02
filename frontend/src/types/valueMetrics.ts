/**
 * Value-metrics contract types — PMF backlog US-12.1 / US-12.2 (hours-saved
 * ROI model). Mirrors the pinned backend contract verbatim:
 *
 *   GET /api/v1/value-metrics?project_id=<uuid>&months=<n, default 6>
 *   GET /api/v1/value-metrics/methodology
 *   GET/PUT /api/v1/projects/{project_id}/value-metrics/assumptions
 *
 * The legacy flat counters (period_days, triage_time_saved_*, …) remain on
 * the same response, so `ValueMetrics` is the union of old + new keys.
 */

/** One month of the hours-saved model. `month` is "YYYY-MM-01", ascending. */
export interface ValueMetricsMonthly {
  month: string
  auto_triaged: number
  clustered_failures: number
  duplicates_absorbed: number
  quarantine_suppressed_failures: number
  runs_unblocked_proxy: number
  hours_triage: number
  hours_quarantine: number
  hours_dedup: number
  hours_total: number
}

export interface ValueMetricsHeadline {
  hours_saved_30d: number
  fte_equivalent_30d: number
}

/** The three tunable model inputs (minutes, bounds 0 < x <= 480). */
export interface ValueAssumptions {
  triage_minutes_per_failure: number
  blocked_run_wait_minutes: number
  defect_filing_minutes: number
}

export type AssumptionsSource = 'default' | 'custom'

/**
 * The pre-US-12 flat counters. Kept as a standalone base so consumers that
 * only need the legacy counters (e.g. workflowPresets) don't have to carry
 * the full hours-saved model in their fixtures.
 */
export interface ValueMetricsLegacy {
  period_days: number
  project_id: string | null
  triage_time_saved_minutes: number
  triage_time_saved_hours: number
  defects_auto_grouped: number
  tests_grouped: number
  duplicate_tickets_avoided: number
  defects_promoted: number
  flaky_tests_identified: number
  quarantine_recommended: number
  risky_releases_blocked: number
  releases_conditional: number
  release_overrides: number
  intelligence_reports_generated: number
}

export interface ValueMetrics extends ValueMetricsLegacy {
  // ── hours-saved model (US-12.1) ────────────────────────────────────────
  /** false ⇒ not enough signal to estimate; render the reason, never "0h". */
  available: boolean
  insufficient_data_reason: string | null
  headline: ValueMetricsHeadline
  /** Ascending by month. */
  monthly: ValueMetricsMonthly[]
  assumptions: ValueAssumptions
  assumptions_source: AssumptionsSource
  methodology_version: number
}

/** One leg of the methodology ("show the math"). */
export interface MethodologyLeg {
  key: string
  title: string
  formula: string
  inputs: string[]
  caveats: string[]
}

export interface ValueMethodology {
  version: number
  legs: MethodologyLeg[]
  defaults: ValueAssumptions
  research_notes: string[]
}

/** Normalized shape of GET .../value-metrics/assumptions (see normalizer). */
export interface AssumptionsRead {
  assumptions: ValueAssumptions
  source: AssumptionsSource
}

/** PUT payload — partial; only the fields being changed. */
export type AssumptionsWrite = Partial<ValueAssumptions>

/** Client-side bound mirror of the backend validation: 0 < x <= 480. */
export const ASSUMPTION_MIN_EXCLUSIVE = 0
export const ASSUMPTION_MAX = 480

export function isValidAssumptionMinutes(value: number): boolean {
  return Number.isFinite(value) && value > ASSUMPTION_MIN_EXCLUSIVE && value <= ASSUMPTION_MAX
}
