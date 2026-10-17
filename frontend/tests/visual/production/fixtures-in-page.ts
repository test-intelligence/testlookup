/**
 * W27a "in-page panels" hermetic answers (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §4.4): ONLY the
 * writes and reads the characterization specs (`tests/ci-e2e/characterize-*.spec.ts`) need and no existing
 * world answers. Each factory COMPOSES an existing world (never edits one) and lists its own handlers FIRST
 * (first match wins, `production-pages.ts`), so a W27a write is answered before the page's reads.
 *
 * Every factory returns fresh state per call and records every write it answered (`writes`: `METHOD path` and
 * the parsed JSON body), so a spec pins the whole body with `toEqual`.
 * Not a spec file.
 */
import { respond, respondRaw, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { DEEP_INVESTIGATION, isoAgo, LAYOUT, PROJECT_ID, RUNS, RUNS_PAGE, RUN_ID } from './fixtures'
import { defectsWorld } from './fixtures-defects'
import { flakyQuarantineWorld } from './fixtures-notify-flaky'
import { inboxHandlers, releaseRow, TEST_CASE, TEST_CASE_API, TEST_CASE_ID } from './fixtures-pages'
import { releasesHandlers, type RecordedWrite, type ReleasesOptions } from './fixtures-releases'
import { RUN_PAGE, runTests } from './fixtures-run'
import { hintsWorld } from './fixtures-runs-failures'

export type { RecordedWrite }

/** The parsed JSON body of a request (null when it has none). */
export const bodyOf = (request: ApiRequest): unknown => {
  try {
    return request.route.request().postDataJSON()
  } catch {
    return null
  }
}

/** Answer `request` with the first of `handlers` that matches its method and path (to wrap an existing read). */
export function delegate(handlers: ApiHandlers, request: ApiRequest): unknown {
  const hit = handlers.find(
    ([matcher, , verb = 'GET']) =>
      verb === request.method && (typeof matcher === 'string' ? matcher === request.path : matcher.test(request.path)),
  )
  if (!hit) throw new Error(`no handler for ${request.method} ${request.path}`)
  return hit[1](request)
}

// ── /releases (PAN-02, R2-R5, R8, R9) ──────────────────────────────────────

/** The id the next created release gets. */
export const NEW_RELEASE_ID = 'aaaaaaaa-0000-4000-8000-0000000000f1'

/** A run the Link Run form offers (from `RUNS`), linked to a release by R5. */
export const LINKABLE_RUN_ID = RUN_ID

/**
 * The W05 `/releases` world (`releasesHandlers`) plus the writes W27a characterizes and that world does not
 * answer: create (`POST /releases`), delete a release, remove a phase, link a run (each changes the world, so
 * the re-reads that follow show it), and the run list the Link Run form reads.
 */
export function releasesInPageWorld(options: ReleasesOptions = {}) {
  const base = releasesHandlers(options)
  const { world, writes } = base
  const linked: Record<string, string[]> = {}
  const record = (request: ApiRequest) => {
    const body = bodyOf(request)
    writes.push({ line: `${request.method} ${request.path}`, body })
    return body
  }
  const releaseIdOf = (path: string) => path.split('/')[4]

  const handlers: ApiHandlers = [
    // R2: create. The server answers the new row; the list re-read shows its card.
    ['/api/v1/releases', (request) => {
      const body = record(request) as { name: string; version?: string; description?: string; status: string; planned_date?: string }
      const row = releaseRow(NEW_RELEASE_ID, body.name, body.status, {
        version: body.version ?? body.name,
        description: body.description ?? null,
        planned_date: body.planned_date ?? null,
        created_at: isoAgo(0),
      })
      world.rows.push(row)
      return row
    }, 'POST'],
    // R8: remove a phase.
    [/^\/api\/v1\/releases\/[^/]+\/phases\/[^/]+$/, (request) => {
      record(request)
      const phaseId = request.path.split('/')[6]
      const row = world.rows.find((r) => r.id === releaseIdOf(request.path))
      if (!row || !row.phases.some((p) => p.id === phaseId)) return respond(404, { detail: 'Phase not found' })
      row.phases = row.phases.filter((p) => p.id !== phaseId)
      return respond(204)
    }, 'DELETE'],
    // R9: delete a release (and its phases).
    [/^\/api\/v1\/releases\/[^/]+$/, (request) => {
      record(request)
      const id = releaseIdOf(request.path)
      if (!world.rows.some((r) => r.id === id)) return respond(404, { detail: 'Release not found' })
      world.rows = world.rows.filter((r) => r.id !== id)
      return respond(204)
    }, 'DELETE'],
    // R5: link a run; the detail re-read lists it.
    [/^\/api\/v1\/releases\/[^/]+\/test-runs$/, (request) => {
      const body = record(request) as { test_run_id: string; phase_id?: string }
      const id = releaseIdOf(request.path)
      linked[id] = [...(linked[id] ?? []), body.test_run_id]
      return { release_id: id, test_run_id: body.test_run_id, phase_id: body.phase_id ?? null }
    }, 'POST'],
    // The detail, with the runs R5 linked appended to the world's own.
    [/^\/api\/v1\/releases\/[^/]+$/, (request) => {
      const answer = delegate(base.handlers, request)
      const extra = linked[releaseIdOf(request.path)] ?? []
      if (!extra.length || !answer || typeof answer !== 'object' || !('linked_runs' in answer)) return answer
      const detail = answer as { linked_runs: Record<string, unknown>[] }
      const runs = (RUNS[0][1]({ ...request, url: new URL('http://x/api/v1/runs?size=100') }) as { items: Record<string, unknown>[] }).items
      return {
        ...detail,
        linked_runs: [
          ...detail.linked_runs,
          ...extra.map((runId) => {
            const run = runs.find((r) => r.id === runId) ?? {}
            return { ...run, id: runId, status: 'failed', phase_id: null }
          }),
        ],
      }
    }],
    // The Link Run form's run list (`useRuns`).
    ...RUNS,
    ...base.handlers,
  ]
  return { handlers, world, writes }
}

// ── multipart bodies (U1) ──────────────────────────────────────────────────

/** One part of a `multipart/form-data` body, in wire order. */
export interface MultipartPart {
  name: string
  /** Present for a file part. */
  filename?: string
  contentType?: string
  value: string
}

/** The request's `Content-Type` header and its multipart parts, in order (none when it is not multipart). */
export function multipartOf(request: ApiRequest): { contentType: string; parts: MultipartPart[] } {
  const req = request.route.request()
  const contentType = req.headers()['content-type'] ?? ''
  const boundary = /boundary=(?:"([^"]+)"|([^;\s]+))/.exec(contentType)
  const body = req.postDataBuffer()?.toString('latin1') ?? ''
  const parts: MultipartPart[] = []
  if (!boundary || !body) return { contentType, parts }
  for (const chunk of body.split(`--${boundary[1] ?? boundary[2]}`).slice(1)) {
    if (chunk.startsWith('--')) break
    const at = chunk.indexOf('\r\n\r\n')
    const head = chunk.slice(0, at)
    const value = chunk.slice(at + 4).replace(/\r\n$/, '')
    const disposition = /name="([^"]*)"(?:; filename="([^"]*)")?/.exec(head)
    const type = /content-type:\s*([^\r\n]+)/i.exec(head)
    parts.push({
      name: disposition?.[1] ?? '',
      ...(disposition?.[2] !== undefined ? { filename: disposition[2] } : {}),
      ...(type ? { contentType: type[1].trim() } : {}),
      value: Buffer.from(value, 'latin1').toString('utf8'),
    })
  }
  return { contentType, parts }
}

// ── /runs (PAN-03, U1-U3) ──────────────────────────────────────────────────

/** The upload's task id; its run is `RUN_ID`, so "View run" lands on `RUN_PAGE`'s run. */
export const UPLOAD_TASK_ID = 'task-w27a-upload-1'

/** One recorded upload: the request line, its content type and its parts. */
export interface RecordedUpload {
  line: string
  contentType: string
  parts: MultipartPart[]
}

/**
 * `/runs` (`RUNS_PAGE`) and the run it lands on (`RUN_PAGE`), plus the upload: `POST /api/v1/ingest/file`
 * answers 202 with `RUN_ID`, and `GET /api/v1/ingest/uploads/{task}` answers `parsing` the first time and
 * `succeeded` (3 tests: 2 passed, 1 failed) from then on.
 */
export function runsUploadWorld() {
  const uploads: RecordedUpload[] = []
  const state = { polls: 0 }
  const handlers: ApiHandlers = [
    ['/api/v1/ingest/file', (request) => {
      uploads.push({ line: `${request.method} ${request.path}`, ...multipartOf(request) })
      return respond(202, { run_id: RUN_ID, task_id: UPLOAD_TASK_ID, total_results: 0 })
    }, 'POST'],
    [`/api/v1/ingest/uploads/${UPLOAD_TASK_ID}`, () => {
      state.polls += 1
      return state.polls === 1
        ? { task_id: UPLOAD_TASK_ID, run_id: RUN_ID, state: 'parsing', progress: { total: 3, parsed: 1 }, result: null, error: null }
        : {
            task_id: UPLOAD_TASK_ID,
            run_id: RUN_ID,
            state: 'succeeded',
            progress: { total: 3, parsed: 3 },
            result: { total: 3, passed: 2, failed: 1, skipped: 0, broken: 0 },
            error: null,
          }
    }],
    ...RUNS_PAGE,
    ...RUN_PAGE,
  ]
  return { handlers, uploads, state }
}

// ── /runs/:runId: the test pane, Analysis and Evidence (PAN-03, T1, A1-A3, E1, E2) ──

/** The cluster of `RUN_INTELLIGENCE` (`fixtures.ts`): the one failure block of the Analysis tab. */
export const CLUSTER_ID = 'cl-1'

/** What `GET …/clusters/cl-1/defect-candidate` prefills the promotion form with. */
export const DEFECT_CANDIDATE = {
  cluster_id: CLUSTER_ID,
  run_id: RUN_ID,
  title: 'Gateway timeouts in the Payments suite',
  description: 'TimeoutError: gateway did not answer within 5000 ms (8 tests).',
  severity: 'HIGH',
  component: 'payment-gateway',
  owner_team: 'payments-team',
  labels: ['regression', 'cluster-promoted'],
  duplicate_hint: '',
  duplicate_detected: false,
  duplicate_defect_id: null,
  criticality_scores: { user_impact: 64 },
  composite_score: 64.5,
  evidence_bundle: { stack_traces: ['TimeoutError: gateway did not answer within 5000 ms'], log_anomalies: [], data_sources: ['stacktrace'] },
  failure_category: 'INFRASTRUCTURE',
  member_count: 8,
}

/** The promoted defect's id. */
export const PROMOTED_DEFECT_ID = 'dddddddd-0000-4000-8000-0000000000a1'

/** The published decision report the Evidence tab shows (E1, E2). */
export const DECISION_REPORT = { report_id: 'report-w27a-1', report_version: 1, status: 'published', generated_at: isoAgo(0, 1) }

/** Its two claims: a fact with one metric, an inference with one cluster (and one counter-evidence). */
export const DECISION_CLAIMS = [
  {
    claim_id: 'fact-1',
    kind: 'fact',
    text: '12 of 197 tests failed.',
    confidence: 1,
    confidence_basis: 'Deterministic metric snapshot.',
    evidence: [{ id: 'metric-failed', type: 'metric', source: 'run metrics', kind: 'count' }],
    counter_evidence: [],
    source_stage: 'decision_report',
  },
  {
    claim_id: 'inference-1',
    kind: 'inference',
    text: 'The gateway timeouts cause the Payments failures.',
    confidence: 0.82,
    confidence_basis: 'Cluster cohesion and the shared error.',
    evidence: [{ id: 'cluster-cl-1', type: 'cluster', source: 'failure clustering', excerpt: 'TimeoutError: gateway did not answer within 5000 ms' }],
    counter_evidence: [{ id: 'quality-1', type: 'quality', source: 'quality review' }],
    source_stage: 'release_risk',
  },
]

const DECISION_INTELLIGENCE = {
  schema_version: 1,
  status: 'complete',
  generated_at: isoAgo(0, 1),
  metrics: { total_tests: 197, passed_tests: 180, failed_tests: 12, broken_tests: 3, pass_rate: 92.3, failure_cluster_count: 1, flaky_finding_count: 0, test_health_finding_count: 0 },
  failure_clusters: [{ cluster_id: CLUSTER_ID, label: 'Gateway timeouts', size: 8 }],
  deep_findings: {},
  flaky_findings: [],
  test_health_findings: [],
  release_decision: { recommendation: 'CONDITIONAL_GO', risk_score: 58, reasoning: 'Infrastructure failures dominate.' },
  quality_review: { missing_or_failed_specialists: [], contradictions: [], gap_report: null, refined_report: null, requires_human_review: false },
  source_stages: ['release_risk', 'decision_report'],
  evidence_bundle_sha256: 'b'.repeat(64),
  verification: { status: 'passed', checks: [{ name: 'durable_snapshot_binding', status: 'pass' }], repairs: [] },
  claims: DECISION_CLAIMS,
  proposed_actions: [],
}

export interface RunAnalysisOptions {
  /** `POST …/promote`'s `approval_status`: `'pending_review'` (a QA lead must approve) or null. */
  approval?: 'pending_review' | null
  /** Publish a verified decision report on the run (the Evidence tab's panel). */
  decisionReport?: boolean
}

/**
 * The run page (`RUN_PAGE`) plus what its overlays read and write:
 *  - every test of the run's own reads (rich detail, history, steps, step flips, the stored AI analysis), built
 *    from `TEST_CASE`'s answers with the row's id, name and status (the Tests tab's pane, T1, A3);
 *  - the decision trail (`DEEP_INVESTIGATION`'s, which `RUN_PAGE` does not carry: A2);
 *  - the defect candidate and the promotion (A1), the classification feedback (A3), the decision report and its
 *    claim-correction feedback (E1, E2).
 */
export function runAnalysisWorld(options: RunAnalysisOptions = {}) {
  const writes: RecordedWrite[] = []
  const record = (request: ApiRequest) => {
    const body = bodyOf(request)
    writes.push({ line: `${request.method} ${request.path}`, body })
    return body
  }
  const tests = runTests()
  const testOf = (path: string) => tests.find((t) => t.id === path.split('/')[6])
  const caseAnswer = (request: ApiRequest, suffix: string) => {
    const path = `${TEST_CASE_API}${suffix}`
    return delegate(TEST_CASE, { ...request, path, url: new URL(`http://x${path}`) }) as Record<string, unknown>
  }
  const RUN_TEST = `^\\/api\\/v1\\/runs\\/${RUN_ID}\\/tests\\/[0-9a-f-]{36}`
  const handlers: ApiHandlers = [
    [new RegExp(`${RUN_TEST}\\/rich-detail$`), (request) => {
      const row = testOf(request.path)
      if (!row) return respond(404, { detail: 'Test case not found' })
      const detail = caseAnswer(request, '/rich-detail')
      const name = String(row.test_name)
      return {
        ...detail,
        id: row.id,
        test_name: name,
        full_name: `${row.class_name}.${name}`,
        class_name: row.class_name,
        suite_name: row.suite_name,
        status: row.status,
        identity: { ...(detail.identity as object), test_case_id: row.id, fingerprint: `fp-${name}` },
        execution: { ...(detail.execution as object), status: row.status, failure_category: row.failure_category },
      }
    }],
    [new RegExp(`${RUN_TEST}\\/history$`), (request) => ({ ...caseAnswer(request, '/history'), test_id: testOf(request.path)?.id, test_name: testOf(request.path)?.test_name })],
    [new RegExp(`${RUN_TEST}\\/steps$`), (request) => ({ ...caseAnswer(request, '/steps'), test_id: testOf(request.path)?.id, test_name: testOf(request.path)?.test_name })],
    [new RegExp(`${RUN_TEST}\\/step-flips$`), (request) => ({ ...caseAnswer(request, '/step-flips'), test_id: testOf(request.path)?.id })],
    [/^\/api\/v1\/analyze\/[0-9a-f-]{36}$/, (request) => {
      const id = request.path.split('/').pop() as string
      const row = tests.find((t) => t.id === id)
      if (!row) return respond(404, { detail: 'No analysis' })
      const stored = delegate(TEST_CASE, { ...request, path: `/api/v1/analyze/${TEST_CASE_ID}`, url: new URL(`http://x/api/v1/analyze/${TEST_CASE_ID}`) }) as Record<string, unknown>
      return { ...stored, test_case_id: id, analysis_id: `analysis-${id}`, failure_category: row.failure_category }
    }],
    // A3 / F4: the classification feedback.
    [/^\/api\/v1\/feedback\/[^/]+$/, (request) => {
      record(request)
      return { id: `feedback-${writes.length}`, analysis_id: request.path.split('/').pop(), rating: 'incorrect' }
    }, 'POST'],
    // A2: the decision trail.
    [/^\/api\/v1\/runs\/[^/]+\/decision-trail$/, (request) => delegate(DEEP_INVESTIGATION, request)],
    // A1: the candidate, then the promotion.
    [`/api/v1/deep-investigate/${RUN_ID}/clusters/${CLUSTER_ID}/defect-candidate`, () => DEFECT_CANDIDATE],
    [`/api/v1/deep-investigate/${RUN_ID}/clusters/${CLUSTER_ID}/promote`, (request) => {
      const body = record(request) as { title: string; severity: string; owner_team: string; component: string }
      const pending = options.approval === 'pending_review'
      return {
        defect_id: PROMOTED_DEFECT_ID,
        cluster_id: CLUSTER_ID,
        severity: body.severity,
        title: body.title,
        owner_team: body.owner_team,
        component: body.component,
        duplicate_detected: false,
        duplicate_defect_id: null,
        jira_ticket: null,
        jira_url: null,
        approval_status: pending ? 'pending_review' : null,
        requires_approval: pending,
        policy_reasons: pending ? ['HIGH severity needs a QA lead'] : null,
      }
    }, 'POST'],
    // E2: a claim correction on the published report.
    [`/api/v1/runs/${RUN_ID}/decision-reports/${DECISION_REPORT.report_id}/feedback`, (request) => {
      record(request)
      return { feedback_id: `report-feedback-${writes.length}`, report_id: DECISION_REPORT.report_id, report_version: DECISION_REPORT.report_version }
    }, 'POST'],
    // E1 / E2: the run's intelligence with the published report, when asked.
    [/^\/api\/v1\/runs\/[^/]+\/intelligence$/, (request) => {
      const answer = delegate(RUN_PAGE, request) as { structured_summary: Record<string, unknown> }
      if (!options.decisionReport) return answer
      return {
        ...answer,
        structured_summary: {
          ...answer.structured_summary,
          decision_intelligence: DECISION_INTELLIGENCE,
          decision_report_verification: { status: 'passed', checks: [{ name: 'release_policy_replay', status: 'pass' }] },
          latest_decision_attempt: { pipeline_run_id: 'pipeline-w27a', status: 'published', verification_status: 'passed', at: isoAgo(0, 1) },
          decision_report: DECISION_REPORT,
        },
      }
    }],
    ...RUN_PAGE,
  ]
  return { handlers, writes, tests }
}

// ── Create Jira issue (F2, Q2, C2): one project's Jira, reachable, with a webhook receiver too ──

/** The key the next created issue gets, and where it lives. */
export const CREATED_JIRA_KEY = 'SHOP-901'
export const CREATED_JIRA_URL = `https://jira.example.test/browse/${CREATED_JIRA_KEY}`

/** `GET …/defects/jira/metadata`: Jira reachable (two projects, two issue types) and a webhook receiver. */
export const JIRA_AVAILABLE = {
  available: true,
  reason: null,
  projects: [
    { key: 'SHOP', name: 'Shop' },
    { key: 'QA', name: 'Quality' },
  ],
  issue_types: ['Bug', 'Task'],
  default_project_key: 'SHOP',
  webhook_available: true,
}

/**
 * The Create Jira issue form's three calls for `projectId`, writes recorded into `writes`: the metadata, the
 * server-assembled preview (for the fingerprint or the cluster asked), and the create (a new issue; the
 * webhook target answers one notified subscription).
 */
export function jiraHandlers(writes: RecordedWrite[], projectId = PROJECT_ID): ApiHandlers {
  const base = `/api/v1/projects/${projectId}/defects/jira`
  return [
    [`${base}/metadata`, () => JIRA_AVAILABLE],
    [`${base}/preview`, ({ url }) => {
      const signature = url.searchParams.get('fingerprint') ?? url.searchParams.get('cluster_id') ?? ''
      return {
        signature,
        summary: `[TestLookup] ${signature} keeps failing`,
        description: `Failure: ${signature}\nFailing runs: 3\nFirst seen: ${isoAgo(6)}`,
        test_name: signature,
        suite_name: 'Payments',
        cluster_id: url.searchParams.get('cluster_id'),
        error_message: 'AssertionError: expected "Card declined" but was "Payment failed"',
        occurrences: { first_seen: isoAgo(6), last_seen: isoAgo(0, 2), failing_runs: 3 },
        context: { branch: 'main', build_number: '240', ci_run_url: null },
        ai_analysis: null,
        deep_link: `/failures?fingerprint=${signature}`,
        latest_run_id: RUN_ID,
        existing_defect: null,
      }
    }],
    [base, (request) => {
      const body = bodyOf(request) as { target?: string }
      writes.push({ line: `${request.method} ${request.path}`, body })
      const webhook = body?.target === 'webhook'
      return {
        target: webhook ? 'webhook' : 'jira',
        deduplicated: false,
        defect_id: webhook ? null : 'dddddddd-0000-4000-8000-0000000000b1',
        jira_key: webhook ? null : CREATED_JIRA_KEY,
        jira_url: webhook ? null : CREATED_JIRA_URL,
        external_status: webhook ? null : 'To Do',
        recurrence_count: 0,
        recurrence_comment_posted: false,
        subscriptions_notified: webhook ? 1 : null,
        message: webhook ? 'Requested.' : 'Created.',
      }
    }, 'POST'],
  ]
}

// ── /failures (PAN-04, F1, F2, F4, F5, F6) ─────────────────────────────────

/** The hint `failuresInPageWorld` starts with (F5 toggles and deletes it). */
export const SEEDED_HINT = {
  id: '99999999-9999-4999-8999-0000000000aa',
  project_id: PROJECT_ID,
  match_field: 'error_message',
  pattern: 'ETIMEDOUT',
  category: 'INFRASTRUCTURE',
  note: null,
  enabled: true,
  created_by_name: 'QA Lead',
  created_at: isoAgo(3),
  updated_at: isoAgo(3),
}

/** Most failures uncategorised (26 of 36): the category card offers Correct and Classify (≥ 50 %). */
export const UNCATEGORISED_CATEGORIES = {
  items: [
    { category: 'ASSERTION', count: 7, kind: 'product' },
    { category: 'TIMEOUT', count: 3, kind: 'infrastructure' },
    { category: 'UNKNOWN', count: 26, kind: 'unknown' },
  ],
  by_kind: [
    { kind: 'product', count: 7 },
    { kind: 'infrastructure', count: 3 },
    { kind: 'test_code', count: 0 },
    { kind: 'unknown', count: 26 },
  ],
  period_days: 30,
}

/** How many failures `POST /analytics/classify-uncategorized` reports it tagged. */
export const CLASSIFIED_COUNT = 26

/**
 * `/failures` (`hintsWorld()` over `FAILURES_ON`) with: one stored hint (F5), the hint toggle and delete, most
 * failures uncategorised (F4, F6), Jira reachable (F2), the analysis lookup and the classification feedback
 * (F4), the quarantine proposal (F1) and the bulk classification (F6). Every write is recorded in `writes`.
 */
export function failuresInPageWorld() {
  const hints = hintsWorld()
  const { writes } = hints
  hints.hints.push({ ...SEEDED_HINT })
  const HINT = /^\/api\/v1\/projects\/[^/]+\/classification-hints\/[^/]+$/
  const record = (request: ApiRequest) => {
    const body = bodyOf(request)
    writes.push({ line: `${request.method} ${request.path}`, body })
    return body
  }
  const handlers: ApiHandlers = [
    [HINT, (request) => {
      const body = record(request) as { enabled?: boolean }
      const hint = hints.hints.find((h) => h.id === request.path.split('/').pop())
      if (!hint) return respond(404, { detail: 'Hint not found' })
      Object.assign(hint, body, { updated_at: isoAgo(0) })
      return hint
    }, 'PATCH'],
    [HINT, (request) => {
      record(request)
      const index = hints.hints.findIndex((h) => h.id === request.path.split('/').pop())
      if (index < 0) return respond(404, { detail: 'Hint not found' })
      hints.hints.splice(index, 1)
      return respond(204)
    }, 'DELETE'],
    ['/api/v1/analytics/failure-categories', () => UNCATEGORISED_CATEGORIES],
    ...jiraHandlers(writes),
    [`/api/v1/projects/${PROJECT_ID}/analyses/lookup`, ({ url }) => ({
      analysis_id: `analysis-${url.searchParams.get('fingerprint')}`,
      failure_category: 'UNKNOWN',
      analyzed_at: isoAgo(0, 3),
    })],
    [/^\/api\/v1\/feedback\/[^/]+$/, (request) => {
      record(request)
      return { feedback_id: `feedback-${writes.length}`, message: 'Feedback recorded' }
    }, 'POST'],
    ['/api/v1/quarantine', (request) => {
      const body = record(request) as Record<string, unknown>
      return {
        id: '00000000-0000-4000-8000-000000001201',
        ...body,
        status: 'PROPOSED',
        proposed_at: isoAgo(0),
        created_at: isoAgo(0),
        updated_at: isoAgo(0),
      }
    }, 'POST'],
    ['/api/v1/analytics/classify-uncategorized', (request) => {
      const body = record(request) as { category: string }
      return { updated: CLASSIFIED_COUNT, category: body.category }
    }, 'POST'],
    ...hints.handlers,
  ]
  return { handlers, writes, hints: hints.hints }
}

// ── /flaky's request tabs (PAN-04, Q2) ─────────────────────────────────────

/** The W09 `/flaky` quarantine world (`flakyQuarantineWorld()`) with Jira reachable (`jiraHandlers`), writes shared. */
export function flakyJiraWorld() {
  const world = flakyQuarantineWorld()
  return { ...world, handlers: [...jiraHandlers(world.writes), ...world.handlers] as ApiHandlers }
}

// ── /my-failures (PAN-04, M1, M2) ──────────────────────────────────────────

/** Who a failure may be reassigned to: its suite's owner and two QA engineers. */
export const REASSIGN_OPTIONS = {
  suiteOwner: { user_id: '00000000-0000-4000-8000-0000000000b2', email: 'dana@example.test', username: 'dana', full_name: 'Dana Whitfield' },
  engineers: [
    { user_id: '00000000-0000-4000-8000-0000000000b1', email: 'priya@example.test', username: 'priya', full_name: 'Priya Nair' },
    { user_id: '00000000-0000-4000-8000-0000000000e1', email: 'qa.engineer@example.test', username: 'qa_engineer', full_name: 'QA Engineer' },
  ],
}

/**
 * The Inbox (`inboxHandlers()`) plus Reassign and Triage: the reassignment options of a failure, the reassign and
 * the triage PUTs (recorded); a reassigned or triaged failure leaves the caller's list (and its totals) on the
 * next read, as the server's does.
 */
export function inboxInPageWorld() {
  const writes: RecordedWrite[] = []
  const base = inboxHandlers()
  const gone = new Set<string>()
  const failureId = (path: string) => path.split('/')[5]
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/me\/assigned-failures\/[^/]+\/reassign-options$/, () => ({
      project_id: PROJECT_ID,
      suite_name: 'Payments',
      suite_owner: REASSIGN_OPTIONS.suiteOwner,
      qa_engineers: REASSIGN_OPTIONS.engineers,
    })],
    [/^\/api\/v1\/me\/assigned-failures\/[^/]+\/(reassign|triage)$/, (request) => {
      const body = bodyOf(request)
      writes.push({ line: `${request.method} ${request.path}`, body })
      gone.add(failureId(request.path))
      return { id: failureId(request.path), ...(body as object) }
    }, 'PUT'],
    ['/api/v1/me/assigned-failures', (request) => {
      const list = delegate(base, request) as { items: { id: string }[]; total: number; unresolved_total: number }
      const items = list.items.filter((item) => !gone.has(item.id))
      const dropped = list.items.length - items.length
      return { ...list, items, total: list.total - dropped, unresolved_total: list.unresolved_total - dropped }
    }],
    ...base,
  ]
  return { handlers, writes }
}

// ── /chat (PAN-04, C1, C2) ─────────────────────────────────────────────────

/** The conversation the chat world streams into. */
export const CHAT_SESSION_ID = '99999999-9999-4999-8999-99999999aaaa'

/** The failing test the copilot suggests acting on (the `/failures` headline test). */
export const CHAT_TEST = { fingerprint: 'fp-top-0', name: 'card declined shows reason', suite: 'Payments', fails: 9 }

/** The answer's two suggested actions (AI-6): a quarantine proposal and a Jira issue, prefilled. */
export const CHAT_ACTIONS = [
  {
    type: 'propose_quarantine',
    label: 'Propose quarantine',
    prefill: { project_id: PROJECT_ID, test_fingerprint: CHAT_TEST.fingerprint, test_name: CHAT_TEST.name, suite_name: CHAT_TEST.suite, fail_count: CHAT_TEST.fails },
  },
  {
    type: 'create_jira',
    label: 'Create Jira issue',
    prefill: { project_id: PROJECT_ID, fingerprint: CHAT_TEST.fingerprint, test_name: CHAT_TEST.name, suite_name: CHAT_TEST.suite },
  },
]

/** The server's refusal of a quarantine proposal below QA lead. */
export const PROPOSE_REFUSAL = 'Requires at least QA_LEAD role'

const sse = (events: Array<[string, unknown]>) =>
  respondRaw(events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''), 'text/event-stream')

/**
 * Ask AI with one scripted answer that carries `CHAT_ACTIONS` (the shape `chat-conversation.spec.ts` streams: the
 * session is created on the first question, the answer arrives whole as server-sent events), plus the two writes
 * its actions open: the quarantine proposal (403 `PROPOSE_REFUSAL` with `refuse`) and Jira (`jiraHandlers`).
 */
export function chatInPageWorld(options: { refuse?: boolean } = {}) {
  const writes: RecordedWrite[] = []
  const messages: Record<string, unknown>[] = []
  let sessions: Record<string, unknown>[] = []
  const handlers: ApiHandlers = [
    ['/api/v1/quarantine', (request) => {
      const body = bodyOf(request) as Record<string, unknown>
      writes.push({ line: `${request.method} ${request.path}`, body })
      if (options.refuse) return respond(403, { detail: PROPOSE_REFUSAL })
      return { id: '00000000-0000-4000-8000-000000001301', ...body, status: 'PROPOSED', created_at: isoAgo(0), updated_at: isoAgo(0) }
    }, 'POST'],
    ...jiraHandlers(writes),
    ['/api/v1/settings/ai/mode', () => ({ analysis_mode: 'llm' })],
    ['/api/v1/chat/run-summaries', () => []],
    ['/api/v1/chat/sessions', () => sessions],
    ['/api/v1/chat/sessions', () => {
      sessions = [{ id: CHAT_SESSION_ID, project_id: PROJECT_ID, title: 'New conversation', created_at: isoAgo(0), updated_at: isoAgo(0) }]
      return sessions[0]
    }, 'POST'],
    [`/api/v1/chat/sessions/${CHAT_SESSION_ID}/messages`, () => messages],
    [`/api/v1/chat/sessions/${CHAT_SESSION_ID}/messages/stream`, (request) => {
      const body = bodyOf(request) as { message?: string }
      messages.push({ id: 'q-1', session_id: CHAT_SESSION_ID, role: 'user', content: body?.message ?? '', sources: null, created_at: isoAgo(0) })
      const meta = { type: 'meta', status: 'complete', model: 'mistralai/mistral-nemo', first_token_ms: 900, total_ms: 2100, tool_calls: 1 }
      const answer = {
        id: 'a-1',
        session_id: CHAT_SESSION_ID,
        role: 'assistant',
        content: `${CHAT_TEST.name} failed ${CHAT_TEST.fails} times this week and looks flaky.`,
        created_at: isoAgo(0),
        sources: [{ type: 'suggested_actions', actions: CHAT_ACTIONS }, meta],
      }
      messages.push(answer)
      return sse([
        ['start', { user_message_id: 'q-1', retry: false }],
        ['delta', { text: answer.content }],
        ['done', { message: answer, sources: [], tool_trace: [], suggested_actions: CHAT_ACTIONS, meta }],
      ])
    }, 'POST'],
    ...LAYOUT,
  ]
  return { handlers, writes }
}

// ── /defects (PAN-04, D1) ──────────────────────────────────────────────────

/** The id the next intake gets (its toast names the first 8 characters). */
export const NEW_DEFECT_ID = 'abcdef12-0000-4000-8000-0000000000d1'

/** The server's refusal of an intake below QA engineer. */
export const INTAKE_REFUSAL = 'Requires at least QA_ENGINEER role'

/**
 * The W06 `/defects` world (`defectsWorld({ releases: true })`, so the top bar lists `RELEASES`) plus the intake:
 * `POST /api/v1/analytics/defects` adds an OPEN row (its title as the test name, so the table shows it) and
 * answers it; with `refuse` it answers 403 with `INTAKE_REFUSAL`.
 */
export function defectsIntakeWorld(options: { refuse?: boolean } = {}) {
  const world = defectsWorld({ releases: true })
  const handlers: ApiHandlers = [
    ['/api/v1/analytics/defects', (request) => {
      const body = bodyOf(request) as Record<string, unknown>
      world.writes.push({ line: `${request.method} ${request.path}`, body })
      if (options.refuse) return respond(403, { detail: INTAKE_REFUSAL })
      const row = {
        ...world.rows[0],
        id: NEW_DEFECT_ID,
        title: String(body.title),
        test_name: (body.test_name as string | undefined) ?? String(body.title),
        suite_name: String(body.suite_name ?? ''),
        priority: String(body.severity),
        failure_category: String(body.failure_category),
        resolution_status: 'OPEN',
        jira_ticket_id: null,
        jira_ticket_url: null,
        jira_status: null,
        assignee_user_id: null,
        assignee_name: null,
        owner_team: null,
        owner_source: null,
        created_at: isoAgo(0),
        resolved_at: null,
      }
      world.rows.unshift(row)
      return respond(201, { id: NEW_DEFECT_ID, project_id: row.project_id, title: row.title, resolution_status: 'OPEN' })
    }, 'POST'],
    ...world.handlers,
  ]
  return { handlers, writes: world.writes, rows: world.rows }
}
