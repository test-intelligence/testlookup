import { isAxiosError } from 'axios'

import type {
  DeclineCode,
  ErasurePreview,
  ErasureRequestFilter,
  ErasureRequestList,
  ErasureRequestRead,
  MyErasureRequest,
  SubjectJob,
  SubjectJobList,
} from '@/types/subjectRights'
import { downloadBlob } from '@/utils/download'
import { api } from './api'
import { extractErrorMessage } from './apiErrors'
import { getData, postData } from './http'

/**
 * W31 STRAT-12: a person's own data export (Art. 15 / 20) and an ADMIN's pseudonymising erasure (Art. 17).
 *
 * Every request path is written out in full (the SPA wire contract reads the
 * quoted literals). The POSTs and the download suppress the shared toast: the
 * cards render the `{code, message}` refusal inline (a 409 `export_running`
 * resumes the running job instead of failing).
 */

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

async function refusalDetail(error: unknown): Promise<unknown> {
  if (!isAxiosError(error)) return undefined
  const body: unknown = error.response?.data
  let parsed: unknown = body
  if (body instanceof Blob) {
    // A blob request receives a JSON refusal as a Blob: read it before anything else.
    try {
      parsed = JSON.parse(await body.text())
    } catch {
      parsed = undefined
    }
  }
  return parsed && typeof parsed === 'object' ? (parsed as { detail?: unknown }).detail : undefined
}

/**
 * The words for a failed subject-rights request — a structured `{code, message}` refusal reads as its
 * sentence (also out of a Blob, for the download), never "Request failed with status code 409".
 */
export async function subjectRightsErrorMessage(error: unknown, fallback = 'The request failed.'): Promise<string> {
  const detail = await refusalDetail(error)
  if (detail !== undefined && detail !== null) return extractErrorMessage(detail, fallback)
  if (!isAxiosError(error) && error instanceof Error && error.message) return error.message
  return fallback
}

/** The refusal's `code` (`detail.code`, also out of a Blob), else null. */
export async function subjectRightsErrorCode(error: unknown): Promise<string | null> {
  const detail = await refusalDetail(error)
  if (!detail || typeof detail !== 'object') return null
  const { code } = detail as { code?: unknown }
  return typeof code === 'string' && code ? code : null
}

/** The running export's id from a 409 `export_running` refusal (the card resumes polling it), else null. */
export function runningSubjectExportJobId(error: unknown): string | null {
  if (!isAxiosError(error) || error.response?.status !== 409) return null
  const data: unknown = error.response?.data
  const detail = data && typeof data === 'object' ? (data as { detail?: unknown }).detail : undefined
  if (!detail || typeof detail !== 'object') return null
  const { code, job_id: jobId } = detail as { code?: unknown; job_id?: unknown }
  return code === 'export_running' && typeof jobId === 'string' && jobId ? jobId : null
}

export const subjectRightsService = {
  /** Queue an export of MY OWN data (any signed-in person). 202 → the queued job (`source: 'self'`). */
  startMyExport: () => postData<SubjectJob>('/api/v1/me/data-export', undefined, { suppressToast: true }),

  /** My own exports of myself, newest first (`total` = all of them). */
  listMyExports: () => getData<SubjectJobList>('/api/v1/me/data-exports'),

  /** ADMIN: queue an export of any person's data. 202 → the queued job (`source: 'admin'`). */
  startUserExport: (userId: string) =>
    postData<SubjectJob>(`/api/v1/users/${userId}/data-export`, undefined, { suppressToast: true }),

  /** ADMIN: a person's export and erasure jobs, newest first. */
  listUserJobs: (userId: string) => getData<SubjectJobList>(`/api/v1/users/${userId}/data-jobs`),

  /** One job (the polling read). */
  getJob: (jobId: string) => getData<SubjectJob>(`/api/v1/subject-data-jobs/${jobId}`),

  /**
   * Download a succeeded export: an authenticated blob read (the token rides the Authorization header, never a
   * URL) saved through `downloadBlob`. Resolves the saved file name. A refusal rejects; read it with
   * `subjectRightsErrorMessage`, which parses the JSON out of the Blob.
   */
  downloadExport: async (jobId: string, fallbackName = 'testlookup-personal-data.zip'): Promise<string> => {
    const response = await api.get<Blob>(`/api/v1/subject-data-jobs/${jobId}/download`, {
      responseType: 'blob',
      suppressToast: true,
    })
    const headers = (response.headers ?? {}) as Record<string, unknown>
    const filename = filenameFromDisposition(headers['content-disposition']) ?? fallbackName
    downloadBlob(response.data, filename)
    return filename
  },

  /** ADMIN: what an erasure would change, keep and leave open (reads only; blockers listed, never raised). */
  getErasurePreview: (userId: string) => getData<ErasurePreview>(`/api/v1/users/${userId}/erasure-preview`),

  /**
   * ADMIN: erase a person (the typed username must equal theirs exactly). 202 → the erasure job at
   * `phase: 'lockout'` — or, for an unfinished erasure, that job re-queued (the resume).
   */
  eraseUser: (userId: string, confirmUsername: string) =>
    postData<SubjectJob>(
      `/api/v1/users/${userId}/erasure`,
      { confirm_username: confirmUsername },
      { suppressToast: true },
    ),

  // ── W38 (W31 F-1): the person's erasure request and the administrators' queue ──

  /** My NEWEST erasure request (any status), or null when I never asked. */
  getMyErasureRequest: () => getData<MyErasureRequest | null>('/api/v1/me/erasure-request'),

  /**
   * Ask an administrator to erase my account (no body: D-2, no free text). 201 → the pending request. Refusals
   * render inline: 409 `request_pending` (with `detail.request_id`), `last_admin_self` / `last_admin`,
   * `not_a_person`, `user_erased`; 403 `session_required`.
   */
  fileErasureRequest: () =>
    postData<MyErasureRequest>('/api/v1/me/erasure-request', undefined, { suppressToast: true }),

  /** Withdraw my pending request. 200 → the request at `withdrawn`; 409 `no_pending_request`. */
  withdrawErasureRequest: () =>
    postData<MyErasureRequest>('/api/v1/me/erasure-request/withdraw', undefined, { suppressToast: true }),

  /** ADMIN: the queue — pending first (oldest first), then decided (newest decided first), at most 200. */
  listErasureRequests: (status: ErasureRequestFilter) =>
    getData<ErasureRequestList>('/api/v1/users/erasure-requests', { params: { status } }),

  /**
   * ADMIN: decline a pending request with one of the fixed codes. 200 → the decided row; 404
   * `request_not_found`, 409 `request_not_pending` / `own_request`, 422 `decline_code_invalid`.
   */
  declineErasureRequest: (requestId: string, declineCode: DeclineCode) =>
    postData<ErasureRequestRead>(
      `/api/v1/users/erasure-requests/${requestId}/decline`,
      { decline_code: declineCode },
      { suppressToast: true },
    ),
}
