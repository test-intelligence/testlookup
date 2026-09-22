/**
 * Abort requests for a SUPERSEDED report scope (VIZ-303, "in-flight requests
 * aborted").
 *
 * The data hooks do not each own an AbortController (only `useChartData`
 * does), so the boundary is the shared axios instance: `services/api.ts`
 * calls `trackScopeRequest` from its request interceptor and
 * `untrackScopeRequest` when the request settles.
 *
 * Those hooks, `scopedFetch` and the SWR middleware live in the EAGER
 * `scopeAbortCore.ts` (re-exported below); the tracker here is lazy — only the
 * scoped data hooks and `store/settledScope` import it — and plugs itself into
 * the core as it loads.
 *
 * Which requests are tracked — OPT-IN
 * ----------------------------------
 * Only a request that says so. The SWR data fetchers of the scoped hooks
 * (`useRuns`, `useMetrics`, `useMyFailures`, `useSummaryReport`, …) wrap their
 * service call in `scopedFetch`, which marks every GET issued synchronously
 * inside it (`config.scopeTracked`, set by `services/http.getData`). A marked
 * request is then tracked only when, in addition:
 *
 *  - it is a GET, and not a download (`responseType: 'blob'`);
 *  - the caller did not pass its own `signal` (`chartApi` manages its own
 *    cancellation) — a signal THIS module created earlier is not the
 *    caller's: that is a request retried after a 401 refresh, and it stays
 *    tracked (security N4);
 *  - it carries a scope parameter (`release_id`, `suite_name`) and every one
 *    it carries equals the SETTLED scope of that dimension — it follows the
 *    global filter (an existence probe has no scope parameter; a page-local
 *    suite does not match);
 *  - the `viz_multi_filters` flag is on (`setSettledScopeFingerprint(null)`
 *    otherwise).
 *
 * Why opt-in: matching on params alone also caught user-initiated requests
 * that merely carry the same scope — the summary PDF export was cancelled
 * (and toasted) when the picker moved mid-download (E3 review, proof C). A
 * request nobody marked is never aborted.
 *
 * When the settled scope moves (`store/settledScope.ts`), every tracked
 * request whose scope no longer matches is aborted. Turning tracking OFF
 * (`null`: flag off, or a flag flicker) aborts nothing — it only stops
 * tracking. An aborted request's rejection is marked
 * (`isScopeSuperseded`) so the response interceptor raises no toast, and
 * `scopeSupersededMiddleware` hides it from any SWR hook that still reads the
 * key, asking again instead — a superseded request never reaches the UI as an
 * error.
 */
import type { InternalAxiosRequestConfig } from 'axios'
import { scopeKey, type ScopeValue } from '@/lib/scopeParams'
import { markScopeSuperseded, registerScopeRequestTracker } from './scopeAbortCore'

// The eager half (services/scopeAbortCore.ts) — re-exported so every caller
// keeps one import path.
export {
  isScopedFetchActive,
  isScopeSuperseded,
  scopedFetch,
  scopeSupersededMiddleware,
  trackScopeRequest,
  untrackScopeRequest,
} from './scopeAbortCore'

/** The scope parameters, by dimension, as the wire spells them. */
const SCOPE_PARAMS = { release: 'release_id', suite: 'suite_name' } as const
type Dimension = keyof typeof SCOPE_PARAMS

/** A settled scope reduced to one comparable key per dimension (`null` = no filter). */
export type ScopeFingerprint = Record<Dimension, string | null>

interface Tracked {
  controller: AbortController
  fingerprint: Partial<ScopeFingerprint>
}

const inflight = new Map<InternalAxiosRequestConfig, Tracked>()
/** Every signal this module created (with its controller), so the error path
 *  can recognise its own abort after the entry is gone, and a 401 retry — which
 *  carries our signal back through the request interceptor — stays tracked. */
const ownSignals = new WeakMap<AbortSignal, AbortController>()
let settled: ScopeFingerprint | null = null

/** The scope dimensions a request's params carry, keyed like a fingerprint. */
function requestFingerprint(params: unknown): Partial<ScopeFingerprint> {
  const out: Partial<ScopeFingerprint> = {}
  if (params === null || typeof params !== 'object' || params instanceof URLSearchParams) return out
  const record = params as Record<string, unknown>
  for (const [dimension, name] of Object.entries(SCOPE_PARAMS) as Array<[Dimension, string]>) {
    const value = record[name]
    if (value === undefined || value === null) continue
    if (typeof value !== 'string' && !Array.isArray(value)) continue
    const key = scopeKey(value as ScopeValue)
    if (key !== null) out[dimension] = key
  }
  return out
}

function matches(fingerprint: Partial<ScopeFingerprint>, scope: ScopeFingerprint): boolean {
  return (Object.keys(fingerprint) as Dimension[]).every((d) => fingerprint[d] === scope[d])
}

/**
 * Publish the settled scope (or `null` to stop tracking: flag off). Aborts
 * every tracked in-flight request that no longer matches it.
 */
export function setSettledScopeFingerprint(next: ScopeFingerprint | null): void {
  settled = next
  if (next === null) {
    // Stop tracking; abort nothing. `null` is "the flag is off" — including a
    // momentary off during a flag flicker — and says nothing about whether a
    // request in flight is still wanted.
    inflight.clear()
    return
  }
  for (const [config, tracked] of inflight) {
    if (matches(tracked.fingerprint, next)) continue
    inflight.delete(config)
    tracked.controller.abort()
  }
}

/** Attach a scope-owned signal to a MARKED request that follows the settled
 *  scope (the core has already checked `scopeTracked`). */
function track(config: InternalAxiosRequestConfig): InternalAxiosRequestConfig {
  if ((config.method ?? 'get').toLowerCase() !== 'get') return config
  if (config.responseType === 'blob') return config
  const existing = config.signal as AbortSignal | undefined
  const ownController = existing ? ownSignals.get(existing) : undefined
  // A caller's own signal: the caller manages cancellation.
  if (existing && !ownController) return config
  const fingerprint = requestFingerprint(config.params)
  if (settled === null || Object.keys(fingerprint).length === 0) return config
  if (ownController) {
    // Our own signal coming back: a retry after a 401 refresh. Keep it
    // tracked — and if the scope moved on while the token refreshed, it is
    // already superseded.
    if (!matches(fingerprint, settled)) ownController.abort()
    else inflight.set(config, { controller: ownController, fingerprint })
    return config
  }
  if (!matches(fingerprint, settled)) return config
  const controller = new AbortController()
  config.signal = controller.signal
  ownSignals.set(controller.signal, controller)
  inflight.set(config, { controller, fingerprint })
  return config
}

/** Forget the request, and mark an error caused by our own abort. Returns
 *  true when the error is a superseded scope. */
function untrack(config: InternalAxiosRequestConfig, error?: unknown): boolean {
  inflight.delete(config)
  const signal = config.signal as AbortSignal | undefined
  const superseded = signal !== undefined && signal.aborted && ownSignals.has(signal)
  if (superseded && error !== null && typeof error === 'object') {
    markScopeSuperseded(error)
  }
  return superseded
}

/** Test seam: tracked in-flight request count. */
export function trackedScopeRequestCount(): number {
  return inflight.size
}

registerScopeRequestTracker({ track, untrack })
