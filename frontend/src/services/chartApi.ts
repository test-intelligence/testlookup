/**
 * The request path for chart data (VIZ-107).
 *
 * Every chart read goes through `chartGet`, which differs from a plain
 * `api.get` in three ways:
 *
 *   - `suppressToast: true` — the chart frame renders the failure, so the
 *     global interceptor must not also toast it (six failing charts would be
 *     six toasts). 401 still goes through the shared single-flight refresh.
 *   - the caller's `AbortSignal` is passed on, so a request superseded by a
 *     newer filter change is cancelled rather than raced.
 *   - the response's `X-Request-ID` is returned WITH the payload, so a payload
 *     that later fails contract validation can still name its request.
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

/** GET a chart payload: no global toast, abortable, request id attached. */
export async function chartGet(url: string, { params, signal }: ChartGetOptions = {}): Promise<ChartFetchResult> {
  const response = await api.get<unknown>(url, { params, signal, suppressToast: true })
  return { data: response.data, requestId: requestIdOf(response) }
}
