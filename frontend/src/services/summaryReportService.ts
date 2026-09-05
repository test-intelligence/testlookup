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

export interface SummaryReportParams {
  project_id: string | null
  days: number
  mode: SummaryReportMode
  /** Scope every number to one release. Omitted = all releases. */
  release_id?: string | null
}

export const summaryReportService = {
  get: (params: SummaryReportParams): Promise<SummaryReport> =>
    getData<SummaryReport>('/api/v1/reports/summary', {
      params: {
        ...(params.project_id ? { project_id: params.project_id } : {}),
        days: params.days,
        mode: params.mode,
        // Omitted entirely when there is no release, never sent as null: the
        // backend fragment is conditional so that an absent release produces
        // byte-identical SQL, and a `release_id=` on every request would undo
        // that for callers who never asked for the feature.
        ...(params.release_id ? { release_id: params.release_id } : {}),
      },
    }),

  /**
   * Trigger a PDF download. Returns the raw Blob so the page can hand it
   * to the browser via URL.createObjectURL — keeps blob handling in the
   * UI layer where it can attach a filename.
   */
  downloadPdf: async (params: {
    project_id: string
    days: number
    mode: SummaryReportMode
  }): Promise<Blob> => {
    const response = await api.get('/api/v1/reports/summary/pdf', {
      params,
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
