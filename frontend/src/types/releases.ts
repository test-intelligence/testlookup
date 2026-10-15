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
}
