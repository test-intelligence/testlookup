import type {
  LinkedRun,
  PhaseGateResult,
  Release,
  ReleaseActivation,
  ReleaseBaselineComparison,
  ReleaseDetail,
  ReleaseGateDetail,
  ReleaseGateEvaluation,
  ReleaseOutcome,
  ReleasePhase,
  ReleasePhaseGates,
  ReleaseSyncResult,
} from '@/types/releases'
import { deleteData, getData, postData, putData } from './http'

/**
 * `GET /api/v1/releases`: every matching release, unpaged — `{items, total}`,
 * no `page`/`size`/`pages` (`release_service.list_releases`). The router's
 * no-membership path answers the same shape (HYG-10).
 */
export interface ReleaseList {
  items: Release[]
  total: number
}

/** The per-call request options a caller may pass (the global toast opt-out). */
export interface ReleaseRequestOptions {
  /** The caller renders the failure itself: no global error toast. */
  suppressToast?: boolean
}

/**
 * `PUT /releases/{id}/phases/{phase_id}`. The two reason fields are the
 * server's exact names (`PhaseUpdate`): with `release_phase_gate_enforcement`
 * on, completing a phase whose gate is not GO needs `gate_override_reason`,
 * and skipping one needs `skip_reason` (REL-10).
 */
export type PhaseUpdateBody = Partial<ReleasePhase> & {
  gate_override_reason?: string
  skip_reason?: string
}

/** The phase update's answer: the phase, and whether every phase is now done. */
export type PhaseUpdateResult = ReleasePhase & { all_phases_completed?: boolean }

/** The owner travels as an id; `null` clears it (REL-02). */
export type ReleaseCreateBody = {
  project_id: string
  name: string
  version?: string
  description?: string
  status?: string
  planned_date?: string
  phases?: Partial<ReleasePhase>[]
  owner_user_id?: string | null
}
export type ReleaseUpdateBody = Partial<Release> & { owner_user_id?: string | null }

/**
 * `POST /releases/{id}/gate/evaluate` answers the verdict AND the scorecard it
 * was decided on (`release_gate_service.evaluate_release`); the toast after an
 * evaluation quotes the pass rate and the evidence count from it.
 */
export interface ReleaseGateEvaluationResult extends ReleaseGateEvaluation {
  scorecard?: {
    pass_rate?: number | null
    evidence_count?: number
    denominator?: number
    insufficient_reason?: string | null
  } | null
}

/** `POST /releases/sync`: pull releases from GitHub milestones or Jira fix versions (REL-08). */
export interface ReleaseSyncBody {
  project_id: string
  source: 'github' | 'jira'
  jira_project_key?: string
}

function isNotFound(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 404
}

export const releasesService = {
  list: (projectId: string | null, status?: string) =>
    getData<ReleaseList>('/api/v1/releases', {
      params: { ...(projectId ? { project_id: projectId } : {}), ...(status ? { status } : {}) },
    }),

  get: (id: string) =>
    getData<ReleaseDetail>(`/api/v1/releases/${id}`),

  create: (body: ReleaseCreateBody) =>
    postData<Release, ReleaseCreateBody>('/api/v1/releases', body),

  update: (id: string, body: ReleaseUpdateBody) =>
    putData<Release, ReleaseUpdateBody>(`/api/v1/releases/${id}`, body),

  delete: (id: string) => deleteData(`/api/v1/releases/${id}`),

  addPhase: (releaseId: string, body: Partial<ReleasePhase>) =>
    postData<ReleasePhase, Partial<ReleasePhase>>(`/api/v1/releases/${releaseId}/phases`, body),

  /**
   * `config.suppressToast` lets the release page try a transition quietly: a
   * 409 from phase-gate enforcement opens the reason dialog instead of a
   * toast (REL-10).
   */
  updatePhase: (releaseId: string, phaseId: string, body: PhaseUpdateBody, config?: ReleaseRequestOptions) =>
    putData<PhaseUpdateResult, PhaseUpdateBody>(
      `/api/v1/releases/${releaseId}/phases/${phaseId}`,
      body,
      config,
    ),

  deletePhase: (releaseId: string, phaseId: string) =>
    deleteData(`/api/v1/releases/${releaseId}/phases/${phaseId}`),

  linkRun: (releaseId: string, testRunId: string, phaseId?: string) =>
    postData(`/api/v1/releases/${releaseId}/test-runs`, { test_run_id: testRunId, phase_id: phaseId }),

  unlinkRun: (releaseId: string, runId: string) =>
    deleteData(`/api/v1/releases/${releaseId}/test-runs/${runId}`),

  markOutcome: (releaseId: string, outcome: 'incident' | 'rollback', reason: string) =>
    postData<ReleaseOutcome, { outcome: 'incident' | 'rollback'; reason: string }>(
      `/api/v1/releases/${releaseId}/outcomes`,
      { outcome, reason },
    ),

  // ── Release gate (REL-01) ────────────────────────────────────────────────

  /**
   * The release's CURRENT recorded verdict. `null` when it has never been
   * evaluated: the server answers 404 for that ("we have not looked"), which
   * is a different truth from a verdict of NOT_EVALUATED ("we looked and
   * cannot say").
   */
  getGate: (id: string): Promise<ReleaseGateDetail | null> =>
    getData<ReleaseGateDetail>(`/api/v1/releases/${id}/gate`).catch((err: unknown) => {
      if (isNotFound(err)) return null
      throw err
    }),

  /** Release-over-release deltas, or the server's reason they would mislead. */
  getGateBaseline: (id: string) =>
    getData<ReleaseBaselineComparison>(`/api/v1/releases/${id}/gate/baseline`),

  /** Every phase's LIVE gate (nothing is recorded: `record=False` on the server). */
  getPhaseGates: (id: string) =>
    getData<ReleasePhaseGates>(`/api/v1/releases/${id}/phases/gate`),

  /** Evaluate the release gate and record the verdict (QA lead). */
  evaluateGate: (id: string, config?: ReleaseRequestOptions) =>
    postData<ReleaseGateEvaluationResult>(`/api/v1/releases/${id}/gate/evaluate`, undefined, config),

  /** Evaluate ONE phase's gate and record the verdict (QA lead). */
  evaluatePhaseGate: (releaseId: string, phaseId: string) =>
    postData<PhaseGateResult>(`/api/v1/releases/${releaseId}/phases/${phaseId}/gate/evaluate`),

  // ── Active release and sync (REL-08) ─────────────────────────────────────

  /** Make this the project's active release (where unattributed runs land). */
  activate: (id: string) =>
    postData<ReleaseActivation>(`/api/v1/releases/${id}/activate`),

  /**
   * Pull releases in from GitHub milestones or Jira fix versions. No global
   * toast: the sync dialog renders the server's reason itself (a 503 while
   * offline names the cause verbatim).
   */
  sync: (body: ReleaseSyncBody) =>
    postData<ReleaseSyncResult, ReleaseSyncBody>('/api/v1/releases/sync', body, { suppressToast: true }),
}
export type { LinkedRun, Release, ReleaseDetail, ReleaseOutcome, ReleasePhase }
