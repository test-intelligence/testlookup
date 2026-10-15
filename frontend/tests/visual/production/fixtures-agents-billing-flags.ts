/**
 * W04 G-E hermetic answers (`docs/deferred-scope-2026-10-13/work/W04-contract.md` §2.5, §2.6, §2.8, §4.3-§4.6):
 *
 *  - `/agents` with one pipeline row per action state (AGT-04): a failed run (Retry -> resume), a degraded
 *    completed run (Retry -> rerun, the configuration changed), a running deep run (Stop -> asked to stop), a
 *    review-rejected deep run (Retry -> 409 + "Start a new run") and a passed run (no action);
 *  - `/deep-investigate` whose last deep pipeline failed (AGT-04 "Retry from last successful stage") and whose
 *    `/settings/ai/mode` answer can switch deep investigation off (AIC-01);
 *  - `/settings/billing` whose overview computes covered / overage / charge / projection from the stored
 *    quota, the way `services/llm_cost_budget.get_current_usage` does (AIC-06);
 *  - `/settings/feature-flags` with the rows migrations 0208/0209 seed on a fresh install (AIC-07).
 *
 * Every factory returns fresh state per call: an action in one test never leaks into the next. Each records
 * the writes it answered (`posts`) so a spec asserts the body, not only that "something was called".
 * Not a spec file. Never edit `fixtures.ts` / `fixtures-pages.ts` for these (the contract's rule).
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { DEEP_INVESTIGATION, isoAgo, LAYOUT, NOW, PROJECT_ID, RUN_ID, USER } from './fixtures'
import { agentsHandlers } from './fixtures-pages'
import { RUN } from './fixtures-run'

export const ADMIN = {
  ...USER,
  id: '00000000-0000-4000-8000-0000000000ad',
  username: 'admin',
  email: 'admin@example.test',
  full_name: 'Admin',
  role: 'ADMIN',
}
export const VIEWER = { ...USER, id: '00000000-0000-4000-8000-0000000000ee', username: 'viewer', full_name: 'Viewer', role: 'VIEWER' }

/** One answered write: `METHOD path` and its JSON body. */
export interface RecordedWrite {
  line: string
  body: unknown
}

const bodyOf = (request: ApiRequest): unknown => {
  try {
    return request.route.request().postDataJSON()
  } catch {
    return null
  }
}

// ── Pipeline runs (`/agents`) ──────────────────────────────────────────────

export const PIPELINE = {
  failed: 'a0000000-0000-4000-8000-000000000001',
  degraded: 'a0000000-0000-4000-8000-000000000002',
  running: 'a0000000-0000-4000-8000-000000000003',
  rejected: 'a0000000-0000-4000-8000-000000000004',
  passed: 'a0000000-0000-4000-8000-000000000005',
} as const

function pipelineRow(id: string, fields: Record<string, unknown>, minutesAgo: number) {
  const started = new Date(NOW.getTime() - minutesAgo * 60_000).toISOString()
  return {
    id,
    test_run_id: RUN_ID,
    workflow_type: 'offline',
    status: 'failed',
    public_status: 'failed',
    attempt: 1,
    max_attempts: 5,
    next_retry_at: null,
    cancel_requested: false,
    rerun_of: null,
    started_at: started,
    completed_at: new Date(NOW.getTime() - (minutesAgo - 4) * 60_000).toISOString() as string | null,
    error: null,
    created_at: started,
    execution_metadata: {},
    provenance_metadata: null,
    build_number: String(RUN.build_number),
    run_seq: RUN.run_seq,
    suite_name: 'Auth',
    review_summary: null,
    ...fields,
  }
}

export interface AgentsActionsState {
  writes: RecordedWrite[]
  /** GETs of the pipeline list (the revalidation after an action is a second one). */
  listReads: number
}

/**
 * `/agents` (the recent list, no run picked) with the five action states. The list is stateful: a resumed
 * pipeline reads `running` on the next GET, a stop request sets `cancel_requested`.
 */
export function agentsActionsHandlers(): { handlers: ApiHandlers; state: AgentsActionsState } {
  const state: AgentsActionsState = { writes: [], listReads: 0 }
  const rows = {
    failed: pipelineRow(PIPELINE.failed, { status: 'failed', public_status: 'failed', error: 'root_cause_analysis: worker lost' }, 50),
    degraded: pipelineRow(PIPELINE.degraded, {
      status: 'completed', public_status: 'completed', execution_metadata: { stage_quality: 'degraded' },
    }, 40),
    running: pipelineRow(PIPELINE.running, {
      workflow_type: 'deep', status: 'running', public_status: 'in_progress', completed_at: null,
    }, 10),
    rejected: pipelineRow(PIPELINE.rejected, {
      workflow_type: 'deep', status: 'failed', public_status: 'failed', error: 'review_rejected: the reviewer rejected the report',
    }, 30),
    passed: pipelineRow(PIPELINE.passed, {
      status: 'passed', public_status: 'passed',
      review_summary: { state: 'accepted', settled_at: isoAgo(0, 2) },
    }, 120),
  }
  const record = (request: ApiRequest) => {
    state.writes.push({ line: `${request.method} ${request.path}`, body: bodyOf(request) })
  }
  const handlers: ApiHandlers = [
    ['/api/v1/agents/pipelines', () => {
      state.listReads += 1
      return [rows.running, rows.rejected, rows.degraded, rows.failed, rows.passed]
    }],
    [/^\/api\/v1\/agents\/pipelines\/[^/]+\/retry$/, (request) => {
      record(request)
      const id = request.path.split('/')[5]
      if (id === PIPELINE.failed) {
        rows.failed = { ...rows.failed, status: 'running', public_status: 'in_progress', attempt: 2, completed_at: null }
        return respond(202, {
          mode: 'resume', pipeline_run_id: id, rerun_of: null, attempt: 2, max_attempts: 5,
          reason: 'config_unchanged', status: 'failed', public_status: 'failed',
          links: { poll: `/api/v1/agents/pipelines/${id}` },
        })
      }
      if (id === PIPELINE.degraded) {
        return respond(202, {
          mode: 'rerun', pipeline_run_id: null, rerun_of: id, attempt: 1, max_attempts: 5,
          reason: 'config_changed', status: 'pending', public_status: 'in_progress',
          links: { poll: `/api/v1/agents/pipelines?run_id=${RUN_ID}`, replaces: `/api/v1/agents/pipelines/${id}` },
        })
      }
      if (id === PIPELINE.rejected) {
        return respond(409, {
          detail: {
            message: 'Pipeline was rejected in review; start a new run instead',
            reason: 'review_rejected',
            pipeline_run_id: id,
            status: 'failed',
            public_status: 'failed',
            links: { rerun: '/api/v1/agents/pipelines/trigger' },
          },
        })
      }
      return respond(409, { detail: { message: 'Pipeline finished successfully; nothing to retry', pipeline_run_id: id } })
    }, 'POST'],
    [/^\/api\/v1\/agents\/pipelines\/[^/]+\/cancel$/, (request) => {
      record(request)
      const id = request.path.split('/')[5]
      if (id === PIPELINE.running) {
        rows.running = { ...rows.running, cancel_requested: true }
        return { pipeline_run_id: id, accepted: true, terminal: false, status: 'running', public_status: 'in_progress', reason: 'stop requested' }
      }
      return respond(409, {
        detail: { message: 'Pipeline has already finished', pipeline_run_id: id, accepted: false, terminal: true, status: 'completed' },
      })
    }, 'POST'],
    ['/api/v1/agents/pipelines/trigger', (request) => {
      record(request)
      return respond(202, { message: 'Pipeline queued', task_id: 'task-new', run_id: RUN_ID, workflow_ref: 'deep@1' })
    }, 'POST'],
    // The rest of `/agents` (runs picker, live runs, shell) as `fold-agents.spec.ts` answers it.
    ...agentsHandlers(),
  ]
  return { handlers, state }
}

// ── Deep Investigation (`/deep-investigate`) ───────────────────────────────

export const DEEP_PIPELINE_ID = 'b0000000-0000-4000-8000-000000000001'

export interface DeepState {
  /** `/settings/ai/mode`: the effective Deep Investigation toggle (AIC-01). */
  deepEnabled: boolean
  /** The analysis mode the same answer reports. */
  mode: 'llm' | 'ml' | 'rules' | 'auto'
  /** The focused run's last deep pipeline failed (AGT-04). */
  failed: boolean
  writes: RecordedWrite[]
  statusReads: number
}

/**
 * `/deep-investigate?run=RUN_ID` and `/runs/RUN_ID?tab=analysis` (via `RUN_PAGE`): the shared deep handlers
 * with a mode answer and a pipeline status this spec controls. A retry resumes the failed pipeline (the next
 * status read says `running`).
 */
export function deepHandlers(initial: Partial<Omit<DeepState, 'writes' | 'statusReads'>> = {}): { handlers: ApiHandlers; state: DeepState } {
  const state: DeepState = { deepEnabled: true, mode: 'auto', failed: false, writes: [], statusReads: 0, ...initial }
  let resumed = false
  const record = (request: ApiRequest) => {
    state.writes.push({ line: `${request.method} ${request.path}`, body: bodyOf(request) })
  }
  const status = () => {
    state.statusReads += 1
    if (!state.failed) {
      return {
        pipeline_run_id: DEEP_PIPELINE_ID, workflow_type: 'deep', status: 'completed', public_status: 'completed', degraded: false,
        started_at: isoAgo(0, 1.5), completed_at: isoAgo(0, 1.25), error: null,
        stage_summary: { completed: 5, failed: 0, skipped: 0, pending: 0 },
      }
    }
    return {
      pipeline_run_id: DEEP_PIPELINE_ID, workflow_type: 'deep',
      status: resumed ? 'running' : 'failed', public_status: resumed ? 'in_progress' : 'failed', degraded: false,
      started_at: isoAgo(0, 1.5), completed_at: resumed ? null : isoAgo(0, 1.25),
      error: resumed ? null : 'release_risk: worker lost',
      stage_summary: { completed: 4, failed: resumed ? 0 : 1, skipped: 0, pending: resumed ? 1 : 0 },
    }
  }
  const handlers: ApiHandlers = [
    ['/api/v1/settings/ai/mode', () => ({ analysis_mode: state.mode, ai_offline_mode: false, deep_investigation_enabled: state.deepEnabled })],
    [/^\/api\/v1\/agents\/runs\/[^/]+\/pipeline-status$/, status],
    [/^\/api\/v1\/agents\/pipelines\/[^/]+\/retry$/, (request) => {
      record(request)
      resumed = true
      return respond(202, {
        mode: 'resume', pipeline_run_id: DEEP_PIPELINE_ID, rerun_of: null, attempt: 2, max_attempts: 5,
        reason: 'config_unchanged', status: 'failed', public_status: 'failed',
        links: { poll: `/api/v1/agents/pipelines/${DEEP_PIPELINE_ID}` },
      })
    }, 'POST'],
    [/^\/api\/v1\/deep-investigate\/[^/]+$/, (request) => {
      record(request)
      return respond(202, { task_id: 'task-deep', pipeline_run_id: 'b0000000-0000-4000-8000-000000000002', message: 'queued', run_id: RUN_ID })
    }, 'POST'],
    ...DEEP_INVESTIGATION,
  ]
  return { handlers, state }
}

// ── Billing (`/settings/billing`) ──────────────────────────────────────────

export const BILLING_PROJECT = {
  payments: '22222222-2222-4222-8222-000000000001',
  checkout: '22222222-2222-4222-8222-000000000002',
  search: '22222222-2222-4222-8222-000000000003',
} as const

/** The current period (UTC month of `NOW`): 2026-09-01 .. 2026-10-01, 17.5 of 30 days elapsed at `NOW`. */
const PERIOD_START = '2026-09-01T00:00:00Z'
const PERIOD_END = '2026-10-01T00:00:00Z'

interface QuotaFixture {
  enabled: boolean
  included_usd: number
  overage_rate_usd: number
  hard_cap_usd: number
  soft_warn_threshold_pct: number
  at_cap_action: string
}

const round6 = (x: number) => Math.round(x * 1e6) / 1e6

/** `get_current_usage`'s W04 rules (contract §2.5), so the page renders what the server would send. */
export function overageFields(spend: number, quota: QuotaFixture | null, now: Date = NOW) {
  const nulls = {
    included_usd: null, overage_rate_usd: null, covered_usd: null, overage_usd: null, overage_charge_usd: null,
    projected_period_cost_usd: null, projected_overage_usd: null, projected_overage_charge_usd: null, projection_basis: null,
  }
  if (!quota || !quota.enabled) return nulls
  if (quota.included_usd <= 0) return { ...nulls, overage_rate_usd: quota.overage_rate_usd }
  const included = quota.included_usd
  const rate = quota.overage_rate_usd
  const overage = Math.max(0, spend - included)
  const start = Date.parse(PERIOD_START)
  const end = Date.parse(PERIOD_END)
  const elapsed = (now.getTime() - start) / (end - start)
  const days = (end - start) / 86_400_000
  const base = {
    included_usd: included,
    overage_rate_usd: rate,
    covered_usd: round6(Math.min(spend, included)),
    overage_usd: round6(overage),
    overage_charge_usd: round6(overage * rate),
  }
  if (spend <= 0) return { ...base, projected_period_cost_usd: null, projected_overage_usd: null, projected_overage_charge_usd: null, projection_basis: 'no_spend' }
  if (elapsed * days < 1) {
    return { ...base, projected_period_cost_usd: null, projected_overage_usd: null, projected_overage_charge_usd: null, projection_basis: 'insufficient_elapsed_time' }
  }
  const projected = spend / elapsed
  const projectedOverage = Math.max(0, projected - included)
  return {
    ...base,
    projected_period_cost_usd: round6(projected),
    projected_overage_usd: round6(projectedOverage),
    projected_overage_charge_usd: round6(projectedOverage * rate),
    projection_basis: 'linear_elapsed_days',
  }
}

export interface BillingState {
  quotas: Record<string, QuotaFixture | null>
  writes: RecordedWrite[]
}

/**
 * `/settings/billing`: Payments has spent $30 against a cap-only quota (Included $0: no overage computed);
 * Checkout has no quota (UNLIMITED, sub-cent spend); Search has a cap and a $5 allowance it is under.
 * `PUT …/llm-quota` stores the quota and the next overview read computes from it.
 */
export function billingHandlers(): { handlers: ApiHandlers; state: BillingState } {
  const state: BillingState = {
    quotas: {
      [BILLING_PROJECT.payments]: { enabled: true, included_usd: 0, overage_rate_usd: 1, hard_cap_usd: 100, soft_warn_threshold_pct: 80, at_cap_action: 'AUTO_DOWNGRADE_TO_ML' },
      [BILLING_PROJECT.checkout]: null,
      [BILLING_PROJECT.search]: { enabled: true, included_usd: 5, overage_rate_usd: 2, hard_cap_usd: 50, soft_warn_threshold_pct: 80, at_cap_action: 'SOFT_WARN' },
    },
    writes: [],
  }
  const projects = [
    { project_id: BILLING_PROJECT.payments, project_name: 'Payments', current_cost_usd: 30, cap_hits: 0 },
    { project_id: BILLING_PROJECT.checkout, project_name: 'Checkout', current_cost_usd: 0.0012, cap_hits: 0 },
    { project_id: BILLING_PROJECT.search, project_name: 'Search', current_cost_usd: 3.5, cap_hits: 0 },
  ]
  const overview = () => ({
    period_start: PERIOD_START,
    period_end: PERIOD_END,
    total_cost_usd: projects.reduce((sum, p) => sum + p.current_cost_usd, 0),
    total_llm_calls: 412,
    projects: projects.map((p) => {
      const quota = state.quotas[p.project_id]
      const capped = quota && quota.enabled && quota.hard_cap_usd > 0
      const utilization = capped ? (p.current_cost_usd / quota.hard_cap_usd) * 100 : null
      return {
        ...p,
        hard_cap_usd: capped ? quota.hard_cap_usd : null,
        utilization_pct: utilization,
        status: !capped ? 'UNLIMITED' : utilization != null && utilization >= quota.soft_warn_threshold_pct ? 'SOFT_WARN' : 'OK',
        ...overageFields(p.current_cost_usd, quota),
      }
    }),
  })
  const quotaPath = /^\/api\/v1\/projects\/([^/]+)\/llm-quota$/
  const quotaRead = (projectId: string) => {
    const quota = state.quotas[projectId]
    if (!quota) return respond(404, { detail: 'No quota configured' })
    return {
      id: `q-${projectId}`, project_id: projectId, period_type: 'MONTHLY', ...quota,
      created_at: isoAgo(30), updated_at: isoAgo(2), updated_by_user_id: null,
    }
  }
  const handlers: ApiHandlers = [
    ['/api/v1/billing/overview', overview],
    [quotaPath, ({ path }) => quotaRead(path.match(quotaPath)?.[1] ?? '')],
    [quotaPath, (request) => {
      const body = bodyOf(request) as QuotaFixture
      state.writes.push({ line: `${request.method} ${request.path}`, body })
      const projectId = request.path.match(quotaPath)?.[1] ?? ''
      state.quotas[projectId] = {
        enabled: body.enabled, included_usd: body.included_usd, overage_rate_usd: body.overage_rate_usd,
        hard_cap_usd: body.hard_cap_usd, soft_warn_threshold_pct: body.soft_warn_threshold_pct, at_cap_action: body.at_cap_action,
      }
      return quotaRead(projectId)
    }, 'PUT'],
    ...LAYOUT,
  ]
  return { handlers, state }
}

// ── Feature flags (`/settings/feature-flags`) ──────────────────────────────

const flagRow = (key: string, description: string, enabled_global: boolean, i: number) => ({
  id: `f0000000-0000-4000-8000-0000000000${String(i).padStart(2, '0')}`,
  key,
  description,
  enabled_global,
  enabled_projects: null as string[] | null,
  enabled_roles: null as string[] | null,
  rollout_percent: 100,
  created_at: '2026-10-14T00:00:00Z',
  updated_at: '2026-10-14T00:00:00Z',
  updated_by_user_id: null,
})

/** The fresh-install rows of 0208/0209 (contract §1.5, §2.6 descriptions verbatim), in a scrambled server order, among other flags. */
export function seededFlags() {
  return [
    flagRow('ask_ai_chat', 'Ask AI conversational assistant', true, 1),
    flagRow('defect_commander', 'MUTATING deep-pipeline stage: promotes high-confidence failure clusters to defects. Off until Act mode routes its effects through approvals.', false, 2),
    flagRow('change_ownership', 'Baseline-diff and ownership stage of the deep pipeline (read-only).', true, 3),
    flagRow('llm_cost_budget', 'Per-project monthly LLM cost budget and metering.', true, 4),
    flagRow('aiq_gap_refinement', 'Deterministic gap-detection and report-refinement stages of the deep pipeline (no LLM, no egress); adds the "Analysis coverage" line to decision reports.', true, 5),
    flagRow('log_intelligence', 'Log and trace evidence stage of the deep pipeline (read-only; needs Splunk, which stays off while AI_OFFLINE_MODE is on).', true, 6),
    flagRow('async_decision_report_supersession', 'Asynchronous supersession of decision reports in deep pipelines.', false, 7),
    flagRow('contract_validation', 'API-contract specialist stage of the deep pipeline (read-only).', true, 8),
    flagRow('regression_watchman', 'Classifies failures as new regression, known-flaky recurrence or environmental anomaly (read-only; may use the LLM).', true, 9),
    flagRow('manual_upload', 'Upload report files by hand', true, 10),
  ]
}

export interface FlagsState {
  flags: ReturnType<typeof seededFlags>
  writes: RecordedWrite[]
}

/** `/settings/feature-flags` (ADMIN): the list, and `PATCH /feature-flags/{key}` applied to it. */
export function flagsHandlers(): { handlers: ApiHandlers; state: FlagsState } {
  const state: FlagsState = { flags: seededFlags(), writes: [] }
  const handlers: ApiHandlers = [
    ['/api/v1/feature-flags', () => state.flags],
    [/^\/api\/v1\/feature-flags\/[a-z0-9_]+$/, (request) => {
      const body = bodyOf(request) as Record<string, unknown>
      state.writes.push({ line: `${request.method} ${request.path}`, body })
      const key = request.path.split('/')[4]
      const index = state.flags.findIndex((f) => f.key === key)
      if (index < 0) return respond(404, { detail: 'Feature flag not found' })
      state.flags[index] = { ...state.flags[index], ...body, updated_at: NOW.toISOString() }
      return state.flags[index]
    }, 'PATCH'],
    ...LAYOUT,
  ]
  return { handlers, state }
}

export { PROJECT_ID, RUN_ID }
