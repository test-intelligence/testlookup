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
 *
 * Names are non-hex words; timestamps are built at CALL time (never a module constant).
 */
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'

import {
  ERROR_SENTENCES,
  EXPORT_TTL_HOURS,
  fillSentence,
  type ProjectDataErrorCode,
  type ProjectDataImportReport,
  type ProjectDataJob,
  type ProjectDataJobList,
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
  return { ...base, ...overrides, kind, status, id }
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
