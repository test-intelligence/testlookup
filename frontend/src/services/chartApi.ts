/**
 * The request path for chart data (VIZ-107).
 *
 * Every chart read goes through `chartGet`, which differs from a plain
 * `api.get` in four ways:
 *
 *   - `suppressToast: true` — the chart frame renders the failure, so the
 *     global interceptor must not also toast it (six failing charts would be
 *     six toasts). 401 still goes through the shared single-flight refresh.
 *   - the caller's `AbortSignal` is passed on, so a request superseded by a
 *     newer filter change is cancelled rather than raced.
 *   - the response's `X-Request-ID` is returned WITH the payload, so a payload
 *     that later fails contract validation can still name its request.
 *   - at most `MAX_CONCURRENT_CHART_REQUESTS` are in flight at once (VIZ-209);
 *     the rest queue in the order they were asked for.
 *
 * Kept out of `services/api.ts` so none of it lands in the eager bundle.
 */
import type { AxiosRequestConfig } from 'axios'
import { api } from './api'
import { requestIdOf } from './chartHeaders'

// The header helpers live in a leaf module so the frame chunk can use them
// without this request path (see chartHeaders.ts); re-exported for callers.
export { REQUEST_ID_HEADER, headerValue, requestIdOf } from './chartHeaders'

/** What a chart fetcher resolves to: the raw payload and the request that produced it. */
export interface ChartFetchResult {
  data: unknown
  requestId: string | null
}

export interface ChartGetOptions {
  params?: AxiosRequestConfig['params']
  signal?: AbortSignal
}

/**
 * Chart requests allowed in flight at once, per page (VIZ-209).
 *
 * An API pod has 12 database connections (pool 2 + overflow 1, × 4 workers)
 * and a report page can mount 8 chart frames, so an uncapped page is a
 * self-inflicted connection storm — and the slowest of the 8 decides when any
 * of them paints. Four is the cap the story sets; the rest wait here, in the
 * browser, where waiting costs nothing, rather than in the pool.
 *
 * The queue is module state, so it is shared by every chart on the page (the
 * module is loaded once) and reset by a reload.
 */
export const MAX_CONCURRENT_CHART_REQUESTS = 4

interface Waiter {
  admit: () => void
  drop: (reason: unknown) => void
  signal?: AbortSignal
  detach: () => void
}

let active = 0
const waiting: Waiter[] = []

/**
 * The rejection a queued request gets when it is aborted before it ever
 * started. `CanceledError` is axios' own name for a cancelled request, which
 * `chartState.isSuperseded` already reads as "ignore, do not show an error" —
 * a request that never left the queue is superseded, not failed.
 */
function canceled(): Error {
  const error = new Error('canceled')
  error.name = 'CanceledError'
  return error
}

function pump(): void {
  while (active < MAX_CONCURRENT_CHART_REQUESTS && waiting.length > 0) {
    const next = waiting.shift() as Waiter
    next.detach()
    if (next.signal?.aborted) {
      // Its slot is not taken: a superseded request must never hold one, or
      // a page whose filters changed twice would starve itself.
      next.drop(canceled())
      continue
    }
    active += 1
    next.admit()
  }
}

/** Wait for a slot. Resolves in request order; rejects if aborted while queued. */
function acquire(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(canceled())
  return new Promise<void>((resolve, reject) => {
    const waiter: Waiter = { admit: resolve, drop: reject, signal, detach: () => {} }
    if (signal) {
      const onAbort = () => {
        const index = waiting.indexOf(waiter)
        // Not queued any more means it is already running: axios owns the
        // abort from here, and its rejection releases the slot below.
        if (index === -1) return
        waiting.splice(index, 1)
        waiter.detach()
        reject(canceled())
        pump()
      }
      signal.addEventListener('abort', onAbort)
      waiter.detach = () => signal.removeEventListener('abort', onAbort)
    }
    waiting.push(waiter)
    pump()
  })
}

function release(): void {
  active = Math.max(0, active - 1)
  pump()
}

/**
 * GET a chart payload: no global toast, abortable, request id attached, and
 * never more than `MAX_CONCURRENT_CHART_REQUESTS` at a time.
 */
export async function chartGet(url: string, { params, signal }: ChartGetOptions = {}): Promise<ChartFetchResult> {
  await acquire(signal)
  try {
    const response = await api.get<unknown>(url, { params, signal, suppressToast: true })
    return { data: response.data, requestId: requestIdOf(response) }
  } finally {
    release()
  }
}

/** Test-only: the queue is module state, so a test that leaves one queued would leak it. */
export function __resetChartConcurrency(): void {
  active = 0
  for (const waiter of waiting.splice(0)) {
    waiter.detach()
    waiter.drop(canceled())
  }
}
