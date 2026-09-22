import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { useReleaseScope } from './useReleaseScope'
import { summaryReportService } from '@/services/summaryReportService'
import type { SummaryReportMode } from '@/types/summaryReport'

/** Everything the summary report is scoped by, apart from the project. */
export interface SummaryReportScope {
  days: number
  mode: SummaryReportMode
  /** Present only when a release applies — absent, never null (NFR1). */
  release_id?: string
}

/**
 * The scope the summary report is requested with. The on-screen report
 * (`useSummaryReport`) and the PDF export on `SummaryReportPage` BOTH read it,
 * so the exported document cannot be scoped differently from the screen: the
 * PDF once omitted the release and went out as an all-releases report under a
 * release-scoped page.
 *
 * The release axis comes from `useReleaseScope`, which returns a release only
 * when one project is pinned and the selection belongs to it — a release id
 * means nothing in another project, and filtering by one would empty the report
 * while the picker still showed a name.
 */
export function useSummaryReportScope(params: {
  days: number
  mode: SummaryReportMode
}): SummaryReportScope {
  const releaseId = useReleaseScope()
  return {
    days: params.days,
    mode: params.mode,
    ...(releaseId ? { release_id: releaseId } : {}),
  }
}

/**
 * Summary Report data for the active project. Re-keys automatically when
 * the user switches project, time window, or aggregation mode. Background
 * refresh tier — the report is a snapshot view, no need for sub-minute polling.
 */
export function useSummaryReport(params: { days: number; mode: SummaryReportMode }) {
  const scope = useSummaryReportScope(params)

  return useProjectScopedSWR(
    'summary-report',
    (projectId) => summaryReportService.get({ project_id: projectId, ...scope }),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
    // The release belongs in the KEY, not just the request. Without it SWR
    // serves the previously-cached all-releases report on the first render
    // after a selection, so the page shows unfiltered numbers under a release
    // filter until the next revalidation.
    [scope.days, scope.mode, scope.release_id ?? null],
  )
}
