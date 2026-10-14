import useSWR from 'swr'
import { listShareLinks, type ShareLink } from '@/services/reportExportService'

/**
 * A run's report share links (`GET /reports/runs/{run}/share-links`):
 * metadata only (layout, creator, expiry, views, revoked); the raw token is
 * hashed at rest, so a listed link has no URL (GATE-06).
 *
 * Pass `null` to skip the request: the share dialog asks only while open.
 */
export function useShareLinks(runId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<ShareLink[]>(
    runId ? ['share-links', runId] : null,
    () => {
      if (!runId) throw new Error('runId is required')
      return listShareLinks(runId)
    },
    { revalidateOnFocus: false },
  )

  return {
    links: data ?? [],
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}
