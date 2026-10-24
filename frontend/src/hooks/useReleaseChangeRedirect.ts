import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

import { useActiveProjectId } from './useProjectScopedSWR'
import { useReleaseScope } from './useReleaseScope'

/**
 * W11 GATE-08a: navigate to `targetPath` (replace) when the top bar's
 * EFFECTIVE release changes — another release, or All releases — on the same
 * project. The `useProjectChange.ts` shape: a ref of what was seen last, act
 * only on a CHANGE, never on mount.
 *
 * A project switch also clears the release, and that is the project
 * redirect's to handle (`useProjectChangeRedirect`): a change of project in
 * the same render is only recorded here, so the page navigates once.
 * `enabled` false records the release and does nothing else.
 */
export function useReleaseChangeRedirect(targetPath: string | null, enabled = true) {
  const navigate = useNavigate()
  const projectId = useActiveProjectId()
  const releaseId = useReleaseScope()
  const seen = useRef({ projectId, releaseId })

  useEffect(() => {
    const before = seen.current
    seen.current = { projectId, releaseId }
    if (!enabled || !targetPath) return
    if (before.projectId !== projectId) return
    if (before.releaseId !== releaseId) navigate(targetPath, { replace: true })
  }, [projectId, releaseId, enabled, navigate, targetPath])
}
