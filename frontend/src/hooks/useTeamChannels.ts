import useSWR from 'swr'
import { type TeamChannel, listTeamChannels } from '@/services/ownershipService'

/**
 * Team notification channels (US-7.3) as an SWR hook.
 *
 * Same shape as `useOwnershipRules`: keyed on projectId, no fetch for the
 * all-projects view (`null` projectId), single-shot error surfacing.
 */
export function useTeamChannels(projectId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<TeamChannel[]>(
    projectId ? (['ownership-team-channels', projectId] as const) : null,
    ([, pid]: readonly [string, string]) => listTeamChannels(pid),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  return {
    channels: data ?? [],
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}
