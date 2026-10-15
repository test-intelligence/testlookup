/**
 * One project's defects awaiting a QA lead's approval (W06 DEF-06: Inbox ›
 * Approvals › Defects).
 *
 * The server's list is QA_LEAD-only, so the caller passes `enabled` (its
 * `usePermissions().isQaLead`): for anyone else there is no key, no request
 * and no poll, and so no 403 toast on every Inbox visit. A project is required
 * (the queue belongs to one); in All Projects mode nothing is asked either.
 *
 * `total` is the server's COUNT over the scope, not the page's length; it is
 * `undefined` until the list has answered.
 */
import useSWR from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { defectPromotionService } from '@/services/defectPromotionService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import type { PendingDefect, PendingDefectList } from '@/types/defect-promotion'

/** One page wide enough for any real queue; the badge reads `total` regardless. */
const PAGE_SIZE = 100

export function usePendingDefects(projectId: string | null, enabled: boolean): {
  items: PendingDefect[]
  total: number | undefined
  isLoading: boolean
  error: unknown
  mutate: () => Promise<unknown>
} {
  const key = enabled && projectId && projectId !== ALL_PROJECTS_ID ? ['pending-defects', projectId] : null
  const { data, error, isLoading, mutate } = useSWR<PendingDefectList>(
    key,
    () => defectPromotionService.listPendingDefects(projectId as string, 1, PAGE_SIZE),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
  )
  return {
    items: data?.items ?? [],
    total: data?.total,
    isLoading,
    error,
    mutate: () => mutate(),
  }
}
