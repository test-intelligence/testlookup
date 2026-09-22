/**
 * `useChartData` (VIZ-107): SWR for one chart, normalised into the
 * `ChartState` union that `ChartFrame` renders.
 *
 *   - keepPreviousData: a filter change keeps the old chart on screen (dimmed,
 *     `revalidating`) instead of flashing a skeleton;
 *   - an AbortController per request; when the key changes, requests for any
 *     other key are aborted and their rejection is IGNORED — a superseded
 *     request is never shown as an error;
 *   - the payload is validated at the boundary (inside the fetcher, before SWR
 *     caches it): a payload that fails is an `error` naming the request id,
 *     never a half-drawn chart;
 *   - no automatic error retry: the frame owns the message and a Retry button;
 *     a 429 is retried once its Retry-After has elapsed, at most
 *     `MAX_AUTO_RETRIES` times in a row (each new 429 re-arms the wait), then
 *     the reader's Retry;
 *   - `everHadData` is the caller's UNFILTERED existence probe and the only
 *     thing that can produce `never-had-data`.
 *
 * The fetcher should be `chartGet`-based (services/chartApi.ts) so the request
 * carries `suppressToast` and returns its request id.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import useSWR, { unstable_serialize, type SWRConfiguration } from 'swr'
import type { ValidationResult } from '@/lib/viz/contracts'
import type { ChartFetchResult } from '@/services/chartApi'
import {
  CHART_RESPONSE_ACCESSORS,
  ChartRequestSuperseded,
  classifyChartError,
  isSuperseded,
  resolveChartState,
  validateChartPayload,
  type ChartAccessors,
  type ChartResponse,
  type ChartState,
} from '@/components/charts/chartState'

export type ChartKey = string | readonly unknown[]

export type ChartFetcher<K extends ChartKey> = (key: K, context: { signal: AbortSignal }) => Promise<ChartFetchResult>

export interface UseChartDataOptions<T> {
  /** Contract validator for the raw payload (e.g. a `validateChartResponse`). */
  validate?: (payload: unknown) => ValidationResult<T>
  /**
   * The UNFILTERED existence signal: has this project/scope ever had data?
   * `null` while that probe is loading. Never derive it from this chart's
   * (filtered) payload.
   */
  everHadData: boolean | null
  /** For payloads that are not a `ChartResponse`. */
  accessors?: ChartAccessors<T>
  /** Extra SWR options (refresh interval, …). The chart policy keys below win. */
  swr?: Omit<SWRConfiguration, 'keepPreviousData' | 'shouldRetryOnError' | 'onErrorRetry'>
}

/** Cap on the automatic wait after a 429, so a hostile header cannot park a chart for an hour. */
const MAX_AUTO_RETRY_MS = 5 * 60 * 1000
/** Automatic 429 retries in a row before the chart waits for the reader's Retry. */
export const MAX_AUTO_RETRIES = 3
export const RATE_LIMITED_MANUAL = 'Too many requests right now. Try again in a moment.'

export function useChartData<T = ChartResponse, K extends ChartKey = ChartKey>(
  key: K | null,
  fetcher: ChartFetcher<K>,
  options: UseChartDataOptions<T>,
): ChartState<T> {
  // Latest fetcher / validator, synced before SWR's own layout effect can fetch.
  const fetcherRef = useRef(fetcher)
  const validateRef = useRef(options.validate)
  useLayoutEffect(() => {
    fetcherRef.current = fetcher
    validateRef.current = options.validate
  })

  // In-flight requests, by serialised key.
  const inflight = useRef(new Map<string, Set<AbortController>>())
  const serialized = key === null ? null : unstable_serialize(key)

  const swrFetcher = useCallback(async (requestKey: K): Promise<T> => {
    const id = unstable_serialize(requestKey)
    const controller = new AbortController()
    const set = inflight.current.get(id) ?? new Set<AbortController>()
    set.add(controller)
    inflight.current.set(id, set)
    try {
      const result = await fetcherRef.current(requestKey, { signal: controller.signal })
      if (controller.signal.aborted) throw new ChartRequestSuperseded()
      return validateChartPayload(result.data, result.requestId, validateRef.current)
    } catch (error) {
      if (controller.signal.aborted) throw new ChartRequestSuperseded()
      throw error
    } finally {
      set.delete(controller)
      if (set.size === 0 && inflight.current.get(id) === set) inflight.current.delete(id)
    }
  }, [])

  // A new key supersedes every request for any other key.
  useEffect(() => {
    for (const [id, controllers] of inflight.current) {
      if (id === serialized) continue
      for (const controller of controllers) controller.abort()
      inflight.current.delete(id)
    }
  }, [serialized])

  const { data, error, isValidating, mutate } = useSWR<T, unknown>(key, swrFetcher, {
    ...options.swr,
    keepPreviousData: true,
    shouldRetryOnError: false,
  })

  const revalidate = useCallback(() => {
    void mutate()
  }, [mutate])

  // A superseded rejection that landed on the CURRENT key (another component
  // sharing it changed key, or a StrictMode remount) must not strand the chart
  // on "loading": ask again.
  const superseded = error !== undefined && isSuperseded(error)
  useEffect(() => {
    if (superseded) revalidate()
  }, [superseded, revalidate])

  // 429: try again once the server's Retry-After has elapsed — at most
  // MAX_AUTO_RETRIES times in a row, then the frame's manual Retry.
  const classified = error === undefined ? null : classifyChartError(error)
  const rateLimited = classified?.type === 'error' && classified.error.kind === 'rate-limited'
  const waitSeconds = rateLimited ? (classified.error.retryAfterSeconds ?? null) : null
  // Automatic retries so far, per key. Reset (during render, not in an effect)
  // on a new key or on any outcome that is not a 429.
  const [auto, setAuto] = useState({ key: serialized, count: 0 })
  if (auto.key !== serialized || (!rateLimited && auto.count !== 0)) {
    setAuto({ key: serialized, count: 0 })
  }
  const exhausted = auto.count >= MAX_AUTO_RETRIES
  useEffect(() => {
    if (waitSeconds === null || exhausted) return
    const timer = setTimeout(
      () => {
        setAuto((current) => ({ ...current, count: current.count + 1 }))
        revalidate()
      },
      Math.min(waitSeconds * 1000, MAX_AUTO_RETRY_MS),
    )
    return () => clearTimeout(timer)
    // Keyed on the ERROR INSTANCE: a second 429 with the same Retry-After
    // re-arms the timer (the wait alone would not change).
  }, [error, waitSeconds, exhausted, revalidate])

  // The reader's Retry: a fresh request and a fresh automatic-retry budget.
  const retry = useCallback(() => {
    setAuto((current) => ({ ...current, count: 0 }))
    void mutate()
  }, [mutate])

  const accessors = (options.accessors ?? CHART_RESPONSE_ACCESSORS) as ChartAccessors<T>
  const { everHadData } = options
  return useMemo(() => {
    const state = resolveChartState<T>({
      data: serialized === null ? undefined : data,
      error: serialized === null ? undefined : error,
      isValidating,
      everHadData,
      accessors,
      retry,
    })
    if (state.status === 'error' && state.error.kind === 'rate-limited' && (exhausted || waitSeconds === null)) {
      // No timer is running: never promise one.
      return { ...state, error: { ...state.error, message: RATE_LIMITED_MANUAL } }
    }
    return state
  }, [serialized, data, error, isValidating, everHadData, accessors, retry, exhausted, waitSeconds])
}
