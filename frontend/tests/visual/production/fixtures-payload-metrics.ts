/**
 * W23d "payload-metrics" hermetic answers (`docs/deferred-scope-2026-10-13/work/W23d-contract.md` §6.2 G-A, the
 * THEN bullet): the worlds of G-D's three specs (`tests/ci-e2e/test-case-http-exchanges`, `run-specialist-evidence`,
 * `settings-prometheus`) and of `prod-payload-metrics.visual.spec.ts`.
 *
 * Each factory COMPOSES an existing world (`TEST_CASE` of `fixtures-pages.ts`, `runAnalysisWorld()` of
 * `fixtures-in-page.ts`, `integrationsWorld()` of `fixtures-integrations-runtime.ts`; never edits them — no default
 * page load gains a read) and lists its own handlers FIRST (first match wins, `production-pages.ts`), returns fresh
 * state per call, and records what it answered. Every answer has the server's REAL shape (§1.2, §3): the worlds are
 * contract doubles, so each refuses what the real route refuses (the role floors, the template 422s, the egress
 * guard's 422, the exchanges read's 503).
 *
 * - `testCaseHttpWorld({ role, list, answer })`: `GET …/tests/{id}/http-exchanges` — a list (a `[REDACTED]`
 *   Authorization, a body cut at 16 KB, a `<script>` body and an `"><img onerror>` header, all to be rendered as
 *   text) / `total: 0` / `run_cap_reached` / a scripted 503 / a 404.
 * - `runSpecialistWorld({ findings })`: the run's decision report carrying `contract_findings` / `log_findings`
 *   (a drift violation; a `rose` series of 40 points; or an `offline_mode` cluster), and the AI pipeline carrying the
 *   `contract_validation` / `log_intelligence` stages.
 * - `integrationsPrometheusWorld({ role, config, putAnswers, probeAnswer })`: Settings › Integrations with the
 *   Prometheus block; records every PUT body and every probe POST.
 *
 * Captured strings are attacker-controlled on the real server; here they are the contract's samples. Big bodies are
 * built in code, never typed. Not a spec file.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { isoAgo, USER } from './fixtures'
import { delegate, runAnalysisWorld, type RecordedWrite } from './fixtures-in-page'
import { recordIn } from './fixtures-in-page-b'
import { integrationsWorld, TARGET_NOT_ALLOWED, targetOf } from './fixtures-integrations-runtime'
import { TEST_CASE, TEST_CASE_API } from './fixtures-pages'
import {
  BODY_STORED_MAX_BYTES,
  REDACTED,
  TRUNCATION_MARKER,
  type HttpExchange,
  type HttpExchangeList,
  type HttpMessage,
} from '../../../src/types/httpExchanges'
import { FINDING_SENTENCES, OUTCOME_SENTENCES, PROBE_SENTENCES, TEMPLATE_ERRORS, TEMPLATES_MAX } from '../../../src/types/prometheus'
import type { AppMetricsSeries, ContractFindings, LogFindings, MetricPoint } from '../../../src/types/specialistEvidence'

export type { RecordedWrite }

// ── AGT-14: the test case's HTTP exchanges ─────────────────────────────────

/** The tab's one read (`GET /api/v1/runs/{run_id}/tests/{test_id}/http-exchanges`). */
export const HTTP_EXCHANGES_API = `${TEST_CASE_API}/http-exchanges`
export const EXCHANGE_HOST = 'shop.example.test'
export const EXCHANGE_ENDPOINT = '/api/products/{id}'
/** The contract's two render-as-text samples (§4.2). */
export const SCRIPT_BODY = '<script>alert(1)</script>'
export const IMG_HEADER_VALUE = '"><img src=x onerror=alert(1)>'
/** The cut body's size before the store cut (it exceeded the 64 KB wire cap: `too_large`). */
export const CUT_BODY_BYTES = 70000
/** `GET …/http-exchanges`'s 503 (§3.1): never an empty list that reads as "none captured". */
export const EXCHANGES_UNAVAILABLE = { code: 'exchanges_unavailable', message: 'HTTP exchanges could not be read right now.' }

const message = (over: Partial<HttpMessage> = {}): HttpMessage => ({ headers: {}, body: null, body_truncated: false, body_bytes: 0, ...over })

/** A JSON list of products, cut at BODY_STORED_MAX_BYTES with the server's marker (so it no longer parses). */
function cutBody(): string {
  const items = Array.from({ length: 400 }, (_, i) => ({ id: i + 1, name: `Desk lamp ${i + 1}`, price: 12.5 + i, stock: i % 7 }))
  const marker = TRUNCATION_MARKER.replace('{omitted}', String(CUT_BODY_BYTES - BODY_STORED_MAX_BYTES))
  return `${JSON.stringify({ items }).slice(0, BODY_STORED_MAX_BYTES)}${marker}`
}

function exchange(seq: number, over: Partial<HttpExchange>): HttpExchange {
  return {
    seq,
    method: 'GET',
    endpoint: EXCHANGE_ENDPOINT,
    url: `https://${EXCHANGE_HOST}/api/products/41`,
    host: EXCHANGE_HOST,
    status: 200,
    duration_ms: 184,
    started_at: isoAgo(0, 2.05),
    error: null,
    request: message(),
    response: message(),
    response_schema_state: 'parsed',
    source: 'json_batch',
    captured_at: isoAgo(0, 2),
    redacted: false,
    ...over,
  }
}

/** Three stored exchanges in seq order: the redacted product read, a cut body, the `<script>` body. */
export function httpExchangeList(): HttpExchangeList {
  const product = '{"id":41,"name":"Desk lamp","price":12.5,"currency":"EUR","tags":["home","light"]}'
  const cut = cutBody()
  const items = [
    exchange(0, {
      url: `https://${EXCHANGE_HOST}/api/products/41?access_token=${REDACTED}&page=2`,
      request: message({ headers: { Accept: 'application/json', Authorization: REDACTED, 'User-Agent': 'python-httpx/0.27' } }),
      response: message({ headers: { 'Content-Type': 'application/json', 'Set-Cookie': REDACTED }, body: product, body_bytes: product.length }),
      redacted: true,
    }),
    exchange(1, {
      endpoint: '/api/products',
      url: `https://${EXCHANGE_HOST}/api/products?limit=400`,
      duration_ms: 412,
      request: message({ headers: { Accept: 'application/json' } }),
      response: message({ headers: { 'Content-Type': 'application/json' }, body: cut, body_truncated: true, body_bytes: CUT_BODY_BYTES }),
      response_schema_state: 'too_large',
    }),
    exchange(2, {
      method: 'POST',
      endpoint: '/api/cart',
      url: `https://${EXCHANGE_HOST}/api/cart`,
      status: 500,
      duration_ms: 96,
      request: message({ headers: { 'Content-Type': 'application/json', 'X-Note': IMG_HEADER_VALUE }, body: '{"sku":"lamp-41","qty":1}', body_bytes: 25 }),
      response: message({ headers: { 'Content-Type': 'text/html' }, body: SCRIPT_BODY, body_bytes: SCRIPT_BODY.length }),
      response_schema_state: 'not_json',
    }),
  ]
  return { items, total: items.length, dropped: 0, run_cap_reached: false }
}

/**
 * What one read answers: `list` (the option's list, else `httpExchangeList()`), `empty` (`total: 0`), `run_cap`
 * (`total: 0`, `run_cap_reached`, 3 dropped), `unavailable` (503 `exchanges_unavailable`), `not_found` (another
 * run's test id: 404 "Test case not found", the same answer as an unknown id).
 */
export type HttpExchangesAnswer = 'list' | 'empty' | 'run_cap' | 'unavailable' | 'not_found'

export interface TestCaseHttpOptions {
  /** The signed-in user. The route has no role floor (any member, VIEWER included), so the answer is the same. */
  role?: typeof USER
  /** The `list` answer (default `httpExchangeList()`). */
  list?: HttpExchangeList
  /** One answer for every read, or one per read in order (the last repeats) — e.g. `['unavailable', 'list']` for Retry. */
  answer?: HttpExchangesAnswer | readonly HttpExchangesAnswer[]
}

/**
 * The test case page (`TEST_CASE`, unchanged) plus the `HTTP exchanges` tab's read. The page asks it only while
 * the tab is open: `reads` (whole targets) proves when.
 */
export function testCaseHttpWorld(options: TestCaseHttpOptions = {}) {
  const reads: string[] = []
  const script = typeof options.answer === 'string' ? [options.answer] : [...(options.answer ?? ['list'])]
  const answerOf = (kind: HttpExchangesAnswer): unknown => {
    switch (kind) {
      case 'empty':
        return { items: [], total: 0, dropped: 0, run_cap_reached: false }
      case 'run_cap':
        return { items: [], total: 0, dropped: 3, run_cap_reached: true }
      case 'unavailable':
        return respond(503, { detail: EXCHANGES_UNAVAILABLE })
      case 'not_found':
        return respond(404, { detail: 'Test case not found' })
      default:
        return options.list ?? httpExchangeList()
    }
  }
  const handlers: ApiHandlers = [
    [HTTP_EXCHANGES_API, (request) => {
      reads.push(targetOf(request))
      return answerOf(script[Math.min(reads.length, script.length) - 1] ?? 'list')
    }],
    ...TEST_CASE,
  ]
  return { handlers, reads, user: options.role ?? USER }
}

// ── AGT-14 / AGT-15: the decision report's specialist evidence ─────────────

/** `CONTRACT_SENTENCES.missing_field` as the contract stage fills it in. */
export const DRIFT_SENTENCE = 'field `price` missing from GET /api/products/{id}; drift since build 41'
/** `contract_agent`'s summary for one critical violation, then the first violation sentence. */
export const CONTRACT_SUMMARY = `CRITICAL CONTRACT VIOLATIONS: 1 required field missing across 1 endpoint. ${DRIFT_SENTENCE}`
export const ROSE_SENTENCE = FINDING_SENTENCES.rose.replace('{label}', 'p95 latency').replace('{service}', 'checkout').replace('{ratio}', '3.1')
export const OFFLINE_SENTENCE = OUTCOME_SENTENCES.offline_mode
export const LOG_SUMMARY = 'checkout: 3 error spans in the failure window; log rate 4.2× its baseline.'

/** `drift`: a drift violation + an `ok` metrics cluster; `offline`: no capture + an `offline_mode` cluster. */
export type SpecialistFindings = 'drift' | 'offline'

/** `n` points 30 s apart ending 2 minutes after the failure: ~0.20 s before the window, `inWindow` after. */
function points(n: number, before: number, inWindow: number): MetricPoint[] {
  const end = Math.floor(new Date(isoAgo(0, 1.5)).getTime() / 1000)
  const firstInWindow = Math.round(n * 0.65)
  return Array.from({ length: n }, (_, i): MetricPoint => [end - (n - 1 - i) * 30, i < firstInWindow ? before : inWindow])
}

function series(over: Partial<AppMetricsSeries> & { points: MetricPoint[] }): AppMetricsSeries {
  return { template_id: 'http_p95_latency', label: 'p95 latency', unit: 'seconds', window_max: null, rose: false, ...over }
}

/** The contract stage's findings, as `decision_report_agent` copies them. */
export function contractFindingsOf(kind: SpecialistFindings): ContractFindings {
  if (kind === 'offline') {
    return {
      status: 'not_enough_evidence', violations: [], violation_count: 0, critical_count: 0, drift_count: 0, endpoints_checked: [],
      summary: 'No HTTP exchanges were captured for these failures; enable capture in the reporter to validate API contracts.',
      suggests_product_bug: false,
    }
  }
  return {
    status: 'complete',
    violations: [{
      violation_type: 'schema_drift', severity: 'critical', field_path: 'price', expected: 'present', actual: 'absent',
      method: 'GET', endpoint: EXCHANGE_ENDPOINT, baseline_build: '41', sentence: DRIFT_SENTENCE,
    }],
    violation_count: 1,
    critical_count: 1,
    drift_count: 1,
    endpoints_checked: [EXCHANGE_ENDPOINT, '/api/products', '/api/cart'],
    summary: CONTRACT_SUMMARY,
    suggests_product_bug: true,
  }
}

/** The log stage's findings: one `rose` series of 40 points, or the offline outcome (no series). */
export function logFindingsOf(kind: SpecialistFindings): LogFindings {
  if (kind === 'offline') {
    return {
      status: 'complete', log_summary: LOG_SUMMARY, metrics_summary: OFFLINE_SENTENCE, cluster_count: 1,
      app_metrics: { outcome: 'offline_mode', sentence: OFFLINE_SENTENCE, service: 'checkout', series: [] },
    }
  }
  const rose = series({ points: points(40, 0.2, 0.62), baseline_mean: 0.2, window_mean: 0.62, window_max: 0.62, ratio: 3.1, rose: true, sentence: ROSE_SENTENCE })
  return {
    status: 'complete', log_summary: LOG_SUMMARY, metrics_summary: ROSE_SENTENCE, cluster_count: 1,
    app_metrics: { outcome: 'ok', sentence: ROSE_SENTENCE, service: 'checkout', series: [rose] },
  }
}

/** The two specialists' stage rows (`AgentStageResult`), completed, as the pipeline's stages / timeline carry them. */
const specialistStages = (template: Record<string, unknown>) => [
  { ...template, stage_name: 'contract_validation', result_data: { status: 'complete', violation_count: 1 } },
  { ...template, stage_name: 'log_intelligence', result_data: { status: 'complete', cluster_count: 1 } },
]

/**
 * The run page with a published decision report (`runAnalysisWorld({ decisionReport: true })`, unchanged) whose
 * `decision_intelligence` also carries both findings, and whose AI pipeline also ran both specialists.
 */
export function runSpecialistWorld({ findings = 'drift' }: { findings?: SpecialistFindings } = {}) {
  const base = runAnalysisWorld({ decisionReport: true })
  const withStages = (stages: Array<Record<string, unknown>>) => [...stages, ...specialistStages(stages[stages.length - 1] ?? {})]
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/runs\/[^/]+\/intelligence$/, (request) => {
      const answer = delegate(base.handlers, request) as { structured_summary: Record<string, unknown> }
      const report = answer.structured_summary.decision_intelligence as Record<string, unknown>
      return {
        ...answer,
        structured_summary: {
          ...answer.structured_summary,
          decision_intelligence: {
            ...report,
            contract_findings: contractFindingsOf(findings),
            log_findings: logFindingsOf(findings),
            source_stages: ['contract_validation', 'log_intelligence', ...(report.source_stages as string[])],
          },
        },
      }
    }],
    [/^\/api\/v1\/agents\/pipelines\/[^/]+\/stages$/, (request) => withStages(delegate(base.handlers, request) as Array<Record<string, unknown>>)],
    [/^\/api\/v1\/agents\/pipelines\/[^/]+\/timeline$/, (request) => {
      const timeline = delegate(base.handlers, request) as { stages: Array<Record<string, unknown>>; summary: Record<string, number> }
      const stages = withStages(timeline.stages)
      return { ...timeline, stages, summary: { ...timeline.summary, total_stages: stages.length, completed_stages: stages.length } }
    }],
    ...base.handlers,
  ]
  return { handlers, writes: base.writes, tests: base.tests }
}

// ── AGT-15: Settings › Integrations › Prometheus ───────────────────────────

/** `prometheus_vocab.DEFAULT_METRIC_TEMPLATES`, with their PromQL: what the read answers while none are saved. */
export const DEFAULT_PROMETHEUS_TEMPLATES = [
  { id: 'http_p95_latency', label: 'p95 latency', unit: 'seconds', promql: 'histogram_quantile(0.95, sum by (le) (rate(http_server_requests_seconds_bucket{service="{service}"}[1m])))' },
  { id: 'http_5xx_rate', label: '5xx error rate', unit: 'per_second', promql: 'sum(rate(http_server_requests_seconds_count{service="{service}",status=~"5.."}[1m]))' },
  { id: 'memory_working_set', label: 'Memory working set', unit: 'bytes', promql: 'sum(container_memory_working_set_bytes{container="{service}"})' },
  { id: 'cpu_usage', label: 'CPU usage', unit: 'ratio', promql: 'sum(rate(container_cpu_usage_seconds_total{container="{service}"}[1m]))' },
]

/** The read's Prometheus block on a deployment where nobody saved one (the homelab's state): off, no URL, the defaults. */
export const PROMETHEUS_UNCONFIGURED = {
  prometheus_enabled: false,
  prometheus_base_url: null as string | null,
  prometheus_token_set: false,
  prometheus_metric_templates: DEFAULT_PROMETHEUS_TEMPLATES,
  prometheus_templates_source: 'default',
}

/** A scripted PUT answer (one per PUT, in order; after the script, the real rules answer). */
export type PrometheusPutAnswer = 'ok' | 'templates_422' | 'duplicate_422' | 'url_422'
/** What `POST /integration-health/probe?provider=prometheus` answers (default: from the saved block). */
export type PrometheusProbeAnswer = 'healthy' | 'skipped' | 'offline' | 'down' | 'auth_error' | 'timeout'

export interface IntegrationsPrometheusOptions {
  /** The signed-in user: the read and the probe need a QA lead or above, the PUT an administrator (403 otherwise). */
  role?: typeof USER
  /** Overrides of the read's Prometheus block (default `PROMETHEUS_UNCONFIGURED`). */
  config?: Partial<typeof PROMETHEUS_UNCONFIGURED>
  putAnswers?: readonly PrometheusPutAnswer[]
  probeAnswer?: PrometheusProbeAnswer
}

const ROLE_ORDER = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']
const atLeast = (user: typeof USER, role: string) => ROLE_ORDER.indexOf(user.role) >= ROLE_ORDER.indexOf(role)
const forbidden = (role: string) => respond(403, { detail: `Requires at least ${role} role` })

/** A FastAPI 422 item (`loc` from the body). */
const fieldError = (loc: Array<string | number>, msg: string, type: string) => ({ loc: ['body', ...loc], msg, type })

/** The hosts the egress guard refuses (`url_safety.egress_block_reason`): loopback, link-local, unspecified. */
function blockedHost(url: string): boolean {
  let host: string
  try {
    host = new URL(url).hostname.replace(/^\[|\]$/g, '')
  } catch {
    return false
  }
  return host === 'localhost' || host === '::1' || host === '0.0.0.0' || /^127\./.test(host) || /^169\.254\./.test(host)
}

const SECRET_SET_FLAG: Record<string, string> = {
  jira_api_token: 'jira_token_set',
  splunk_api_token: 'splunk_token_set',
  ocp_sa_token: 'ocp_token_set',
  slack_webhook_url: 'slack_webhook_set',
  teams_webhook_url: 'teams_webhook_set',
  prometheus_bearer_token: 'prometheus_token_set',
}

/** `IntegrationsConfigUpdate.prometheus_metric_templates`' validators: the per-item placeholder, then unique ids, ≤ 12. */
function templateProblems(list: unknown): ReturnType<typeof fieldError>[] {
  const field = 'prometheus_metric_templates'
  if (!Array.isArray(list)) return []
  if (list.length > TEMPLATES_MAX) return [fieldError([field], TEMPLATE_ERRORS.too_many, 'templates_too_many')]
  const items = list.map((t, i) => (String((t as { promql?: unknown })?.promql ?? '').includes('{service}')
    ? null
    : fieldError([field, i, 'promql'], TEMPLATE_ERRORS.placeholder, 'template_placeholder')))
    .filter((e): e is ReturnType<typeof fieldError> => e !== null)
  if (items.length) return items
  const ids = list.map((t) => (t as { id?: unknown })?.id)
  return new Set(ids).size === ids.length ? [] : [fieldError([field], TEMPLATE_ERRORS.duplicate_id, 'templates_duplicate_id')]
}

/** The probe's one result row, the route's `_probe_answer` shape. */
function probeRow(kind: PrometheusProbeAnswer) {
  const rows: Record<PrometheusProbeAnswer, [string, number, string, boolean | null, boolean | null]> = {
    healthy: ['healthy', 42, PROBE_SENTENCES.healthy, true, true],
    skipped: ['skipped', 0, PROBE_SENTENCES.skipped_off, null, null],
    offline: ['skipped', 0, 'AI_OFFLINE_MODE=true -- outbound prometheus calls are disabled', null, null],
    down: ['down', 0, 'ConnectError', null, null],
    auth_error: ['auth_error', 37, 'HTTP 401', false, null],
    timeout: ['timeout', 10000, 'Timed out', null, null],
  }
  const [status, responseMs, message, authValid, payloadValid] = rows[kind]
  return { provider: 'prometheus', status, response_ms: responseMs, message, auth_valid: authValid, payload_valid: payloadValid, scope_key: '', project_id: null }
}

/**
 * Settings › Integrations (`integrationsWorld()`'s read and LAYOUT, unchanged) with the Prometheus block (§2.2 M-1,
 * §3.2): the read grows the five `prometheus_*` fields (never a token); the PUT answers the way the server does —
 * ADMIN only, the template 422s (`loc` `prometheus_metric_templates[.N.promql]`), the egress guard's ONE 422 for every
 * refused host (nothing applied), else the sent fields applied (a token saved reads `prometheus_token_set`; `[]` =
 * back to the defaults) and the view returned; the probe answers `provider=prometheus` for a QA lead or above.
 * `writes` records every PUT body and every probe POST (`METHOD path`); `probes` every probe's whole target.
 */
export function integrationsPrometheusWorld(options: IntegrationsPrometheusOptions = {}) {
  const user = options.role ?? USER
  const base = integrationsWorld()
  const config = base.config
  Object.assign(config, PROMETHEUS_UNCONFIGURED, options.config ?? {})
  const writes: RecordedWrite[] = []
  const probes: string[] = []
  const script = [...(options.putAnswers ?? [])]
  let puts = 0
  const view = () => ({ ...config, environment_fields: [...(config.environment_fields as string[])] })

  const scripted: Record<Exclude<PrometheusPutAnswer, 'ok'>, () => unknown> = {
    templates_422: () => respond(422, { detail: [fieldError(['prometheus_metric_templates', 0, 'promql'], TEMPLATE_ERRORS.placeholder, 'template_placeholder')] }),
    duplicate_422: () => respond(422, { detail: [fieldError(['prometheus_metric_templates'], TEMPLATE_ERRORS.duplicate_id, 'templates_duplicate_id')] }),
    url_422: () => respond(422, { detail: [fieldError(['prometheus_base_url'], TARGET_NOT_ALLOWED, 'target_not_allowed')] }),
  }

  const put = (request: ApiRequest) => {
    const body = { ...((recordIn(writes, request) as Record<string, unknown> | null) ?? {}) }
    if (!atLeast(user, 'ADMIN')) return forbidden('ADMIN')
    const next = script[puts]
    puts += 1
    if (next && next !== 'ok') return scripted[next]()
    const invalid = templateProblems(body.prometheus_metric_templates)
    if (invalid.length) return respond(422, { detail: invalid })
    // `_INTEGRATION_HOST_FIELDS`' order (routers/app_settings.py).
    const hosts: Array<[string, string]> = [
      ['splunk_base_url', String(body.splunk_base_url ?? '')],
      ['ocp_api_url', String(body.ocp_api_url ?? '')],
      ['jira_domain', body.jira_domain ? `https://${String(body.jira_domain)}` : ''],
      ['prometheus_base_url', String(body.prometheus_base_url ?? '')],
    ]
    const refused = hosts.filter(([, url]) => url && blockedHost(url)).map(([field]) => fieldError([field], TARGET_NOT_ALLOWED, 'target_not_allowed'))
    if (refused.length) return respond(422, { detail: refused })
    delete body.slack_default_channel
    const envFields = new Set(config.environment_fields as string[])
    for (const [key, value] of Object.entries(body)) {
      envFields.delete(key)
      if (key in SECRET_SET_FLAG) config[SECRET_SET_FLAG[key]] = typeof value === 'string' && value !== ''
      else if (key === 'prometheus_metric_templates') {
        const saved = (value as Array<Record<string, unknown>>).map((t) => ({ ...t, unit: t.unit ?? 'count' }))
        config.prometheus_metric_templates = saved.length ? saved : DEFAULT_PROMETHEUS_TEMPLATES
        config.prometheus_templates_source = saved.length ? 'saved' : 'default'
      } else if (key === 'prometheus_base_url') config[key] = typeof value === 'string' && value.trim() ? value.trim() : null
      else config[key] = value
    }
    config.environment_fields = [...envFields].sort()
    return view()
  }

  const probe = (request: ApiRequest) => {
    recordIn(writes, request)
    probes.push(targetOf(request))
    if (!atLeast(user, 'QA_LEAD')) return forbidden('QA_LEAD')
    const provider = request.url.searchParams.get('provider')
    if (provider !== 'prometheus') {
      return respond(501, { detail: `integrationsPrometheusWorld answers provider=prometheus only, not ${String(provider)}` })
    }
    const configured = config.prometheus_enabled === true && Boolean(config.prometheus_base_url)
    return [probeRow(options.probeAnswer ?? (configured ? 'healthy' : 'skipped'))]
  }

  const handlers: ApiHandlers = [
    ['/api/v1/settings/integrations', put, 'PUT'],
    ['/api/v1/settings/integrations', () => (atLeast(user, 'QA_LEAD') ? view() : forbidden('QA_LEAD'))],
    ['/api/v1/integration-health/probe', probe, 'POST'],
    ...base.handlers,
  ]
  return { handlers, writes, probes, config, user }
}
