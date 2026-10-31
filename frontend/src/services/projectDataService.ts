import { isAxiosError } from 'axios'

import type { ProjectDataJob, ProjectDataJobList } from '@/types/projectData'
import { downloadBlob } from '@/utils/download'
import { api } from './api'
import { extractErrorMessage } from './apiErrors'
import { getData, postData } from './http'

/**
 * W24 ADM-05 phase 1: export a project's settings + catalog, import an export as a NEW project.
 *
 * Every request path is written out in full (the SPA wire contract reads the
 * quoted literals). The two POSTs and the download suppress the shared toast:
 * the cards render the `{code, message}` refusal inline (a 409 `export_running`
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

export const projectDataService = {
  /** Queue an export of the project (QA lead / ADMIN with access). 202 → the queued job. */
  startExport: (projectId: string) =>
    postData<ProjectDataJob>(`/api/v1/projects/${projectId}/export`, undefined, { suppressToast: true }),

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

  /**
   * Upload an export to create a NEW project (ADMIN). Multipart: `file`, `client_token` (minted once per chosen
   * file, so a retry of the same selection is the same job) and `name` only when one was typed. Resolves the
   * job — a new one (202) or the existing job of that token (200).
   */
  startImport: (file: File, { name, clientToken }: { name?: string | null; clientToken: string }) => {
    const form = new FormData()
    form.append('file', file)
    form.append('client_token', clientToken)
    const trimmed = name?.trim()
    if (trimmed) form.append('name', trimmed)
    return api
      .post<ProjectDataJob>('/api/v1/projects/import', form, {
        // The shared client defaults Content-Type to JSON: unset it so the
        // browser writes multipart/form-data WITH its boundary.
        headers: { 'Content-Type': undefined as unknown as string },
        suppressToast: true,
      })
      .then(({ data }) => data)
  },
}
