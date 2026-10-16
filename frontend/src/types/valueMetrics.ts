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
 *
 * W08 TRND-08 adds `GET /api/v1/value-metrics/by-team` (`TeamValueMetrics`,
 * read through `parseTeamValueMetrics`).
 */
import type { EnvelopeMeta } from '@/lib/viz/contracts'

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
  /**
   * TRND-05: hours saved over the page's window (`window_days`, the `days`
   * the request sent). Optional: a payload from before TRND-05 carries only
   * the 30-day pair below, which the page then shows as "last 30 days".
   */
  hours_saved?: number
  fte_equivalent?: number
  window_days?: number
  /** The trailing 30 days whatever the window (digests, older readers). */
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

// ── W08 TRND-08: value by team (`GET /api/v1/value-metrics/by-team`) ─────────

/** One owning team's row. "Unassigned" is a row like any other, sent last. */
export interface TeamValueRow {
  team: string
  /** Distinct tests the team owns that executed in the window. */
  tests: number
  executions: number
  evaluated: number
  passed: number
  /** 1 dp over evaluated executions; `null` when nothing was evaluated (never 0). */
  pass_rate: number | null
  flaky_tests: number
  defects: number
  /** 2 dp; `null` when no defect of the team was resolved. */
  mttr_hours: number | null
  hours_saved: number
  hours: { triage: number; dedup: number }
}

export interface TeamValueMetrics {
  window_days: number
  /** `false` with `reason` when the project has no ownership rules. */
  measured: boolean
  reason: string | null
  rules_configured: boolean
  teams: TeamValueRow[]
  /** The runs-unblocked leg: counted per run, not per test. */
  not_attributable_hours: number
  /** The window headline over the same days (teams + not attributable sum to it). */
  headline_hours: number
  meta?: EnvelopeMeta
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function finite(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

/** A number, or `null` for null / absent; `undefined` for any other type (malformed). */
function numOrNull(v: unknown): number | null | undefined {
  if (v === null || v === undefined) return null
  return finite(v) ? v : undefined
}

function parseTeamRow(raw: unknown): TeamValueRow | null {
  if (!isRecord(raw)) return null
  const { team, tests, executions, evaluated, passed, flaky_tests, defects, hours_saved } = raw
  if (typeof team !== 'string' || team.trim() === '') return null
  if (
    !finite(tests) ||
    !finite(executions) ||
    !finite(evaluated) ||
    !finite(passed) ||
    !finite(flaky_tests) ||
    !finite(defects) ||
    !finite(hours_saved)
  ) {
    return null
  }
  const rate = numOrNull(raw.pass_rate)
  const mttr = numOrNull(raw.mttr_hours)
  if (rate === undefined || mttr === undefined) return null
  const legs = isRecord(raw.hours) ? raw.hours : {}
  return {
    team,
    tests,
    executions,
    evaluated,
    passed,
    pass_rate: rate,
    flaky_tests,
    defects,
    mttr_hours: mttr,
    hours_saved,
    hours: {
      triage: finite(legs.triage) ? legs.triage : 0,
      dedup: finite(legs.dedup) ? legs.dedup : 0,
    },
  }
}

/**
 * The defensive guard over the by-team read: a missing or malformed answer is
 * `null` (the view says "not available"), a single malformed row is dropped.
 */
export function parseTeamValueMetrics(raw: unknown): TeamValueMetrics | null {
  if (!isRecord(raw)) return null
  if (!Array.isArray(raw.teams)) return null
  if (typeof raw.measured !== 'boolean' || typeof raw.rules_configured !== 'boolean') return null
  if (!finite(raw.not_attributable_hours) || !finite(raw.headline_hours)) return null
  const reason = raw.reason === undefined ? null : raw.reason
  if (reason !== null && typeof reason !== 'string') return null
  const teams: TeamValueRow[] = []
  for (const item of raw.teams) {
    const row = parseTeamRow(item)
    if (row) teams.push(row)
  }
  return {
    window_days: finite(raw.window_days) ? raw.window_days : 0,
    measured: raw.measured,
    reason,
    rules_configured: raw.rules_configured,
    teams,
    not_attributable_hours: raw.not_attributable_hours,
    headline_hours: raw.headline_hours,
    meta: isRecord(raw.meta) ? (raw.meta as unknown as EnvelopeMeta) : undefined,
  }
}
