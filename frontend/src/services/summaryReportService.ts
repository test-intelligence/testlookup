/**
 * Service for the Project Summary Report endpoints.
 *
 * Mirrors ``backend/app/routers/summary_report.py``:
 *   - ``GET /api/v1/reports/summary``     → JSON (powers the page)
 *   - ``GET /api/v1/reports/summary/pdf`` → PDF blob (the export button)
 */
import { api } from './api'
import { getData } from './http'
import type { SummaryReport, SummaryReportMode } from '@/types/summaryReport'
import { scopeParam, type ScopeValue } from '@/lib/scopeParams'

export interface SummaryReportParams {
  project_id: string | null
  days: number
  mode: SummaryReportMode
  /** Scope every number to one release (or, VIZ-303, several). Omitted = all
   *  releases. */
  release_id?: ScopeValue
}

/** Release scoping, OMITTED when absent rather than sent as null or empty —
 *  the same convention as `analyticsService.releaseParam`. One release is one
 *  scalar `release_id=a`, so the wire is unchanged for single-value callers;
 *  several are a repeated `release_id` (contract C1). */
function releaseParam(releaseId: ScopeValue): Record<string, string | string[]> {
  return scopeParam('release_id', releaseId)
}

/**
 * The query string for BOTH summary endpoints — the on-screen report and its
 * PDF export.
 *
 * One builder, because the two used to be built separately and drifted: the
 * screen sent `release_id` and the PDF did not, so with a release selected the
 * page showed that release while the exported PDF — the document that gets
 * attached to a sign-off thread — silently covered every release. Routing both
 * requests through this function makes that disagreement impossible to write.
 */
export function summaryReportQueryParams(
  params: SummaryReportParams,
): Record<string, string | number | string[]> {
  return {
    ...(params.project_id ? { project_id: params.project_id } : {}),
    days: params.days,
    mode: params.mode,
    // Omitted entirely when there is no release, never sent as null: the
    // backend fragment is conditional so that an absent release produces
    // byte-identical SQL, and a `release_id=` on every request would undo
    // that for callers who never asked for the feature.
    ...releaseParam(params.release_id),
  }
}

export const summaryReportService = {
  get: (params: SummaryReportParams): Promise<SummaryReport> =>
    getData<SummaryReport>('/api/v1/reports/summary', {
      params: summaryReportQueryParams(params),
    }),

  /**
   * Trigger a PDF download. Returns the raw Blob so the page can hand it
   * to the browser via URL.createObjectURL — keeps blob handling in the
   * UI layer where it can attach a filename.
   *
   * Takes the same params as `get` (with a required project) and builds the
   * same query string, so the PDF covers exactly what the screen showed.
   */
  downloadPdf: async (params: SummaryReportParams & { project_id: string }): Promise<Blob> => {
    const response = await api.get('/api/v1/reports/summary/pdf', {
      params: summaryReportQueryParams(params),
      responseType: 'blob',
    })
    return response.data as Blob
  },

  /**
   * US-7.5: the self-contained HTML analysis report — the same document
   * the digest dispatcher attaches to daily/weekly digest emails.
   * ``GET /api/v1/projects/{id}/reports/analysis?window=1d|7d``.
   */
  downloadAnalysisReport: async (params: {
    project_id: string
    window: '1d' | '7d'
  }): Promise<Blob> => {
    const response = await api.get(
      `/api/v1/projects/${params.project_id}/reports/analysis`,
      { params: { window: params.window }, responseType: 'blob' },
    )
    return response.data as Blob
  },
}
