import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { useReleaseScope } from './useReleaseScope'
import { summaryReportService } from '@/services/summaryReportService'
import type { SummaryReportMode } from '@/types/summaryReport'

/**
 * Summary Report data for the active project. Re-keys automatically when
 * the user switches project, time window, or aggregation mode. Background
 * refresh tier — the report is a snapshot view, no need for sub-minute polling.
 */
export function useSummaryReport(params: { days: number; mode: SummaryReportMode }) {
  // The release axis. `useReleaseScope` returns a release only when one project
  // is pinned and the selection belongs to it — a release id means nothing in
  // another project, and filtering by one would empty the report while the
  // picker still showed a name.
  const releaseId = useReleaseScope()

  return useProjectScopedSWR(
    'summary-report',
    (projectId) =>
      summaryReportService.get({
        project_id: projectId,
        days: params.days,
        mode: params.mode,
        release_id: releaseId,
      }),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    // The release belongs in the KEY, not just the request. Without it SWR
    // serves the previously-cached all-releases report on the first render
    // after a selection, so the page shows unfiltered numbers under a release
    // filter until the next revalidation.
    [params.days, params.mode, releaseId],
  )
}
