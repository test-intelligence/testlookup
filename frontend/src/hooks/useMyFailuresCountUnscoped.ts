import useSWR from 'swr'
import { useAuthStore } from '@/store/authStore'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { myFailuresService } from '@/services/myFailuresService'

/**
 * Non-project-scoped variant for the top-level "all my work" sidebar badge.
 *
 * Deliberately NOT release-scoped, unlike its project-scoped sibling above. A
 * release belongs to one project, so filtering a badge that spans every project
 * by one project's release would answer a question nobody asked — and
 * `useReleaseScope` returns null without a single pinned project anyway.
 */
export function useMyFailuresCountUnscoped(params: { days?: number } = {}) {
  const userId = useAuthStore((state) => state.user?.id ?? null)
  return useSWR(
    userId ? ['my-failures-count-unscoped', userId, params.days] : null,
    () => myFailuresService.count({ days: params.days }),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
  )
}
