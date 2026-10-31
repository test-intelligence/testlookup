import useSWR, { unstable_serialize, useSWRConfig } from 'swr'

import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { suiteParityService } from '@/services/suiteParityService'
import type { SuiteParityHistory, SuiteParityRunDetail } from '@/types/suiteParity'

/**
 * W25a HYG-04 phase 1: the suite-membership cutover reads (SWR), for the card on /settings/storage.
 *
 * - `useSuiteParityHistory(enabled, { awaitRunAfter })` — the last 14 UTC days of comparisons with the streak. A
 *   `null` key unless enabled, so a non-admin never requests. Re-read every `REFRESH_INTERVALS.ACTIVE` (15 s) ONLY
 *   while the newest run is `running`, or while the card waits for a comparison it has just queued to appear.
 * - `useSuiteParityRun(id)` — one comparison's detail; never without an id (the card asks only after
 *   "Show differences").
 *
 * The `refreshInterval` is a NUMBER recomputed from the cached history on every render, never SWR's function form:
 * SWR re-reads a function only when a timer it armed fires, so a function that once answered 0 never re-arms (the
 * W22b demo bug). New data re-renders the hook, the number changes, and SWR re-arms at once.
 */

/** The history's SWR key (exported so the card can `mutate` it after "Compare now"). */
export const SUITE_PARITY_HISTORY_KEY = ['suite-parity-history'] as const
export const suiteParityRunKey = (id: string) => ['suite-parity-run', id] as const

/** True while the newest comparison (the first row; the list is newest first) is still running. */
export function isParityRunning(history: SuiteParityHistory | null | undefined): boolean {
  return history?.runs?.[0]?.status === 'running'
}

/**
 * `REFRESH_INTERVALS.ACTIVE` while the newest comparison runs, or while `awaitRunAfter` (the newest run's id when a
 * comparison was queued, `''` for none) is still the newest — the queued one has not been claimed yet; else 0.
 */
export function suiteParityRefreshInterval(
  history: SuiteParityHistory | null | undefined,
  awaitRunAfter: string | null = null,
): number {
  const awaiting = awaitRunAfter !== null && (history?.runs?.[0]?.id ?? '') === awaitRunAfter
  return isParityRunning(history) || awaiting ? REFRESH_INTERVALS.ACTIVE : 0
}

export function useSuiteParityHistory(enabled: boolean, { awaitRunAfter = null }: { awaitRunAfter?: string | null } = {}) {
  const key = enabled ? SUITE_PARITY_HISTORY_KEY : null
  const { cache } = useSWRConfig()
  const cached = key ? (cache.get(unstable_serialize(key))?.data as SuiteParityHistory | undefined) : undefined
  return useSWR<SuiteParityHistory>(key, () => suiteParityService.history(), {
    refreshInterval: suiteParityRefreshInterval(cached, awaitRunAfter),
    revalidateOnFocus: false,
  })
}

export function useSuiteParityRun(id: string | null | undefined) {
  return useSWR<SuiteParityRunDetail>(
    id ? suiteParityRunKey(id) : null,
    () => suiteParityService.detail(id as string),
    { revalidateOnFocus: false },
  )
}
