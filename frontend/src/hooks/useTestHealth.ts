import useSWR from 'swr'
import {
  testHealthService,
  TestHealthResponse,
  FlakyCoachResponse,
} from '@/services/testHealthService'

export function useRunTestHealth(runId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<TestHealthResponse>(
    runId ? `test-health-${runId}` : null,
    () => {
      if (!runId) throw new Error('runId is required')
      return testHealthService.getRunTestHealth(runId)
    },
    { revalidateOnFocus: false },
  )

  return {
    health: data,
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}

export function useFlakyCoach(projectId: string | null, days = 30, limit = 50) {
  const { data, error, isLoading, mutate } = useSWR<FlakyCoachResponse>(
    projectId ? `flaky-coach-${projectId}-${days}` : null,
    () => {
      if (!projectId) throw new Error('projectId is required')
      return testHealthService.getFlakyCoach(projectId, days, limit)
    },
    { revalidateOnFocus: false },
  )

  return {
    coach: data,
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}
