import { getEffectivePolicy } from '@/services/policyService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { readinessBands, type ReadinessBands } from '@/utils/passRateBands'
import { useActiveProjectId, useProjectScopedSWR } from './useProjectScopedSWR'

/**
 * W11 GATE-04: the pass-rate bands of the selected project's EFFECTIVE policy
 * (project → system default → none), for a page with no summary read of its
 * own (`/live`). ONE `GET /release-gate-policies/effective/{project_id}` per
 * project selection, cached; All Projects (or no project) asks nothing and
 * reads the default bands, labelled as such.
 */
export function useEffectivePassRateBands(): ReadinessBands {
  const projectId = useActiveProjectId()
  const oneProject = projectId !== null && projectId !== ALL_PROJECTS_ID
  const { data } = useProjectScopedSWR(
    'effective-policy',
    (scoped) => (scoped ? getEffectivePolicy(scoped) : Promise.resolve(null)),
    { revalidateOnFocus: false },
    [],
    oneProject,
  )
  return readinessBands(data ?? null)
}
