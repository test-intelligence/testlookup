import useSWR from 'swr'
import { investigatorService } from '@/services/investigatorService'
import {
  isInvestigationActive,
  type InvestigationDetail,
  type InvestigationListResponse,
} from '@/types/investigator'

/** Poll cadence for an in-flight investigation (queued/running/synthesizing). */
export const INVESTIGATION_POLL_MS = 2500

/**
 * One investigation, polled live while active.
 *
 * `refreshInterval` is the SWR 2 function form: it re-evaluates against the
 * latest data on every tick, so polling starts at 2.5 s while the status is
 * queued/running/synthesizing and stops (interval 0) the moment a terminal
 * status (completed/cancelled/failed) lands — no effect bookkeeping needed.
 */
export function useInvestigation(investigationId: string | null) {
  return useSWR<InvestigationDetail>(
    investigationId ? `/investigations/${investigationId}` : null,
    () => investigatorService.getInvestigation(investigationId ?? ''),
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) =>
        latest && isInvestigationActive(latest.status) ? INVESTIGATION_POLL_MS : 0,
    },
  )
}

/** Paged investigation summaries for a project (cockpit history list). */
export function useInvestigations(projectId: string | null, limit = 20, offset = 0) {
  return useSWR<InvestigationListResponse>(
    projectId ? `/projects/${projectId}/investigations?limit=${limit}&offset=${offset}` : null,
    () => investigatorService.listInvestigations(projectId ?? '', limit, offset),
    { revalidateOnFocus: false },
  )
}
