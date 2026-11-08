import { isAxiosError } from 'axios'

import { sha256Parts } from '@/lib/sha256Stream'
import {
  ERROR_SENTENCES,
  IMPORT_ARCHIVE_MAX_BYTES,
  IMPORT_PART_MAX_BYTES,
  IMPORT_UPLOAD_MAX_BYTES,
  type ProjectDataDownloadTicket,
  type ProjectDataJob,
  type ProjectDataJobList,
  type ProjectExportEstimate,
  type ProjectExportRequest,
  type ProjectImportPartReceipt,
  type ProjectImportUploadStart,
} from '@/types/projectData'
import { downloadBlob } from '@/utils/download'
import { api } from './api'
import { extractErrorMessage } from './apiErrors'
import { getData, postData } from './http'

/**
 * W24 ADM-05 phase 1: export a project's settings + catalog, import an export as a NEW project.
 * W30 (ADM-05 run history): the export may carry run history (an optional body), an estimate is read first, and an archive
 * larger than IMPORT_UPLOAD_MAX_BYTES is imported through the PARTED upload (start → parts → complete).
 * W37: the estimate may name a release; an archive over 512 MB downloads through a one-time ticket (POST, then a
 * same-origin navigation); the import's `add_members` / `keep_reviews` travel only when chosen.
 *
 * Every request path is written out in full (the SPA wire contract reads the
 * quoted literals). The POSTs, the PUT and the download suppress the shared toast:
 * the cards render the `{code, message}` refusal inline (a 409 `export_running`
 * resumes the running job instead of failing).
 */

/** What `importArchive` reports while it works: hashing (bytes read) then uploading (parts received). */
export interface ImportArchiveProgress {
  phase: 'hashing' | 'uploading'
  done: number
  total: number
}

/** W37: the import's two options — sent ONLY when true (the default POST stays `file` + `client_token`). */
export interface ImportChoices {
  addMembers?: boolean
  keepReviews?: boolean
}

export interface ImportArchiveOptions extends ImportChoices {
  name?: string | null
  clientToken: string
  onProgress?: (progress: ImportArchiveProgress) => void
}

/**
 * W37 (F-W30-6): the ticket's link only when it is THIS job's same-origin ticket route —
 * `/api/v1/project-data-downloads/<job id>?ticket=<url-safe token>` — else null. The link is the server's, but it
 * becomes an href, so nothing else (another origin, another path, another job) is ever navigated to.
 */
function ticketLink(url: unknown, jobId: string): string | null {
  const prefix = `/api/v1/project-data-downloads/${jobId}?ticket=`
  if (typeof url !== 'string' || !url.startsWith(prefix)) return null
  return /^[\w\-.~%]+$/.test(url.slice(prefix.length)) ? url : null
}

/**
 * W37: navigate a hidden same-origin `<a href>` (no new window): the response is an attachment, so the browser saves
 * it straight to disk while this page stays where it is.
 */
function followDownloadLink(href: string): void {
  const link = document.createElement('a')
  link.href = href
  link.rel = 'noopener'
  link.style.display = 'none'
  document.body.appendChild(link)
  try {
    link.click()
  } finally {
    link.remove()
  }
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
 * The words for a failed project-data request — a structured `{code, message}` refusal reads as its
 * sentence (also out of a Blob, for the download), never "Request failed with status code 409".
 */
export async function projectDataErrorMessage(error: unknown, fallback = 'The request failed.'): Promise<string> {
  const detail = await refusalDetail(error)
  if (detail !== undefined && detail !== null) return extractErrorMessage(detail, fallback)
  if (!isAxiosError(error) && error instanceof Error && error.message) return error.message
  return fallback
}

/** The running export's id from a 409 `export_running` refusal (the card resumes polling it), else null. */
export function runningExportJobId(error: unknown): string | null {
  if (!isAxiosError(error) || error.response?.status !== 409) return null
  const data: unknown = error.response?.data
  const detail = data && typeof data === 'object' ? (data as { detail?: unknown }).detail : undefined
  if (!detail || typeof detail !== 'object') return null
  const { code, job_id: jobId } = detail as { code?: unknown; job_id?: unknown }
  return code === 'export_running' && typeof jobId === 'string' && jobId ? jobId : null
}

/** The trimmed name when one was typed, else undefined (the server then names the copy itself). */
function typedName(name?: string | null): string | undefined {
  const trimmed = name?.trim()
  return trimmed ? trimmed : undefined
}

export const projectDataService = {
  /**
   * Queue an export of the project (QA lead / ADMIN with access). 202 → the queued job. The body is sent ONLY
   * when given (W30 run history) — a catalog-only export stays the body-less phase-1 call.
   */
  startExport: (projectId: string, request?: ProjectExportRequest) =>
    postData<ProjectDataJob>(`/api/v1/projects/${projectId}/export`, request, { suppressToast: true }),

  /**
   * What an export with history would carry (counts only; a store that could not be read answers null with
   * `measured[store] = false`). `historyDays` null = all history (the parameter is then omitted). W37: a
   * `historyReleaseId` reads the runs linked to that release (`?include_history=true&history_release_id=<id>`).
   */
  getExportEstimate: (
    projectId: string,
    {
      includeHistory,
      historyDays,
      maxRuns,
      historyReleaseId,
    }: { includeHistory: boolean; historyDays?: number | null; maxRuns?: number; historyReleaseId?: string | null },
  ) =>
    getData<ProjectExportEstimate>(`/api/v1/projects/${projectId}/export-estimate`, {
      params: {
        include_history: includeHistory,
        ...(historyDays != null ? { history_days: historyDays } : {}),
        ...(historyReleaseId ? { history_release_id: historyReleaseId } : {}),
        ...(maxRuns != null ? { max_runs: maxRuns } : {}),
      },
    }),

  /** The project's exports, newest first (`total` = all of them). */
  listExports: (projectId: string, limit?: number) =>
    getData<ProjectDataJobList>(`/api/v1/projects/${projectId}/data-jobs`, {
      params: limit != null ? { limit } : undefined,
    }),

  /** The caller's own imports, newest first (ADMIN). */
  listMyImports: (limit?: number) =>
    getData<ProjectDataJobList>('/api/v1/project-imports', {
      params: limit != null ? { limit } : undefined,
    }),

  /** One job (the polling read). */
  getJob: (jobId: string) => getData<ProjectDataJob>(`/api/v1/project-data-jobs/${jobId}`),

  /**
   * Download a succeeded export: an authenticated blob read (the token rides the Authorization header, never a
   * URL) saved through `downloadBlob`. Resolves the saved file name. A refusal rejects; read it with
   * `projectDataErrorMessage`, which parses the JSON out of the Blob.
   */
  downloadExport: async (jobId: string, fallbackName?: string): Promise<string> => {
    const response = await api.get<Blob>(`/api/v1/project-data-jobs/${jobId}/download`, {
      responseType: 'blob',
      suppressToast: true,
    })
    const headers = (response.headers ?? {}) as Record<string, unknown>
    const filename =
      filenameFromDisposition(headers['content-disposition']) ?? fallbackName ?? `project-export-${jobId}.zip`
    downloadBlob(response.data, filename)
    return filename
  },

  /** W37 (F-W30-6): a 60-second, single-use download link for a succeeded export (`{ticket, expires_in, url}`). */
  createDownloadTicket: (jobId: string) =>
    postData<ProjectDataDownloadTicket>(`/api/v1/project-data-jobs/${jobId}/download-ticket`, undefined, {
      suppressToast: true,
    }),

  /**
   * W37 (F-W30-6): download an archive larger than 512 MB straight to disk — ONE ticket POST (its refusal rejects,
   * with the same `{code, message}` as the Blob download's), then a hidden same-origin `<a href>` navigates to the
   * ticket's link (no Blob in memory, no Axios timeout, no new window). Resolves the link it followed.
   */
  downloadLargeExport: async (jobId: string): Promise<string> => {
    const ticket = await projectDataService.createDownloadTicket(jobId)
    const href = ticketLink(ticket?.url, jobId)
    if (!href) throw new Error(ERROR_SENTENCES.ticket_unavailable)
    followDownloadLink(href)
    return href
  },

  /**
   * Upload an export to create a NEW project (ADMIN). Multipart: `file`, `client_token` (minted once per chosen
   * file, so a retry of the same selection is the same job) and `name` only when one was typed. Resolves the
   * job — a new one (202) or the existing job of that token (200). W37: `add_members` / `keep_reviews` = "true"
   * only when chosen (unchosen = no field, so the default POST is W24's `file` + `client_token` exactly).
   */
  startImport: (
    file: File,
    { name, clientToken, addMembers, keepReviews }: { name?: string | null; clientToken: string } & ImportChoices,
  ) => {
    const form = new FormData()
    form.append('file', file)
    form.append('client_token', clientToken)
    const trimmed = typedName(name)
    if (trimmed) form.append('name', trimmed)
    if (addMembers) form.append('add_members', 'true')
    if (keepReviews) form.append('keep_reviews', 'true')
    return api
      .post<ProjectDataJob>('/api/v1/projects/import', form, {
        // The shared client defaults Content-Type to JSON: unset it so the
        // browser writes multipart/form-data WITH its boundary.
        headers: { 'Content-Type': undefined as unknown as string },
        suppressToast: true,
      })
      .then(({ data }) => data)
  },

  /**
   * W30: open a parted upload (ADMIN). 201 → a new job in `uploading`; 200 → the caller's existing job of the
   * same token (its `upload.received`, or its later status) — the resume.
   */
  startPartedImport: (start: ProjectImportUploadStart) =>
    api
      .post<ProjectDataJob>('/api/v1/project-imports/uploads', start, { suppressToast: true })
      .then(({ data }) => data),

  /** W30: send ONE part as raw bytes with its SHA-256 (`X-Part-Sha256`); a repeat of the same part is a no-op. */
  uploadPart: (jobId: string, part: number, blob: Blob, sha256: string) =>
    api
      .put<ProjectImportPartReceipt>(`/api/v1/project-imports/uploads/${jobId}/parts/${part}`, blob, {
        headers: { 'Content-Type': 'application/octet-stream', 'X-Part-Sha256': sha256 },
        suppressToast: true,
      })
      .then(({ data }) => data),

  /** W30: every part sent → the job is queued (202), or the job as it stands on a repeat (200). */
  completeUpload: (jobId: string) =>
    api
      .post<ProjectDataJob>(`/api/v1/project-imports/uploads/${jobId}/complete`, undefined, { suppressToast: true })
      .then(({ data }) => data),

  /**
   * W30: import a chosen archive. ≤ IMPORT_UPLOAD_MAX_BYTES → the phase-1 single multipart POST, unchanged.
   * Larger (≤ IMPORT_ARCHIVE_MAX_BYTES, else refused here with no request) → ONE streaming read hashes the
   * whole file and each 32 MiB part (at most one slice in memory), then start → the parts the server lacks,
   * ONE in flight, each a `Blob.slice` → complete. A start that answers a job no longer `uploading` (the same
   * file was already imported) resolves that job without sending anything.
   */
  importArchive: async (
    file: File,
    { name, clientToken, onProgress, addMembers, keepReviews }: ImportArchiveOptions,
  ): Promise<ProjectDataJob> => {
    // W37: the options travel only when chosen — as Form fields on the single POST, as booleans in the parted start.
    const choices = { ...(addMembers ? { addMembers: true } : {}), ...(keepReviews ? { keepReviews: true } : {}) }
    if (file.size <= IMPORT_UPLOAD_MAX_BYTES) return projectDataService.startImport(file, { name, clientToken, ...choices })
    if (file.size > IMPORT_ARCHIVE_MAX_BYTES) throw new Error(ERROR_SENTENCES.upload_too_large_history)
    const digests = await sha256Parts(file, {
      sliceBytes: IMPORT_PART_MAX_BYTES,
      onProgress: (done, total) => onProgress?.({ phase: 'hashing', done, total }),
    })
    const total = digests.parts.length
    const job = await projectDataService.startPartedImport({
      client_token: clientToken,
      name: typedName(name) ?? null,
      bytes: file.size,
      parts: total,
      sha256: digests.sha256,
      ...(addMembers ? { add_members: true } : {}),
      ...(keepReviews ? { keep_reviews: true } : {}),
    })
    if (job.status !== 'uploading') return job
    const received = new Set(job.upload?.received ?? [])
    onProgress?.({ phase: 'uploading', done: received.size, total })
    for (let part = 1; part <= total; part += 1) {
      if (received.has(part)) continue
      const start = (part - 1) * IMPORT_PART_MAX_BYTES
      const blob = file.slice(start, Math.min(start + IMPORT_PART_MAX_BYTES, file.size))
      const receipt = await projectDataService.uploadPart(job.id, part, blob, digests.parts[part - 1])
      onProgress?.({ phase: 'uploading', done: receipt.received.length, total })
    }
    return projectDataService.completeUpload(job.id)
  },
}
