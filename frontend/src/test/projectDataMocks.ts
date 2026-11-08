/**
 * Fixtures for the project export / import vitests (W24 ADM-05 phase 1), in the REAL §1.2 shapes.
 *
 * - `jobFixture({kind, status, …})`: one `ProjectDataJob` whose fields match what the server returns for that
 *   kind + status together (a running export has progress 12 / 29; a succeeded export has bytes, 29 counted
 *   tables, a download path and an expiry; a failed job has an `error`; an import that succeeded carries
 *   `importReportFixture()`).
 * - `importReportFixture(overrides)`: three inserted entities, one switched-off line, one skipped row (the
 *   singular) and two cleared assignments.
 * - `jobList(items, total?)`: the `{items, total}` answer.
 * - `errorBody(code, fmt?, extra?)` / `exportRunningBody(jobId)` / `errorBlob(code)` / `refusal(status, body)`:
 *   the `{detail: {code, message}}` refusals, as JSON, as a Blob (a blob download's refusal) and as an AxiosError.
 * - W30 run history: `estimateFixture()` (measured), `unmeasuredEstimateFixture(store)` (null counts, never 0),
 *   `overLimitEstimateFixture()`, `historyJobFixture({kind, status, phase})` (a running job per phase / a
 *   succeeded one), `historyReportFixture()` / `historyImportReportFixture()` (one skip, the retention pause,
 *   recent runs, search rebuilt), `uploadFixture()` / `partReceiptFixture()`; `jobFixture({status: 'uploading'})`
 *   is a parted import with its upload state.
 * - W37: `RELEASE_ID`, `downloadTicketFixture(jobId)` (the ticket POST's `{ticket, expires_in, url}`) and
 *   `peopleImportReportFixture()` (members / suite owners / reviews inserted, an e-mail skip, an owner skip, one
 *   case returned to Review requested, `options_ignored`).
 *
 * Names are non-hex words; timestamps are built at CALL time (never a module constant).
 */
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'

import {
  ERROR_SENTENCES,
  EXPORT_TTL_HOURS,
  fillSentence,
  type ProjectDataDownloadTicket,
  type ProjectDataErrorCode,
  type ProjectDataHistoryReport,
  type ProjectDataImportReport,
  type ProjectDataJob,
  type ProjectDataJobList,
  type ProjectDataJobPhase,
  type ProjectDataUpload,
  type ProjectExportEstimate,
  type ProjectImportPartReceipt,
} from '@/types/projectData'

export const PROJECT_DATA_PROJECT_ID = '00000000-0000-4000-8000-00000000da7a'
export const IMPORTED_PROJECT_ID = '00000000-0000-4000-8000-00000000da7b'
export const SOURCE_PROJECT_NAME = 'Payment Service'
export const IMPORTED_PROJECT_NAME = 'Payment Service (imported)'
export const REQUESTER_NAME = 'Quinn Lead'
const MB = 1024 * 1024

/** The 29 exported entities in plan order (`project_export_entities.EXPORTED`; W25d HYG-02 dropped `quality_gates`). */
export const EXPORTED_ENTITY_NAMES = [
  'projects', 'test_suites', 'managed_test_cases', 'test_case_versions', 'test_case_requirement_links',
  'dismissed_duplicate_pairs', 'canonical_test_cases', 'test_plans', 'test_plan_items', 'test_strategies',
  'releases', 'release_phases', 'release_gate_policies', 'release_attribution_rules',
  'quarantine_lifecycle_policies', 'notification_transition_policies', 'team_notification_channels',
  'webhook_subscriptions', 'github_integrations', 'gitlab_integrations', 'service_ownership_rules',
  'project_retention_policies', 'workflow_definitions', 'project_workflow_defaults', 'agent_configs',
  'run_cadences', 'value_metric_assumptions', 'classification_hints', 'project_llm_quota',
] as const

let sequence = 0

function jobUuid(): string {
  sequence += 1
  return `00000000-0000-4000-8000-${String(0xd000 + sequence).padStart(12, '0')}`
}

function minutesFromNow(minutes: number): string {
  return new Date(Date.now() + minutes * 60_000).toISOString()
}

/** 4,210 rows across the 29 exported entities (one row each, the rest in managed_test_cases). */
export function exportCounts(): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const name of EXPORTED_ENTITY_NAMES) counts[name] = 1
  counts.managed_test_cases = 4210 - (EXPORTED_ENTITY_NAMES.length - 1)
  return counts
}

export function importReportFixture(overrides: Partial<ProjectDataImportReport> = {}): ProjectDataImportReport {
  return {
    project_id: IMPORTED_PROJECT_ID,
    project_name: IMPORTED_PROJECT_NAME,
    project_slug: 'payment-service-imported',
    inserted: { test_suites: 3, managed_test_cases: 42, releases: 2 },
    skipped: [{ entity: 'test_plan_items', column: 'test_case_id', reason: 'row_unresolved', count: 1 }],
    cleared: [{ entity: 'managed_test_cases', column: 'assignee_id', reason: 'assignment', count: 2 }],
    switched_off: { webhook_subscriptions: 3 },
    signature: 'valid',
    source_project_name: SOURCE_PROJECT_NAME,
    source_app_version: '2.4.0',
    exported_at: minutesFromNow(-30),
    ...overrides,
  }
}

type JobOverrides = Partial<ProjectDataJob> & { kind?: 'export' | 'import'; status?: ProjectDataJob['status'] }

export function jobFixture(overrides: JobOverrides = {}): ProjectDataJob {
  const kind = overrides.kind ?? 'export'
  const status = overrides.status ?? 'succeeded'
  const id = overrides.id ?? jobUuid()
  const isExport = kind === 'export'
  const base: ProjectDataJob = {
    id,
    kind,
    status,
    project_id: isExport ? PROJECT_DATA_PROJECT_ID : null,
    project_name: isExport ? SOURCE_PROJECT_NAME : null,
    requested_by_name: REQUESTER_NAME,
    requested_at: minutesFromNow(-5),
    started_at: status === 'queued' ? null : minutesFromNow(-4),
    finished_at: null,
    progress: null,
    bytes: null,
    file_count: null,
    manifest_sha256: null,
    signature: null,
    expires_at: null,
    counts: {},
    import_report: null,
    error: null,
    download_path: null,
  }
  if (status === 'running') base.progress = isExport ? { done: 12, total: 29 } : { done: 5, total: 29 }
  if (status === 'succeeded' || status === 'expired') {
    base.finished_at = minutesFromNow(-1)
    base.progress = { done: 29, total: 29 }
    if (isExport) {
      base.bytes = Math.round(2.1 * MB)
      base.file_count = 32
      base.manifest_sha256 = 'f00d'.repeat(16)
      base.signature = 'signed'
      base.counts = exportCounts()
      base.expires_at = status === 'expired' ? minutesFromNow(-60) : minutesFromNow(EXPORT_TTL_HOURS * 60 - 1)
      if (status === 'succeeded') base.download_path = `/api/v1/project-data-jobs/${id}/download`
    } else {
      const report = importReportFixture()
      base.project_id = report.project_id
      base.project_name = SOURCE_PROJECT_NAME
      base.import_report = report
      base.counts = report.inserted
      base.signature = report.signature
    }
  }
  if (status === 'failed') {
    base.finished_at = minutesFromNow(-1)
    base.error = isExport
      ? { code: 'export_build', message: fillSentence(ERROR_SENTENCES.export_build, { error_type: 'OperationalError' }) }
      : { code: 'import_failed', message: fillSentence(ERROR_SENTENCES.import_failed, { error_type: 'IntegrityError' }) }
  }
  if (status === 'uploading') {
    // W30: only a parted import uploads — a 60 MB archive in 2 parts, none received yet
    base.started_at = null
    base.upload = uploadFixture()
  }
  return { ...base, ...overrides, kind, status, id }
}

// ── W30 ADM-05: run history ─────────────────────────────────────────────────

/** The 27 history entities in plan order (`project_export_entities.HISTORY_TABLES`). */
export const HISTORY_ENTITY_NAMES = [
  'test_runs', 'agent_pipeline_runs', 'test_cases', 'test_case_history', 'suite_executions', 'suite_run_reviews',
  'test_execution_reviews', 'test_steps', 'test_step_runs', 'test_attachments', 'failure_clusters',
  'agent_stage_results', 'ai_analysis', 'ai_analysis_versions', 'failure_attribution', 'deep_findings',
  'release_decisions', 'release_test_run_links', 'run_baselines', 'run_diffs', 'run_commit_ranges',
  'run_intelligence_snapshots', 'test_health_recommendations', 'evidence_artifacts', 'release_outcomes',
  'release_gate_decisions', 'timeline_notes',
] as const

/** The ten run-scoped collections in export order (`project_export_documents.MONGO_COLLECTIONS`). */
export const MONGO_COLLECTION_NAMES = [
  'raw_allure_json', 'rest_api_payloads', 'live_execution_events', 'run_summaries', 'ai_run_versions',
  'decision_evidence_snapshots', 'ai_analysis_payloads', 'pipeline_event_log', 'decision_reports',
  'decision_report_attempts',
] as const

/** 9,120 documents across the ten collections (most of them raw Allure results). */
export function documentCounts(): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const name of MONGO_COLLECTION_NAMES) counts[name] = 12
  counts.raw_allure_json = 9120 - 12 * (MONGO_COLLECTION_NAMES.length - 1)
  return counts
}

/** A measured estimate: "About 412.3 MB · 412 runs · 9,120 documents · 1,830 attachments". */
export function estimateFixture(overrides: Partial<ProjectExportEstimate> = {}): ProjectExportEstimate {
  return {
    include_history: true,
    history_days: 90,
    runs: 412,
    runs_in_progress: 1,
    runs_over_cap: 0,
    test_cases: 17_304,
    documents: documentCounts(),
    objects: 1830,
    object_bytes: 380 * MB,
    estimated_bytes: Math.round(412.3 * MB),
    over_limit: false,
    measured: { postgres: true, documents: true, objects: true },
    ...overrides,
  }
}

/** The documents store (or the object store) could not be read: its counts are null, never 0. */
export function unmeasuredEstimateFixture(store: 'documents' | 'objects' = 'documents'): ProjectExportEstimate {
  const estimate = estimateFixture({ estimated_bytes: null, over_limit: null })
  if (store === 'documents') estimate.documents = null
  else {
    estimate.objects = null
    estimate.object_bytes = null
  }
  estimate.measured = { postgres: true, documents: store !== 'documents', objects: store !== 'objects' }
  return estimate
}

/** Larger than the 2 GB cap — the card disables Export. */
export function overLimitEstimateFixture(): ProjectExportEstimate {
  return estimateFixture({ history_days: null, runs: 2000, estimated_bytes: 2300 * MB, over_limit: true })
}

export function uploadFixture(overrides: Partial<ProjectDataUpload> = {}): ProjectDataUpload {
  return { parts: 2, bytes: 60 * MB, received: [], received_bytes: 0, ...overrides }
}

export function partReceiptFixture(overrides: Partial<ProjectImportPartReceipt> = {}): ProjectImportPartReceipt {
  return { part: 1, bytes: 32 * MB, received: [1], complete: false, ...overrides }
}

/** Where a running history job stands in each phase (documents written of documents planned, and so on). */
const PHASE_PROGRESS: Record<ProjectDataJobPhase, { phase_done: number; phase_total: number }> = {
  catalog: { phase_done: 12, phase_total: 29 },
  runs: { phase_done: 120, phase_total: 412 },
  documents: { phase_done: 5000, phase_total: 9120 },
  objects: { phase_done: 900, phase_total: 1830 },
  verify: { phase_done: 0, phase_total: 1 },
  index: { phase_done: 0, phase_total: 1 },
}

/** The history block of a finished import: one skip, a retention pause, recent runs, search rebuilt. */
export function historyReportFixture(overrides: Partial<ProjectDataHistoryReport> = {}): ProjectDataHistoryReport {
  return {
    runs: 412,
    test_cases: 17_304,
    oldest_run_at: minutesFromNow(-90 * 24 * 60),
    newest_run_at: minutesFromNow(-2 * 24 * 60),
    documents: documentCounts(),
    objects: 1830,
    object_bytes: 380 * MB,
    skipped: [{ entity: 'objects', reason: 'too_large', count: 2 }],
    retention_paused_until: minutesFromNow(90 * 24 * 60),
    runs_past_retention: 0,
    recent_runs: 3,
    search_reindex: 'queued',
    ...overrides,
  }
}

/** A finished history import's report: phase 1's report + the history block. */
export function historyImportReportFixture(
  overrides: Partial<ProjectDataImportReport> = {},
  history: Partial<ProjectDataHistoryReport> = {},
): ProjectDataImportReport {
  return importReportFixture({ history: historyReportFixture(history), ...overrides })
}

type HistoryJobOverrides = JobOverrides & { phase?: ProjectDataJobPhase }

/**
 * A history job as the server returns it: an export with `options.include_history` and `schema_version` 2,
 * running in `phase` (default `runs`), or succeeded with documents / objects counted; an import running in
 * `phase` (default `documents`), or succeeded with `historyImportReportFixture()`.
 */
export function historyJobFixture(overrides: HistoryJobOverrides = {}): ProjectDataJob {
  const { phase, ...rest } = overrides
  const kind = rest.kind ?? 'export'
  const status = rest.status ?? 'running'
  const job = jobFixture({ ...rest, kind, status })
  job.schema_version = 2
  if (kind === 'export') job.options = { include_history: true, history_days: 90, max_runs: 2000 }
  if (status === 'running') {
    const current = phase ?? (kind === 'export' ? 'runs' : 'documents')
    job.progress = { done: 29, total: 56, phase: current, ...PHASE_PROGRESS[current] }
  }
  if (status === 'succeeded' && kind === 'export') {
    const counts: Record<string, number> = { ...exportCounts() }
    for (const name of HISTORY_ENTITY_NAMES) counts[name] = name === 'test_runs' ? 412 : 1
    for (const [name, count] of Object.entries(documentCounts())) counts[`documents.${name}`] = count
    counts.objects = 1830
    job.counts = counts
    job.bytes = Math.round(412.3 * MB)
    job.progress = { done: 56, total: 56 }
  }
  if (status === 'succeeded' && kind === 'import') {
    const report = historyImportReportFixture()
    job.import_report = report
    job.counts = report.inserted
  }
  return { ...job, ...rest, kind, status, id: job.id }
}

// ── W37: the download ticket, the release-scoped export, the people / reviews report ──

/** A release of the source project (the top bar's cached list). */
export const RELEASE_ID = '00000000-0000-4000-8000-0000000071e5'

/** `POST …/download-ticket`'s answer: a 60-second, single-use same-origin link for `jobId`. */
export function downloadTicketFixture(jobId: string, ticket = 'tkt_Quokka-Lantern_42'): ProjectDataDownloadTicket {
  return { ticket, expires_in: 60, url: `/api/v1/project-data-downloads/${jobId}?ticket=${ticket}` }
}

/**
 * A finished import with Add members + Keep reviews: two members and one suite owner added, one member whose e-mail
 * matched nobody, one owner who is not a QA lead, one case returned to Review requested, and `options_ignored` as
 * given (default `[]`).
 */
export function peopleImportReportFixture(overrides: Partial<ProjectDataImportReport> = {}): ProjectDataImportReport {
  return importReportFixture({
    inserted: { test_suites: 3, managed_test_cases: 42, releases: 2, project_members: 2, test_suite_owners: 1, test_case_reviews: 3, test_case_comments: 5 },
    skipped: [
      { entity: 'project_members', column: null, reason: 'email_not_found', count: 1 },
      { entity: 'test_suite_owners', column: null, reason: 'owner_not_lead', count: 1 },
    ],
    switched_off: { webhook_subscriptions: 3, 'managed_test_cases.review_unclaimed': 1 },
    options_ignored: [],
    ...overrides,
  })
}

export function jobList(items: ProjectDataJob[], total = items.length): ProjectDataJobList {
  return { items, total }
}

export function errorBody(
  code: ProjectDataErrorCode,
  fmt: Record<string, string | number> = {},
  extra: Record<string, unknown> = {},
): { detail: { code: string; message: string } & Record<string, unknown> } {
  return { detail: { code, message: fillSentence(ERROR_SENTENCES[code], fmt), ...extra } }
}

export function exportRunningBody(jobId: string) {
  return errorBody('export_running', {}, { job_id: jobId })
}

/** A refusal as a blob download receives it. */
export function errorBlob(code: ProjectDataErrorCode, fmt: Record<string, string | number> = {}): Blob {
  return new Blob([JSON.stringify(errorBody(code, fmt))], { type: 'application/json' })
}

/** An AxiosError carrying `body` with HTTP `status`, as the service's callers receive it. */
export function refusal(status: number, body: unknown): AxiosError {
  const config = { headers: {} } as InternalAxiosRequestConfig
  const response = { status, statusText: '', headers: {}, config, data: body } as AxiosResponse
  return new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_REQUEST', config, undefined, response)
}
