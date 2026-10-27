import type {
  LinkedRun,
  PhaseDraftResult,
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
  ReleasePhasesBulkBody,
  ReleasePhasesCloneBody,
  ReleasePhasesCreated,
  ReleasePortfolioTrend,
  ReleaseSyncResult,
} from '@/types/releases'
import { downloadBlob, sanitizeFilename } from '@/utils/download'
import { api } from './api'
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

/** `POST /releases/sync`: pull releases from GitHub milestones, GitLab milestones (W19 INT-05) or Jira fix versions (REL-08). */
export interface ReleaseSyncBody {
  project_id: string
  source: 'github' | 'jira' | 'gitlab'
  jira_project_key?: string
}

function isNotFound(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 404
}

/** `attachment; filename="x.ics"` (plain, quoted or RFC 5987) → `x.ics`; else `null`. */
function filenameFromDisposition(header: unknown): string | null {
  if (typeof header !== 'string' || !header) return null
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1].trim())
    } catch {
      return null
    }
  }
  const named = header.match(/filename="([^"]+)"/i) ?? header.match(/filename=([^;]+)/i)
  return named?.[1]?.trim() || null
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

  // ── W11 release planning (REL-05 / REL-07 / REL-06 / REL-03) ─────────────
  // New methods only; none is called on mount, so a factory mock without them
  // still renders the page.

  /** Copy another release's phases into one that has none (REL-05). The form renders a refusal itself. */
  clonePhases: (releaseId: string, body: ReleasePhasesCloneBody) =>
    postData<ReleasePhasesCreated, ReleasePhasesCloneBody>(
      `/api/v1/releases/${releaseId}/phases/clone`,
      body,
      { suppressToast: true },
    ),

  /** A release's first schedule in one request (REL-07's Create). The form renders a refusal itself. */
  createPhasesBulk: (releaseId: string, body: ReleasePhasesBulkBody) =>
    postData<ReleasePhasesCreated, ReleasePhasesBulkBody>(
      `/api/v1/releases/${releaseId}/phases/bulk`,
      body,
      { suppressToast: true },
    ),

  /**
   * Draft phases from a PRD (REL-07): multipart, the pasted `text` OR one
   * `file` (.md / .txt / .docx), never both. Nothing is stored. The PRD goes
   * in the request body only: never a URL, never storage.
   */
  draftPhases: (releaseId: string, input: { text?: string; file?: File }): Promise<PhaseDraftResult> => {
    const form = new FormData()
    if (input.file) form.append('file', input.file)
    else if (input.text != null) form.append('text', input.text)
    return api
      .post<PhaseDraftResult>(`/api/v1/releases/${releaseId}/phases/generate`, form, {
        // The shared client defaults Content-Type to JSON: unset it so the
        // browser writes multipart/form-data WITH its boundary.
        headers: { 'Content-Type': undefined as unknown as string },
        suppressToast: true,
      })
      .then(({ data }) => data)
  },

  /**
   * Download the release schedule (REL-06) as `.ics` or `.csv`: an
   * authenticated blob read (the token rides the Authorization header, never
   * a URL) saved through `downloadBlob`. `projectId` `null` = every project
   * the reader can see. Rejects on failure; the page says so.
   */
  exportSchedule: async (format: 'ics' | 'csv', projectId: string | null): Promise<void> => {
    // Two literal paths (not `schedule.${format}`): the SPA wire-contract test resolves every service path to a
    // real backend route, and a template it cannot read is reported as a 404.
    const url = format === 'ics' ? '/api/v1/releases/schedule.ics' : '/api/v1/releases/schedule.csv'
    const response = await api.get<Blob>(url, {
      responseType: 'blob',
      params: projectId ? { project_id: projectId } : {},
      suppressToast: true,
    })
    const named = filenameFromDisposition(response.headers?.['content-disposition'])
    downloadBlob(response.data, sanitizeFilename(named ?? `testlookup-releases.${format}`))
  },

  /** The release KPI cards' trend (REL-03); `projectId` `null` = every project the reader can see. */
  portfolioTrend: (projectId: string | null, weeks?: number) =>
    getData<ReleasePortfolioTrend>('/api/v1/releases/portfolio-trend', {
      params: { ...(projectId ? { project_id: projectId } : {}), ...(weeks ? { weeks } : {}) },
    }),
}
export type { LinkedRun, Release, ReleaseDetail, ReleaseOutcome, ReleasePhase }
