import useSWR, { type SWRConfiguration } from 'swr'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

export function useActiveProjectId() {
  return useProjectStore((state) => state.activeProjectId)
}

export function useProjectScopedSWR<Data>(
  baseKey: string,
  fetcher: (projectId: string | null) => Promise<Data>,
  options?: SWRConfiguration<Data>,
  deps: unknown[] = [],
) {
  const projectId = useActiveProjectId()

  // null  → no project selected, don't fetch
  // "all" → all-projects mode, fetch without project filter (pass null to fetcher)
  // uuid  → project-scoped fetch
  const fetcherProjectId = projectId === ALL_PROJECTS_ID ? null : projectId

  return useSWR<Data>(
    projectId !== null ? [baseKey, projectId, ...deps] : null,
    () => fetcher(fetcherProjectId),
    options,
  )
}
