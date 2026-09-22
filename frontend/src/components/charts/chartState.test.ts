import { describe, expect, it, vi } from 'vitest'
import type { ChartSeries, EnvelopeMeta } from '@/lib/viz/contracts'
import { StaleBuildError } from './engines/lazyChartEngine'
import {
  CHART_RESPONSE_ACCESSORS,
  ChartPayloadError,
  ChartRequestSuperseded,
  classifyChartError,
  hasChartData,
  isChartSeriesEmpty,
  rejectedParam,
  resolveChartState,
  retryAfterSeconds,
  shownCount,
  validateChartPayload,
  validateChartResponse,
  type ChartResponse,
  type ChartState,
} from './chartState'

const META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'payments' }],
    releases: [],
    suites: [],
    window: { from: '2026-09-01', to: '2026-09-07', days: 7, timezone: 'UTC' },
  },
  totals: { matched_runs: 3, total_runs: 5, matched_executions: 30, total_executions: 50 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-08T00:00:00Z',
  as_of: '2026-09-08T00:00:00Z',
}

const SERIES: ChartSeries = {
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: [{ key: 'pass', label: 'Pass rate', points: [{ x: '2026-09-01', y: 90, n: 10 }] }],
}
const EMPTY_SERIES: ChartSeries = { ...SERIES, series: [{ key: 'pass', label: 'Pass rate', points: [{ x: '2026-09-01', y: null, n: 0 }] }] }

const response = (series: ChartSeries, meta: EnvelopeMeta | null = META): ChartResponse => ({ meta, series })

const httpError = (status: number, data: unknown = {}, headers: Record<string, string> = {}) => ({
  isAxiosError: true,
  response: { status, data, headers },
})

function resolve(input: Partial<Parameters<typeof resolveChartState<ChartResponse>>[0]>): ChartState<ChartResponse> {
  return resolveChartState<ChartResponse>({
    data: undefined,
    error: undefined,
    isValidating: false,
    everHadData: true,
    accessors: CHART_RESPONSE_ACCESSORS,
    ...input,
  })
}

describe('classifyChartError', () => {
  it('ignores a superseded request (never an error)', () => {
    expect(classifyChartError(new ChartRequestSuperseded())).toEqual({ type: 'ignore' })
    expect(classifyChartError({ name: 'CanceledError' })).toEqual({ type: 'ignore' })
    expect(classifyChartError({ name: 'AbortError' })).toEqual({ type: 'ignore' })
  })

  it('ignores a 401: the shared refresh owns it, no error frame flashes', () => {
    expect(classifyChartError(httpError(401))).toEqual({ type: 'ignore' })
  })

  it('maps 403 to forbidden with its request id', () => {
    expect(classifyChartError(httpError(403, {}, { 'X-Request-ID': 'r-403' }))).toEqual({ type: 'forbidden', requestId: 'r-403' })
  })

  it('names the rejected parameter of a 422', () => {
    const result = classifyChartError(httpError(422, { code: 'bad', message: 'no', request_id: 'r-422', param: 'release_id' }))
    expect(result).toMatchObject({ type: 'error', error: { kind: 'invalid-param', param: 'release_id', requestId: 'r-422' } })
    if (result.type === 'error') expect(result.error.message).toContain('"release_id"')
  })

  it('falls back to a generic 422 message when no parameter is named', () => {
    const result = classifyChartError(httpError(422, {}))
    expect(result).toMatchObject({ type: 'error', error: { kind: 'invalid-param', param: null } })
  })

  it('reads a 429 Retry-After as a wait state', () => {
    const result = classifyChartError(httpError(429, {}, { 'retry-after': '12' }))
    expect(result).toMatchObject({ type: 'error', error: { kind: 'rate-limited', retryAfterSeconds: 12 } })
    if (result.type === 'error') expect(result.error.message).toContain('12 s')
    const noHeader = classifyChartError(httpError(429))
    expect(noHeader).toMatchObject({ type: 'error', error: { kind: 'rate-limited', retryAfterSeconds: null } })
  })

  it('reads the request id from the header first, then the body', () => {
    expect(classifyChartError(httpError(500, { request_id: 'from-body' }, { 'x-request-id': 'from-header' }))).toMatchObject({
      error: { kind: 'server', requestId: 'from-header', status: 500 },
    })
    expect(classifyChartError(httpError(500, { request_id: 'from-body' }))).toMatchObject({ error: { requestId: 'from-body' } })
    expect(classifyChartError(httpError(502))).toMatchObject({ error: { requestId: null } })
  })

  it('maps 404, a timeout and an unreachable server', () => {
    expect(classifyChartError(httpError(404))).toMatchObject({ error: { kind: 'not-found' } })
    expect(classifyChartError({ code: 'ECONNABORTED' })).toMatchObject({ error: { kind: 'network', message: 'The server took too long to answer.' } })
    expect(classifyChartError(new Error('Network Error'))).toMatchObject({ error: { kind: 'network', message: 'The server could not be reached.' } })
  })

  it('maps a stale lazy chunk to "A new version is available"', () => {
    const result = classifyChartError(new StaleBuildError(new TypeError('Failed to fetch dynamically imported module')))
    expect(result).toMatchObject({ type: 'error', error: { kind: 'stale-build', message: 'A new version is available' } })
  })

  it('maps a contract failure to invalid-payload with the request id', () => {
    expect(classifyChartError(new ChartPayloadError(['kind_enum: nope'], 'r-bad'))).toMatchObject({
      type: 'error',
      error: { kind: 'invalid-payload', requestId: 'r-bad' },
    })
  })
})

describe('rejectedParam', () => {
  it.each([
    [{ param: 'suite' }, 'suite'],
    [{ detail: { param: 'window' } }, 'window'],
    [{ detail: [{ loc: ['query', 'release_id'], msg: 'bad' }] }, 'release_id'],
    [{ detail: [{ loc: ['query'] }, { loc: ['body', 'days'] }] }, 'days'],
    [{ param: '   ' }, null],
    [{ param: 'x'.repeat(65) }, null],
    [null, null],
    ['oops', null],
  ])('%j → %s', (body, expected) => {
    expect(rejectedParam(body)).toBe(expected)
  })
})

describe('retryAfterSeconds', () => {
  it('reads delta-seconds and an HTTP date, and caps a hostile value', () => {
    expect(retryAfterSeconds({ 'retry-after': '5' })).toBe(5)
    const now = Date.parse('2026-09-21T00:00:00Z')
    expect(retryAfterSeconds({ 'Retry-After': 'Mon, 21 Sep 2026 00:00:30 GMT' }, now)).toBe(30)
    expect(retryAfterSeconds({ 'retry-after': 'Mon, 21 Sep 2026 00:00:00 GMT' }, now + 5000)).toBe(0)
    expect(retryAfterSeconds({ 'retry-after': '999999' })).toBe(3600)
    expect(retryAfterSeconds({ 'retry-after': 'soon' })).toBeNull()
    expect(retryAfterSeconds({})).toBeNull()
  })
})

describe('resolveChartState — every transition', () => {
  const retry = vi.fn()

  it('loading: no data yet', () => {
    expect(resolve({})).toEqual({ status: 'loading' })
  })

  it('loading: a 401 or a superseded request with no data does not flash an error', () => {
    expect(resolve({ error: httpError(401) })).toEqual({ status: 'loading' })
    expect(resolve({ error: new ChartRequestSuperseded() })).toEqual({ status: 'loading' })
  })

  it('a superseded request with previous data keeps showing that data', () => {
    const data = response(SERIES)
    expect(resolve({ data, error: new ChartRequestSuperseded(), isValidating: true })).toMatchObject({
      status: 'ready',
      data,
      revalidating: true,
    })
  })

  it('error wins over data (a failed refetch is not shown as the old chart)', () => {
    const state = resolve({ data: response(SERIES), error: httpError(500, {}, { 'x-request-id': 'r1' }), retry })
    expect(state).toMatchObject({ status: 'error', error: { requestId: 'r1' } })
    if (state.status === 'error') expect(state.retry).toBe(retry)
  })

  it('forbidden', () => {
    expect(resolve({ error: httpError(403) })).toEqual({ status: 'forbidden', requestId: null })
  })

  it('never-had-data comes ONLY from the unfiltered probe', () => {
    // The probe says "never": that wins even over a (stale) non-empty payload.
    expect(resolve({ data: response(EMPTY_SERIES), everHadData: false })).toEqual({ status: 'never-had-data' })
    // The probe says "has had data": an empty FILTERED payload is filtered-empty, never never-had-data.
    expect(resolve({ data: response(EMPTY_SERIES), everHadData: true })).toEqual({ status: 'filtered-empty', meta: META })
  })

  it('an empty payload while the probe is still out stays loading (no guess)', () => {
    expect(resolve({ data: response(EMPTY_SERIES), everHadData: null })).toEqual({ status: 'loading' })
    // A non-empty payload needs no probe to be drawn.
    expect(resolve({ data: response(SERIES), everHadData: null })).toMatchObject({ status: 'ready' })
  })

  it('not-measured carries the server reason and wins over empty', () => {
    const meta = { ...META, measured: false, reason: 'No baseline before 2026-09-01' }
    expect(resolve({ data: response(EMPTY_SERIES, meta) })).toEqual({
      status: 'not-measured',
      reason: 'No baseline before 2026-09-01',
      meta,
    })
  })

  it('truncated reports top N of M from meta.truncated_total', () => {
    const meta = { ...META, truncated: true, truncated_total: 40 }
    const categorical: ChartSeries = {
      kind: 'series',
      dimensions: ['suite'],
      x_type: 'category',
      series: [{ key: 'f', label: 'Failures', points: ['a', 'b', 'c'].map((x) => ({ x, y: 1, n: 1 })) }],
    }
    expect(resolve({ data: response(categorical, meta) })).toMatchObject({ status: 'truncated', shown: 3, total: 40, meta })
  })

  it('ready, dimmed while revalidating', () => {
    const data = response(SERIES)
    expect(resolve({ data })).toEqual({ status: 'ready', data, meta: META, revalidating: false })
    expect(resolve({ data, isValidating: true })).toMatchObject({ revalidating: true })
    expect(resolve({ data: response(SERIES, null) })).toMatchObject({ status: 'ready', meta: null })
  })

  it('hasChartData is true only for the drawn states', () => {
    const drawn = new Set(['ready', 'truncated'])
    const states: ChartState[] = [
      { status: 'loading' },
      { status: 'never-had-data' },
      { status: 'filtered-empty', meta: null },
      { status: 'not-measured', reason: 'r', meta: null },
      { status: 'error', error: { kind: 'server', message: 'm', requestId: null, status: 500 } },
      { status: 'forbidden', requestId: null },
      { status: 'truncated', data: 1, meta: META, shown: 1, total: 2, revalidating: false },
      { status: 'ready', data: 1, meta: null, revalidating: false },
    ]
    for (const state of states) expect(hasChartData(state)).toBe(drawn.has(state.status))
  })
})

describe('fix round — review findings', () => {
  it('a non-empty payload proves data exists: never-had-data needs an EMPTY payload and a "never" probe', () => {
    expect(resolve({ data: response(SERIES), everHadData: false })).toMatchObject({ status: 'ready' })
    const meta = { ...META, truncated: true, truncated_total: 9 }
    expect(resolve({ data: response(SERIES, meta), everHadData: false })).toMatchObject({ status: 'truncated' })
    expect(resolve({ data: response(EMPTY_SERIES), everHadData: false })).toEqual({ status: 'never-had-data' })
  })

  it('a 401 is ignored only until the refresh has retried it; after that it is a session-expired error', () => {
    const first = { response: { status: 401, headers: {}, data: {} }, config: {} }
    const retried = { response: { status: 401, headers: { 'x-request-id': 'r-401' }, data: {} }, config: { _retry: true } }
    expect(classifyChartError(first)).toEqual({ type: 'ignore' })
    expect(classifyChartError(retried)).toMatchObject({
      type: 'error',
      error: { kind: 'session-expired', status: 401, requestId: 'r-401' },
    })
    expect(resolve({ error: retried })).toMatchObject({ status: 'error', error: { kind: 'session-expired' } })
  })

  it('Retry-After: only integer seconds or an RFC 7231 HTTP-date', () => {
    const now = Date.parse('2026-09-21T00:00:00Z')
    for (const junk of ['1.5', '1e2', '-5', '+5', '0x10', '2026-09-21T00:00:30Z', '09/21/2026']) {
      expect(retryAfterSeconds({ 'retry-after': junk }, now), junk).toBeNull()
    }
    expect(retryAfterSeconds({ 'retry-after': '0' }, now)).toBe(0)
    // IMF-fixdate, the obsolete RFC 850 form and asctime (RFC 7231 §7.1.1.1).
    expect(retryAfterSeconds({ 'retry-after': 'Mon, 21 Sep 2026 00:00:30 GMT' }, now)).toBe(30)
    expect(retryAfterSeconds({ 'retry-after': 'Monday, 21-Sep-26 00:00:30 GMT' }, now)).toBe(30)
    expect(retryAfterSeconds({ 'retry-after': 'Mon Sep 21 00:00:30 2026' }, now)).toBe(30)
  })

  it('the wait in a 429 message is formatted with the shared number formatter', () => {
    const result = classifyChartError(httpError(429, {}, { 'retry-after': '1200' }))
    if (result.type !== 'error') throw new Error('expected an error')
    expect(result.error.message).toContain('1,200 s')
  })

  it('shownCount survives a series far past the spread-argument limit', () => {
    const points = Array.from({ length: 300_000 }, (_, i) => ({ x: `c${i}`, y: 1, n: 1 }))
    const big: ChartSeries = { kind: 'series', dimensions: ['c'], x_type: 'category', series: [{ key: 'k', label: 'K', points }] }
    expect(shownCount(big)).toBe(300_000)
  })
})

describe('payload helpers', () => {
  it('isChartSeriesEmpty treats null as no data, never zero', () => {
    expect(isChartSeriesEmpty(SERIES)).toBe(false)
    expect(isChartSeriesEmpty(EMPTY_SERIES)).toBe(true)
    expect(isChartSeriesEmpty({ ...SERIES, series: [{ key: 'z', label: 'z', points: [{ x: 'a', y: 0, n: 1 }] }] })).toBe(false)
    const matrix: ChartSeries = { kind: 'matrix', value_type: 'count', x_labels: ['a'], y_labels: ['b'], cells: [{ x: 0, y: 0, value: null, n: 0 }] }
    expect(isChartSeriesEmpty(matrix)).toBe(true)
    expect(isChartSeriesEmpty({ kind: 'tree', nodes: [] })).toBe(true)
    expect(isChartSeriesEmpty({ kind: 'graph', nodes: [], edges: [] })).toBe(true)
  })

  it('shownCount counts the categories drawn', () => {
    expect(shownCount(SERIES)).toBe(1)
    expect(shownCount({ kind: 'matrix', value_type: 'count', x_labels: ['a', 'b'], y_labels: ['1', '2', '3'], cells: [] })).toBe(3)
    expect(shownCount({ kind: 'tree', nodes: [{ id: 'a', parent_id: null, label: 'a', value: 1, measure: null }] })).toBe(1)
    expect(shownCount({ kind: 'graph', nodes: [{ id: 'a', label: 'a', size: 1 }], edges: [] })).toBe(1)
  })

  it('validateChartResponse accepts a good response and names every failing field', () => {
    expect(validateChartResponse({ meta: META, series: SERIES })).toEqual({ ok: true, value: { meta: META, series: SERIES } })
    expect(validateChartResponse({ meta: null, series: SERIES })).toMatchObject({ ok: true })
    const bad = validateChartResponse({ meta: { schema_version: 0 }, series: { kind: 'pie' } })
    expect(bad.ok).toBe(false)
    if (!bad.ok) {
      expect(bad.errors.some((e) => e.startsWith('meta: '))).toBe(true)
      expect(bad.errors.some((e) => e.startsWith('series: '))).toBe(true)
    }
    expect(validateChartResponse([])).toMatchObject({ ok: false })
    expect(validateChartResponse(null)).toMatchObject({ ok: false })
  })

  it('validateChartPayload throws a ChartPayloadError carrying the request id', () => {
    expect(validateChartPayload({ a: 1 }, 'r', undefined)).toEqual({ a: 1 })
    expect(() => validateChartPayload({ series: { kind: 'pie' } }, 'r-9', validateChartResponse)).toThrow(ChartPayloadError)
    try {
      validateChartPayload({ series: { kind: 'pie' } }, 'r-9', validateChartResponse)
    } catch (error) {
      expect((error as ChartPayloadError).requestId).toBe('r-9')
      expect((error as ChartPayloadError).errors.length).toBeGreaterThan(0)
    }
  })
})
