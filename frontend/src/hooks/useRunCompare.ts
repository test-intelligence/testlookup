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
export function useRunCompare(leftId: string | null, rightId: string | null) {
  const enabled = Boolean(leftId && rightId && leftId !== rightId)
  const { data, error, isLoading } = useSWR<RunCompareResponse>(
    enabled ? ['run-compare', leftId, rightId] : null,
    () => {
      if (!leftId || !rightId) {
        throw new Error('Left and right run IDs are required')
      }
      return runCompareService.compare(leftId, rightId)
    },
    { revalidateOnFocus: false },
  )
  return { compare: data, isLoading, isError: !!error }
}
