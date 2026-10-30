/**
 * Fixtures for the W23d vitests (AGT-14 HTTP exchanges, AGT-15 metric evidence), in the REAL (parsed) wire
 * shapes of `types/httpExchanges.ts` / `types/specialistEvidence.ts`.
 *
 * - `httpExchange(overrides)`: one stored exchange — by default `GET /api/products/{id}` → 200 in 184 ms, its
 *   `Authorization` header `[REDACTED]`, a JSON response body with `price`.
 * - `exchangeList(overrides)`: three exchanges — the default one; a TRUNCATED body (`body_truncated`, a 16 KB
 *   body built in code, `body_bytes` = its 70 000-byte original); a `<script>` sentinel body
 *   (`SCRIPT_BODY_SENTINEL`) and an `"><img onerror>` header value (`IMG_HEADER_SENTINEL`), which must render as
 *   text. `emptyExchangeList()` (`total: 0`), `runCapExchangeList()` (`run_cap_reached`).
 * - `contractFindings(overrides)`: one `schema_drift` violation of `price` since build 41.
 * - `risingSeries(n)` (40 points: ~0.20 s, then ~0.62 s — ratio 3.1), `logFindings(overrides)` (an `ok`
 *   cluster with that series), `offlineLogFindings()` (an `offline_mode` cluster, no series).
 * - `decisionReportWithFindings(overrides)`: a whole `DecisionIntelligence` carrying both findings.
 *
 * Sentinels are non-hex words (never a uuid prefix). Timestamps are built at CALL time (never a module
 * constant). Big bodies are built in code (`'x'.repeat(n)`), never typed.
 */
import type { DecisionIntelligence } from '@/services/runIntelligenceService'
import {
  BODY_STORED_MAX_BYTES,
  REDACTED,
  TRUNCATION_MARKER,
  type HttpExchange,
  type HttpExchangeList,
  type HttpMessage,
} from '@/types/httpExchanges'
import type { AppMetricsSeries, ContractFindings, LogFindings, MetricPoint } from '@/types/specialistEvidence'

export const SENTINEL_HOST = 'shop.sentinel.test'
export const SENTINEL_ENDPOINT = '/api/products/{id}'
export const SENTINEL_URL = `https://${SENTINEL_HOST}/api/products/41?access_token=${REDACTED}&page=2`
export const SCRIPT_BODY_SENTINEL = '<script>alert("w23d-sentinel")</script>'
export const IMG_HEADER_SENTINEL = '"><img src=x onerror=alert("w23d-sentinel")>'
/** The original size of the truncated body (it exceeded BODY_WIRE_MAX_BYTES on the wire). */
export const TRUNCATED_ORIGINAL_BYTES = 70000

export const DRIFT_SENTENCE = 'field `price` missing from GET /api/products/{id}; drift since build 41'
export const CONTRACT_SUMMARY = 'SCHEMA DRIFT DETECTED: 1 field changed since the previous runs.'
export const ROSE_SENTENCE = 'p95 latency for checkout rose 3.1× in the failure window'
export const OFFLINE_SENTENCE = 'Prometheus lookup disabled: offline mode'
export const LOG_SUMMARY = 'checkout: 3 error spans in the failure window; log rate 4.2× its baseline.'

function message(over: Partial<HttpMessage> = {}): HttpMessage {
  return { headers: {}, body: null, body_truncated: false, body_bytes: 0, ...over }
}

/** A stored body cut at BODY_STORED_MAX_BYTES with the server's marker (built, never typed). */
export function truncatedBody(originalBytes = TRUNCATED_ORIGINAL_BYTES): string {
  const marker = TRUNCATION_MARKER.replace('{omitted}', String(originalBytes - BODY_STORED_MAX_BYTES))
  return `{"items":"${'x'.repeat(BODY_STORED_MAX_BYTES - 12)}${marker}`
}

export function httpExchange(over: Partial<HttpExchange> = {}): HttpExchange {
  const body = '{"id":41,"name":"Desk lamp","price":12.5}'
  return {
    seq: 0,
    method: 'GET',
    endpoint: SENTINEL_ENDPOINT,
    url: SENTINEL_URL,
    host: SENTINEL_HOST,
    status: 200,
    duration_ms: 184,
    started_at: new Date(Date.UTC(2026, 9, 30, 9, 14, 58)).toISOString(),
    error: null,
    request: message({ headers: { Accept: 'application/json', Authorization: REDACTED } }),
    response: message({
      headers: { 'Content-Type': 'application/json', 'Set-Cookie': REDACTED },
      body,
      body_bytes: body.length,
    }),
    response_schema_state: 'parsed',
    source: 'json_batch',
    captured_at: new Date(Date.UTC(2026, 9, 30, 9, 15, 0)).toISOString(),
    redacted: true,
    ...over,
  }
}

export function exchangeList(over: Partial<HttpExchangeList> = {}): HttpExchangeList {
  const cut = truncatedBody()
  const items = [
    httpExchange(),
    httpExchange({
      seq: 1,
      endpoint: '/api/products/{id}/reviews',
      url: `https://${SENTINEL_HOST}/api/products/41/reviews`,
      duration_ms: 412,
      request: message({ headers: { Accept: 'application/json' } }),
      response: message({ headers: { 'Content-Type': 'application/json' }, body: cut, body_truncated: true, body_bytes: TRUNCATED_ORIGINAL_BYTES }),
      response_schema_state: 'too_large',
      redacted: false,
    }),
    httpExchange({
      seq: 2,
      method: 'POST',
      endpoint: '/api/cart',
      url: `https://${SENTINEL_HOST}/api/cart`,
      status: 500,
      duration_ms: 96,
      request: message({ headers: { 'Content-Type': 'application/json', 'X-Note': IMG_HEADER_SENTINEL }, body: '{"sku":"lamp-41","qty":1}', body_bytes: 25 }),
      response: message({ headers: { 'Content-Type': 'text/html' }, body: SCRIPT_BODY_SENTINEL, body_bytes: SCRIPT_BODY_SENTINEL.length }),
      response_schema_state: 'not_json',
      redacted: false,
    }),
  ]
  return { items, total: items.length, dropped: 0, run_cap_reached: false, ...over }
}

export function emptyExchangeList(over: Partial<HttpExchangeList> = {}): HttpExchangeList {
  return { items: [], total: 0, dropped: 0, run_cap_reached: false, ...over }
}

/** The run reached EXCHANGES_PER_RUN_MAX before this test's exchanges were stored. */
export function runCapExchangeList(): HttpExchangeList {
  return emptyExchangeList({ dropped: 3, run_cap_reached: true })
}

// ── the decision report's specialist findings ───────────────────────────────

export function contractFindings(over: Partial<ContractFindings> = {}): ContractFindings {
  return {
    status: 'complete',
    violations: [{
      violation_type: 'schema_drift',
      severity: 'critical',
      field_path: 'price',
      expected: 'present',
      actual: 'absent',
      method: 'GET',
      endpoint: SENTINEL_ENDPOINT,
      baseline_build: '41',
      sentence: DRIFT_SENTENCE,
    }],
    violation_count: 1,
    critical_count: 1,
    drift_count: 1,
    endpoints_checked: [SENTINEL_ENDPOINT, '/api/cart'],
    summary: CONTRACT_SUMMARY,
    suggests_product_bug: true,
    ...over,
  }
}

/**
 * `n` points 30 s apart ending at the failure: ~0.20 s before the window, ~0.62 s inside its last 35 %
 * (the fake Prometheus's shape — ratio 3.1).
 */
export function risingPoints(n = 40): MetricPoint[] {
  const end = Date.UTC(2026, 9, 30, 9, 17, 0) / 1000
  const firstInWindow = Math.round(n * 0.65)
  return Array.from({ length: n }, (_, i): MetricPoint => [end - (n - 1 - i) * 30, i < firstInWindow ? 0.2 : 0.62])
}

export function risingSeries(n = 40, over: Partial<AppMetricsSeries> = {}): AppMetricsSeries {
  return {
    template_id: 'http_p95_latency',
    label: 'p95 latency',
    unit: 'seconds',
    points: risingPoints(n),
    baseline_mean: 0.2,
    window_mean: 0.62,
    window_max: 0.62,
    ratio: 3.1,
    rose: true,
    sentence: ROSE_SENTENCE,
    ...over,
  }
}

export function logFindings(over: Partial<LogFindings> = {}): LogFindings {
  return {
    status: 'complete',
    log_summary: LOG_SUMMARY,
    metrics_summary: ROSE_SENTENCE,
    app_metrics: { outcome: 'ok', sentence: ROSE_SENTENCE, service: 'checkout', series: [risingSeries()] },
    cluster_count: 1,
    ...over,
  }
}

/** The offline deployment's answer: the outcome sentence, no series (no chart). */
export function offlineLogFindings(over: Partial<LogFindings> = {}): LogFindings {
  return logFindings({
    metrics_summary: OFFLINE_SENTENCE,
    app_metrics: { outcome: 'offline_mode', sentence: OFFLINE_SENTENCE, service: 'checkout', series: [] },
    ...over,
  })
}

/** A whole decision report (the run-intelligence answer's `decision_intelligence`) carrying both findings. */
export function decisionReportWithFindings(over: Partial<DecisionIntelligence> = {}): DecisionIntelligence {
  return {
    schema_version: 1,
    status: 'complete',
    generated_at: new Date(Date.UTC(2026, 9, 30, 9, 30, 0)).toISOString(),
    metrics: { total_tests: 12, passed_tests: 10, failed_tests: 2, broken_tests: 0, pass_rate: 83.3 },
    failure_clusters: [{ cluster_id: 'cluster-sentinel-checkout', label: 'Checkout API', size: 2 }],
    deep_findings: {},
    flaky_findings: [],
    test_health_findings: [],
    release_decision: { recommendation: 'NO_GO', risk_score: 72, reasoning: 'An API contract drifted.' },
    quality_review: {
      missing_or_failed_specialists: [],
      contradictions: [],
      gap_report: null,
      refined_report: null,
      requires_human_review: false,
    },
    contract_findings: contractFindings(),
    log_findings: logFindings(),
    source_stages: ['contract_validation', 'log_intelligence', 'release_risk', 'decision_report'],
    evidence_bundle_sha256: 'e'.repeat(64),
    verification: { status: 'passed', checks: [{ name: 'durable_snapshot_binding', status: 'pass' }], repairs: [] },
    ...over,
  }
}
