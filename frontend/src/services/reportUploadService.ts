import type { AxiosProgressEvent } from 'axios'
import { api } from './api'

/** Response from POST /api/v1/ingest/file (202 Accepted). */
export interface UploadReportResponse {
  run_id: string
  task_id: string
  /** Always 0 here — the real count is known only after async parsing. */
  total_results: number
  /** False when the server supplied an original receipt after losing publish confirmation. */
  publication_confirmed?: boolean
}

export type UploadState = 'pending' | 'parsing' | 'ingesting' | 'succeeded' | 'failed'

export interface UploadStatus {
  task_id: string
  run_id?: string | null
  state: UploadState
  progress?: { total?: number; parsed?: number } | null
  result?: { total?: number; passed?: number; failed?: number; skipped?: number; broken?: number } | null
  error?: { code?: string; message?: string } | null
}

export type ReportFormat =
  | 'auto'
  | 'junit'
  | 'testng'
  | 'allure'
  | 'cypress'
  | 'playwright'
  | 'pytest'
  | 'robot'
  | 'cucumber'
  | 'nunit'
  | 'trx'
  | 'xunit'

/** Formats the backend `/ingest/file` endpoint accepts. Cypress/Playwright are
 *  feature-flagged server-side and may 503 if disabled for the project. */
export const SUPPORTED_FORMATS: ReadonlyArray<{ value: ReportFormat; label: string }> = [
  { value: 'auto', label: 'Auto-detect' },
  { value: 'junit', label: 'JUnit XML' },
  { value: 'testng', label: 'TestNG XML' },
  { value: 'allure', label: 'Allure JSON' },
  { value: 'playwright', label: 'Playwright JSON' },
  { value: 'cypress', label: 'Cypress (Mochawesome) JSON' },
  { value: 'pytest', label: 'pytest JSON (json-report)' },
  { value: 'robot', label: 'Robot Framework (output.xml)' },
  { value: 'cucumber', label: 'Cucumber JSON' },
  { value: 'nunit', label: 'NUnit3 XML' },
  { value: 'trx', label: 'Visual Studio TRX' },
  { value: 'xunit', label: 'xUnit.net v2 XML' },
]

/** 50 MB — must match the backend MAX_FILE_SIZE cap in routers/ingest.py. */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024

export interface UploadReportParams {
  projectId: string
  file: File
  /** Optional build label. Defaults to `upload-<timestamp>` so the required
   *  backend field is always satisfied without forcing the user to type one. */
  buildNumber?: string
  format?: ReportFormat
  branch?: string
  commitHash?: string
  releaseName?: string
  /** Run the AI analysis pipeline on the upload (default true). */
  runAi?: boolean
  /** 0–100 upload progress callback (multipart transfer, not parse progress). */
  onProgress?: (percent: number) => void
  /** One logical invocation, retained across uncertain retries with identical input. */
  idempotencyKey?: string
}

export function newUploadIdempotencyKey(): string {
  // getRandomValues also works on the supported plain-HTTP development profile.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function unconfirmedUploadReceipt(error: unknown): UploadReportResponse | null {
  const response = (error as { response?: { status?: number; headers?: unknown } } | null)?.response
  if (response?.status !== 503 || !response.headers || typeof response.headers !== 'object') return null
  const headers = Object.fromEntries(Object.entries(response.headers).map(([key, value]) => [key.toLowerCase(), value]))
  const task = headers['x-upload-task-id'], run = headers['x-upload-run-id']
  const marked = task !== undefined || run !== undefined || String(headers.location ?? '').includes('/api/v1/ingest/uploads/')
  if (!marked) return null
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
  if (typeof task !== 'string' || !uuid.test(task) || typeof run !== 'string' || !uuid.test(run)) {
    throw new Error('Upload confirmation is unavailable and its receipt is incomplete. Keep this attempt and check its status before starting another upload.')
  }
  // Always poll the application's configured API by ID. Location is never fetched.
  return { task_id: task.toLowerCase(), run_id: run.toLowerCase(), total_results: 0, publication_confirmed: false }
}

export function defaultBuildLabel(): string {
  // e.g. upload-2026-06-06T17-44-05-123-a1b2 — millisecond + random suffix so
  // back-to-back / concurrent blank-label uploads don't collide on the label.
  const ts = new Date().toISOString().slice(0, 23).replace(/[:.]/g, '-')
  const rand = Math.random().toString(36).slice(2, 6)
  return `upload-${ts}-${rand}`
}

export const reportUploadService = {
  upload: ({
    projectId,
    file,
    buildNumber,
    format = 'auto',
    branch,
    commitHash,
    releaseName,
    runAi = true,
    onProgress,
    idempotencyKey = newUploadIdempotencyKey(),
  }: UploadReportParams): Promise<UploadReportResponse> => {
    const form = new FormData()
    form.append('file', file)
    form.append('project_id', projectId)
    form.append('build_number', buildNumber?.trim() || defaultBuildLabel())
    form.append('format', format)
    form.append('run_ai', runAi ? 'true' : 'false')
    if (branch?.trim()) form.append('branch', branch.trim())
    if (commitHash?.trim()) form.append('commit_hash', commitHash.trim())
    if (releaseName?.trim()) form.append('release_name', releaseName.trim())

    return api
      .post<UploadReportResponse>('/api/v1/ingest/file', form, {
        // The shared axios instance defaults Content-Type to application/json.
        // Null it so the browser sets multipart/form-data WITH the boundary —
        // otherwise FastAPI can't parse the parts.
        headers: { 'Content-Type': undefined as unknown as string, 'Idempotency-Key': idempotencyKey },
        suppressToast: true,
        onUploadProgress: (e: AxiosProgressEvent) => {
          if (onProgress && e.total) {
            onProgress(Math.round((e.loaded / e.total) * 100))
          }
        },
      })
      .then(({ data }) => data)
      .catch((error: unknown) => {
        const receipt = unconfirmedUploadReceipt(error)
        if (receipt) return receipt
        throw error
      })
  },

  /** Poll the async parse/ingest status of an upload (GET /ingest/uploads/{id}). */
  getStatus: (taskId: string): Promise<UploadStatus> =>
    api.get<UploadStatus>(`/api/v1/ingest/uploads/${taskId}`, { suppressToast: true }).then(({ data }) => data),
}
