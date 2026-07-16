import useSWR from 'swr'
import { fixerService } from '@/services/fixerService'
import {
  isFixAttemptActive,
  type FixAttemptDetail,
  type FixAttemptListResponse,
  type FixerConfig,
} from '@/types/fixer'

/** Poll cadence for the attempts list while any attempt is in flight. */
export const FIX_ATTEMPTS_POLL_MS = 5000

/** Per-project Fixer config (Settings → AI Agents card). */
export function useFixerConfig(projectId: string | null) {
  return useSWR<FixerConfig>(
    projectId ? `/projects/${projectId}/fixer/config` : null,
    () => fixerService.getConfig(projectId ?? ''),
    { revalidateOnFocus: false },
  )
}

/**
 * Paged fix attempts for a project.
 *
 * `refreshInterval` is the SWR 2 function form: it re-evaluates against the
 * latest page on every tick, so the list polls at 5 s while ANY attempt on
 * the page is in a non-terminal status (selected/diagnosing/generating/
 * validating) and stops (interval 0) once every attempt is terminal — no
 * effect bookkeeping needed.
 */
export function useFixAttempts(
  projectId: string | null,
  opts: { limit?: number; offset?: number } = {},
) {
  const { limit = 25, offset = 0 } = opts
  return useSWR<FixAttemptListResponse>(
    projectId ? `/projects/${projectId}/fixer/attempts?limit=${limit}&offset=${offset}` : null,
    () => fixerService.listAttempts(projectId ?? '', { limit, offset }),
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) =>
        latest?.items.some((a) => isFixAttemptActive(a.status)) ? FIX_ATTEMPTS_POLL_MS : 0,
    },
  )
}

/** One attempt with its heavyweight fields (patch, runner log digest, ledger id). */
export function useFixAttempt(attemptId: string | null) {
  return useSWR<FixAttemptDetail>(
    attemptId ? `/fixer/attempts/${attemptId}` : null,
    () => fixerService.getAttempt(attemptId ?? ''),
    { revalidateOnFocus: false },
  )
}
