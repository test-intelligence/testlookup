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

export const REQUEST_ID_HEADER = 'x-request-id'

/** What a chart fetcher resolves to: the raw payload and the request that produced it. */
export interface ChartFetchResult {
  data: unknown
  requestId: string | null
}

/** Longest header value we read at all; anything longer is not a value we act on. */
const MAX_HEADER_VALUE = 128

/**
 * What a request id may look like before we show it to a reader. A server (or
 * a proxy in front of it) controls this text, and bidi overrides / isolates or
 * control characters in it could make what the user reads — and pastes into a
 * support ticket — differ from the real id. Anything else is not an id: `null`.
 */
const REQUEST_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/

function cleanValue(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed && trimmed.length <= MAX_HEADER_VALUE ? trimmed : null
}

function cleanId(value: unknown): string | null {
  const trimmed = cleanValue(value)
  return trimmed && REQUEST_ID_PATTERN.test(trimmed) ? trimmed : null
}

/**
 * A header value from axios' `AxiosHeaders` (has `.get`, case-insensitive) or
 * a plain header object (as tests and some adapters hand back).
 */
export function headerValue(headers: unknown, name: string): string | null {
  if (!headers || typeof headers !== 'object') return null
  const getter = (headers as { get?: unknown }).get
  if (typeof getter === 'function') {
    const value = cleanValue((getter as (n: string) => unknown).call(headers, name))
    if (value) return value
  }
  const wanted = name.toLowerCase()
  for (const [key, value] of Object.entries(headers as Record<string, unknown>)) {
    if (key.toLowerCase() === wanted) return cleanValue(value)
  }
  return null
}

/** The request id of a response: the `X-Request-ID` header, else `request_id` in the body. */
export function requestIdOf(response: { headers?: unknown; data?: unknown } | undefined | null): string | null {
  if (!response) return null
  const fromHeader = cleanId(headerValue(response.headers, REQUEST_ID_HEADER))
  if (fromHeader) return fromHeader
  const body = response.data
  if (body && typeof body === 'object') return cleanId((body as { request_id?: unknown }).request_id)
  return null
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
