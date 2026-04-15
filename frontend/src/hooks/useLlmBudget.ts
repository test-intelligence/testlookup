import useSWR from 'swr'
import {
  llmBudgetService,
  type BillingOverviewResponse,
  type LlmQuotaRead,
  type LlmUsageRead,
} from '@/services/llmBudgetService'

export function useBillingOverview() {
  const { data, error, isLoading, mutate } = useSWR<BillingOverviewResponse>(
    'billing-overview',
    () => llmBudgetService.overview(),
    { revalidateOnFocus: false, refreshInterval: 60_000 },
  )
  return { overview: data, isLoading, isError: !!error, refresh: mutate }
}

export function useProjectQuota(projectId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<LlmQuotaRead | null>(
    projectId ? ['llm-quota', projectId] : null,
    async () => {
      try {
        return await llmBudgetService.getQuota(projectId!)
      } catch (err: unknown) {
        const axiosErr = err as { response?: { status?: number } }
        // 404 means "no quota configured" — valid state, return null.
        if (axiosErr?.response?.status === 404) return null
        throw err
      }
    },
  )
  return { quota: data ?? null, isLoading, isError: !!error, refresh: mutate }
}

export function useProjectUsage(projectId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<LlmUsageRead>(
    projectId ? ['llm-usage', projectId] : null,
    () => llmBudgetService.getUsage(projectId!),
    { refreshInterval: 60_000 },
  )
  return { usage: data, isLoading, isError: !!error, refresh: mutate }
}
