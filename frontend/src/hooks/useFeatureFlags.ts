import useSWR from 'swr'
import { featureFlagService, type FeatureFlag } from '@/services/featureFlagService'

export function useFeatureFlags() {
  const { data, error, isLoading, mutate } = useSWR<FeatureFlag[]>(
    'feature-flags',
    () => featureFlagService.list(),
    { revalidateOnFocus: false },
  )
  return { flags: data ?? [], isLoading, isError: !!error, refresh: mutate }
}
