import type { HoldRef } from './retentionHolds'

export interface ReleasePhase {
  id: string
  release_id: string
  name: string
  phase_type: string
  status: string
  description: string | null
  order_index: number
  planned_start: string | null
  planned_end: string | null
  actual_start: string | null
  actual_end: string | null
  exit_criteria: Record<string, unknown> | null
  notes: string | null
  created_at: string
  updated_at: string
}

/** The release's owner as the server resolves it; `null` = "No owner" (none, or a service/synthetic/inactive account). */
export interface ReleaseOwner {
  id: string
  name: string
}

/**
 * The CURRENT recorded release-level gate verdict, embedded in a list/detail
 * item (`release_gate_service.shape_gate_summary`). A release that was never
 * evaluated carries `gate: null`. `verdict` is a plain string from the table
 * vocabulary (`GO` | `NO_GO` | `NOT_EVALUATED` | `CONDITIONAL_GO`); an
 * unknown word maps to "not evaluated", never to a pass.
 */
export interface ReleaseGateSummary {
  verdict: string
  decided_at: string | null
  /** `null` = not measured (no evidence, or a legacy row), never 0. */
  pass_rate: number | null
  denominator: number
  evidence_count: number
  run_count: number
  status_counts: Record<string, number>
  measured: boolean
  insufficient_reason: string | null
  /** At most 20 display strings ("test_pay (checkout-e2e) is FAILED"). */
  blocking_reasons: string[]
  /** The stored array's full length (may exceed `blocking_reasons.length`). */
  blocking_total: number
}

export interface Release {
  id: string
  project_id: string
  project_name?: string | null
  name: string
  version: string | null
  description: string | null
  status: string
  planned_date: string | null
  released_at: string | null
  created_at: string
  updated_at: string
  phases: ReleasePhase[]
  test_run_count?: number
  // Every field below is OPTIONAL: create/update responses and hand-built
  // fixtures omit them.
  /** The project's active release (where unattributed runs land). */
  is_active?: boolean
  activated_at?: string | null
  /** `github` | `jira` for a synced release. */
  source_system?: string | null
  /** The milestone / fix version on its source; rendered as a link only for `http(s)://`. */
  external_url?: string | null
  last_synced_at?: string | null
  owner_user_id?: string | null
  owner?: ReleaseOwner | null
  /** `null` (or absent) = never evaluated. */
  gate?: ReleaseGateSummary | null
}

/** `GET /api/v1/releases/{id}/gate` (404 = never evaluated). */
export interface ReleaseGateDetail {
  release_id: string
  verdict: string
  decided_at: string | null
  blocking_reasons: string[]
  /** At most 200 display strings; present only when `blocking_reasons` is non-empty. */
  blocking_reasons_display?: string[]
  blocking_total?: number
  scorecard: {
    denominator: number
    evidence_count: number
    pass_rate?: number | null
    status_counts: Record<string, number>
    run_count: number
    measured: boolean
    insufficient_reason?: string | null
    evidence_floor: number
  }
  from_snapshot: true
}

/** `GET /api/v1/releases/{id}/gate/baseline`. */
export interface ReleaseBaselineComparison {
  comparable: boolean
  reason?: string
  baseline_release_id?: string
  pass_rate_delta?: number | null
  denominator_delta?: number
  evidence_delta?: number
}

/** One phase of `GET /api/v1/releases/{id}/phases/gate` (a LIVE preview, `record=False`). */
export interface PhaseGateResult {
  phase_id: string
  phase_name: string
  verdict: string
  blocking_reasons: string[]
  may_exit: boolean
  recorded: boolean
  scorecard: { evidence_count: number; denominator: number; pass_rate: number | null; measured: boolean } | null
}

export interface ReleasePhaseGates {
  release_id: string
  status: string
  failing_phases: string[]
  unevaluated_phases: string[]
  phase_count: number
  phases: PhaseGateResult[]
}

/** `POST /api/v1/releases/{id}/gate/evaluate`. */
export interface ReleaseGateEvaluation {
  release_id: string
  verdict: string
  blocking_reasons: string[]
  recorded: boolean
  decision_id: string | null
}

/** `POST /api/v1/releases/{id}/activate`. */
export interface ReleaseActivation {
  release_id: string
  is_active: boolean
  deactivated: string | null
  changed: boolean
}

/** `POST /api/v1/releases/sync`. */
export interface ReleaseSyncResult {
  source: string
  created: number
  updated: number
  skipped: number
}

export interface ReleaseOutcome {
  id: string
  release_id: string
  project_id: string
  outcome_kind: 'incident' | 'rollback'
  reason: string
  marked_by_user_id: string | null
  marked_at: string
}

export interface LinkedRun {
  id: string
  build_number: string | null
  status: string
  total_tests: number
  passed_tests: number
  failed_tests: number
  broken_tests: number
  skipped_tests: number
  pass_rate: number | null
  created_at: string
  primary_suite_name?: string | null
  suite_names?: string[] | null
  phase_id: string | null
}

export interface ReleaseMetrics {
  total_runs: number
  total_tests: number
  total_passed: number
  total_failed: number
  avg_pass_rate: number | null
}

export interface ReleaseDetail extends Release {
  linked_runs: LinkedRun[]
  metrics: ReleaseMetrics
  outcomes: ReleaseOutcome[]
  /**
   * W22a ACT-05: every LIVE retention hold covering the release (its own and
   * the project's). Absent on an older server or fixture: read as `[]`.
   */
  holds?: HoldRef[]
}

// ── W11 release planning (REL-05 / REL-07 / REL-03). Every field the server may
// omit is optional, so hand-built fixtures and mocks still type-check. ────────

/** `POST /releases/{id}/phases/clone`: copy another release's phases (REL-05). */
export interface ReleasePhasesCloneBody {
  from_release_id: string
  /** The earliest dated source phase's NEW planned start (ISO, 00:00 UTC); `null` = the target's cutoff start. */
  shift_to?: string | null
}

/** One phase of a bulk create. */
export interface ReleasePhaseBulkItem {
  name: string
  phase_type?: string
  description?: string | null
  planned_start?: string | null
  planned_end?: string | null
  exit_criteria?: Record<string, unknown> | null
  notes?: string | null
}

/** `POST /releases/{id}/phases/bulk`: a release's FIRST schedule in one request (REL-07's Create). */
export interface ReleasePhasesBulkBody {
  phases: ReleasePhaseBulkItem[]
  /** Who drafted it: `rules` | `llm` | `manual` (recorded in the activity row and the metric only). */
  source?: 'rules' | 'llm' | 'manual' | string
  model_name?: string | null
}

/** The clone / bulk answer: the created phases, in order. */
export interface ReleasePhasesCreated {
  release_id: string
  created: number
  phases: ReleasePhase[]
  /** Clone only. */
  source_release_id?: string | null
  /** Clone only; `null` when no source phase had a date. */
  shifted_by_days?: number | null
}

/** One drafted phase: TEXT the reader edits before anything is saved. */
export interface PhaseDraft {
  name: string
  phase_type: string
  description?: string | null
  exit_criteria_text?: string | null
  /** 1..90; `null` = the PRD names no duration. */
  planned_days?: number | null
  /** The PRD heading it came from (rules drafts). */
  heading?: string | null
}

/** `POST /releases/{id}/phases/generate`: a draft, persisted nowhere. */
export interface PhaseDraftResult {
  release_id: string
  source: 'rules' | 'llm' | string
  /** Set when the model drafted it. */
  model_name?: string | null
  /** Why the rules draft answered (`offline_mode`, `rules_mode`, `llm_timeout`, …); `null` = the model's draft. */
  fallback_reason?: string | null
  phases: PhaseDraft[]
  /** Fixed sentences, never PRD text. */
  warnings?: string[]
  text_chars?: number
}

/** The trend's totals over the whole window (REL-03). */
export interface ReleasePortfolioTotals {
  evaluated: number
  go: number
  conditional_go: number
  no_go: number
  not_evaluated: number
  released: number
  released_with_pack: number
}

/**
 * `GET /releases/portfolio-trend`: each KPI card's rule at `weeks` Monday week
 * ends and now (the last point). A series point is `null` before the scope's
 * first release existed (absence, never 0).
 */
export interface ReleasePortfolioTrend {
  project_id?: string | null
  weeks: number
  points_at: string[]
  in_progress: (number | null)[]
  ready_to_ship: (number | null)[]
  blocked: (number | null)[]
  released_30d: (number | null)[]
  evaluated_in_week?: number[]
  go_in_week?: number[]
  totals: ReleasePortfolioTotals
  /** The series built on TODAY's status (an approximation). */
  approximated?: string[]
  generated_at?: string
}
