import { isAxiosError } from 'axios'

import type {
  DeletionExecuteAccepted,
  DeletionJob,
  DeletionJobList,
  DeletionPreview,
  RetentionCriteria,
  RetentionPolicy,
  RetentionPolicyWrite,
  RetentionPreview,
  RetentionPurgeResponse,
} from '@/types/retention'
import type { ExportChoice } from '@/types/retentionExport'
import { api } from './api'
import { extractErrorMessage } from './apiErrors'
import { getData, postData, putData } from './http'

/** What `criteriaDeletionService.collect` resolves: the archive and the two digests the server sent with it. */
export interface CollectedArchive {
  blob: Blob
  /** `X-Manifest-Sha256` — what `acknowledge` must send back. null when the header is missing. */
  manifestSha256: string | null
  /** `X-Archive-Sha256` — the whole stored object's digest. */
  archiveSha256: string | null
  /** From `Content-Disposition`; null when the response names none (the caller builds one from the job id). */
  filename: string | null
}

/** `attachment; filename="x.zip"` (plain, quoted or RFC 5987) → `x.zip`; else null. */
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

function headerValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/**
 * The words for a failed collect. The request asks for a blob, so a JSON refusal (`{detail: {code, message}}`
 * or a plain-string detail) arrives as a Blob: parse it, then read it with `extractErrorMessage`.
 */
export async function deletionArchiveErrorMessage(error: unknown): Promise<string> {
  const fallback = 'The archive could not be downloaded.'
  if (!isAxiosError(error)) return error instanceof Error && error.message ? error.message : fallback
  const body: unknown = error.response?.data
  let parsed: unknown = body
  if (body instanceof Blob) {
    try {
      parsed = JSON.parse(await body.text())
    } catch {
      parsed = undefined
    }
  }
  const detail = parsed && typeof parsed === 'object' ? (parsed as { detail?: unknown }).detail : undefined
  return detail === undefined || detail === null ? fallback : extractErrorMessage(detail, fallback)
}

/**
 * Retention & purge policy (PMF backlog US-11.4) — per-project data-retention
 * configuration, dry-run preview, and manual purge trigger.
 *
 * Pinned contract; PUT/preview/purge are ADMIN-only on the backend. The purge
 * POST returns 202 {queued: true} (the purge itself runs async and writes an
 * audit record), 409 when the policy is disabled, and 422 when the typed
 * confirmation name does not match the project name.
 *
 * Rides the shared Axios base in `services/api.ts` via the `http` helpers.
 */
export const retentionService = {
  get: (projectId: string) =>
    getData<RetentionPolicy>(`/api/v1/projects/${projectId}/retention-policy`),

  update: (projectId: string, payload: RetentionPolicyWrite) =>
    putData<RetentionPolicy, RetentionPolicyWrite>(
      `/api/v1/projects/${projectId}/retention-policy`,
      payload,
    ),

  preview: (projectId: string) =>
    postData<RetentionPreview>(
      `/api/v1/projects/${projectId}/retention-policy/preview`,
      {},
    ),

  purge: (projectId: string, confirmationName: string) =>
    postData<RetentionPurgeResponse, { confirmation_name: string }>(
      `/api/v1/projects/${projectId}/retention-policy/purge`,
      { confirmation_name: confirmationName },
    ),
}

/**
 * Criteria deletion (S5). Two steps, and the split is the safety property:
 * `preview` FREEZES a candidate set and returns its `job_id`; `execute` takes
 * that id and replays the frozen set. Passing criteria to execute would
 * re-resolve them, and the set deleted would not be the set reviewed.
 *
 * Errors ride the shared Axios interceptor: 403 (run ids from another
 * project), 409 (job already executed, or the frozen set drifted), 422
 * (confirmation name mismatch, or criteria that narrow nothing).
 */
export const criteriaDeletionService = {
  preview: (projectId: string, criteria: RetentionCriteria) =>
    postData<DeletionPreview, RetentionCriteria>(
      `/api/v1/projects/${projectId}/deletion/preview`,
      criteria,
    ),

  /** W22b: `exportChoice` builds (and verifies) an archive first; the body carries `export` only when given. */
  execute: (projectId: string, jobId: string, confirmationName: string, exportChoice?: ExportChoice) =>
    postData<
      DeletionExecuteAccepted,
      { job_id: string; confirmation_name: string; export?: ExportChoice }
    >(
      `/api/v1/projects/${projectId}/deletion/execute`,
      exportChoice
        ? { job_id: jobId, confirmation_name: confirmationName, export: exportChoice }
        : { job_id: jobId, confirmation_name: confirmationName },
    ),

  jobs: (projectId: string) =>
    getData<DeletionJobList>(`/api/v1/projects/${projectId}/deletion/jobs`),

  /**
   * W22b: download a `collect` archive (ADMIN, signed in). Streams through the API — there is no presigned URL —
   * as a blob; the caller saves it with `downloadBlob` and keeps `manifestSha256` for `acknowledge`. Toasts are
   * suppressed: the caller renders `deletionArchiveErrorMessage(error)` inline.
   */
  collect: async (projectId: string, jobId: string): Promise<CollectedArchive> => {
    const response = await api.post<Blob>(
      `/api/v1/projects/${projectId}/deletion/jobs/${jobId}/collect`,
      null,
      { responseType: 'blob', suppressToast: true },
    )
    const headers = (response.headers ?? {}) as Record<string, unknown>
    return {
      blob: response.data,
      manifestSha256: headerValue(headers['x-manifest-sha256']),
      archiveSha256: headerValue(headers['x-archive-sha256']),
      filename: filenameFromDisposition(headers['content-disposition']),
    }
  },

  /** W22b: confirm the downloaded archive was saved, with its exact manifest digest; the job goes back to queued. */
  acknowledge: (projectId: string, jobId: string, manifestSha256: string) =>
    postData<DeletionJob, { manifest_sha256: string }>(
      `/api/v1/projects/${projectId}/deletion/jobs/${jobId}/acknowledge`,
      { manifest_sha256: manifestSha256 },
    ),
}
