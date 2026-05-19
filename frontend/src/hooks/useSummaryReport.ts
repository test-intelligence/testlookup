import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { summaryReportService } from '@/services/summaryReportService'
import type { SummaryReportMode } from '@/types/summaryReport'

/**
 * Summary Report data for the active project. Re-keys automatically when
 * the user switches project, time window, or aggregation mode. Background
 * refresh tier — the report is a snapshot view, no need for sub-minute polling.
 */
export function useSummaryReport(params: { days: number; mode: SummaryReportMode }) {
  return useProjectScopedSWR(
    'summary-report',
    (projectId) =>
      summaryReportService.get({
        project_id: projectId,
        days: params.days,
        mode: params.mode,
      }),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    [params.days, params.mode],
  )
}
