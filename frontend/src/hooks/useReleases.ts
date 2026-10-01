import useSWR from 'swr'
import { releasesService } from '@/services/releasesService'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

export interface UseReleasesOptions {
  /**
   * Read the list the top bar's `ReleasePicker` already keeps fresh, and never
   * re-ask for it (Wave 2.6, B0 finding 1).
   *
   * The catalogue's release markers and comparisons mount lazily, well after
   * SWR's 2 s dedupe window. A plain `useReleases()` there found the top bar's
   * list cached and revalidated it anyway (`revalidateIfStale` defaults to
   * true), so every flag-on report page asked `GET /releases` twice. With
   * `cached`, the SAME SWR entry is read as it is: no revalidation of cached
   * data, no poll of its own (the picker's polling updates the shared entry,
   * and this reader follows it) — and with nothing cached yet it still fetches
   * once. An option rather than a second hook, so every existing
   * `vi.mock('@/hooks/useReleases')` factory keeps working unchanged.
   */
  cached?: boolean
}

const CACHED_READ = { revalidateIfStale: false, revalidateOnFocus: false, revalidateOnReconnect: false } as const

export function useReleases(status?: string, { cached = false }: UseReleasesOptions = {}) {
  return useProjectScopedSWR(
    'releases-list',
    (projectId) => releasesService.list(projectId, status),
    cached ? CACHED_READ : { refreshInterval: REFRESH_INTERVALS.POLLING },
    // The key is the same whichever read: `cached` changes the options only.
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
