/**
 * The EAGER half of superseded-scope aborting (VIZ-303) — what the shared
 * axios instance (`services/api.ts`), `services/http.getData` and the layout's
 * SWR middleware need on every page, the login page included. The tracker
 * itself (in-flight map, settled fingerprint, matching) is `scopeAbort.ts`,
 * which only the scoped data hooks and `store/settledScope` import; see its
 * module comment for the rules.
 *
 * Why the split changes nothing: a request is only ever tracked when the
 * settled scope is published (`setSettledScopeFingerprint`), and publishing
 * requires `scopeAbort.ts` to have loaded — which registers its tracker here
 * as it evaluates. Before that, tracking a request is a no-op there too
 * (`settled === null`), and there is nothing to untrack.
 */
import { useEffect, useRef, useState } from 'react'
import type { InternalAxiosRequestConfig } from 'axios'
import { unstable_serialize, type Middleware } from 'swr'

declare module 'axios' {
  interface AxiosRequestConfig {
    /**
     * Opt this GET into superseded-scope aborting (set by `scopedFetch` via
     * `services/http.getData`; see `services/scopeAbort.ts`). Omitted = never
     * aborted by a scope change.
     */
    scopeTracked?: boolean
  }
}

/** Marker on an error rejected because its scope was superseded. */
const SUPERSEDED = Symbol.for('testlookup.scopeSuperseded')

/** Mark `error` as a superseded-scope rejection (for the tracker). */
export function markScopeSuperseded(error: object): void {
  Object.defineProperty(error, SUPERSEDED, { value: true })
}

/** True for a rejection caused by a superseded scope (never a real failure). */
export function isScopeSuperseded(error: unknown): boolean {
  return error !== null && typeof error === 'object' && (error as Record<symbol, unknown>)[SUPERSEDED] === true
}

/** The tracker `scopeAbort.ts` registers when it loads. */
export interface ScopeRequestTracker {
  track(config: InternalAxiosRequestConfig): InternalAxiosRequestConfig
  untrack(config: InternalAxiosRequestConfig, error?: unknown): boolean
}

let tracker: ScopeRequestTracker | null = null

/** Called once, by `scopeAbort.ts` as it evaluates. */
export function registerScopeRequestTracker(next: ScopeRequestTracker): void {
  tracker = next
}

/** Request interceptor hook: attach a scope-owned signal to a MARKED request that follows the settled scope. */
export function trackScopeRequest(config: InternalAxiosRequestConfig): InternalAxiosRequestConfig {
  if (config.scopeTracked !== true || tracker === null) return config
  return tracker.track(config)
}

/**
 * Response/error interceptor hook: forget the request, and mark an error
 * caused by our own abort. Returns true when the error is a superseded scope.
 */
export function untrackScopeRequest(config: InternalAxiosRequestConfig | undefined, error?: unknown): boolean {
  if (!config || tracker === null) return false
  return tracker.untrack(config, error)
}

let markDepth = 0

/**
 * Run a scoped hook's SWR fetcher so that every GET it issues SYNCHRONOUSLY
 * (the service call itself) is marked for superseded-scope aborting. Nothing
 * outside such a fetcher is ever marked.
 */
export function scopedFetch<T>(run: () => Promise<T>): Promise<T> {
  markDepth += 1
  try {
    return run()
  } finally {
    markDepth -= 1
  }
}

/** For `services/http.getData`: is a `scopedFetch` on the stack right now? */
export function isScopedFetchActive(): boolean {
  return markDepth > 0
}

/**
 * SWR middleware: a hook whose key's last fetch was aborted as superseded
 * shows no error — it asks again (the scope came back to this key). Same
 * policy as `useChartData`'s `ChartRequestSuperseded`, for every hook.
 */
export const scopeSupersededMiddleware: Middleware = (useSWRNext) => (key, fetcher, config) => {
  const swr = useSWRNext(key, fetcher, config)
  const superseded = isScopeSuperseded(swr.error)
  const mutateRef = useRef(swr.mutate)
  useEffect(() => {
    mutateRef.current = swr.mutate
  })
  // Ask again only for a superseded error that landed while this hook was
  // already on the key. One that was cached when the hook ARRIVED at the key
  // (mount, or the scope came back to it) needs nothing: SWR revalidates a
  // key with no data by itself, and a second request would be a duplicate.
  const serialized = unstable_serialize(key)
  const [arrival, setArrival] = useState({ key: serialized, error: swr.error as unknown })
  if (arrival.key !== serialized) setArrival({ key: serialized, error: swr.error })
  const inherited = arrival.key === serialized ? arrival.error : swr.error
  const retry = superseded && swr.error !== inherited
  useEffect(() => {
    if (retry) void mutateRef.current()
  }, [retry])
  if (!superseded) return swr
  // Forward lazily (SWR tracks which fields a component reads through these
  // getters), hiding only the superseded error.
  return Object.defineProperties({} as typeof swr, {
    data: { get: () => swr.data, enumerable: true },
    error: { get: () => undefined, enumerable: true },
    isValidating: { get: () => swr.isValidating, enumerable: true },
    isLoading: { get: () => swr.isLoading || swr.data === undefined, enumerable: true },
    mutate: { value: swr.mutate, enumerable: true },
  })
}
