/**
 * A release's gate reads (REL-01): the recorded verdict, the comparison with
 * its baseline, and each phase's live gate.
 *
 * In their own module, not `useReleases.ts`: every existing
 * `vi.mock('@/hooks/useReleases')` factory names that module's two exports
 * and keeps working unchanged.
 *
 * None of these polls. The verdict is a recorded snapshot that changes only
 * when somebody evaluates (the page revalidates these keys after it does), and
 * the baseline and phase reads each run full rollups on the server.
 */
import useSWR from 'swr'
import { releasesService } from '@/services/releasesService'

const ON_DEMAND = { revalidateOnFocus: false } as const

/** `GET /releases/{id}/gate`: the current verdict, or `null` when never evaluated (the 404). */
export function useReleaseGate(releaseId: string | null) {
  return useSWR(
    releaseId ? ['release-gate', releaseId] : null,
    () => releasesService.getGate(releaseId as string),
    ON_DEMAND,
  )
}

/** `GET /releases/{id}/gate/baseline`: the deltas, or the server's reason there are none. */
export function useReleaseBaseline(releaseId: string | null) {
  return useSWR(
    releaseId ? ['release-gate-baseline', releaseId] : null,
    () => releasesService.getGateBaseline(releaseId as string),
    ON_DEMAND,
  )
}

/** `GET /releases/{id}/phases/gate`: each phase's LIVE gate (nothing recorded). */
export function usePhaseGates(releaseId: string | null) {
  return useSWR(
    releaseId ? ['release-phase-gates', releaseId] : null,
    () => releasesService.getPhaseGates(releaseId as string),
    ON_DEMAND,
  )
}
