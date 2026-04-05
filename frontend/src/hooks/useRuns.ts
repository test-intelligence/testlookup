import useSWR from 'swr'
import { runsService } from '@/services/runsService'
import { useProjectScopedSWR } from './useProjectScopedSWR'

export function useRuns(params?: Record<string, unknown>) {
  return useProjectScopedSWR(
    'runs',
    (projectId) => runsService.list(projectId, params),
    { refreshInterval: 15_000 },
    [params],
  )
}

export function useRun(runId?: string) {
  return useSWR(runId ? ['run', runId] : null, () => runsService.get(runId as string))
}

export function useTestCases(runId?: string, params?: Record<string, unknown>) {
  return useSWR(
    runId ? ['test-cases', runId, params] : null,
    () => runsService.listTests(runId as string, params)
  )
}

export function useTestCase(runId?: string, testId?: string) {
  return useSWR(
    runId && testId ? ['test-case', runId, testId] : null,
    () => runsService.getTest(runId as string, testId as string)
  )
}
