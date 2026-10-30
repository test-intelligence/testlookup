/**
 * Fixtures for the W23c review-gate / Try-an-agent vitests (AGT-10, AGT-11), in the REAL wire shapes.
 *
 * Review gate (`GET /api/v1/settings/review-gate`):
 * - `gateUnset()` / `gateScheduled()` / `gateDate()` / `gateEnv()`: the four sources, would-refuse
 *   MEASURED (12 runs, 42 distribution attempts over three channels);
 * - `gateNotMeasured(source?)`: the audit read failed — `measured: false`, every number null;
 * - `reviewGate(overrides)`: the base (unset) answer; `wouldRefuseMeasured()` / `wouldRefuseNotMeasured()`.
 *
 * Try an agent:
 * - `mockCatalog()`: the REAL `GET /api/v1/agents/catalog` answer (a bare array): `agent.summary.v1`
 *   inline-supported, `agent.root_cause_analysis.v1` not, one non-invokable capability;
 * - `summaryDetail()`: the summary agent's detail with the REAL `inline_input_json_schema`;
 * - `invocationInProgress()` / `invocationAwaitingReview()` / `invocationWithStoredInput()`: one inline
 *   invocation through its life; `invocation(overrides)` the base (a stored-run invocation in progress).
 *
 * Sentinels are non-hex words ("checkout pays sentinel", "payments-sentinel"); the redacted stored input
 * carries `[REDACTED]` where the caller's token was. Timestamps are built at CALL time.
 */
import type {
  AgentCatalogDetail,
  AgentCatalogEntry,
  AgentInvocation,
} from '@/types/agentInvoke'
import type { GatedChannel, ReviewGateRead, WouldRefuseSummary } from '@/types/reviewGate'

export const RG_PROJECT_ID = '00000000-0000-4000-8000-00000000c3a1'
export const RG_RUN_ID = '00000000-0000-4000-8000-00000000c3b2'
export const RG_INVOCATION_ID = '00000000-0000-4000-8000-00000000c3c3'
export const RG_PIPELINE_ID = '00000000-0000-4000-8000-00000000c3d4'
export const RG_REVIEW_ID = '00000000-0000-4000-8000-00000000c3e5'
export const RG_INPUT_SHA = '1a2b3c4d5e6f' + 'ab'.repeat(26)
export const SENTINEL_TEST_NAME = 'checkout pays sentinel'
export const SENTINEL_SUITE = 'payments-sentinel'
/** `review_envelope.envelope_from_review`'s pending sentence. */
export const REVIEW_PENDING_MESSAGE =
  'AI-generated. Human review required before use; a reviewer can accept or reject it in the review queue.'

/** `review_gate_vocab.GATED_CHANNELS`, labels included, in order. */
export const RG_CHANNELS: GatedChannel[] = [
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

function daysFromNow(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString()
}

export function wouldRefuseMeasured(overrides: Partial<WouldRefuseSummary> = {}): WouldRefuseSummary {
  return {
    measured: true,
    window_days: 30,
    since: daysFromNow(-30),
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

export function wouldRefuseNotMeasured(): WouldRefuseSummary {
  return { measured: false, window_days: 30, since: null, decisions: null, runs: null, by_channel: null }
}

export function reviewGate(overrides: Partial<ReviewGateRead> = {}): ReviewGateRead {
  return {
    enforced: false,
    source: 'unset',
    enforced_from: null,
    env_override: false,
    updated_at: null,
    would_refuse: wouldRefuseMeasured(),
    channels: RG_CHANNELS.map((c) => ({ ...c })),
    ...overrides,
  }
}

export function gateUnset(): ReviewGateRead {
  return reviewGate()
}

/** A date 4 days ahead: audit only until then. */
export function gateScheduled(): ReviewGateRead {
  return reviewGate({ source: 'scheduled', enforced_from: daysFromNow(4), updated_at: daysFromNow(-1) })
}

/** A date 2 days ago: enforced since then. */
export function gateDate(): ReviewGateRead {
  return reviewGate({ enforced: true, source: 'date', enforced_from: daysFromNow(-2), updated_at: daysFromNow(-2) })
}

/** REVIEW_GATE_ENFORCED=true on the server (a stored date does not apply). */
export function gateEnv(): ReviewGateRead {
  return reviewGate({ enforced: true, source: 'env', env_override: true })
}

export function gateNotMeasured(source: ReviewGateRead['source'] = 'unset'): ReviewGateRead {
  return reviewGate({ source, would_refuse: wouldRefuseNotMeasured() })
}

// ── Try an agent ──────────────────────────────────────────────────────────────

/** `agent_catalog.list_catalog()`'s `agent.summary.v1` entry (+ the W23c flag). */
export function catalogEntry(overrides: Partial<AgentCatalogEntry> = {}): AgentCatalogEntry {
  return {
    agent_id: 'agent.summary.v1',
    version: 1,
    stage_name: 'summary',
    permission: 'read_only',
    execution: 'planned',
    default_tier: 'slm',
    escalation: ['validation_failure'],
    sync_eligible: false,
    invokable: true,
    produces_report: true,
    input_schema: 'RunEvidenceBundleV1',
    output_schema: 'PreliminarySummary',
    input_schema_resolved: true,
    output_schema_resolved: true,
    dependencies: ['ingestion'],
    required_evidence: [],
    expected_latency_ms: 10000,
    expected_cost_usd: 0.02,
    timeout_seconds: 60,
    max_retries: 0,
    fallback: 'deterministic_degraded_output',
    concurrency_class: 'analysis',
    inline_input_supported: true,
    ...overrides,
  }
}

export function mockCatalog(): AgentCatalogEntry[] {
  return [
    catalogEntry(),
    catalogEntry({
      agent_id: 'agent.root_cause_analysis.v1',
      stage_name: 'root_cause_analysis',
      escalation: ['low_confidence', 'multi_artifact_evidence'],
      output_schema: 'AnalysisAgentOutput',
      required_evidence: ['failed_test_results'],
      expected_latency_ms: 20000,
      expected_cost_usd: 0.05,
      timeout_seconds: 120,
      inline_input_supported: false,
    }),
    catalogEntry({
      agent_id: 'agent.reviewer.v1',
      stage_name: 'reviewer',
      invokable: false,
      produces_report: false,
      inline_input_supported: false,
    }),
  ]
}

/** `InlineSummaryInputV1.model_json_schema()`, verbatim. */
export function inlineInputJsonSchema(): Record<string, unknown> {
  return {
    $defs: {
      InlineFailureV1: {
        additionalProperties: false,
        description: 'One failed test as the caller describes it (its own claim, wrapped as data).',
        properties: {
          test_name: { maxLength: 300, minLength: 1, title: 'Test Name', type: 'string' },
          suite_name: { anyOf: [{ maxLength: 300, type: 'string' }, { type: 'null' }], default: null, title: 'Suite Name' },
          failure_category: {
            default: 'UNKNOWN',
            enum: ['PRODUCT_BUG', 'INFRASTRUCTURE', 'TEST_DATA', 'AUTOMATION_DEFECT', 'FLAKY', 'UNKNOWN'],
            title: 'Failure Category',
            type: 'string',
          },
          confidence_score: { maximum: 100, minimum: 0, title: 'Confidence Score', type: 'integer' },
          root_cause: { default: '', maxLength: 4000, title: 'Root Cause', type: 'string' },
          log_excerpt: { anyOf: [{ maxLength: 8000, type: 'string' }, { type: 'null' }], default: null, title: 'Log Excerpt' },
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

export function summaryDetail(overrides: Partial<AgentCatalogDetail> = {}): AgentCatalogDetail {
  return {
    ...catalogEntry(),
    input_wrapper: 'SummaryInvokeInput',
    input_json_schema: { title: 'SummaryInvokeInput', type: 'object' },
    output_json_schema: { title: 'PreliminarySummary', type: 'object' },
    inline_input_json_schema: inlineInputJsonSchema(),
    ...overrides,
  }
}

/** The server's REDACTED stored copy of an inline input (the caller's `ghp_…` token became `[REDACTED]`). */
export function redactedStoredInput(): Record<string, unknown> {
  return {
    schema_version: 1,
    failures: [
      {
        test_name: SENTINEL_TEST_NAME,
        suite_name: SENTINEL_SUITE,
        failure_category: 'PRODUCT_BUG',
        confidence_score: 70,
        root_cause: 'NullPointerException in CardVault.lookup',
        log_excerpt: 'at CardVault.lookup(CardVault.java:42)\nAuthorization: token [REDACTED]',
      },
    ],
  }
}

/** The base: a STORED-RUN invocation of agent.summary.v1, in progress (no W23c fields, as an older server). */
export function invocation(overrides: Partial<AgentInvocation> = {}): AgentInvocation {
  return {
    id: RG_INVOCATION_ID,
    project_id: RG_PROJECT_ID,
    agent_id: 'agent.summary.v1',
    test_run_id: RG_RUN_ID,
    pipeline_run_id: RG_PIPELINE_ID,
    mode: 'async',
    status: 'in_progress',
    attempt: 1,
    max_attempts: 5,
    next_retry_at: null,
    error: null,
    output: null,
    config_snapshot: null,
    requires_human_review: true,
    review: { state: 'pending_review', message: REVIEW_PENDING_MESSAGE },
    created_at: daysFromNow(0),
    links: {
      self: `/api/v1/agents/invocations/${RG_INVOCATION_ID}`,
      pipeline: `/api/v1/agents/pipelines/${RG_PIPELINE_ID}`,
    },
    ...overrides,
  }
}

/** An INLINE invocation, just accepted (202): in progress, no output yet. */
export function invocationInProgress(): AgentInvocation {
  return invocation({ input_kind: 'inline', input_sha256: RG_INPUT_SHA, input: redactedStoredInput() })
}

/** The same invocation, completed, awaiting a person's review (its review row exists). */
export function invocationAwaitingReview(): AgentInvocation {
  return invocation({
    status: 'completed',
    input_kind: 'inline',
    input_sha256: RG_INPUT_SHA,
    input: redactedStoredInput(),
    output: {
      executive_summary: `1 failure supplied: ${SENTINEL_TEST_NAME} (PRODUCT_BUG, 70%).`,
      key_findings: ['CardVault.lookup raised a NullPointerException.'],
    },
    review: {
      state: 'pending_review',
      message: REVIEW_PENDING_MESSAGE,
      review_id: RG_REVIEW_ID,
      reviewed_at: null,
    },
    links: {
      self: `/api/v1/agents/invocations/${RG_INVOCATION_ID}`,
      pipeline: `/api/v1/agents/pipelines/${RG_PIPELINE_ID}`,
      review: `/api/v1/reviews/${RG_REVIEW_ID}`,
    },
  })
}

/** The redacted stored input differs from what a test TYPES (proves the SPA shows the server's copy). */
export function invocationWithStoredInput(): AgentInvocation {
  return invocationAwaitingReview()
}
