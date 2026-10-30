/**
 * W23d AGT-14: the HTTP exchanges of ONE test of ONE run (`getHttpExchanges`).
 *
 * Keyed by the request URL (`httpExchangesKey`) ONLY while `enabled` — the `HTTP exchanges` tab is open — so no
 * page's default load gains a request. The answer goes through `parseExchangeList`: a malformed one comes back
 * as `data: null` WITH an `error`, so the panel says "Could not load the HTTP exchanges." (never "none
 * captured") and never crashes.
 *
 * No polling (no `refreshInterval`), no revalidation on focus and no automatic retry of a failed read (a 404 is
 * answered once): `mutate()` is the panel's explicit "Retry".
 */
import { useMemo } from 'react'
import useSWR from 'swr'
import { getHttpExchanges } from '@/services/httpExchangesService'
import { parseExchangeList, type HttpExchangeList } from '@/types/httpExchanges'

/** The malformed-answer error (the request itself succeeded). */
export const MALFORMED_HTTP_EXCHANGES = 'The HTTP exchanges answer could not be read.'

const OPTS = { revalidateOnFocus: false, shouldRetryOnError: false } as const

/** The SWR key (= the request URL), or `null` (no request) while disabled or without both ids. */
export function httpExchangesKey(runId: string | null | undefined, testId: string | null | undefined, enabled: boolean): string | null {
  if (!enabled || !runId || !testId) return null
  return `/api/v1/runs/${runId}/tests/${testId}/http-exchanges`
}

export interface HttpExchangesState {
  /** `undefined` while disabled / loading / after a failed read; `null` for a malformed answer. */
  data: HttpExchangeList | null | undefined
  /** Set on a failed read (404 / 503 / network) AND on a malformed answer. */
  error: unknown
  isLoading: boolean
  /** Read again (the panel's "Retry"). */
  mutate: () => Promise<unknown>
}

export function useHttpExchanges(
  runId: string | null | undefined,
  testId: string | null | undefined,
  enabled: boolean,
): HttpExchangesState {
  const key = httpExchangesKey(runId, testId, enabled)
  const swr = useSWR<unknown>(key, key && runId && testId ? () => getHttpExchanges(runId, testId) : null, OPTS)
  const raw = swr.data
  const data = useMemo(() => (raw === undefined ? undefined : parseExchangeList(raw)), [raw])
  return {
    data,
    error: swr.error ?? (data === null ? new Error(MALFORMED_HTTP_EXCHANGES) : undefined),
    isLoading: Boolean(swr.isLoading),
    mutate: () => swr.mutate(),
  }
}
