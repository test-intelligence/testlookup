import useSWR from 'swr'

import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import {
  compliancePackService,
  type ProjectCompliancePackList,
} from '@/services/compliancePackService'
import { appMutate } from '@/utils/swrCacheMutate'

/**
 * REL-04: the compliance packs that EXIST, for the /releases rail.
 *
 * The rail used to derive "packs" from release stages and print a literal
 * size and an invented checksum. This reads `GET /api/v1/compliance-packs`:
 * one project's packs, or (`projectId === null`, the All-projects view) every
 * project the caller is a member of, newest first, with `total` counted by the
 * server. The project comes from the caller (not the active-project store):
 * on a release's own page it is that release's project.
 */
export const PROJECT_COMPLIANCE_PACKS_KEY = 'project-compliance-packs'

/** The rail shows at most 5; 10 leaves room to tell which releases already have one. */
export const RAIL_PACK_LIMIT = 10

export function useProjectCompliancePacks(projectId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<ProjectCompliancePackList>(
    [PROJECT_COMPLIANCE_PACKS_KEY, projectId ?? 'all'],
    () => compliancePackService.listForProject({ projectId, limit: RAIL_PACK_LIMIT }),
    { refreshInterval: REFRESH_INTERVALS.POLLING, revalidateOnFocus: false },
  )
  return {
    packs: data?.items ?? [],
    /** `null` until the first answer: an unknown count is not 0. */
    total: data ? data.total : null,
    isLoading,
    /** Absence is not health: a failed read is not "no packs". */
    isError: !!error,
    refresh: mutate,
  }
}

/** Revalidate every scope of the list (a pack generated in a release panel shows in the rail). */
export function refreshProjectCompliancePacks() {
  return appMutate((key: unknown) => Array.isArray(key) && key[0] === PROJECT_COMPLIANCE_PACKS_KEY)
}
