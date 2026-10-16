/**
 * W08 TRND-08: Value metrics › By team. The key is null unless `enabled` (the
 * page is on its By team view) AND one project is pinned: the Overview view
 * and All Projects ask nothing.
 *
 * `teams` is `null` while loading and for a malformed answer; `malformed`
 * tells the two apart (the view then says "not available", the header stays).
 */
import useSWR from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { teamValueMetricsService } from '@/services/teamValueMetricsService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { parseTeamValueMetrics, type TeamValueMetrics } from '@/types/valueMetrics'

export const TEAM_VALUE_KEY = 'value-metrics-by-team'

export interface TeamValueRead {
  teams: TeamValueMetrics | null
  malformed: boolean
  isLoading: boolean
  error: unknown
  refresh: () => Promise<unknown>
}

export function useTeamValueMetrics(projectId: string | null | undefined, days: number, enabled: boolean): TeamValueRead {
  const pinned = projectId && projectId !== ALL_PROJECTS_ID ? projectId : null
  const { data, error, isLoading, mutate } = useSWR(
    enabled && pinned ? [TEAM_VALUE_KEY, pinned, days] : null,
    // An empty body is an answer too (`null`): it must read as malformed, not as "still loading".
    ([, pid, d]: readonly [string, string, number]) => teamValueMetricsService.getByTeam(pid, d).then((raw) => raw ?? null),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND, revalidateOnFocus: false },
  )
  const teams = data === undefined ? null : parseTeamValueMetrics(data)
  return {
    teams,
    malformed: (data !== undefined && teams === null) || (error !== undefined && data === undefined),
    isLoading,
    error,
    refresh: () => mutate(),
  }
}
