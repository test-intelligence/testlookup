/**
 * The chart data state model (VIZ-107) — pure, so every transition is
 * table-testable without SWR or a DOM.
 *
 * "Absence is not health": each way a chart can show nothing, or less than
 * everything, is its own state with its own copy, so missing data is never
 * mistaken for good news.
 *
 *   loading         — nothing to show yet (also: a 401 the shared refresh has
 *                     not retried yet, and a request superseded by a newer one)
 *   never-had-data  — the UNFILTERED existence probe says this scope has never
 *                     had a run AND the payload is empty. An empty (filtered)
 *                     payload alone is not evidence — a filter must not poison
 *                     "has this project ever had a run?" — and a non-empty
 *                     payload proves data exists whatever the probe says
 *   filtered-empty  — it has had data, but nothing matches the filters
 *   not-measured    — the server says the value was not measured (`meta.measured
 *                     === false`); shown as "—" with the server's reason, never 0
 *   error           — the request or the payload failed; carries the request id
 *   forbidden       — 403
 *   truncated       — drawn, but the server returned the top N of M
 *   ready           — drawn
 */
import {
  validateAnyChartSeries,
  validateChartSeries,
  validateEnvelopeMeta,
  type AnyChartSeries,
  type EnvelopeMeta,
  type ValidationResult,
} from '@/lib/viz/contracts'
import { isStaleBuildError, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'
// The leaf, not `@/services/chartApi`: that module is the request queue, and
// importing it here placed it in whatever chunk this resolver lands in.
import { headerValue, requestIdOf } from '@/services/chartHeaders'
import { formatNumber } from '@/utils/formatters'
import type { AnyChartResponse, ChartError, ChartResponse, ChartState } from './chartStateCore'

// The states and the checks on them live in a leaf (see chartStateCore.ts) so
// the frame chunk can use them without the resolver below; re-exported here.
export { CHART_STATUSES, hasChartData, readyState, seriesHasPoints } from './chartStateCore'
export type { AnyChartResponse, ChartError, ChartErrorKind, ChartResponse, ChartState, ChartStatus } from './chartStateCore'

// ── Errors the hook throws itself ─────────────────────────────────────────────

/** A request aborted because the key changed. Ignored: never shown as an error. */
export class ChartRequestSuperseded extends Error {
  constructor() {
    super('Superseded by a newer request')
    this.name = 'ChartRequestSuperseded'
  }
}

/** A payload that failed contract validation. The chart must not be drawn from it. */
export class ChartPayloadError extends Error {
  constructor(
    readonly errors: string[],
    readonly requestId: string | null,
  ) {
    super('The chart data failed validation')
    this.name = 'ChartPayloadError'
  }
}

/** A `meta` + `series` check with the series validator of the caller's choosing. */
function checkResponse<S extends AnyChartSeries>(
  input: unknown,
  validateSeries: (series: unknown) => ValidationResult<S>,
): ValidationResult<ChartResponse<S>> {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    return { ok: false, errors: ['invalid_type: a chart response must be an object'] }
  }
  const { meta, series } = input as { meta?: unknown; series?: unknown }
  const errors: string[] = []
  let metaValue: EnvelopeMeta | null = null
  if (meta !== null && meta !== undefined) {
    const checked = validateEnvelopeMeta(meta)
    if (checked.ok) metaValue = checked.value
    else errors.push(...checked.errors.map((e) => `meta: ${e}`))
  }
  const checkedSeries = validateSeries(series)
  if (!checkedSeries.ok) errors.push(...checkedSeries.errors.map((e) => `series: ${e}`))
  if (errors.length || !checkedSeries.ok) return { ok: false, errors }
  return { ok: true, value: { meta: metaValue, series: checkedSeries.value } }
}

/**
 * The contract validator for a `ChartResponse`: `meta` is a C2 envelope (or
 * `null`), `series` is a C3 chart series of the FOUR kinds every pre-Wave-3
 * reader handles. A `points` series fails here (`kind_enum`), exactly as it did
 * before Wave 3, so it can never reach a reader with no branch for it. Errors
 * are prefixed with the field.
 */
export function validateChartResponse(input: unknown): ValidationResult<ChartResponse> {
  return checkResponse(input, validateChartSeries)
}

/**
 * The same, over ALL of C3 (`points` included): what a Wave-3 source, whose
 * reader has a branch for every kind, validates with.
 */
export function validateAnyChartResponse(input: unknown): ValidationResult<AnyChartResponse> {
  return checkResponse(input, validateAnyChartSeries)
}

/** Runs `validate` over `payload`; a failure throws `ChartPayloadError` naming the request. */
export function validateChartPayload<T>(
  payload: unknown,
  requestId: string | null,
  validate: ((input: unknown) => ValidationResult<T>) | undefined,
): T {
  if (!validate) return payload as T
  const checked = validate(payload)
  if (!checked.ok) throw new ChartPayloadError(checked.errors, requestId)
  return checked.value
}

// ── Classifying a thrown error ────────────────────────────────────────────────

interface HttpLike {
  response?: { status?: number; headers?: unknown; data?: unknown }
  code?: string
  /** Axios' request config; `_retry` is set by the 401 refresh interceptor. */
  config?: { _retry?: unknown }
}

/** Longest parameter name we echo back. */
const MAX_PARAM = 64
/** Past this, "try again in N s" is not a wait state worth showing a countdown for. */
const MAX_RETRY_AFTER_SECONDS = 3600

const LOCATION_PREFIXES = new Set(['query', 'body', 'path', 'header', 'cookie'])

function cleanParam(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed && trimmed.length <= MAX_PARAM ? trimmed : null
}

/**
 * The parameter a 422 rejected: `{param}` in the error body (or in its
 * `detail`), else the last segment of a FastAPI validation item's `loc`.
 */
export function rejectedParam(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null
  const record = body as { param?: unknown; detail?: unknown }
  const direct = cleanParam(record.param)
  if (direct) return direct
  const detail = record.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const nested = cleanParam((detail as { param?: unknown }).param)
    if (nested) return nested
  }
  if (Array.isArray(detail)) {
    for (const item of detail) {
      const loc = (item as { loc?: unknown } | null)?.loc
      if (!Array.isArray(loc)) continue
      const segments = loc.filter((part) => typeof part === 'string' && !LOCATION_PREFIXES.has(part))
      const last = cleanParam(segments[segments.length - 1])
      if (last) return last
    }
  }
  return null
}

const DAY = '(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)'
const LONG_DAY = '(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)'
const MONTH = '(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
const TIME = '\\d{2}:\\d{2}:\\d{2}'
/** RFC 7231 §7.1.1.1: IMF-fixdate, then the obsolete RFC 850 and asctime forms a recipient must accept. */
const IMF_FIXDATE = new RegExp(`^${DAY}, \\d{2} ${MONTH} \\d{4} ${TIME} GMT$`)
const RFC850_DATE = new RegExp(`^${LONG_DAY}, \\d{2}-${MONTH}-\\d{2} ${TIME} GMT$`)
const ASCTIME_DATE = new RegExp(`^${DAY} ${MONTH} [ \\d]\\d ${TIME} \\d{4}$`)

/** The instant an RFC 7231 HTTP-date names, or `null` for anything else (V8's `Date.parse` accepts far more). */
function parseHttpDate(raw: string): number | null {
  let at = Number.NaN
  if (IMF_FIXDATE.test(raw)) at = Date.parse(raw)
  else if (RFC850_DATE.test(raw)) {
    // "Monday, 21-Sep-26 00:00:30 GMT": a two-digit year is the nearest one not in the future past 50 years.
    const [, day, month, yy, time] = /, (\d{2})-(\w{3})-(\d{2}) (\S+) GMT$/.exec(raw) ?? []
    const year = 2000 + Number(yy) > new Date().getUTCFullYear() + 50 ? 1900 + Number(yy) : 2000 + Number(yy)
    at = Date.parse(`${day} ${month} ${year} ${time} GMT`)
  } else if (ASCTIME_DATE.test(raw)) at = Date.parse(`${raw} GMT`)
  return Number.isNaN(at) ? null : at
}

/** Seconds to wait from a `Retry-After` header: integer delta-seconds or an RFC 7231 HTTP-date, else `null`. */
export function retryAfterSeconds(headers: unknown, now: number = Date.now()): number | null {
  const raw = headerValue(headers, 'retry-after')
  if (!raw) return null
  if (/^\d+$/.test(raw)) return Math.min(Number(raw), MAX_RETRY_AFTER_SECONDS)
  const at = parseHttpDate(raw)
  if (at === null) return null
  return Math.min(Math.max(0, Math.ceil((at - now) / 1000)), MAX_RETRY_AFTER_SECONDS)
}

export type ClassifiedError =
  /** Not an error the reader should see (superseded, or a 401 the refresh owns). */
  | { type: 'ignore' }
  | { type: 'forbidden'; requestId: string | null }
  | { type: 'error'; error: ChartError }

/** A cancelled request: ours (the key changed) or an abort from elsewhere. Never an error. */
export function isSuperseded(error: unknown): boolean {
  if (error instanceof ChartRequestSuperseded) return true
  const name = (error as { name?: unknown } | null)?.name
  return name === 'CanceledError' || name === 'AbortError'
}

export function classifyChartError(error: unknown): ClassifiedError {
  if (isSuperseded(error)) return { type: 'ignore' }
  if (isStaleBuildError(error)) {
    return { type: 'error', error: { kind: 'stale-build', message: STALE_BUILD_MESSAGE, requestId: null, status: null } }
  }
  if (error instanceof ChartPayloadError) {
    return {
      type: 'error',
      error: {
        kind: 'invalid-payload',
        message: 'The server sent chart data this page could not read.',
        requestId: error.requestId,
        status: null,
      },
    }
  }
  const response = (error as HttpLike | null)?.response
  const status = typeof response?.status === 'number' ? response.status : null
  const requestId = requestIdOf(response)
  if (status === 401) {
    // The shared single-flight refresh (services/api.ts) marks the request it
    // retries with `_retry`. Until then the 401 is the refresh's to handle and
    // the chart keeps loading; a 401 on the RETRIED request is final.
    if (!(error as HttpLike | null)?.config?._retry) return { type: 'ignore' }
    return {
      type: 'error',
      error: {
        kind: 'session-expired',
        message: 'Your session has expired. Sign in again to see this chart.',
        requestId,
        status,
      },
    }
  }
  if (status === 403) return { type: 'forbidden', requestId }
  if (status === 422) {
    const param = rejectedParam(response?.data)
    return {
      type: 'error',
      error: {
        kind: 'invalid-param',
        message: param
          ? `The server did not accept the value of "${param}". Change that filter and try again.`
          : 'The server did not accept one of the filter values. Change the filters and try again.',
        requestId,
        status,
        param,
      },
    }
  }
  if (status === 429) {
    const wait = retryAfterSeconds(response?.headers)
    return {
      type: 'error',
      error: {
        kind: 'rate-limited',
        message:
          wait !== null
            ? `Too many requests right now. This chart will try again in ${formatNumber(wait)} s.`
            : 'Too many requests right now. Try again in a moment.',
        requestId,
        status,
        retryAfterSeconds: wait,
      },
    }
  }
  if (status === 404) {
    return {
      type: 'error',
      error: { kind: 'not-found', message: 'The data for this chart was not found.', requestId, status },
    }
  }
  if (status === null) {
    const code = (error as HttpLike | null)?.code
    return {
      type: 'error',
      error: {
        kind: 'network',
        message:
          code === 'ECONNABORTED' || code === 'ETIMEDOUT'
            ? 'The server took too long to answer.'
            : 'The server could not be reached.',
        requestId,
        status,
      },
    }
  }
  return {
    type: 'error',
    error: { kind: 'server', message: 'The server could not produce this chart.', requestId, status },
  }
}

// ── Payload accessors ─────────────────────────────────────────────────────────

/** True when the series holds no measured value at all (`null` is "no data", not zero). */
export function isChartSeriesEmpty(series: AnyChartSeries): boolean {
  switch (series.kind) {
    case 'series':
      return !series.series.some((s) => s.points.some((p) => p.y !== null))
    case 'matrix':
      return !series.cells.some((cell) => cell.value !== null)
    case 'tree':
      return series.nodes.length === 0
    case 'graph':
      return series.nodes.length === 0
    case 'points':
      // A point always has a finite x and y (a test that cannot be placed is
      // EXCLUDED and counted, never drawn at 0), so no points is no data.
      return series.points.length === 0
  }
}

/** How many categories a truncated chart is SHOWING (the N of "top N of M"). */
export function shownCount(series: AnyChartSeries): number {
  switch (series.kind) {
    case 'series': {
      if (series.x_type !== 'category') return series.series.length
      // A loop, not Math.max(...spread): a spread past ~100k arguments overflows the stack.
      let most = 0
      for (const s of series.series) if (s.points.length > most) most = s.points.length
      return most
    }
    case 'matrix':
      return series.y_labels.length
    case 'tree':
      return series.nodes.length
    case 'graph':
      return series.nodes.length
    case 'points':
      return series.points.length
  }
}

export interface ChartAccessors<T> {
  meta: (value: T) => EnvelopeMeta | null | undefined
  isEmpty: (value: T) => boolean
  shown: (value: T) => number
}

/** Accessors for the canonical `ChartResponse`, of any C3 kind. */
export const CHART_RESPONSE_ACCESSORS: ChartAccessors<AnyChartResponse> = {
  meta: (value) => value.meta,
  isEmpty: (value) => isChartSeriesEmpty(value.series),
  shown: (value) => shownCount(value.series),
}

// ── Resolution ────────────────────────────────────────────────────────────────

export interface ResolveChartStateInput<T> {
  /** SWR's data for the key (with keepPreviousData: possibly the previous key's). */
  data: T | undefined
  error: unknown
  isValidating: boolean
  /**
   * The UNFILTERED existence signal: has this scope ever had data?
   * `null` = the probe has not answered yet.
   */
  everHadData: boolean | null
  accessors: ChartAccessors<T>
  retry?: () => void
}

export function resolveChartState<T>({
  data,
  error,
  isValidating,
  everHadData,
  accessors,
  retry,
}: ResolveChartStateInput<T>): ChartState<T> {
  if (error !== undefined && error !== null) {
    const classified = classifyChartError(error)
    if (classified.type === 'forbidden') return { status: 'forbidden', requestId: classified.requestId }
    if (classified.type === 'error') {
      return isValidating
        ? { status: 'error', error: classified.error, retry, revalidating: true }
        : { status: 'error', error: classified.error, retry }
    }
    // 'ignore' falls through: show what we have, or keep loading.
  }
  if (data === undefined) return { status: 'loading' }

  const meta = accessors.meta(data) ?? null
  const empty = accessors.isEmpty(data)
  // never-had-data needs BOTH: an empty payload and the unfiltered probe saying
  // "never". An empty filtered payload alone is not evidence of "never", and a
  // non-empty payload proves data exists whatever a (stale) probe says.
  if (empty && everHadData === false) return { status: 'never-had-data' }
  if (meta && meta.measured === false) {
    return { status: 'not-measured', reason: meta.reason ?? '', meta }
  }
  if (empty) {
    // Empty, and the probe has not said whether the scope ever had data: we
    // cannot tell "no runs yet" from "no matches", so do not guess.
    if (everHadData === null) return { status: 'loading' }
    return { status: 'filtered-empty', meta }
  }
  const revalidating = isValidating
  if (meta && meta.truncated && meta.truncated_total !== null) {
    return {
      status: 'truncated',
      data,
      meta,
      shown: accessors.shown(data),
      total: meta.truncated_total,
      revalidating,
    }
  }
  return { status: 'ready', data, meta, revalidating }
}
