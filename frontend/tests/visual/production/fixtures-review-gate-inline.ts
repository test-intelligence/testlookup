/**
 * W23c AGT-10 / AGT-11 hermetic world: Settings › AI › Review gate, and `/agents` › Try an agent with the
 * invocation detail and its Inbox › Approvals row (`docs/deferred-scope-2026-10-13/work/W23c-contract.md` §6.2
 * G-A, §6.3 specs).
 *
 * `reviewGateWorld({ role, gate, putAnswers })` answers FIRST, with the REAL server shapes (`ReviewGateRead`):
 *  - `GET /api/v1/settings/review-gate` → the stored state, `enforced` / `source` recomputed against `NOW` the way
 *    `review_gate_setting.gate_state` does (env → `env`; no date → `unset`; date <= now → `date`; later →
 *    `scheduled`); every GET is counted (`state.reads`: the page makes ONE on load);
 *  - `PUT /api/v1/settings/review-gate` → records EVERY body (`writes`), then, in the server's order: below ADMIN
 *    → 403 `Requires at least ADMIN role`; the next scripted `putAnswers` entry (a `{status, detail}` refusal —
 *    the 403 `interactive_only` / 422 `naive_datetime` bodies are `worldGateRefusal(code)`) or `'apply'`; an unknown
 *    key / a non-string → FastAPI's 422; a naive datetime → 422 `naive_datetime`; else the instant is stored as
 *    UTC (`null` clears) and the new read is answered.
 *  Below ADMIN, the GET is the same 403 (a QA lead reaching the URL; the page should never ask).
 *
 * `tryAgentWorld({ role, catalog, detail, invokeAnswers, invocationSequence })` answers FIRST:
 *  - `GET /api/v1/agents/catalog` (a bare array, as the server's) and `GET /api/v1/agents/catalog/{id}` (the detail
 *    of a known agent, else 404 `Unknown agent`); each read counted;
 *  - `POST /api/v1/agents/{id}/invoke` → records the body AND the `Idempotency-Key` (`state.invokes`); below
 *    QA_ENGINEER → 403; the next scripted `invokeAnswers` entry (`worldInvokeRefusal(...)`: 413 / 422 with `errors` /
 *    404) or the server's own checks (the same key with another body → 422; inline input for an agent outside
 *    `INLINE_INPUT_AGENTS` → 422 `inline_input_unsupported`; no anchor → 422 `inline_input_needs_anchor`; over the cap
 *    → 413; an invalid input → 422 `inline_input_invalid` with `errors` (never the input value); a wrong anchor run or
 *    project → 404 `anchor_run_not_found`); else 202 with an `in_progress` invocation whose `input` is the server's
 *    REDACTED copy (`[REDACTED]` for every `ghp_…` / `github_pat_…` / `glpat-…` / `sk-…` / `AKIA…` token);
 *  - `GET /api/v1/agents/invocations/{id}` → `invocationSequence` in order, the last repeating (default: the accepted
 *    invocation `in_progress`, then `completed` awaiting review); an unknown id → 404 `Invocation not found`;
 *  - `GET /api/v1/projects/{PROJECT_ID}/reviews` → ONE `subject_type: "invocation"` review row (`worldInvocationReview`).
 *  Then `base`, then the shared `/agents` set (`agentsHandlers()`, as `fold-agents.spec.ts` uses it) and the Inbox
 *  set (`inboxHandlers()`, for `/my-failures?tab=approvals&source=reports`). NO existing world is edited: no page's
 *  default load gains a read.
 *
 * Fresh state per call; `state` is live and mutable. The clock is `NOW` (the pinned page clock). Sentinels are
 * non-hex words. Not a spec file.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import {
  INLINE_ERRORS,
  INLINE_FAILURES_MAX,
  INLINE_INPUT_AGENTS,
  INLINE_INPUT_MAX_BYTES,
  type AgentCatalogDetail,
  type AgentCatalogEntry,
  type AgentInvocation,
} from '../../../src/types/agentInvoke'
import {
  REVIEW_GATE_ERRORS,
  type GatedChannel,
  type ReviewGateRead,
  type WouldRefuseSummary,
} from '../../../src/types/reviewGate'
import { LAYOUT, NOW, PROJECT_ID, RUN_ID, USER } from './fixtures'
import { bodyOf, type RecordedWrite } from './fixtures-in-page'
import { agentsHandlers, inboxHandlers } from './fixtures-pages'
import { ROLE_USERS, WORLD_ROLES, type WorldRole } from './fixtures-retention'

export type { RecordedWrite, WorldRole }

const DAY_MS = 86_400_000
const atLeast = (role: WorldRole, min: WorldRole) => WORLD_ROLES.indexOf(role) >= WORLD_ROLES.indexOf(min)
const roleRefusal = (min: WorldRole) => respond(403, { detail: `Requires at least ${min} role` })

/** A `{status, detail}` refusal a spec scripts (the detail is sent as `{"detail": detail}`). */
export interface WorldRefusal {
  status: number
  detail: unknown
}

const isRefusal = (value: unknown): value is WorldRefusal =>
  typeof value === 'object' && value !== null && 'status' in value && 'detail' in value

// ── Review gate: the REAL shapes ─────────────────────────────────────────────

/** `review_gate_vocab.GATED_CHANNELS`, labels included, in order. */
export const WORLD_CHANNELS: GatedChannel[] = [
  { channel: 'report_pdf', label: 'PDF export of a run report' },
  { channel: 'share_link_html', label: 'Shared report link (web page)' },
  { channel: 'share_link_pdf', label: 'Shared report link (PDF)' },
  { channel: 'intelligence_export', label: 'Run intelligence export (JSON)' },
  { channel: 'ai_history_export', label: 'AI history export (JSON)' },
  { channel: 'analysis_report', label: 'Window analysis report download' },
  { channel: 'digest_attachment', label: 'Analysis report attached to digests' },
  { channel: 'email_run_report', label: 'Run report e-mail' },
  { channel: 'ai_summary_notification', label: 'AI summary e-mail and event-driven digests' },
  { channel: 'ai_summary_notification_relay', label: 'AI summary in notification relays' },
  { channel: 'github_pr_comment', label: 'AI failure-kind labels in GitHub PR comments' },
  { channel: 'gitlab_mr_note', label: 'AI failure-kind labels in GitLab MR notes' },
  { channel: 'release.decided.webhook', label: 'release.decided webhooks' },
]

/** 12 runs' reports, 42 distribution attempts over three channels, a full 30-day trail. */
export function worldWouldRefuse(overrides: Partial<WouldRefuseSummary> = {}): WouldRefuseSummary {
  return {
    measured: true,
    window_days: 30,
    since: new Date(NOW.getTime() - 30 * DAY_MS).toISOString(),
    decisions: 42,
    runs: 12,
    by_channel: [
      { channel: 'report_pdf', decisions: 20 },
      { channel: 'github_pr_comment', decisions: 14 },
      { channel: 'ai_summary_notification', decisions: 8 },
    ],
    ...overrides,
  }
}

/** The audit log could not be read: every number null (never 0). */
export function worldWouldRefuseNotMeasured(): WouldRefuseSummary {
  return { measured: false, window_days: 30, since: null, decisions: null, runs: null, by_channel: null }
}

/** `review_gate_setting.gate_state` at `NOW`. */
export function worldGateState(enforcedFrom: string | null, env: boolean): Pick<ReviewGateRead, 'enforced' | 'source'> {
  if (env) return { enforced: true, source: 'env' }
  if (enforcedFrom === null) return { enforced: false, source: 'unset' }
  return new Date(enforcedFrom).getTime() <= NOW.getTime()
    ? { enforced: true, source: 'date' }
    : { enforced: false, source: 'scheduled' }
}

export interface WorldGateSpec {
  /** The stored instant (UTC ISO) or null (unset). */
  enforcedFrom?: string | null
  /** The server's REVIEW_GATE_ENFORCED flag. */
  env?: boolean
  wouldRefuse?: WouldRefuseSummary
  updatedAt?: string | null
}

/** A `ReviewGateRead` with `enforced` / `source` derived from the stored date, the env flag and `NOW`. */
export function worldGate(spec: WorldGateSpec = {}): ReviewGateRead {
  const enforcedFrom = spec.enforcedFrom ?? null
  const env = spec.env ?? false
  return {
    ...worldGateState(enforcedFrom, env),
    enforced_from: enforcedFrom,
    env_override: env,
    updated_at: spec.updatedAt !== undefined ? spec.updatedAt : enforcedFrom === null ? null : NOW.toISOString(),
    would_refuse: spec.wouldRefuse ?? worldWouldRefuse(),
    channels: WORLD_CHANNELS.map((c) => ({ ...c })),
  }
}

/** Audit only, no date (D17's upgrade default). */
export const worldGateUnset = () => worldGate()
/** A date four days after `NOW`: audit only until then. */
export const worldGateScheduled = () => worldGate({ enforcedFrom: new Date(NOW.getTime() + 4 * DAY_MS).toISOString() })
/** A date two days before `NOW`: enforced since. */
export const worldGateDate = () => worldGate({ enforcedFrom: new Date(NOW.getTime() - 2 * DAY_MS).toISOString() })
/** REVIEW_GATE_ENFORCED=true on the server. */
export const worldGateEnv = () => worldGate({ env: true })
/** The audit read failed. */
export const worldGateNotMeasured = () => worldGate({ wouldRefuse: worldWouldRefuseNotMeasured() })

/** The PUT's `{"detail": {"code", "message"}}` refusals (403 `interactive_only`, 422 `naive_datetime`). */
export function worldGateRefusal(code: keyof typeof REVIEW_GATE_ERRORS): WorldRefusal {
  return { status: code === 'interactive_only' ? 403 : 422, detail: { code, message: REVIEW_GATE_ERRORS[code] } }
}

// ── reviewGateWorld ──────────────────────────────────────────────────────────

export const REVIEW_GATE_PATH = '/api/v1/settings/review-gate'

/** One scripted PUT answer: a refusal, or `'apply'` (the server's own checks, then store). */
export type ReviewGatePutAnswer = WorldRefusal | 'apply'

export interface ReviewGateWorldOptions {
  /** The signed-in role (default ADMIN; the page is admin-only). */
  role?: WorldRole
  /** The stored state the first GET answers (default `worldGateUnset()`). */
  gate?: ReviewGateRead
  /** The answers of the PUTs, in order; past the end every PUT is `'apply'`. */
  putAnswers?: ReviewGatePutAnswer[]
  /** Where every PUT body is recorded (default a fresh array). */
  writes?: RecordedWrite[]
  /** Extra handlers, answered after the world's routes and before `LAYOUT`. */
  base?: ApiHandlers
}

export interface ReviewGateWorldState {
  gate: ReviewGateRead
  /** GETs answered (the page makes ONE on load). */
  reads: number
  puts: number
}

export interface ReviewGateWorld {
  handlers: ApiHandlers
  writes: RecordedWrite[]
  state: ReviewGateWorldState
  /** The `user` to sign in as (`ROLE_USERS[role]`). */
  user: typeof USER
}

/** FastAPI's own 422 for one body field. */
function body422(type: string, field: string, msg: string, input: unknown) {
  return respond(422, { detail: [{ type, loc: ['body', field], msg, input }] })
}

const OFFSET_RE = /(?:Z|[+-]\d{2}:?\d{2})$/i

export function reviewGateWorld(options: ReviewGateWorldOptions = {}): ReviewGateWorld {
  const role: WorldRole = options.role ?? 'ADMIN'
  const writes = options.writes ?? []
  const putAnswers = [...(options.putAnswers ?? [])]
  const state: ReviewGateWorldState = { gate: options.gate ?? worldGateUnset(), reads: 0, puts: 0 }

  /** The stored state with `enforced` / `source` recomputed at `NOW` (a scheduled date can have passed). */
  const read = (): ReviewGateRead => ({ ...state.gate, ...worldGateState(state.gate.enforced_from, state.gate.env_override) })

  const put = (request: ApiRequest) => {
    const body = bodyOf(request)
    writes.push({ line: `PUT ${request.path}`, body })
    state.puts += 1
    if (!atLeast(role, 'ADMIN')) return roleRefusal('ADMIN')
    const scripted = putAnswers.shift()
    if (isRefusal(scripted)) return respond(scripted.status, { detail: scripted.detail })
    if (typeof body !== 'object' || body === null || Array.isArray(body)) {
      return respond(422, { detail: [{ type: 'model_attributes_type', loc: ['body'], msg: 'Input should be a valid dictionary or object to extract fields from', input: body }] })
    }
    const extra = Object.keys(body).find((key) => key !== 'enforced_from')
    if (extra) return body422('extra_forbidden', extra, 'Extra inputs are not permitted', (body as Record<string, unknown>)[extra])
    const value = (body as { enforced_from?: unknown }).enforced_from ?? null
    if (value !== null && typeof value !== 'string') return body422('datetime_type', 'enforced_from', 'Input should be a valid datetime', value)
    if (typeof value === 'string' && Number.isNaN(Date.parse(value))) {
      return body422('datetime_from_date_parsing', 'enforced_from', 'Input should be a valid datetime or date, invalid character in year', value)
    }
    if (typeof value === 'string' && !OFFSET_RE.test(value)) {
      return respond(422, { detail: { code: 'naive_datetime', message: REVIEW_GATE_ERRORS.naive_datetime } })
    }
    const enforcedFrom = value === null ? null : new Date(value).toISOString()
    state.gate = {
      ...state.gate,
      enforced_from: enforcedFrom,
      updated_at: enforcedFrom === state.gate.enforced_from ? state.gate.updated_at : NOW.toISOString(),
    }
    return read()
  }

  const routes: ApiHandlers = [
    [REVIEW_GATE_PATH, () => {
      state.reads += 1
      return atLeast(role, 'ADMIN') ? read() : roleRefusal('ADMIN')
    }],
    [REVIEW_GATE_PATH, put, 'PUT'],
  ]
  return {
    handlers: [...routes, ...(options.base ?? []), ...LAYOUT],
    writes,
    state,
    user: ROLE_USERS[role],
  }
}

// ── Try an agent: the REAL shapes ────────────────────────────────────────────

export const WORLD_INVOCATION_ID = 'c0000000-0000-4000-8000-0000000000a1'
export const WORLD_INVOCATION_PIPELINE_ID = 'c0000000-0000-4000-8000-0000000000b2'
export const WORLD_INVOCATION_REVIEW_ID = 'c0000000-0000-4000-8000-0000000000c3'
/** == review_envelope.envelope_from_review's pending sentence. */
export const WORLD_REVIEW_PENDING_MESSAGE =
  'AI-generated. Human review required before use; a reviewer can accept or reject it in the review queue.'
/** The redaction marker the server writes in place of a token (`untrusted_input.scrub`). */
export const WORLD_REDACTED = '[REDACTED]'
export const WORLD_TEST_NAME = 'checkout pays sentinel'
export const WORLD_SUITE = 'payments-sentinel'

/** `agent_catalog.list_catalog()`'s `agent.summary.v1` entry, plus the W23c flag. */
export function worldCatalogEntry(overrides: Partial<AgentCatalogEntry> = {}): AgentCatalogEntry {
  return {
    agent_id: 'agent.summary.v1', version: 1, stage_name: 'summary', permission: 'read_only', execution: 'planned',
    default_tier: 'slm', escalation: ['validation_failure'], sync_eligible: false, invokable: true, produces_report: true,
    input_schema: 'RunEvidenceBundleV1', output_schema: 'PreliminarySummary', input_schema_resolved: true,
    output_schema_resolved: true, dependencies: ['ingestion'], required_evidence: [], expected_latency_ms: 10000,
    expected_cost_usd: 0.02, timeout_seconds: 60, max_retries: 0, fallback: 'deterministic_degraded_output',
    concurrency_class: 'analysis', inline_input_supported: true,
    ...overrides,
  }
}

/** Summary (inline input), Root cause analysis (stored run only), and a capability that is not invokable. */
export function worldCatalog(): AgentCatalogEntry[] {
  return [
    worldCatalogEntry(),
    worldCatalogEntry({
      agent_id: 'agent.root_cause_analysis.v1', stage_name: 'root_cause_analysis',
      escalation: ['low_confidence', 'multi_artifact_evidence'], output_schema: 'AnalysisAgentOutput',
      required_evidence: ['failed_test_results'], expected_latency_ms: 20000, expected_cost_usd: 0.05,
      timeout_seconds: 120, inline_input_supported: false,
    }),
    worldCatalogEntry({
      agent_id: 'agent.reviewer.v1', stage_name: 'reviewer', invokable: false, produces_report: false,
      inline_input_supported: false,
    }),
  ]
}

/** `InlineSummaryInputV1.model_json_schema()`, verbatim. */
export function worldInlineJsonSchema(): Record<string, unknown> {
  const maxString = (maxLength: number, title: string) => ({
    anyOf: [{ maxLength, type: 'string' }, { type: 'null' }], default: null, title,
  })
  return {
    $defs: {
      InlineFailureV1: {
        additionalProperties: false,
        description: 'One failed test as the caller describes it (its own claim, wrapped as data).',
        properties: {
          test_name: { maxLength: 300, minLength: 1, title: 'Test Name', type: 'string' },
          suite_name: maxString(300, 'Suite Name'),
          failure_category: {
            default: 'UNKNOWN', enum: ['PRODUCT_BUG', 'INFRASTRUCTURE', 'TEST_DATA', 'AUTOMATION_DEFECT', 'FLAKY', 'UNKNOWN'],
            title: 'Failure Category', type: 'string',
          },
          confidence_score: { maximum: 100, minimum: 0, title: 'Confidence Score', type: 'integer' },
          root_cause: { default: '', maxLength: 4000, title: 'Root Cause', type: 'string' },
          log_excerpt: maxString(8000, 'Log Excerpt'),
        },
        required: ['test_name', 'confidence_score'],
        title: 'InlineFailureV1',
        type: 'object',
      },
    },
    additionalProperties: false,
    description: 'The inline input of ``agent.summary.v1``: 1..INLINE_FAILURES_MAX failures.',
    properties: {
      schema_version: { const: 1, default: 1, title: 'Schema Version', type: 'integer' },
      failures: { items: { $ref: '#/$defs/InlineFailureV1' }, maxItems: 10, minItems: 1, title: 'Failures', type: 'array' },
    },
    required: ['failures'],
    title: 'InlineSummaryInputV1',
    type: 'object',
  }
}

/** `GET /api/v1/agents/catalog/{id}`: the entry plus its wrapper and schemas (the inline schema when supported). */
export function worldCatalogDetail(entry: AgentCatalogEntry = worldCatalogEntry()): AgentCatalogDetail {
  const wrapper = `${entry.stage_name.split('_').map((w) => w[0].toUpperCase() + w.slice(1)).join('')}InvokeInput`
  return {
    ...entry,
    input_wrapper: wrapper,
    input_json_schema: { title: wrapper, type: 'object' },
    output_json_schema: { title: entry.output_schema, type: 'object' },
    inline_input_json_schema: entry.inline_input_supported ? worldInlineJsonSchema() : null,
  }
}

/** Token shapes the server's scrub replaces whole (a subset of W23b's `TOKEN_SHAPES`, enough for the specs). */
const TOKEN_RE = /\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}|glpat-[A-Za-z0-9_-]{20}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b/g

/** The server's REDACTED stored copy of an inline input: every string scrubbed, the shape kept. */
export function worldRedacted<T>(value: T): T {
  if (typeof value === 'string') return value.replace(TOKEN_RE, WORLD_REDACTED) as T
  if (Array.isArray(value)) return value.map((item) => worldRedacted(item)) as T
  if (typeof value === 'object' && value !== null) {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, worldRedacted(item)])) as T
  }
  return value
}

/** A stored inline input (already redacted): one failure with a log excerpt. */
export function worldStoredInput(): Record<string, unknown> {
  return {
    schema_version: 1,
    failures: [{
      test_name: WORLD_TEST_NAME, suite_name: WORLD_SUITE, failure_category: 'PRODUCT_BUG', confidence_score: 70,
      root_cause: 'NullPointerException in CardVault.lookup',
      log_excerpt: `at CardVault.lookup(CardVault.java:42)\nAuthorization: token ${WORLD_REDACTED}`,
    }],
  }
}

/** A deterministic 64-hex sha256-shaped value for a stored input (the world does not hash; the server does). */
function worldSha(seed: string): string {
  let hash = 0x811c9dc5
  for (const ch of seed) hash = Math.imul(hash ^ ch.charCodeAt(0), 0x01000193) >>> 0
  return hash.toString(16).padStart(8, '0').repeat(8)
}

const invocationLinks = (id: string, reviewId: string | null) => ({
  self: `/api/v1/agents/invocations/${id}`,
  pipeline: `/api/v1/agents/pipelines/${WORLD_INVOCATION_PIPELINE_ID}`,
  ...(reviewId ? { review: `/api/v1/reviews/${reviewId}` } : {}),
})

/** An INLINE invocation of agent.summary.v1 on `RUN_ID`, just accepted: `in_progress`, no output yet. */
export function worldInvocationInProgress(overrides: Partial<AgentInvocation> = {}): AgentInvocation {
  const input = (overrides.input ?? worldStoredInput()) as Record<string, unknown>
  return {
    id: WORLD_INVOCATION_ID, project_id: PROJECT_ID, agent_id: 'agent.summary.v1', test_run_id: RUN_ID,
    pipeline_run_id: WORLD_INVOCATION_PIPELINE_ID, mode: 'async', status: 'in_progress', attempt: 1, max_attempts: 5,
    next_retry_at: null, error: null, output: null, config_snapshot: null, requires_human_review: true,
    review: { state: 'pending_review', message: WORLD_REVIEW_PENDING_MESSAGE, review_id: null, reviewed_at: null },
    created_at: NOW.toISOString(), links: invocationLinks(overrides.id ?? WORLD_INVOCATION_ID, null),
    input_kind: 'inline', input_sha256: worldSha(JSON.stringify(input)), input,
    ...overrides,
  }
}

/** The same invocation, completed, awaiting a person's review (its review row exists). */
export function worldInvocationAwaitingReview(overrides: Partial<AgentInvocation> = {}): AgentInvocation {
  const base = worldInvocationInProgress(overrides)
  return {
    ...base,
    status: 'completed',
    output: {
      executive_summary: `1 failure supplied: ${WORLD_TEST_NAME} (PRODUCT_BUG, 70%). CardVault.lookup raised a NullPointerException.`,
      key_findings: ['CardVault.lookup raised a NullPointerException.'],
      risk_level: 'medium',
    },
    review: {
      state: 'pending_review', message: WORLD_REVIEW_PENDING_MESSAGE, review_id: WORLD_INVOCATION_REVIEW_ID, reviewed_at: null,
    },
    links: invocationLinks(base.id, WORLD_INVOCATION_REVIEW_ID),
    ...overrides,
  }
}

/** The Inbox › Approvals row of an inline invocation (`ReviewResponse`, `subject_type: "invocation"`). */
export function worldInvocationReview(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: WORLD_INVOCATION_REVIEW_ID, project_id: PROJECT_ID, kind: 'report', subject_type: 'invocation',
    subject_id: WORLD_INVOCATION_ID, pipeline_run_id: WORLD_INVOCATION_PIPELINE_ID, test_run_id: RUN_ID,
    workflow_type: 'offline', state: 'pending_review', reviewed: false, reviewed_at: null, reason_code: null, notes: null,
    evidence_bundle_sha256: worldSha(JSON.stringify(worldStoredInput())), superseded_by: null,
    created_at: new Date(NOW.getTime() - 10 * 60_000).toISOString(), requires_human_review: true,
    ai_disclaimer: 'AI-generated content. Verify before acting.', ai_disclaimer_version: '2026-09-12.v1',
    ...overrides,
  }
}

/** The invoke refusals the panel renders inline: 413, 422 (with Pydantic `errors`, never the input), 404. */
export function worldInvokeRefusal(
  code: keyof typeof INLINE_ERRORS,
  extra: { agentId?: string; errors?: Array<{ loc: Array<string | number>; msg: string; type: string }> } = {},
): WorldRefusal {
  const status = code === 'inline_input_too_large' ? 413 : code === 'anchor_run_not_found' ? 404 : 422
  const message = INLINE_ERRORS[code].replace('{agent_id}', extra.agentId ?? 'agent.root_cause_analysis.v1')
  const detail: Record<string, unknown> = { code, message }
  if (code === 'inline_input_invalid') {
    detail.errors = extra.errors ?? [
      { loc: ['failures', 0, 'confidence_score'], msg: 'Input should be less than or equal to 100', type: 'less_than_equal' },
    ]
  }
  return { status, detail }
}

// ── The server's own inline checks (a faithful subset of `InlineSummaryInputV1` + the route's order) ──

type PydanticError = { loc: Array<string | number>; msg: string; type: string }

/** `inline_invocation.canonical_bytes`: sorted keys, compact separators, UTF-8. */
function canonicalLength(value: unknown): number {
  const canonical = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(canonical)
    if (typeof v === 'object' && v !== null) {
      return Object.fromEntries(Object.keys(v).sort().map((key) => [key, canonical((v as Record<string, unknown>)[key])]))
    }
    return v
  }
  return new TextEncoder().encode(JSON.stringify(canonical(value))).length
}

const FAILURE_FIELDS: Record<string, number | null> = {
  test_name: 300, suite_name: 300, failure_category: null, confidence_score: null, root_cause: 4000, log_excerpt: 8000,
}
const CATEGORIES = ['PRODUCT_BUG', 'INFRASTRUCTURE', 'TEST_DATA', 'AUTOMATION_DEFECT', 'FLAKY', 'UNKNOWN']
// eslint-disable-next-line no-control-regex -- the model's own rule: NUL and C0 except \t \n
const CONTROL_RE = /[\u0000-\u0008\u000b-\u001f]/

/** Pydantic's errors for `InlineSummaryInputV1` (loc / msg / type only — the input value is never echoed). */
function inlineErrors(raw: unknown): PydanticError[] {
  const errors: PydanticError[] = []
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return [{ loc: [], msg: 'Input should be a valid dictionary or instance of InlineSummaryInputV1', type: 'model_type' }]
  }
  const input = raw as Record<string, unknown>
  for (const key of Object.keys(input)) {
    if (key !== 'schema_version' && key !== 'failures') errors.push({ loc: [key], msg: 'Extra inputs are not permitted', type: 'extra_forbidden' })
  }
  if ('schema_version' in input && input.schema_version !== 1) {
    errors.push({ loc: ['schema_version'], msg: 'Input should be 1', type: 'literal_error' })
  }
  const failures = input.failures
  if (!Array.isArray(failures)) {
    errors.push({ loc: ['failures'], msg: failures === undefined ? 'Field required' : 'Input should be a valid list', type: failures === undefined ? 'missing' : 'list_type' })
    return errors
  }
  if (failures.length < 1) errors.push({ loc: ['failures'], msg: 'List should have at least 1 item after validation, not 0', type: 'too_short' })
  if (failures.length > INLINE_FAILURES_MAX) {
    errors.push({ loc: ['failures'], msg: `List should have at most ${INLINE_FAILURES_MAX} items after validation, not ${failures.length}`, type: 'too_long' })
  }
  failures.forEach((failure, i) => {
    if (typeof failure !== 'object' || failure === null || Array.isArray(failure)) {
      errors.push({ loc: ['failures', i], msg: 'Input should be a valid dictionary or instance of InlineFailureV1', type: 'model_type' })
      return
    }
    const f = failure as Record<string, unknown>
    for (const key of Object.keys(f)) {
      if (!(key in FAILURE_FIELDS)) errors.push({ loc: ['failures', i, key], msg: 'Extra inputs are not permitted', type: 'extra_forbidden' })
    }
    const name = typeof f.test_name === 'string' ? f.test_name.trim() : f.test_name
    if (name === undefined) errors.push({ loc: ['failures', i, 'test_name'], msg: 'Field required', type: 'missing' })
    else if (typeof name !== 'string') errors.push({ loc: ['failures', i, 'test_name'], msg: 'Input should be a valid string', type: 'string_type' })
    else if (name.length < 1) errors.push({ loc: ['failures', i, 'test_name'], msg: 'String should have at least 1 character', type: 'string_too_short' })
    const score = f.confidence_score
    if (score === undefined) errors.push({ loc: ['failures', i, 'confidence_score'], msg: 'Field required', type: 'missing' })
    else if (typeof score !== 'number' || !Number.isInteger(score)) {
      errors.push({ loc: ['failures', i, 'confidence_score'], msg: 'Input should be a valid integer', type: 'int_type' })
    } else if (score < 0) errors.push({ loc: ['failures', i, 'confidence_score'], msg: 'Input should be greater than or equal to 0', type: 'greater_than_equal' })
    else if (score > 100) errors.push({ loc: ['failures', i, 'confidence_score'], msg: 'Input should be less than or equal to 100', type: 'less_than_equal' })
    if (f.failure_category !== undefined && !CATEGORIES.includes(String(f.failure_category))) {
      errors.push({ loc: ['failures', i, 'failure_category'], msg: `Input should be ${CATEGORIES.map((c) => `'${c}'`).join(', ')}`, type: 'literal_error' })
    }
    for (const [field, max] of Object.entries(FAILURE_FIELDS)) {
      const value = f[field]
      if (max === null || value === undefined || value === null) continue
      if (typeof value !== 'string') {
        if (field !== 'test_name') errors.push({ loc: ['failures', i, field], msg: 'Input should be a valid string', type: 'string_type' })
        continue
      }
      if (value.trim().length > max) {
        errors.push({ loc: ['failures', i, field], msg: `String should have at most ${max} characters`, type: 'string_too_long' })
      } else if (CONTROL_RE.test(value)) {
        errors.push({ loc: ['failures', i, field], msg: 'Value error, must not contain NUL or control characters other than tab and newline', type: 'value_error' })
      }
    }
  })
  return errors
}

// ── tryAgentWorld ────────────────────────────────────────────────────────────

/** One scripted invoke answer: a refusal, or `'accept'` (the server's own checks, then 202). */
export type TryAgentInvokeAnswer = WorldRefusal | 'accept'

export interface TryAgentWorldOptions {
  /** The signed-in role (default QA_ENGINEER; invoking needs QA_ENGINEER, a VIEWER sees no button). */
  role?: WorldRole
  /** `GET /api/v1/agents/catalog` (default `worldCatalog()`). */
  catalog?: AgentCatalogEntry[]
  /** `GET /api/v1/agents/catalog/{id}` per agent id (default `worldCatalogDetail(entry)` for every catalog entry). */
  detail?: Record<string, AgentCatalogDetail>
  /** The answers of the invokes, in order; past the end every invoke is `'accept'`. */
  invokeAnswers?: TryAgentInvokeAnswer[]
  /**
   * `GET /api/v1/agents/invocations/{id}` answers, in order, the last repeating. Default, per invocation: the accepted
   * invocation `in_progress`, then `completed` awaiting review (its `input` the server's redacted copy).
   */
  invocationSequence?: AgentInvocation[]
  /** Where every invoke (and review decision) is recorded (default a fresh array). */
  writes?: RecordedWrite[]
  /** Extra handlers, answered after the world's routes and before the shared sets. */
  base?: ApiHandlers
}

export interface RecordedInvoke {
  agentId: string
  body: unknown
  /** The `Idempotency-Key` header as sent (undefined when absent). */
  idempotencyKey: string | undefined
}

export interface TryAgentWorldState {
  catalog: AgentCatalogEntry[]
  detail: Record<string, AgentCatalogDetail>
  invokes: RecordedInvoke[]
  catalogReads: number
  /** The agent ids whose detail was read, in order. */
  detailReads: string[]
  /** Reads per invocation id. */
  invocationReads: Record<string, number>
  /** The stored (redacted) input per accepted invocation id. */
  storedInputs: Record<string, Record<string, unknown>>
  reviewState: 'pending_review' | 'accepted'
}

export interface TryAgentWorld {
  handlers: ApiHandlers
  writes: RecordedWrite[]
  state: TryAgentWorldState
  user: typeof USER
}

const INVOKE_RE = /^\/api\/v1\/agents\/([^/]+)\/invoke$/
const CATALOG_DETAIL_RE = /^\/api\/v1\/agents\/catalog\/([^/]+)$/
const INVOCATION_RE = /^\/api\/v1\/agents\/invocations\/([^/]+)$/
const KEY_REUSED = 'Idempotency-Key was already used for a different request; send a new key'

export function tryAgentWorld(options: TryAgentWorldOptions = {}): TryAgentWorld {
  const role: WorldRole = options.role ?? 'QA_ENGINEER'
  const writes = options.writes ?? []
  const invokeAnswers = [...(options.invokeAnswers ?? [])]
  const catalog = options.catalog ?? worldCatalog()
  const state: TryAgentWorldState = {
    catalog,
    detail: options.detail ?? Object.fromEntries(catalog.map((entry) => [entry.agent_id, worldCatalogDetail(entry)])),
    invokes: [],
    catalogReads: 0,
    detailReads: [],
    invocationReads: {},
    storedInputs: { [WORLD_INVOCATION_ID]: worldStoredInput() },
    reviewState: 'pending_review',
  }
  const byKey = new Map<string, { body: string; answer: AgentInvocation }>()
  let accepted = 0

  const nextId = () => (accepted++ === 0 ? WORLD_INVOCATION_ID : `c0000000-0000-4000-8000-0000000000a${accepted + 1}`)

  /** The server's checks, in its order (cheapest refusal first, nothing stored before all pass). */
  const check = (agentId: string, body: Record<string, unknown>): WorldRefusal | null => {
    const entry = state.catalog.find((candidate) => candidate.agent_id === agentId)
    if (!entry) return { status: 404, detail: 'Unknown agent' }
    const payload = (body.input as { payload?: unknown } | undefined)?.payload as Record<string, unknown> | undefined
    const inline = body.inline_input
    if (inline === undefined || inline === null) {
      if (payload?.test_run_id !== RUN_ID) return { status: 404, detail: 'TestRun not found' }
      if (body.project_id !== PROJECT_ID) return { status: 400, detail: "project_id does not match the test run's project" }
      return null
    }
    if (!(INLINE_INPUT_AGENTS as readonly string[]).includes(agentId) || !entry.input_schema_resolved) {
      return worldInvokeRefusal('inline_input_unsupported', { agentId })
    }
    const anchorOnly = payload && typeof payload === 'object' && Object.keys(payload).length === 1 && typeof payload.test_run_id === 'string'
    if (!anchorOnly) return worldInvokeRefusal('inline_input_needs_anchor')
    if (canonicalLength(inline) > INLINE_INPUT_MAX_BYTES) return worldInvokeRefusal('inline_input_too_large')
    const errors = inlineErrors(inline)
    if (errors.length > 0) return worldInvokeRefusal('inline_input_invalid', { errors })
    if (payload.test_run_id !== RUN_ID || body.project_id !== PROJECT_ID) return worldInvokeRefusal('anchor_run_not_found')
    return null
  }

  const invoke = (request: ApiRequest) => {
    const agentId = decodeURIComponent(INVOKE_RE.exec(request.path)?.[1] ?? '')
    const body = bodyOf(request)
    const idempotencyKey = request.route.request().headers()['idempotency-key']
    state.invokes.push({ agentId, body, idempotencyKey })
    writes.push({ line: `POST ${request.path}`, body })
    if (!atLeast(role, 'QA_ENGINEER')) return roleRefusal('QA_ENGINEER')
    const scripted = invokeAnswers.shift()
    if (isRefusal(scripted)) return respond(scripted.status, { detail: scripted.detail })
    if (typeof body !== 'object' || body === null || Array.isArray(body)) return respond(422, { detail: 'Invalid body' })
    const refusal = check(agentId, body as Record<string, unknown>)
    if (refusal) return respond(refusal.status, { detail: refusal.detail })
    const fingerprint = JSON.stringify(body)
    const replay = idempotencyKey ? byKey.get(idempotencyKey) : undefined
    if (replay && replay.body !== fingerprint) return respond(422, { detail: KEY_REUSED })
    if (replay) return replay.answer
    const id = nextId()
    const inline = (body as Record<string, unknown>).inline_input
    const stored = inline ? worldRedacted(inline as Record<string, unknown>) : null
    if (stored) state.storedInputs[id] = stored
    const answer = stored
      ? worldInvocationInProgress({ id, agent_id: agentId, input: stored, links: invocationLinks(id, null) })
      : worldInvocationInProgress({ id, agent_id: agentId, input_kind: 'stored_run', input_sha256: null, input: null, links: invocationLinks(id, null) })
    if (idempotencyKey) byKey.set(idempotencyKey, { body: fingerprint, answer })
    return respond(202, answer)
  }

  const invocation = ({ path }: ApiRequest) => {
    const id = decodeURIComponent(INVOCATION_RE.exec(path)?.[1] ?? '')
    const explicit = options.invocationSequence
    const known = id in state.storedInputs || (explicit ?? []).some((answer) => answer.id === id)
    if (!known) return respond(404, { detail: 'Invocation not found' })
    const reads = (state.invocationReads[id] ?? 0) + 1
    state.invocationReads[id] = reads
    if (explicit && explicit.length > 0) return explicit[Math.min(reads, explicit.length) - 1]
    const input = state.storedInputs[id]
    const sequence = [
      worldInvocationInProgress({ id, input, links: invocationLinks(id, null) }),
      worldInvocationAwaitingReview({ id, input }),
    ]
    const answer = sequence[Math.min(reads, sequence.length) - 1]
    if (state.reviewState === 'accepted' && answer.status === 'completed') {
      return { ...answer, status: 'passed', review: { ...answer.review, state: 'accepted', reviewed_at: NOW.toISOString() } }
    }
    return answer
  }

  const review = () => worldInvocationReview(
    state.reviewState === 'accepted' ? { state: 'accepted', reviewed: true, reviewed_at: NOW.toISOString() } : {},
  )

  const routes: ApiHandlers = [
    ['/api/v1/agents/catalog', () => {
      state.catalogReads += 1
      return state.catalog
    }],
    [CATALOG_DETAIL_RE, ({ path }) => {
      const id = decodeURIComponent(CATALOG_DETAIL_RE.exec(path)?.[1] ?? '')
      state.detailReads.push(id)
      return state.detail[id] ?? respond(404, { detail: 'Unknown agent' })
    }],
    [INVOKE_RE, invoke, 'POST'],
    [INVOCATION_RE, invocation],
    [`/api/v1/projects/${PROJECT_ID}/reviews`, ({ url }) => {
      const filter = url.searchParams.get('state')
      return !filter || filter === state.reviewState ? [review()] : []
    }],
    [`/api/v1/reviews/${WORLD_INVOCATION_REVIEW_ID}/accept`, (request) => {
      writes.push({ line: `POST ${request.path}`, body: bodyOf(request) })
      if (!atLeast(role, 'QA_LEAD')) return roleRefusal('QA_LEAD')
      if (state.reviewState !== 'pending_review') {
        return respond(409, { detail: { code: 'review_not_pending', message: 'This review was already decided.' } })
      }
      state.reviewState = 'accepted'
      return review()
    }, 'POST'],
  ]
  return {
    handlers: [...routes, ...(options.base ?? []), ...agentsHandlers(), ...inboxHandlers()],
    writes,
    state,
    user: ROLE_USERS[role],
  }
}
