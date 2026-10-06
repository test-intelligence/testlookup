import useSWR from 'swr'
import liveStreamService from '@/services/liveStreamService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { isActivelyRunning } from '@/utils/liveSessionFreshness'

/** How often the sidebar asks; the Live page itself polls every 5-10 s. */
export const NAV_LIVE_POLL_MS = 30_000

/**
 * How many runs are reporting right now, for the sidebar's Runs dot (UX
 * redesign P1). The same `/api/v1/stream/active` the Live page reads, over the
 * last day, in the selected project (all of them in All Projects mode), and the
 * same freshness rule as the Live page's "N active runs": a `running` session
 * that has gone quiet is not counted. No project selected: no request, 0.
 */
export function useLiveRunningCount(): number {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? undefined : activeProjectId ?? null
  const { data } = useSWR(
    projectId === null ? null : ['nav-live-running', projectId ?? 'all'],
    // Counted when the answer arrives (each poll), not while rendering.
    async () => {
      const { sessions } = await liveStreamService.getActiveSessions(projectId ?? undefined, undefined, 1)
      const now = Date.now()
      return sessions.filter((s) => isActivelyRunning(s, now)).length
    },
    { refreshInterval: NAV_LIVE_POLL_MS, revalidateOnFocus: false },
  )
  return data ?? 0
}
