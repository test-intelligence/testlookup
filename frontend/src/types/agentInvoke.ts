/**
 * W23c AGT-11: invoking one agent from the SPA (`/agents` › Try an agent) — the catalog and invocation
 * shapes, the inline-input contract of `agent.summary.v1`, the wire vocabulary and the defensive parser.
 *
 * An invocation always runs against a STORED test run of the project (the anchor, `input.payload.test_run_id`);
 * an inline input (`inline_input`) is accepted only for `INLINE_INPUT_AGENTS`, is redacted by the server
 * before it is stored, and its output is a draft kept out of the run's report, gates and notifications.
 *
 * The constants mirror `backend/app/services/inline_input_vocab.py` value for value (pinned by
 * `backend/tests/test_review_gate_inline_vocab_parity.py`, which reads this file as TEXT — keep every
 * constant a plain literal on its own `export const` line, strings in one piece, one `key: 'value',` per
 * record line). Every new response field is OPTIONAL so older answers and fixtures still type-check.
 * Every name, output and input is rendered as React text (`<pre>` for JSON), never as HTML.
 */
import type { ReviewBlock } from '@/types/review'

export const INLINE_INPUT_AGENTS = ['agent.summary.v1'] as const
/** Canonical compact JSON of `inline_input`, UTF-8 bytes; more is the server's 413. */
export const INLINE_INPUT_MAX_BYTES = 262144
export const INLINE_FAILURES_MAX = 10
/** Characters. */
export const TEST_NAME_MAX = 300
export const SUITE_NAME_MAX = 300
export const ROOT_CAUSE_MAX = 4000
export const LOG_EXCERPT_MAX = 8000
export const CONFIDENCE_RANGE = [0, 100] as const
export const FAILURE_CATEGORIES = ['PRODUCT_BUG', 'INFRASTRUCTURE', 'TEST_DATA', 'AUTOMATION_DEFECT', 'FLAKY', 'UNKNOWN'] as const
export type FailureCategory = (typeof FAILURE_CATEGORIES)[number]
export const INPUT_KINDS = ['stored_run', 'inline'] as const
export type InputKind = (typeof INPUT_KINDS)[number]

/** detail = {code, message} (+ `errors` for inline_input_invalid); `{agent_id}` is filled by the server. */
export const INLINE_ERRORS = {
  inline_input_too_large: 'inline_input is larger than 262144 bytes.',
  inline_input_unsupported: '{agent_id} does not accept inline input in this release; invoke it on a stored test run.',
  inline_input_invalid: 'inline_input does not match InlineSummaryInputV1.',
  anchor_run_not_found: 'No test run with that id in this project.',
  inline_input_needs_anchor: 'inline_input needs input.payload = {"test_run_id": "<uuid>"}: a stored test run of the project anchors it.',
} as const

export const INLINE_SENTENCES = {
  awaiting_review: 'Awaiting review — a person must accept this output before it counts anywhere.',
  excluded: "Inline-input output: kept out of the run's report, release gates, decisions, notifications and digests.",
  stored_input_redacted: 'Stored input (secrets and personal data redacted before storage)',
} as const

/** The editor's example confidence: a visible, editable value, never a default the server applies. */
export const TEMPLATE_CONFIDENCE = 60

// ── the catalog (`GET /api/v1/agents/catalog`, `GET /api/v1/agents/catalog/{agent_id}`) ──

export interface AgentCatalogEntry {
  agent_id: string
  version: number
  stage_name: string
  permission: string
  execution: string
  default_tier: 'deterministic' | 'slm' | 'llm'
  escalation: string[]
  sync_eligible: boolean
  invokable: boolean
  produces_report: boolean
  input_schema: string
  output_schema: string
  input_schema_resolved: boolean
  output_schema_resolved: boolean
  dependencies: string[]
  required_evidence: string[]
  expected_latency_ms: number
  expected_cost_usd: number
  timeout_seconds: number
  max_retries: number
  fallback: string
  concurrency_class: string
  /** W23c: `agent_id` is in INLINE_INPUT_AGENTS (absent from an older server = false). */
  inline_input_supported?: boolean
}

export interface AgentCatalogDetail extends AgentCatalogEntry {
  input_wrapper: string
  input_json_schema: Record<string, unknown>
  output_json_schema?: Record<string, unknown> | null
  /** W23c: `InlineSummaryInputV1.model_json_schema()` for an inline-capable agent, else null / absent. */
  inline_input_json_schema?: Record<string, unknown> | null
}

// ── the inline input of agent.summary.v1 (`models/inline_agent_input.py`) ──

export interface InlineFailure {
  test_name: string
  suite_name?: string | null
  failure_category?: FailureCategory
  /** 0..100, required: the caller's own claim. */
  confidence_score: number
  root_cause?: string
  log_excerpt?: string | null
}

export interface InlineSummaryInput {
  schema_version: 1
  failures: InlineFailure[]
}

// ── invoking (`POST /api/v1/agents/{agent_id}/invoke`) ──

export interface AgentInvokeBody {
  /** An assertion: the anchor run's project. */
  project_id: string
  /** The agent's wrapper; for inline input `payload` is the anchor `{test_run_id}`. */
  input: { agent_id: string; payload: { test_run_id: string } }
  mode?: 'async' | 'sync'
  inline_input?: InlineSummaryInput | Record<string, unknown> | null
}

/** One Pydantic error echoed by a 422 `inline_input_invalid` (never the input value). */
export interface InlineValidationError {
  loc: Array<string | number>
  msg: string
  type?: string
}

export type InvocationStatus = 'in_progress' | 'completed' | 'failed' | 'passed'
export const INVOCATION_STATUSES = ['in_progress', 'completed', 'failed', 'passed'] as const

/** `GET /api/v1/agents/invocations/{invocation_id}` and the invoke 200 / 202 body. */
export interface AgentInvocation {
  id: string
  project_id: string
  agent_id: string
  test_run_id: string
  pipeline_run_id: string
  mode: 'sync' | 'async'
  status: InvocationStatus
  attempt: number
  max_attempts: number
  next_retry_at?: string | null
  error?: string | null
  output?: Record<string, unknown> | null
  config_snapshot?: Record<string, unknown> | null
  requires_human_review: boolean
  review: ReviewBlock
  created_at?: string | null
  links: Record<string, string>
  /** W23c: absent from an older server = 'stored_run'. */
  input_kind?: InputKind
  input_sha256?: string | null
  /** The REDACTED stored inline input (null for a stored-run invocation). */
  input?: Record<string, unknown> | null
}

// ── the defensive parser and the editor template ──────────────────────────────

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function strOrNull(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}

function intOr(v: unknown, fallback: number): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback
}

const REVIEW_STATES = ['pending_review', 'accepted', 'rejected', 'superseded', 'not_applicable'] as const
const SHA256_HEX = /^[0-9a-f]{64}$/

function parseReview(raw: unknown): ReviewBlock {
  // Fail closed: an unreadable review block reads as not accepted.
  if (!isRecord(raw) || !(REVIEW_STATES as readonly unknown[]).includes(raw.state)) {
    return { state: 'pending_review', message: '' }
  }
  return {
    state: raw.state as ReviewBlock['state'],
    message: typeof raw.message === 'string' ? raw.message : '',
    review_id: strOrNull(raw.review_id),
    reviewed_at: strOrNull(raw.reviewed_at),
  }
}

/**
 * An invocation answer, or `null` when it cannot be read. The W23c fields default for an older answer:
 * no `input_kind` = 'stored_run', no hash, no stored input.
 */
export function parseInvocation(raw: unknown): AgentInvocation | null {
  if (!isRecord(raw)) return null
  const { id, agent_id: agentId, test_run_id: testRunId, status } = raw
  if (typeof id !== 'string' || typeof agentId !== 'string' || typeof testRunId !== 'string') return null
  if (!(INVOCATION_STATUSES as readonly unknown[]).includes(status)) return null
  const sha = typeof raw.input_sha256 === 'string' && SHA256_HEX.test(raw.input_sha256) ? raw.input_sha256 : null
  const links: Record<string, string> = {}
  if (isRecord(raw.links)) {
    for (const [key, value] of Object.entries(raw.links)) if (typeof value === 'string') links[key] = value
  }
  return {
    id,
    project_id: typeof raw.project_id === 'string' ? raw.project_id : '',
    agent_id: agentId,
    test_run_id: testRunId,
    pipeline_run_id: typeof raw.pipeline_run_id === 'string' ? raw.pipeline_run_id : '',
    mode: raw.mode === 'sync' ? 'sync' : 'async',
    status: status as InvocationStatus,
    attempt: intOr(raw.attempt, 0),
    max_attempts: intOr(raw.max_attempts, 0),
    next_retry_at: strOrNull(raw.next_retry_at),
    error: strOrNull(raw.error),
    output: isRecord(raw.output) ? raw.output : null,
    config_snapshot: isRecord(raw.config_snapshot) ? raw.config_snapshot : null,
    requires_human_review: raw.requires_human_review === true,
    review: parseReview(raw.review),
    created_at: strOrNull(raw.created_at),
    links,
    input_kind: raw.input_kind === 'inline' ? 'inline' : 'stored_run',
    input_sha256: sha,
    input: isRecord(raw.input) ? raw.input : null,
  }
}

/** The editor's one example failure (every value visible and editable). */
const TEMPLATE_FAILURE: Record<string, unknown> = {
  test_name: '',
  suite_name: '',
  failure_category: 'UNKNOWN',
  confidence_score: TEMPLATE_CONFIDENCE,
  root_cause: '',
  log_excerpt: '',
}

/** The property names of the failure model in `inline_input_json_schema`, or null when unreadable. */
function failureProperties(schema: unknown): Set<string> | null {
  if (!isRecord(schema) || !isRecord(schema.$defs)) return null
  const failure = schema.$defs.InlineFailureV1
  if (!isRecord(failure) || !isRecord(failure.properties)) return null
  return new Set(Object.keys(failure.properties))
}

/**
 * The JSON the inline editor is pre-filled with: one failure, keyed by the server schema's properties
 * (a key the schema does not declare is dropped — the model is closed), pretty-printed. '' for an agent
 * that does not accept inline input.
 */
export function inlineTemplate(detail: AgentCatalogDetail | null | undefined): string {
  if (!detail || detail.inline_input_supported !== true) return ''
  const known = failureProperties(detail.inline_input_json_schema)
  const failure: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(TEMPLATE_FAILURE)) {
    if (known === null || known.has(key)) failure[key] = value
  }
  return JSON.stringify({ schema_version: 1, failures: [failure] }, null, 2)
}
