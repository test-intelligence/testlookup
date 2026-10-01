/**
 * Reading a chart response's headers: the request id and any other single
 * value (`Retry-After`).
 *
 * A leaf module with no imports, kept apart from `chartApi.ts` on purpose:
 * `chartState`'s error classifier (in the frame chunk every chart page loads)
 * needs these two helpers, and importing them from `chartApi` placed the whole
 * request path — `chartGet`, its queue and the axios client wiring — in that
 * shared chunk, so a flag-off page downloaded code only a catalogue section
 * runs (Wave 2.6 R1-3). `chartApi` re-exports them for its callers.
 */

export const REQUEST_ID_HEADER = 'x-request-id'

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
