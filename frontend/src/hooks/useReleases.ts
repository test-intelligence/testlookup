import useSWR from 'swr'
import { releasesService } from '@/services/releasesService'
import { useProjectScopedSWR } from './useProjectScopedSWR'

export function useReleases(status?: string) {
  return useProjectScopedSWR(
    'releases-list',
    (projectId) => releasesService.list(projectId, status),
    { refreshInterval: 30_000 },
    [status ?? ''],
  )
}

export function useRelease(id: string | null) {
  return useSWR(
    id ? ['release-detail', id] : null,
    () => releasesService.get(id as string),
    { revalidateOnFocus: false },
  )
}
