import useSWR from 'swr'
import toast from 'react-hot-toast'
import { onboardingService, type OnboardingStatus } from '@/services/onboardingService'

/**
 * Onboarding progress as an SWR hook.
 *
 * Replaces a load-on-mount `useEffect(() => { detectProgress() })` in
 * OnboardingPage that drove the `status`/`loading` state from inside the effect
 * — the pattern the react-hooks `set-state-in-effect` rule (correctly)
 * discourages. SWR owns the loading/data state declaratively.
 *
 * The key is `null` (no fetch) when there is no resolved project — exactly the
 * old `if (!projectId) { setStatus(null); setLoading(false); return }` guard,
 * where the all-projects view (`null` projectId) never fetched and showed the
 * 0%/no-steps workspace view. `status` is `null` while loading or before a
 * project is selected, preserving the prior
 * `useState<OnboardingStatus | null>(null)` semantics. `mutate` lets the page
 * apply the server response from a skip-step mutation, exactly as the old
 * `setStatus(updated)` did. `shouldRetryOnError` is off so a failed load
 * surfaces immediately, toasting "Could not load onboarding status" (the one
 * failed-read wording, W29a UIX-11) where the old `.catch` raised its toast.
 */
export function useOnboardingStatus(projectId: string | null) {
  const { data, isLoading, mutate } = useSWR<OnboardingStatus>(
    projectId ? ['onboarding-status', projectId] : null,
    ([, id]: [string, string]) => onboardingService.detectProgress(id),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false,
      onError: () => toast.error('Could not load onboarding status'),
    },
  )

  return {
    status: data ?? null,
    isLoading,
    mutate,
  }
}
