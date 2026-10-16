import { useCallback } from 'react'
import useSWR from 'swr'

import { getSourceRoots, previewSourceRoots, putSourceRoots } from '@/services/sourceRootsService'
import type { SourceRoot, SourceRootsPreview, SourceRootsResponse } from '@/types/sourceRoots'

/** The server's cap (`SourceRootsResponse.max_roots`) until the first read answers. */
export const DEFAULT_MAX_SOURCE_ROOTS = 20

/**
 * A project's Java source roots (FAIL-08) for the Ownership page.
 *
 * Keyed on `projectId` like `useOwnershipRules` (null = All Projects: no
 * fetch); no poll and no focus revalidation: only this page edits the list.
 * `save` PUTs the WHOLE list and writes the answer into the cache (no second
 * read); `preview` asks what an edited list would locate and writes nothing.
 * Both reject with the Axios error so the caller can show the server's 422
 * text.
 */
export function useSourceRoots(projectId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<SourceRootsResponse>(
    projectId ? (['source-roots', projectId] as const) : null,
    ([, pid]: readonly [string, string]) => getSourceRoots(pid),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  const save = useCallback(
    async (roots: SourceRoot[]): Promise<SourceRootsResponse> => {
      if (!projectId) throw new Error('No project selected')
      const saved = await putSourceRoots(projectId, roots)
      await mutate(saved, { revalidate: false })
      return saved
    },
    [projectId, mutate],
  )

  const preview = useCallback(
    (roots: SourceRoot[]): Promise<SourceRootsPreview> => {
      if (!projectId) return Promise.reject(new Error('No project selected'))
      return previewSourceRoots(projectId, roots)
    },
    [projectId],
  )

  return {
    roots: data?.roots ?? [],
    maxRoots: data?.max_roots ?? DEFAULT_MAX_SOURCE_ROOTS,
    isLoading,
    isError: !!error,
    refresh: mutate,
    save,
    preview,
  }
}
