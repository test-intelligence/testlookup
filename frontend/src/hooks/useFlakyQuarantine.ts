import useSWR from 'swr'
import {
  flakyQuarantineService,
  type FlakyQuarantineRead,
  type QuarantineStatsResponse,
  type QuarantineStatus,
} from '@/services/flakyQuarantineService'

interface ListOptions {
  projectId?: string | null
  statusFilter?: QuarantineStatus
  liveOnly?: boolean
  limit?: number
}

export function useQuarantineList({
  projectId,
  statusFilter,
  liveOnly = false,
  limit = 200,
}: ListOptions = {}) {
  const key = [
    'quarantine-list',
    projectId ?? 'all',
    statusFilter ?? 'any',
    liveOnly,
    limit,
  ]
  const { data, error, isLoading, mutate } = useSWR<FlakyQuarantineRead[]>(
    key,
    () =>
      flakyQuarantineService.list({
        project_id: projectId ?? undefined,
        status_filter: statusFilter,
        live_only: liveOnly,
        limit,
      }),
    { refreshInterval: 30_000 },
  )
  return {
    requests: data ?? [],
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}

export function useQuarantineStats(projectId?: string | null) {
  const { data, error, isLoading, mutate } = useSWR<QuarantineStatsResponse>(
    ['quarantine-stats', projectId ?? 'all'],
    () => flakyQuarantineService.stats(projectId ?? undefined),
    { refreshInterval: 30_000 },
  )
  return { stats: data, isLoading, isError: !!error, refresh: mutate }
}
