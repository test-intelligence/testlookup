/**
 * NTF-02: one project's notification policy, for the policy card (Email
 * (SMTP) & channels) and the hints on My notifications.
 *
 * Its own module, NOT `useNotifications.ts`: that module is fixed-factory
 * mocked in seven test files, where a hook added to it would be undefined.
 *
 * `projectId === null` (All Projects, or no project) asks for nothing. The
 * answer is parsed here (`parseTransitionPolicy`), so no reader ever touches
 * `.enabled_events` on a raw response: a malformed answer is `policy: null`.
 */
import useSWR from 'swr'
import { getTransitionPolicy } from '@/services/notificationPolicyService'
import { parseTransitionPolicy, type TransitionPolicy } from '@/types/notifications'

export const transitionPolicyKey = (projectId: string) => ['transition-policy', projectId] as const

export function useTransitionPolicy(projectId: string | null): {
  policy: TransitionPolicy | null
  error: unknown
  isLoading: boolean
  mutate: () => Promise<unknown>
} {
  const { data, error, isLoading, mutate } = useSWR<unknown>(
    projectId ? transitionPolicyKey(projectId) : null,
    () => getTransitionPolicy(projectId as string),
    // A failed read is shown once, with Retry; no background retry loop.
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  return { policy: parseTransitionPolicy(data), error, isLoading, mutate: () => mutate() }
}
