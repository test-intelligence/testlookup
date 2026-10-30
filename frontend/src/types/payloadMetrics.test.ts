/**
 * W23d AGT-14 / AGT-15 — the frontend vocabularies and readers (`types/httpExchanges.ts`, `types/prometheus.ts`,
 * `types/specialistEvidence.ts`).
 *
 * Value-for-value equality with the Python twins (`http_exchange_vocab.py`, `prometheus_vocab.py`) is pinned on
 * the backend by `tests/test_payload_metrics_vocab_parity.py`, which reads these files as text (this suite does
 * not read backend files: the frontend image builds from `frontend/` only). Here: the readers never crash and
 * never guess, and the copies agree with their own caps.
 */
import { describe, expect, it } from 'vitest'
import {
  BODY_STORED_MAX_BYTES,
  CAPTURE_SOURCES,
  EXCHANGE_PANEL_SENTENCES,
  EXCHANGES_PER_RUN_MAX,
  EXCHANGES_PER_TEST_MAX,
  HEADER_DENYLIST,
  LIST_PAGE_MAX,
  QUERY_DENYLIST,
  REDACTED,
  SCHEMA_STATES,
  TRUNCATION_MARKER,
  parseExchangeList,
} from './httpExchanges'
import {
  DEFAULT_METRIC_TEMPLATES,
  FINDING_SENTENCES,
  OUTCOMES,
  OUTCOME_SENTENCES,
  POINTS_MAX,
  SERVICE_PLACEHOLDER,
  SERVICE_RE,
  TEMPLATE_ERRORS,
  TEMPLATE_ID_RE,
  TEMPLATE_UNITS,
  TEMPLATES_MAX,
  TEMPLATES_PER_CALL_MAX,
} from './prometheus'
import { parseAppMetrics, parseContractFindings, parseLogFindings } from './specialistEvidence'
import {
  DRIFT_SENTENCE,
  IMG_HEADER_SENTINEL,
  ROSE_SENTENCE,
  SCRIPT_BODY_SENTINEL,
  contractFindings,
  exchangeList,
  logFindings,
  offlineLogFindings,
  risingPoints,
  risingSeries,
} from '@/test/payloadMetricsMocks'

describe('parseExchangeList — an answer without `items` is null, never a crash (W15)', () => {
  it.each([
    ['undefined', undefined],
    ['null', null],
    ['a string (an HTML proxy page)', '<html>gateway</html>'],
    ['an array', [exchangeList().items[0]]],
    ['an object without items', { total: 3, dropped: 0, run_cap_reached: false }],
    ['items that is not an array', { items: { 0: {} }, total: 1 }],
    ['a FastAPI error body', { detail: { code: 'exchanges_unavailable', message: 'HTTP exchanges could not be read right now.' } }],
  ])('%s → null', (_label, raw) => {
    expect(parseExchangeList(raw)).toBeNull()
  })

  it('reads the real shape unchanged, and reading it again yields an equal list (idempotent)', () => {
    const list = exchangeList()
    const once = parseExchangeList(JSON.parse(JSON.stringify(list)))
    expect(once).toEqual(list)
    expect(parseExchangeList(once)).toEqual(once)
  })

  it('keeps `total` as the server counted it (a count, never items.length) and `dropped` / `run_cap_reached`', () => {
    const parsed = parseExchangeList({ items: [exchangeList().items[0]], total: 7, dropped: 2, run_cap_reached: true })
    expect(parsed?.items).toHaveLength(1)
    expect(parsed?.total).toBe(7)
    expect(parsed?.dropped).toBe(2)
    expect(parsed?.run_cap_reached).toBe(true)
  })

  it('an empty answer is a list with total 0 (the panel says "none captured"), not null', () => {
    expect(parseExchangeList({ items: [], total: 0, dropped: 0, run_cap_reached: false })).toEqual({
      items: [], total: 0, dropped: 0, run_cap_reached: false,
    })
  })

  it('skips malformed items and normalises missing fields; non-string header values are dropped', () => {
    const parsed = parseExchangeList({
      items: [null, 'GET /x', 42, { method: 'GET', url: 'https://api.sentinel.test/x', request: { headers: { Accept: 'text/plain', 'X-Count': 3 } } }],
      total: 'four',
      run_cap_reached: 'yes',
    })
    expect(parsed?.items).toHaveLength(1)
    const [item] = parsed?.items ?? []
    expect(item).toMatchObject({ seq: 0, method: 'GET', endpoint: 'https://api.sentinel.test/x', status: null, redacted: false })
    expect(item.request.headers).toEqual({ Accept: 'text/plain' })
    expect(item.response).toEqual({ headers: {}, body: null, body_truncated: false, body_bytes: 0 })
    expect(item.response_schema_state).toBe('empty')
    // An unreadable total falls back to what was read; an unreadable flag is false (absence is not a "yes").
    expect(parsed?.total).toBe(1)
    expect(parsed?.dropped).toBe(0)
    expect(parsed?.run_cap_reached).toBe(false)
  })

  it('keeps attacker-controlled text byte for byte (React renders it as text later)', () => {
    const items = parseExchangeList(exchangeList())?.items ?? []
    const post = items[2]
    expect(post.response.body).toBe(SCRIPT_BODY_SENTINEL)
    expect(post.request.headers['X-Note']).toBe(IMG_HEADER_SENTINEL)
    expect(items[0].request.headers.Authorization).toBe(REDACTED)
  })
})

describe('parseLogFindings / parseAppMetrics — bounded, and nothing guessed', () => {
  it('reads the real shape unchanged (one rose series of 40 points)', () => {
    const findings = logFindings()
    expect(parseLogFindings(JSON.parse(JSON.stringify(findings)))).toEqual(findings)
    expect(findings.app_metrics?.series?.[0].points).toHaveLength(40)
  })

  it('keeps ≤ 4 series × ≤ 120 points', () => {
    const series = Array.from({ length: 6 }, (_, i) => risingSeries(150, { template_id: `tpl_${'abcdef'[i]}` }))
    const parsed = parseLogFindings({ status: 'complete', app_metrics: { outcome: 'ok', sentence: ROSE_SENTENCE, service: 'checkout', series } })
    const kept = parsed?.app_metrics?.series ?? []
    expect(kept).toHaveLength(TEMPLATES_PER_CALL_MAX)
    for (const s of kept) expect(s.points).toHaveLength(POINTS_MAX)
    // The FIRST points are kept (oldest first), in order.
    expect(kept[0].points?.[0]).toEqual(risingPoints(150)[0])
  })

  it('maps an unknown metrics outcome to null (a newer server, a broken block): no chart, no sentence guessed', () => {
    const parsed = parseLogFindings({ status: 'complete', log_summary: 'x', app_metrics: { outcome: 'great', sentence: 'all fine', series: [risingSeries()] } })
    expect(parsed).not.toBeNull()
    expect(parsed?.app_metrics).toBeNull()
    expect(parsed?.log_summary).toBe('x')
    expect(parseAppMetrics({ sentence: 'no outcome' })).toBeNull()
    expect(parseAppMetrics('ok')).toBeNull()
  })

  it('keeps an offline outcome and its sentence, with no series', () => {
    const parsed = parseLogFindings(offlineLogFindings())
    expect(parsed?.app_metrics).toEqual({ outcome: 'offline_mode', sentence: OUTCOME_SENTENCES.offline_mode, service: 'checkout', series: [] })
  })

  it('drops malformed points (not a pair, a non-numeric time) and keeps a null value as a gap', () => {
    const parsed = parseAppMetrics({
      outcome: 'ok',
      series: [{ template_id: 'http_p95_latency', points: [[1, 0.2], [2, null], ['3', 0.4], [4], [5, 'high'], [6, 0.6]] }],
    })
    expect(parsed?.series?.[0].points).toEqual([[1, 0.2], [2, null], [5, null], [6, 0.6]])
  })

  it('a report without log findings (every report before W23d) reads null; one without app_metrics has none', () => {
    expect(parseLogFindings(undefined)).toBeNull()
    expect(parseLogFindings(['complete'])).toBeNull()
    expect(parseLogFindings({ status: 'complete', log_summary: 'spans' })).toEqual({ status: 'complete', log_summary: 'spans' })
  })
})

describe('parseContractFindings', () => {
  it('reads the real shape unchanged; anything but an object is null', () => {
    const findings = contractFindings()
    expect(parseContractFindings(JSON.parse(JSON.stringify(findings)))).toEqual(findings)
    expect(findings.violations?.[0].sentence).toBe(DRIFT_SENTENCE)
    for (const raw of [undefined, null, 'complete', 3, [findings]]) expect(parseContractFindings(raw)).toBeNull()
  })

  it('keeps ≤ 200 violations, string fields only (a number becomes its text, an object is dropped)', () => {
    const many = Array.from({ length: 230 }, (_, i) => ({ field_path: `f${i}`, baseline_build: 41, sentence: { html: '<b>x</b>' } }))
    const parsed = parseContractFindings({ status: 'complete', violations: [null, ...many], endpoints_checked: ['/a', 7, '/b'] })
    expect(parsed?.violations).toHaveLength(200)
    expect(parsed?.violations?.[0]).toEqual({ field_path: 'f0', baseline_build: '41' })
    expect(parsed?.endpoints_checked).toEqual(['/a', '/b'])
  })
})

describe('the vocabularies agree with their own caps', () => {
  it('the panel sentences name the caps the server enforces', () => {
    expect(EXCHANGE_PANEL_SENTENCES.dropped).toContain(`limit ${EXCHANGES_PER_TEST_MAX} per test`)
    expect(EXCHANGE_PANEL_SENTENCES.dropped).toContain(`${EXCHANGES_PER_RUN_MAX / 1000} 000 per run`)
    expect(EXCHANGE_PANEL_SENTENCES.dropped).toContain('{dropped}')
    expect(EXCHANGE_PANEL_SENTENCES.truncated).toContain(`${BODY_STORED_MAX_BYTES / 1024} KB`)
    expect(EXCHANGE_PANEL_SENTENCES.redacted).toContain(REDACTED)
    expect(TRUNCATION_MARKER).toContain('{omitted}')
    expect(LIST_PAGE_MAX).toBe(EXCHANGES_PER_TEST_MAX)
  })

  it('the enum-like lists are distinct lower-case names', () => {
    for (const list of [CAPTURE_SOURCES, SCHEMA_STATES, HEADER_DENYLIST, QUERY_DENYLIST, TEMPLATE_UNITS, OUTCOMES]) {
      expect(new Set(list).size).toBe(list.length)
      for (const name of list) expect(name).toBe(name.toLowerCase())
    }
  })

  it('every default template has a valid id and a known unit; every sentence key is an outcome', () => {
    for (const t of DEFAULT_METRIC_TEMPLATES) {
      expect(t.id).toMatch(TEMPLATE_ID_RE)
      expect(TEMPLATE_UNITS).toContain(t.unit)
    }
    for (const key of Object.keys(OUTCOME_SENTENCES)) expect(OUTCOMES).toContain(key)
    expect(TEMPLATE_ERRORS.placeholder).toContain(SERVICE_PLACEHOLDER)
    expect(TEMPLATE_ERRORS.too_many).toContain(String(TEMPLATES_MAX))
    expect(FINDING_SENTENCES.rose.replace('{label}', 'p95 latency').replace('{service}', 'checkout').replace('{ratio}', '3.1')).toBe(ROSE_SENTENCE)
    expect('checkout').toMatch(SERVICE_RE)
    expect('checkout service').not.toMatch(SERVICE_RE)
  })
})
