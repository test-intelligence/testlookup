import { useMemo } from 'react'
import { useRuns } from './useRuns'
import { collectSuiteOptionsFromRuns } from '@/utils/suiteFilters'
import type { TestRun } from '@/types/runs'

export function useSuiteOptions(days?: number | null, size = 100) {
  const query = useMemo(
    () => ({ page: 1, size, ...(days && days > 0 ? { days } : { days: 0 }) }),
    [days, size],
  )
  const { data, isLoading } = useRuns(query)
  const runs = useMemo<TestRun[]>(() => (data?.items ?? []) as TestRun[], [data?.items])
  const options = useMemo(() => collectSuiteOptionsFromRuns(runs), [runs])
  return { options, runs, isLoading }
}
