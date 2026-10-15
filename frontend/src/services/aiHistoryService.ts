/**
 * W03 AIH-02/03/04/06: the run's AI history, the previous run and the cross-run AI compare
 * (`backend/app/routers/ai_history.py`, contract §6). Every call goes through the shared API client.
 *
 * The reads are rendered by components that show their own failure (an "unavailable" note with Retry, never a
 * blank tab), so they opt out of the global error toast (`suppressToast`). Query parameter names are the REST ones
 * (`from`, `to`, `baseline_run_id`, ...) and an option that is not given is not sent at all.
 *
 * The export is fetched as a blob through the shared client and saved with `downloadBlob`: never a bare
 * `<a href>` at the endpoint, which would carry no Authorization header (HON-04).
 */
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'

import { api } from './api'
import { getData } from './http'
import { downloadBlob } from '@/utils/download'
import type {
  AICompare,
  AIDigestDiffResponse,
  AIHistoryKind,
  AIHistoryList,
  AIPreviousRun,
  AIVersion,
} from '@/types/aiHistory'

const runPath = (runId: string) => `/api/v1/runs/${encodeURIComponent(runId)}`

/** The FE always asks for 100 (the per-run cap of 20 per kind keeps lists far below it). */
export const AI_HISTORY_LIST_LIMIT = 100

export interface AICompareOptions {
  baselineRunId?: string | null
  suiteName?: string | null
  versionId?: string | null
  baselineVersionId?: string | null
}

/** The file name the browser is given when the response names none: `run-<build>-ai-history.json`. */
export function aiHistoryExportFilename(runId: string, buildNumber?: string | number | null): string {
  const raw = buildNumber == null || buildNumber === '' ? runId.slice(0, 8) : String(buildNumber)
  const safe = raw.replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 60)
  return `run-${safe}-ai-history.json`
}

/** `attachment; filename="x.json"` (plain, quoted or RFC 5987) → `x.json`; else `fallback`. */
function filenameFromDisposition(header: unknown, fallback: string): string {
  if (typeof header !== 'string' || !header) return fallback
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1].trim())
    } catch {
      return fallback
    }
  }
  const named = header.match(/filename="([^"]+)"/i) ?? header.match(/filename=([^;]+)/i)
  return named?.[1]?.trim() || fallback
}

/**
 * The words for a failed export. A 409 is the review gate refusing distribution: its body is
 * `{detail: {code, message, ...}}` and the `message` says what to do. Any other failure reads its `detail`.
 */
export async function aiHistoryExportErrorMessage(error: unknown): Promise<string> {
  const fallback = 'AI history export failed'
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
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message
  }
  return fallback
}

export const aiHistoryService = {
  /** R1: the stored versions of one kind, newest first. */
  listVersions: (runId: string, options?: { kind?: AIHistoryKind; limit?: number }) =>
    getData<AIHistoryList>(`${runPath(runId)}/ai-history`, {
      params: { kind: options?.kind ?? 'analysis_digest', limit: options?.limit ?? AI_HISTORY_LIST_LIMIT },
      suppressToast: true,
    }),

  /** R4: one stored version with its payload. */
  getVersion: (runId: string, versionId: string) =>
    getData<AIVersion>(`${runPath(runId)}/ai-history/${encodeURIComponent(versionId)}`, { suppressToast: true }),

  /** R2: the structured diff of two digests of this run (`to`: a version id, `latest` or `live`; default latest). */
  diffVersions: (runId: string, options: { from: string; to?: string | null }) =>
    getData<AIDigestDiffResponse>(`${runPath(runId)}/ai-history/diff`, {
      params: { from: options.from, ...(options.to ? { to: options.to } : {}) },
      suppressToast: true,
    }),

  /** R6: the server's previous run (D42: same project, suite and branch, finished before this run). */
  getPreviousRun: (runId: string, options?: { suiteName?: string | null }) =>
    getData<AIPreviousRun>(`${runPath(runId)}/previous`, {
      params: options?.suiteName ? { suite_name: options.suiteName } : {},
      suppressToast: true,
    }),

  /** R7: this run's AI analysis against a baseline (the previous run by default). */
  compare: (runId: string, options?: AICompareOptions) =>
    getData<AICompare>(`${runPath(runId)}/ai-compare`, {
      params: {
        ...(options?.baselineRunId ? { baseline_run_id: options.baselineRunId } : {}),
        ...(options?.suiteName ? { suite_name: options.suiteName } : {}),
        ...(options?.versionId ? { version_id: options.versionId } : {}),
        ...(options?.baselineVersionId ? { baseline_version_id: options.baselineVersionId } : {}),
      },
      suppressToast: true,
    }),

  /**
   * R3: every stored version, the diffs between consecutive ones and the per-test versions, as
   * `run-<build>-ai-history.json`. Resolves `false` after a toast when the download failed (a 409 says the
   * report awaits human review).
   */
  downloadExport: async (runId: string, buildNumber?: string | number | null): Promise<boolean> => {
    const fallback = aiHistoryExportFilename(runId, buildNumber)
    try {
      const response = await api.get<Blob>(`${runPath(runId)}/ai-history/export`, {
        responseType: 'blob',
        suppressToast: true,
      })
      downloadBlob(response.data, filenameFromDisposition(response.headers?.['content-disposition'], fallback))
      return true
    } catch (error) {
      toast.error(await aiHistoryExportErrorMessage(error))
      return false
    }
  },
}
