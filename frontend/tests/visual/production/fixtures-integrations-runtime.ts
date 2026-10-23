/**
 * W10 "integrations-runtime" hermetic answers (`docs/deferred-scope-2026-10-13/work/W10-contract.md` §2.1-§2.6, §3,
 * §6.2 G-E): the worlds of the five W10 specs (`tests/ci-e2e/integrations-runtime-settings`, `projects-integration-
 * fields`, `integration-health-projects`, `webhooks-circuit-breaker`, `run-changes-commits`) and of
 * `prod-integrations-runtime.visual.spec.ts`.
 *
 * Each factory COMPOSES the existing worlds (`projectsInPageWorld()`, `webhooksInPageWorld()`, `RUN_PAGE`, `LAYOUT`;
 * never edits them) and lists its own W10 handlers FIRST (first match wins, `production-pages.ts`), returns fresh
 * state per call, and records every write it answered (`writes`: `METHOD path` and the parsed JSON body), so a spec
 * pins a whole body with `toEqual`. Every answer has the server's shape (§1.1): the worlds are contract doubles, so
 * each refuses what the real route refuses (the egress guard's 422, the Splunk-index / namespace 422).
 *
 * Users: `ADMIN`, `QA_LEAD` (the fixture `USER`, a member of the one project Checkout) and `QA_ENGINEER`.
 * Not a spec file.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { isoAgo, LAYOUT, PROJECT_ID, PROJECTS, RUN_ID, USER } from './fixtures'
import { bodyOf, delegate, type RecordedWrite } from './fixtures-in-page'
import { fakeGet, GITLAB_CONFIG, LEGACY_PROJECT, projectsInPageWorld, recordIn, WEBHOOK, webhooksInPageWorld } from './fixtures-in-page-b'
import { ADMIN, QA_ENGINEER } from './fixtures-releases'
import { RUN_PAGE } from './fixtures-run'

export type { RecordedWrite }
export { ADMIN, QA_ENGINEER }
/** The QA lead of the fixtures: a member of Checkout only (`PROJECTS`), not an administrator. */
export const QA_LEAD = USER

/** A request's target as the server saw it: the path and the query, whole (a spec compares the whole value). */
export const targetOf = (request: ApiRequest): string => `${request.url.pathname}${request.url.search}`

/** The sentence the egress guard answers for a loopback / link-local / unspecified target (`TARGET_NOT_ALLOWED`). */
export const TARGET_NOT_ALLOWED = 'Target host is not allowed'

/** `backend/app/core/integration_values.py`, verbatim (§1.1). */
export const SPLUNK_INDEX_SENTENCE = "Splunk index may use letters, digits, '_' and '-' only (1-80 characters, starting with a letter or digit)."
export const OCP_NAMESPACE_SENTENCE =
  "OpenShift namespace must be a DNS label: lowercase letters, digits and '-', at most 63 characters, starting and ending with a letter or digit."
const SPLUNK_INDEX_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/
const DNS_LABEL_RE = /^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$/

/** A 422 body the way FastAPI answers a refused field. */
const fieldError = (field: string, msg: string, type: string) => ({ loc: ['body', field], msg, type })

// ── /settings/integrations (INT-01, INT-02) ────────────────────────────────

/** The Splunk base URL the deployment's environment supplies (`environment_fields` names it). */
export const ENV_SPLUNK_URL = 'https://splunk.env.example:8089'
/** The URL the admin types (an RFC 1918 / public host: allowed). */
export const NEW_SPLUNK_URL = 'https://splunk.corp.example:8089'
/** A loopback target the egress guard refuses at save (§2.1 step 2). */
export const BLOCKED_SPLUNK_URL = 'http://127.0.0.1:6379'

/**
 * `GET /settings/integrations` (§1.1 `IntegrationsConfigRead`): Jira saved here (its token `(set)`), the Splunk base
 * URL and token, and the OpenShift default namespace from the deployment's environment (`environment_fields`), Slack
 * on with a stored webhook, `slack_default_channel` still sent (deprecated, ignored).
 */
export const INTEGRATIONS_CONFIG = {
  jira_enabled: true,
  jira_domain: 'acme.atlassian.net',
  jira_email: 'qa@acme.test',
  jira_token_set: true,
  jira_default_project_key: 'QA',
  splunk_enabled: true,
  splunk_base_url: ENV_SPLUNK_URL,
  splunk_token_set: true,
  ocp_enabled: true,
  ocp_api_url: 'https://api.cluster.example.test:6443',
  ocp_token_set: false,
  ocp_default_namespace: 'qa-testing',
  slack_enabled: true,
  slack_webhook_url: null,
  slack_webhook_set: true,
  slack_default_channel: '#qa-alerts',
  teams_enabled: false,
  teams_webhook_url: null,
  teams_webhook_set: false,
  github_repo: null,
  github_token_set: false,
  environment_fields: ['ocp_default_namespace', 'splunk_api_token', 'splunk_base_url'],
}

const SECRET_SET_FLAG: Record<string, string> = {
  jira_api_token: 'jira_token_set',
  splunk_api_token: 'splunk_token_set',
  ocp_sa_token: 'ocp_token_set',
  slack_webhook_url: 'slack_webhook_set',
  teams_webhook_url: 'teams_webhook_set',
}

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

/**
 * `/settings/integrations`: the read above, and the PUT the way the server answers it (§2.1): `slack_default_channel`
 * dropped, every refused host in ONE 422 (`loc: ['body', field]`, `msg: TARGET_NOT_ALLOWED`) with nothing applied,
 * otherwise the sent fields applied to the effective view (a saved field leaves `environment_fields`; a token saved
 * here reads `*_set`) and the view returned.
 */
export function integrationsWorld() {
  const writes: RecordedWrite[] = []
  const config: Record<string, unknown> = { ...INTEGRATIONS_CONFIG, environment_fields: [...INTEGRATIONS_CONFIG.environment_fields] }
  const handlers: ApiHandlers = [
    ['/api/v1/settings/integrations', (request) => {
      const body = { ...((recordIn(writes, request) as Record<string, unknown> | null) ?? {}) }
      delete body.slack_default_channel
      const hosts: Array<[string, string]> = [
        ['splunk_base_url', String(body.splunk_base_url ?? '')],
        ['ocp_api_url', String(body.ocp_api_url ?? '')],
        ['jira_domain', body.jira_domain ? `https://${String(body.jira_domain)}` : ''],
      ]
      const refused = hosts.filter(([, url]) => url && blockedHost(url)).map(([field]) => fieldError(field, TARGET_NOT_ALLOWED, 'target_not_allowed'))
      if (refused.length) return respond(422, { detail: refused })
      const envFields = new Set(config.environment_fields as string[])
      for (const [key, value] of Object.entries(body)) {
        envFields.delete(key)
        if (key in SECRET_SET_FLAG) config[SECRET_SET_FLAG[key]] = typeof value === 'string' && value !== ''
        else config[key] = value
      }
      config.environment_fields = [...envFields].sort()
      return { ...config }
    }, 'PUT'],
    ['/api/v1/settings/integrations', () => ({ ...config })],
    ...LAYOUT,
  ]
  return { handlers, writes, config }
}

// ── /projects, Edit Project (INT-03, INT-03b) ──────────────────────────────

/** The project Edit Project opens: `LEGACY_PROJECT` with a Jira key, no Splunk index or namespace, and a stored Jenkins pattern (D22: kept, no longer shown). */
export const EDITED_PROJECT = {
  ...LEGACY_PROJECT,
  jira_project_key: 'LEG',
  splunk_index: '',
  ocp_namespace: '',
  jenkins_job_pattern: '**/legacy-*',
}
/** An index the SPL-safe rule refuses (§1.1 `SPLUNK_INDEX_RE`). */
export const BAD_SPLUNK_INDEX = 'main | delete'

/**
 * `/projects` with Edit Project's W10 rules: `projectsInPageWorld()` (its GET / POST / PUT / DELETE, unedited) with
 * `EDITED_PROJECT`'s integration fields, and a PUT that answers the schema's 422 first (`ProjectUpdate`'s two
 * validators: `""` clears; `SPLUNK_INDEX_SENTENCE` / `OCP_NAMESPACE_SENTENCE`, `msg` exact) — a refused PUT is
 * recorded in `refused` and applies nothing; an accepted one is the base world's (recorded in `writes`).
 */
export function projectsIntegrationWorld() {
  const base = projectsInPageWorld()
  const legacy = base.projects.find((p) => p.id === LEGACY_PROJECT.id)
  if (legacy) Object.assign(legacy, EDITED_PROJECT)
  const refused: RecordedWrite[] = []
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/projects\/[^/]+$/, (request) => {
      const body = (bodyOf(request) as Record<string, unknown> | null) ?? {}
      const detail: ReturnType<typeof fieldError>[] = []
      const index = typeof body.splunk_index === 'string' ? body.splunk_index.trim() : null
      const namespace = typeof body.ocp_namespace === 'string' ? body.ocp_namespace.trim() : null
      if (index && !SPLUNK_INDEX_RE.test(index)) detail.push(fieldError('splunk_index', SPLUNK_INDEX_SENTENCE, 'splunk_index'))
      if (namespace && !DNS_LABEL_RE.test(namespace)) detail.push(fieldError('ocp_namespace', OCP_NAMESPACE_SENTENCE, 'ocp_namespace'))
      if (detail.length) {
        refused.push({ line: `${request.method} ${request.path}`, body })
        return respond(422, { detail })
      }
      return delegate(base.handlers, request)
    }, 'PUT'],
    ...base.handlers,
  ]
  return { handlers, writes: base.writes, refused, projects: base.projects }
}

// ── /settings/integration-health (INT-04) ──────────────────────────────────

/** Two projects only an administrator is a member of (the QA lead's world has Checkout alone). */
export const PAYMENT_SERVICE = { ...PROJECTS[0], id: '22222222-2222-4222-8222-2222222222a1', name: 'Payment Service', slug: 'payment-service' }
export const API_GATEWAY = { ...PROJECTS[0], id: '22222222-2222-4222-8222-2222222222a2', name: 'API Gateway', slug: 'api-gateway' }
/** `NOT_PROBED_MESSAGE` of `integration_probe_service` (§1.1). */
export const NOT_PROBED_MESSAGE = 'Not probed yet. Probe now, or wait for the 15-minute check.'
/** The GitHub Enterprise base of Payment Service, as `target_label` prints it. */
export const PAYMENT_TARGET = 'ghe.fake.test/api/v3'
/** The paused subscription of Checkout (`kind: "webhook"`), by name. */
export const PAUSED_WEBHOOK_NAME = 'Pager relay'
export const PAUSED_WEBHOOK_ID = 'wh000000-0000-4000-8000-0000000000c3'
/** What `POST /integration-health/probe?provider=gitlab&project_id=<API Gateway>` finds. */
export const GITLAB_PROBED_MESSAGE = 'Project group/api-gateway reachable'

const globalRow = (provider: string, status: string, responseMs: number | null, message: string | null) => ({
  provider,
  scope_key: '',
  kind: 'global',
  project_id: null,
  project_name: null,
  label: null,
  target: null,
  status,
  probed: true,
  last_checked_at: isoAgo(0, 0.2),
  message,
  response_ms: responseMs,
  consecutive_failures: status === 'healthy' || status === 'skipped' ? 0 : 2,
  last_success_at: status === 'healthy' ? isoAgo(0, 0.2) : isoAgo(1),
  breaker_threshold: null,
})

/** The deployment-wide rows (`scope_key: ''`): what `GET /status` answers without `scope=all`. */
export const GLOBAL_HEALTH_ROWS = [
  globalRow('jira', 'healthy', 210, null),
  globalRow('splunk', 'healthy', 180, null),
  globalRow('github', 'skipped', null, 'No instance token configured'),
  globalRow('ocp', 'down', 900, 'connection refused'),
]

interface ProjectRow {
  provider: 'github' | 'gitlab'
  project: { id: string; name: string }
  target: string
  status: string
  probed: boolean
  message: string
  response_ms: number | null
  consecutive_failures: number
}

const projectRow = (r: ProjectRow) => ({
  provider: r.provider,
  scope_key: r.project.id,
  kind: 'project',
  project_id: r.project.id,
  project_name: r.project.name,
  label: r.project.name,
  target: r.target,
  status: r.status,
  probed: r.probed,
  last_checked_at: r.probed ? isoAgo(0, 0.25) : null,
  message: r.message,
  response_ms: r.response_ms,
  consecutive_failures: r.consecutive_failures,
  last_success_at: r.status === 'healthy' ? isoAgo(0, 0.25) : null,
  breaker_threshold: null,
})

const PAUSED_WEBHOOK_ROW = {
  provider: 'webhook',
  scope_key: PAUSED_WEBHOOK_ID,
  kind: 'webhook',
  project_id: PROJECT_ID,
  project_name: PROJECTS[0].name,
  label: PAUSED_WEBHOOK_NAME,
  target: 'hooks.example.test',
  status: 'paused',
  probed: true,
  last_checked_at: isoAgo(0, 2),
  message: 'HTTP 500: receiver error',
  response_ms: null,
  consecutive_failures: 3,
  last_success_at: null,
  breaker_threshold: 3,
}

export interface IntegrationHealthOptions {
  /** `ADMIN` reads every project (the default); `QA_LEAD` reads the rows of Checkout, the one project it is a member of. */
  viewer?: 'ADMIN' | 'QA_LEAD'
  /** Answer the paused webhook row with `scope=all` (default true; the project-rows baseline leaves it out). */
  pausedWebhook?: boolean
}

/**
 * `/settings/integration-health` (§2.4, §3.1): `GET /status` answers the deployment-wide rows, and with `scope=all`
 * also the project rows of the viewer's projects (an administrator: GitHub · Payment Service healthy on GHE, GitHub ·
 * Checkout auth_error, GitLab · API Gateway never probed; a QA lead: Checkout's only) and the paused webhook of
 * Checkout. `POST /probe` records its whole target (`probes`) and, for a project probe, answers and updates that row;
 * a `project_id` outside the viewer's projects is refused 403 (as `resolve_project_scope`). History and trends read
 * empty. The projects list is the viewer's (an administrator's has all three, so a row's project switch can find it).
 */
export function integrationHealthWorld(options: IntegrationHealthOptions = {}) {
  const viewer = options.viewer ?? 'ADMIN'
  const projects = viewer === 'ADMIN' ? [...PROJECTS, PAYMENT_SERVICE, API_GATEWAY] : [...PROJECTS]
  const visible = new Set(projects.map((p) => p.id))
  const probes: string[] = []
  const rows = [
    projectRow({ provider: 'github', project: PAYMENT_SERVICE, target: PAYMENT_TARGET, status: 'healthy', probed: true, message: 'Repository acme/payments reachable', response_ms: 140, consecutive_failures: 0 }),
    projectRow({ provider: 'github', project: PROJECTS[0], target: 'api.github.com', status: 'auth_error', probed: true, message: 'HTTP 401: the token was refused', response_ms: 95, consecutive_failures: 3 }),
    projectRow({ provider: 'gitlab', project: API_GATEWAY, target: 'gitlab.fake.test', status: 'unknown', probed: false, message: NOT_PROBED_MESSAGE, response_ms: null, consecutive_failures: 0 }),
  ].filter((row) => visible.has(row.project_id))
  const handlers: ApiHandlers = [
    ['/api/v1/integration-health/status', ({ url }) => {
      const scope = url.searchParams.get('scope') ?? 'global'
      if (scope === 'global') return GLOBAL_HEALTH_ROWS
      if (scope !== 'all') return respond(422, { detail: 'scope must be global or all' })
      return [...GLOBAL_HEALTH_ROWS, ...rows.map((r) => ({ ...r })), ...(options.pausedWebhook === false ? [] : [{ ...PAUSED_WEBHOOK_ROW }])]
    }],
    ['/api/v1/integration-health/probe', (request) => {
      probes.push(targetOf(request))
      const provider = request.url.searchParams.get('provider')
      const projectId = request.url.searchParams.get('project_id')
      if (projectId) {
        if (!visible.has(projectId)) return respond(403, { detail: 'Access denied to this project' })
        const row = rows.find((r) => r.project_id === projectId && r.provider === provider)
        if (!row) return respond(404, { detail: `This project has no enabled ${provider === 'gitlab' ? 'GitLab' : 'GitHub'} integration.` })
        Object.assign(row, { status: 'healthy', probed: true, message: provider === 'gitlab' ? GITLAB_PROBED_MESSAGE : 'Repository reachable', response_ms: 120, consecutive_failures: 0, last_checked_at: isoAgo(0), last_success_at: isoAgo(0) })
        return [{ provider: row.provider, status: row.status, response_ms: 120, message: row.message, auth_valid: true, payload_valid: true, scope_key: row.scope_key, project_id: row.project_id }]
      }
      return GLOBAL_HEALTH_ROWS.map((r) => ({ provider: r.provider, status: r.status, response_ms: r.response_ms ?? 0, message: r.message ?? '', auth_valid: null, payload_valid: null, scope_key: '', project_id: null }))
    }, 'POST'],
    [/^\/api\/v1\/integration-health\/history\/[^/]+$/, () => []],
    ['/api/v1/integration-health/trends', () => []],
    // A row's "Open GitHub / GitLab settings" lands on that project's settings page.
    [/^\/api\/v1\/projects\/[^/]+\/github-integration$/, ({ path }) => githubSettingsOf(path.split('/')[4])],
    [/^\/api\/v1\/projects\/[^/]+\/integrations\/gitlab$/, () => ({ ...GITLAB_CONFIG, base_url: 'https://gitlab.fake.test', project_path: 'group/api-gateway' })],
    ['/api/v1/projects', () => projects],
    ...LAYOUT,
  ]
  return { handlers, probes, rows, projects }
}

/** `GET /projects/{id}/github-integration` of the health world's projects (Payment Service on GitHub Enterprise). */
function githubSettingsOf(projectId: string) {
  const enterprise = projectId === PAYMENT_SERVICE.id
  return {
    id: `gh000000-0000-4000-8000-${projectId.slice(-12)}`,
    project_id: projectId,
    enabled: true,
    repo_owner: 'acme',
    repo_name: enterprise ? 'payments' : 'checkout',
    api_base_url: enterprise ? `https://${PAYMENT_TARGET}` : 'https://api.github.com',
    has_pat: true,
    pr_comment_mode: 'failures_only',
    last_posted_at: null,
    last_error: enterprise ? null : 'HTTP 401: the token was refused',
    last_error_at: enterprise ? null : isoAgo(0, 0.25),
    created_at: isoAgo(10),
    updated_at: isoAgo(2),
  }
}

// ── /settings/webhooks (INT-07) ────────────────────────────────────────────

/** The subscription the breaker paused: Max retries 0, Pause after 3, three 500s in a row. */
export const PAUSED_WEBHOOK = {
  ...WEBHOOK,
  id: PAUSED_WEBHOOK_ID,
  name: PAUSED_WEBHOOK_NAME,
  target_url: 'https://hooks.example.test/pager',
  enabled: false,
  max_retries: 0,
  last_delivered_at: isoAgo(1),
  last_failure_at: isoAgo(0, 2),
  last_error: 'HTTP 500: receiver error',
  failure_count: 3,
  total_delivered: 12,
  consecutive_failures: 3,
  breaker_threshold: 3,
  disabled_reason: 'circuit_open' as string | null,
  disabled_at: isoAgo(0, 2) as string | null,
}
/** `WEBHOOK` (Release bot), enabled, two failed deliveries in a row of its 10. */
export const STREAK_WEBHOOK = {
  ...WEBHOOK,
  failure_count: 2,
  last_failure_at: isoAgo(0, 1),
  last_error: 'HTTP 503: upstream unavailable',
  consecutive_failures: 2,
  breaker_threshold: 10,
  disabled_reason: null as string | null,
  disabled_at: null as string | null,
}
/** The id the next created subscription gets. */
export const NEW_WEBHOOK_ID = 'wh000000-0000-4000-8000-0000000000f1'

/**
 * `/settings/webhooks` with the circuit breaker (§2.5): `webhooksInPageWorld()` (its event catalog, its list and its
 * DELETE, unedited) over `PAUSED_WEBHOOK` and `STREAK_WEBHOOK`, with the PATCH (the full write; `enabled: true` on a
 * disabled row resets the count and clears `disabled_*`, as `update_subscription` does) and the create (201, the
 * threshold or the default 10) prepended. Both are recorded in `writes`.
 */
export function webhooksBreakerWorld() {
  const base = webhooksInPageWorld()
  const rows = base.rows as Array<Record<string, unknown>>
  rows.splice(0, rows.length, { ...PAUSED_WEBHOOK }, { ...STREAK_WEBHOOK })
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/webhooks\/[^/]+$/, (request) => {
      const body = (recordIn(base.writes, request) as Record<string, unknown> | null) ?? {}
      const row = rows.find((r) => request.path.endsWith(`/${String(r.id)}`))
      if (!row) return respond(404, { detail: 'Webhook subscription not found' })
      const fields = { ...body }
      delete fields.secret
      if (body.enabled === true && row.enabled === false) Object.assign(row, { consecutive_failures: 0, disabled_reason: null, disabled_at: null })
      if (body.enabled === false && row.enabled === true) Object.assign(row, { disabled_reason: 'manual', disabled_at: isoAgo(0) })
      Object.assign(row, fields, { breaker_threshold: body.breaker_threshold ?? row.breaker_threshold, updated_at: isoAgo(0) })
      return { ...row }
    }, 'PATCH'],
    ['/api/v1/webhooks', (request) => {
      const body = (recordIn(base.writes, request) as Record<string, unknown> | null) ?? {}
      const fields = { ...body }
      delete fields.secret
      const row = {
        ...WEBHOOK,
        ...fields,
        id: NEW_WEBHOOK_ID,
        has_secret: typeof body.secret === 'string' && body.secret !== '',
        total_delivered: 0,
        failure_count: 0,
        last_error: null,
        consecutive_failures: 0,
        breaker_threshold: body.breaker_threshold ?? 10,
        disabled_reason: null,
        disabled_at: null,
        created_at: isoAgo(0),
        updated_at: isoAgo(0),
      }
      rows.push(row)
      return respond(201, row)
    }, 'POST'],
    // The catalogue in the server's shape (`event_type`): the create form's checkboxes carry the event type.
    ['/api/v1/webhooks/events', () => ({ events: WEBHOOK_EVENT_CATALOG })],
    ...base.handlers,
  ]
  return { handlers, writes: base.writes, rows }
}

/** `GET /webhooks/events` (`WebhookEventCatalogResponse`), the three events the create form lists. */
export const WEBHOOK_EVENT_CATALOG = [
  { event_type: 'run.completed', description: 'A run finished ingestion and post-processing' },
  { event_type: 'defect.promoted', description: 'A defect promoted from a failure cluster took effect' },
  { event_type: 'release.decided', description: 'A release decision was written or overridden' },
]

// ── /runs/:id?tab=changes (FAIL-09) ────────────────────────────────────────

/** The Changes tab's worlds: offline, a project without GitHub, and a listed range counted from a weak anchor. */
export type RunChangesKind = 'offline' | 'no_integration' | 'listed'

/** The build the listed range starts after: the last COMPLETED run (239), not the diff's green baseline (238). */
export const RANGE_BASE_BUILD = '239'
/** The three commits of the listed world, oldest first, each with its server-built https link. */
export const RANGE_COMMITS = [
  { full: '3f1c9a2b7d4e8f60a1b2c3d4e5f60718293a4b5c', message: 'Shorten the session token TTL', author: 'dev-one', at: isoAgo(0, 7) },
  { full: '8e2d4b6a0c1f3e5d7b9a1c3e5f7092b4d6f8a0c2', message: 'Move MFA to the new provider', author: 'dev-two', at: isoAgo(0, 6) },
  { full: 'b7a9c1e3f5d7092b4d6f8a0c2e4f6a8b0c2d4e6f', message: 'Retry the token refresh once', author: 'dev-one', at: isoAgo(0, 5) },
].map((c) => ({
  sha: c.full.slice(0, 8),
  full_sha: c.full,
  message: c.message,
  author: c.author,
  timestamp: c.at,
  commit_url: `https://github.fake.test/acme/payments/commit/${c.full}`,
}))

export interface RunChangesOptions {
  /** Add a fourth commit whose `commit_url` is a `javascript:` URL (it must render as text, never a link). */
  hostileUrl?: boolean
}

/**
 * `/runs/:id` with the W10 regression diff (§1.1 `CommitRangeStatus`, §2.6) prepended to `RUN_PAGE`: the diff of
 * `RUN_PAGE` (baseline build 238, its new failures) with the commit range of the world — `offline` (status
 * `offline_mode`, no commits), `no_integration`, or `listed` (three `connector` commits after build 239, a weak
 * anchor: `last_completed_run`). The tab still asks exactly the diff and the duration regressions.
 */
export function runChangesWorld(kind: RunChangesKind, options: RunChangesOptions = {}) {
  const diffPath = `/api/v1/runs/${RUN_ID}/regression-diff`
  const base = delegate(RUN_PAGE, fakeGet(diffPath)) as Record<string, unknown>
  const commits = kind === 'listed'
    ? [...RANGE_COMMITS, ...(options.hostileUrl ? [{ ...RANGE_COMMITS[0], sha: 'bad0bad0', full_sha: 'bad0bad0'.repeat(5), message: 'A link that is not http', commit_url: 'javascript:alert(1)' }] : [])]
    : []
  const status = {
    available: kind === 'listed',
    source: kind === 'listed' ? 'connector' : 'unavailable',
    base_source: kind === 'listed' ? 'last_completed_run' : 'green_baseline',
    base_anchor_is_strong: kind !== 'listed',
    base_run_id: kind === 'listed' ? '33333333-3333-4333-8333-3333333332b9' : (base.baseline_run_id as string),
    base_build_number: kind === 'listed' ? RANGE_BASE_BUILD : (base.baseline_build_number as string),
    reason: kind === 'listed' ? null : kind === 'offline' ? 'offline_mode' : 'no_integration',
    commit_count: commits.length,
    resolved_at: kind === 'listed' ? isoAgo(0, 1) : null,
  }
  const diff = { ...base, commit_range: commits, commit_count: commits.length, commit_range_status: status }
  const handlers: ApiHandlers = [[diffPath, () => diff], ...RUN_PAGE]
  return { handlers, diff }
}
