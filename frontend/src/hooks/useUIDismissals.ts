import useSWR from 'swr'
import { uiDismissalService, type UIDismissalList } from '@/services/uiDismissalService'

/**
 * UI prompts the current user has dismissed.
 *
 * Not project-scoped — a dismissal is a property of the person, so this fetches
 * once and every prompt reads the same cache entry.
 *
 * `revalidateOnFocus: false` because the answer only changes when this tab
 * changes it, and a prompt that flickers back on window focus is worse than a
 * slightly stale one.
 */
export function useUIDismissals() {
  return useSWR<UIDismissalList>(
    '/api/v1/auth/me/dismissals',
    () => uiDismissalService.list(),
    { revalidateOnFocus: false },
  )
}

/**
 * Dismiss a prompt. Imperative (side-effecting POST), so it is a thin wrapper
 * the component calls on button press rather than an SWR resource; the caller
 * mutates the cache with the returned list.
 */
export function dismissUIPrompt(dismissalKey: string): Promise<UIDismissalList> {
  return uiDismissalService.dismiss(dismissalKey)
}
