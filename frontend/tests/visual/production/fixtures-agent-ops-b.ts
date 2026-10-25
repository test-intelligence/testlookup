/**
 * W14 G-E hermetic worlds (`docs/deferred-scope-2026-10-13/work/W14-contract.md` §2.4, §2.5, §4.2):
 *
 *  - `bulkJobWorld({ steps })` — `/runs` whose bulk POST (`/agents/pipelines/bulk-trigger`, a selection or the
 *    list's `run_filter`) answers a tracked job, and whose job read (`/agents/pipelines/bulk-jobs/<id>`) ADVANCES
 *    one step per poll (the way the worker drains the queue), until the last step; Cancel answers the job
 *    cancelled. A deep batch can be refused the way W04's gate refuses it (`deepRefused`: ONE 422);
 *  - `retriggerWorld()` — the same world with a job already tracked in the URL (`?bulk_job=`), for a reload;
 *  - `deepConfigWorld({ gate })` — `/deep-investigate` on the shared `DEEP_INVESTIGATION` answers (pre-scan,
 *    history, a measured local-model estimate), whose estimate's server gate can refuse (`gate: 'rules'`: a
 *    configured Auto that resolves to Rules), and whose deep trigger records its body.
 *
 * W14's answers are PREPENDED to the shared lists (first match wins). Every world records the writes it answered
 * (`state.writes`: the line and the JSON body) so a spec asserts the body, not only that "something was called".
 * Fresh state per call. Not a spec file; `fixtures.ts` is G-A's and is not edited here.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { DEEP_INVESTIGATION, deepEstimateBody, NOW, RUN_ID, RUNS_PAGE, runs } from './fixtures'

/** One answered write: `METHOD path` and its JSON body. */
export interface RecordedWrite {
  line: string
  body: unknown
}

const bodyOf = (request: ApiRequest): Record<string, unknown> => {
  try {
    return (request.route.request().postDataJSON() ?? {}) as Record<string, unknown>
  } catch {
    return {}
  }
}

/** W04's sentence for a deep request under Rules (`services/deep_investigation_gate.MESSAGE_RULES_MODE`). */
export const RULES_SENTENCE = 'Deep investigation requires LLM or Auto mode. Current mode: rules.'

/** The job ids the world hands out, in order (a UUID each: the page puts only an id in `?bulk_job=`). */
export const BULK_JOB_IDS = [
  'b0000000-0000-4000-8000-0000000000a1',
  'b0000000-0000-4000-8000-0000000000a2',
  'b0000000-0000-4000-8000-0000000000a3',
] as const

/** How the job's pipelines stand at one poll: completed (of which failed) and running; the rest not started. */
export interface BulkStep {
  completed: number
  failed: number
  running: number
}

/** The default drain of a job: two running, then two done and two running, then everything done. */
export const DEFAULT_STEPS: readonly BulkStep[] = [
  { completed: 0, failed: 0, running: 2 },
  { completed: 2, failed: 1, running: 2 },
  { completed: 99, failed: 1, running: 0 },
]

interface JobRecord {
  id: string
  workflow_type: string
  source: 'selection' | 'filter'
  requested: number
  queued: number
  skipped: number
  not_found: number
  matched: number | null
  created_at: string
  cancelled_at: string | null
  polls: number
  /** The step the job stood at when it was cancelled (it stays there). */
  frozen: BulkStep | null
}

/** The run_filter resolved over the world's runs the way `GET /runs` filters them (status and window). */
function matchedRuns(filter: Record<string, unknown>): number {
  const days = typeof filter.days === 'number' ? filter.days : 30
  const status = typeof filter.status === 'string' ? filter.status : null
  return runs().filter((run) => {
    const ageDays = Math.floor((NOW.getTime() - Date.parse(run.created_at)) / 86_400_000)
    if (days > 0 && ageDays >= days) return false
    return !status || run.status === status
  }).length
}

function stepOf(job: JobRecord, steps: readonly BulkStep[]): BulkStep {
  if (job.frozen) return job.frozen
  const raw = steps[Math.min(Math.max(job.polls - 1, 0), steps.length - 1)]
  const completed = Math.min(raw.completed, job.queued)
  return { completed, failed: Math.min(raw.failed, completed), running: Math.min(raw.running, job.queued - completed) }
}

/** `BulkJobRead` for the job at its current step. */
function jobRead(job: JobRecord, step: BulkStep) {
  const started = step.completed + step.running
  const notStarted = job.queued - started
  const finished = !job.cancelled_at && step.running === 0 && notStarted === 0
  return {
    bulk_job_id: job.id,
    workflow_type: job.workflow_type,
    source: job.source,
    state: job.cancelled_at ? 'cancelled' : finished ? 'finished' : 'active',
    counters_source: 'redis',
    created_at: job.created_at,
    cancelled_at: job.cancelled_at,
    requested: job.requested,
    queued: job.queued,
    skipped_in_progress: job.skipped,
    not_found: job.not_found,
    matched: job.matched,
    truncated: false,
    progress: {
      started, in_progress: step.running, completed: step.completed,
      passed: step.completed - step.failed, failed: step.failed, not_started: notStarted,
    },
    note: null,
  }
}

export interface BulkWorldState {
  writes: RecordedWrite[]
  /** Reads per job id (the panel polls while the job is active). */
  polls: Record<string, number>
}

export interface BulkWorldOptions {
  /** The drain, one step per poll (the last one repeats). */
  steps?: readonly BulkStep[]
  /** A deep batch is refused as W04's gate refuses it: ONE 422, nothing queued. */
  deepRefused?: boolean
  /** A filter job is already tracked as `BULK_JOB_IDS[0]` (a reload with `?bulk_job=`). */
  preloaded?: boolean
}

export function bulkJobWorld({ steps = DEFAULT_STEPS, deepRefused = false, preloaded = false }: BulkWorldOptions = {}) {
  const state: BulkWorldState = { writes: [], polls: {} }
  const jobs = new Map<string, JobRecord>()
  let issued = 0
  const create = (fields: Omit<JobRecord, 'id' | 'created_at' | 'cancelled_at' | 'polls' | 'frozen'>): JobRecord => {
    const job: JobRecord = { ...fields, id: BULK_JOB_IDS[issued++ % BULK_JOB_IDS.length], created_at: NOW.toISOString(), cancelled_at: null, polls: 0, frozen: null }
    jobs.set(job.id, job)
    return job
  }
  if (preloaded) {
    const matched = matchedRuns({ status: 'FAILED', days: 30 })
    create({ workflow_type: 'offline', source: 'filter', requested: matched, queued: matched - 1, skipped: 1, not_found: 0, matched })
  }
  const handlers: ApiHandlers = [
    ['/api/v1/agents/pipelines/bulk-trigger', (request) => {
      const body = bodyOf(request)
      state.writes.push({ line: `${request.method} ${request.path}`, body })
      const workflowType = body.workflow_type === 'deep' ? 'deep' : 'offline'
      if (workflowType === 'deep' && deepRefused) return respond(422, { detail: RULES_SENTENCE })
      let job: JobRecord
      if (body.run_filter && typeof body.run_filter === 'object') {
        const matched = matchedRuns(body.run_filter as Record<string, unknown>)
        const requested = Math.min(matched, 2000)
        // The newest matching run already has a pipeline in progress: skipped, not queued twice.
        const skipped = requested > 1 ? 1 : 0
        job = create({ workflow_type: workflowType, source: 'filter', requested, queued: requested - skipped, skipped, not_found: 0, matched })
      } else {
        const ids = Array.isArray(body.run_ids) ? body.run_ids : []
        job = create({ workflow_type: workflowType, source: 'selection', requested: ids.length, queued: ids.length, skipped: 0, not_found: 0, matched: null })
      }
      return respond(202, {
        queued: job.queued, not_found: job.not_found, workflow_type: job.workflow_type, not_found_ids: [],
        requested: job.requested, skipped_in_progress: job.skipped, skipped_in_progress_ids: job.skipped ? [RUN_ID] : [],
        source: job.source, matched: job.matched, truncated: false, bulk_job_id: job.id, tracking: true, replayed: false,
      })
    }, 'POST'],
    [/^\/api\/v1\/agents\/pipelines\/bulk-jobs\/[^/]+\/cancel$/, (request) => {
      state.writes.push({ line: `${request.method} ${request.path}`, body: null })
      const job = jobs.get(request.path.split('/')[6])
      if (!job) return respond(404, { detail: 'Bulk job not found' })
      job.frozen = stepOf(job, steps)
      job.cancelled_at = NOW.toISOString()
      return jobRead(job, job.frozen)
    }, 'POST'],
    [/^\/api\/v1\/agents\/pipelines\/bulk-jobs\/[^/]+$/, (request) => {
      const id = request.path.split('/')[6]
      const job = jobs.get(id)
      if (!job) return respond(404, { detail: 'Bulk job not found' })
      job.polls += 1
      state.polls[id] = job.polls
      return jobRead(job, stepOf(job, steps))
    }],
    ...RUNS_PAGE,
  ]
  return { handlers, state }
}

/**
 * A "Re-trigger all matching" job already tracked (`?bulk_job=BULK_JOB_IDS[0]`): the failed runs of the window,
 * the newest skipped as already running, held MID-WAY (one step: active on every read), for a reload and the
 * `runs-bulk-job-panel` region.
 */
export function retriggerWorld() {
  return bulkJobWorld({ steps: [{ completed: 2, failed: 1, running: 2 }], preloaded: true })
}

// ── /deep-investigate ─────────────────────────────────────────────────────

/** The estimate's server gate for a configured Auto that resolves to Rules (no model reachable). */
export const RULES_GATE = {
  allowed: false, reason_code: 'rules_mode', message: RULES_SENTENCE, analysis_mode: 'rules', deep_investigation_enabled: true,
}

export interface DeepConfigState {
  writes: RecordedWrite[]
  /** Every estimate query the page asked, in order. */
  estimateQueries: URLSearchParams[]
}

export function deepConfigWorld({ gate = 'allowed' }: { gate?: 'allowed' | 'rules' } = {}) {
  const state: DeepConfigState = { writes: [], estimateQueries: [] }
  const handlers: ApiHandlers = [
    // The CONFIGURED mode is Auto with deep investigation on (the instant answer offers Run); only the estimate's
    // server gate knows what Auto resolves to.
    ['/api/v1/settings/ai/mode', () => ({ analysis_mode: 'auto', ai_offline_mode: false, deep_investigation_enabled: true })],
    [/^\/api\/v1\/deep-investigate\/[^/]+\/estimate$/, (request) => {
      state.estimateQueries.push(new URLSearchParams(request.url.search))
      return deepEstimateBody(request, gate === 'rules' ? { gate: RULES_GATE } : {})
    }],
    [/^\/api\/v1\/deep-investigate\/[^/]+$/, (request) => {
      state.writes.push({ line: `${request.method} ${request.path}`, body: bodyOf(request) })
      return respond(202, {
        task_id: 'task-deep-config', pipeline_run_id: 'b0000000-0000-4000-8000-0000000000d1', message: 'queued',
        run_id: request.path.split('/')[4], already_in_progress: false, run_configuration: null,
      })
    }, 'POST'],
    ...DEEP_INVESTIGATION,
  ]
  return { handlers, state }
}
