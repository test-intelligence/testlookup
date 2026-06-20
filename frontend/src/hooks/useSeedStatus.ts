import useSWR from 'swr'
import { api } from '@/services/api'

export interface SeedStatus {
  seeded: boolean
}

/**
 * Seed-data status as an SWR hook.
 *
 * Replaces a load-on-mount `useEffect(() => { fetchStatus() })` in
 * SeedDataPage that drove the `seeded`/`loading` state from inside the effect
 * — the pattern the react-hooks `set-state-in-effect` rule (correctly)
 * discourages. SWR owns the loading/data/error state declaratively.
 *
 * `seeded` is `null` while loading or when the status check fails (the page
 * renders an "unable to check" state for the error case, distinguished via
 * `isError`), matching the prior `useState<boolean | null>(null)` semantics.
 * `refresh()` lets the page re-check status after a load/reset/delete
 * mutation, exactly as the old `await fetchStatus()` did. `shouldRetryOnError`
 * is off so a failed check surfaces immediately like the old `.catch`.
 */
export function useSeedStatus() {
  const { data, error, isLoading, mutate } = useSWR<SeedStatus>(
    'dev-seed-status',
    () => api.get<SeedStatus>('/api/v1/dev/seed/status').then(r => r.data),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false,
    },
  )

  return {
    seeded: data ? data.seeded : null,
    isLoading,
    isError: !!error,
    refresh: () => mutate(),
  }
}
