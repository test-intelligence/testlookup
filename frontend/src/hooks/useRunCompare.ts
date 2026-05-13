import useSWR from 'swr'
import {
  runCompareService,
  type RunCompareResponse,
} from '@/services/runCompareService'

/**
 * SWR hook for the two-run compare endpoint. Returns ``null`` when
 * either side is missing so the caller can render an empty state
 * without firing an invalid request.
 */
export function useRunCompare(
  leftId: string | null,
  rightId: string | null,
  suiteName?: string | null,
) {
  const enabled = Boolean(leftId && rightId && leftId !== rightId)
  const { data, error, isLoading } = useSWR<RunCompareResponse>(
    enabled ? ['run-compare', leftId, rightId, suiteName || ''] : null,
    () => {
      if (!leftId || !rightId) {
        throw new Error('Left and right run IDs are required')
      }
      return runCompareService.compare(leftId, rightId, suiteName)
    },
    { revalidateOnFocus: false },
  )
  return { compare: data, isLoading, isError: !!error }
}

export function useLatestSuiteCompare(suiteName: string | null, projectId?: string | null) {
  const enabled = Boolean(suiteName?.trim())
  const { data, error, isLoading } = useSWR<RunCompareResponse>(
    enabled ? ['run-compare-latest-suite', suiteName, projectId || ''] : null,
    () => runCompareService.compareLatestSuite(suiteName!.trim(), projectId),
    { revalidateOnFocus: false },
  )
  return { compare: data, isLoading, isError: !!error }
}
