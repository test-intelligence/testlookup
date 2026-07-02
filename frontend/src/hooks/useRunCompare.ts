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
  return {
    compare: data,
    isLoading,
    isError: !!error,
    error: error as unknown,
  }
}

export function useLatestSuiteCompare(suiteName: string | null, projectId?: string | null) {
  const enabled = Boolean(suiteName?.trim())
  const { data, error, isLoading } = useSWR<RunCompareResponse>(
    enabled ? ['run-compare-latest-suite', suiteName, projectId || ''] : null,
    () => {
      if (!suiteName) {
        throw new Error('Suite name is required')
      }
      return runCompareService.compareLatestSuite(suiteName.trim(), projectId)
    },
    { revalidateOnFocus: false },
  )
  return {
    compare: data,
    isLoading,
    isError: !!error,
    error: error as unknown,
  }
}

/**
 * Extract the backend's ``detail`` from an axios error, falling back to
 * the JS Error message, then a generic string. Centralised so both the
 * page and any future shared component render the same copy.
 */
export function extractCompareErrorMessage(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (err instanceof Error && err.message) return err.message
  return 'Failed to load compare'
}
