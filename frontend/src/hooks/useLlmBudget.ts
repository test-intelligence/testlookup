import useSWR from 'swr'
import {
  llmBudgetService,
  type BillingOverviewResponse,
  type LlmQuotaRead,
  type LlmUsageRead,
} from '@/services/llmBudgetService'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

export function useBillingOverview() {
  const { data, error, isLoading, mutate } = useSWR<BillingOverviewResponse>(
    'billing-overview',
    () => llmBudgetService.overview(),
    { revalidateOnFocus: false, refreshInterval: REFRESH_INTERVALS.BACKGROUND },
  )
  return { overview: data, isLoading, isError: !!error, refresh: mutate }
}

export function useProjectQuota(projectId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<LlmQuotaRead | null>(
    projectId ? ['llm-quota', projectId] : null,
    async () => {
      if (!projectId) {
        throw new Error('Project ID is required')
      }
      try {
        return await llmBudgetService.getQuota(projectId)
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
    () => {
      if (!projectId) {
        throw new Error('Project ID is required')
      }
      return llmBudgetService.getUsage(projectId)
    },
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND },
  )
  return { usage: data, isLoading, isError: !!error, refresh: mutate }
}
