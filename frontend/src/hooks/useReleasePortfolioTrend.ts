/**
 * The release KPI cards' twelve-week trend (W11 REL-03): `GET
 * /api/v1/releases/portfolio-trend`, scoped like every project read (All
 * Projects sends no `project_id`: the server answers the reader's accessible
 * set).
 *
 * Called only by `PortfolioKpis`, which mounts only while the "Release health
 * · this week" disclosure is open: `/releases` makes no new request on load.
 */
import { releasesService } from '@/services/releasesService'
import { useProjectScopedSWR } from './useProjectScopedSWR'

export function useReleasePortfolioTrend(enabled = true) {
  return useProjectScopedSWR(
    'release-portfolio-trend',
    (projectId) => releasesService.portfolioTrend(projectId),
    { revalidateOnFocus: false },
    [],
    enabled,
  )
}
