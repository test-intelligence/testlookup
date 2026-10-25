/**
 * W15 G-E hermetic worlds (`docs/deferred-scope-2026-10-13/work/W15-contract.md` §2.1, §2.3, §6.2, §6.5):
 *
 *  - `agentActionsWorld({ pending, decided, role })`: Inbox › Approvals with the Agent actions queue. Its
 *    `/api/v1/projects/<P>/agent-actions` answers go FIRST (first match wins) in front of `inboxHandlers(writes)`, so
 *    the world holds whether or not the shared inbox world answers the read itself. The PATCH records its body in
 *    `writes` and answers the decided row, or the server's `{code, message}` refusal; an approved row moves
 *    `executing` on the next decided read and `executed` on the one after (the decided list polls while one runs).
 *  - `aiAgentsWorld({ mode, ticks })`: `/settings/ai-agents` — the investigator's agent-config GET / PUT with
 *    `config_version` and `If-Match` (every PUT recorded), the Fixer card's document and the config panel's list.
 *  - `runDecisionWorld()`: the Run page's Evidence tab with a published decision report whose three proposed actions
 *    have ledger rows (waiting, acknowledged, rejected) keyed by their `idempotency_key`.
 *
 * Agent text is run data rendered as TEXT; no id here looks like an e-mail, a phone or a card number.
 */
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'
import { isoAgo, LAYOUT, PROJECT_ID, RUN_ID, USER } from './fixtures'
import { QA_ENGINEER } from './fixtures-defects'
import { bodyOf, delegate, runAnalysisWorld } from './fixtures-in-page'
import { inboxHandlers, type InboxWrite } from './fixtures-pages'

const P = PROJECT_ID
/** The queue's path (list); a decision is `${AGENT_ACTIONS_PATH}/<id>`. */
export const AGENT_ACTIONS_PATH = `/api/v1/projects/${P}/agent-actions`
const ACTION_PATH = new RegExp(`^/api/v1/projects/${P}/agent-actions/[^/]+$`)
/** The decider every outcome names (the fixture user). */
export const DECIDER = USER.full_name

const REVIEW_BLOCKED = 'Accept the AI report first: an approved action runs only on an accepted report.'
export const REVIEW_REASON_CODES = ['wrong_category', 'unsupported_claim', 'missing_evidence', 'contradiction', 'stale_data', 'other']

type Row = Record<string, unknown> & { id: string; status: string }

/** An `AgentActionRead` (§1.1): the twenty old keys and the W15 fields. */
export function agentActionRow(over: Partial<Row> & { id: string }): Row {
  return {
    project_id: P,
    test_run_id: RUN_ID,
    pipeline_run_id: 'pipe-inv-a1',
    action_type: 'quarantine.apply',
    target_type: 'test',
    target_id: 'fpA1b2C3d4',
    status: 'pending_review',
    approval_required: true,
    idempotency_key: `inv:a1:${over.id}`,
    request_sha256: 'sha-a1',
    request_payload: {},
    approved_by: null,
    approved_at: null,
    execution_started_at: null,
    execution_completed_at: null,
    result_payload: null,
    rollback_payload: null,
    error_code: null,
    created_at: isoAgo(0, 3),
    canonical_type: 'quarantine.apply',
    title: 'Quarantine checkout-retry',
    rationale: 'Failing in this run and already in the known-flaky set.',
    risk: 'low',
    side_effect: 'internal',
    reversible: true,
    proposing_agent_id: 'investigator',
    proposing_agent_label: 'Investigator',
    proposing_review_state: 'accepted',
    review_id: 'review-inv-a1',
    test_run_build_number: '128',
    test_run_branch: 'main',
    pipeline_workflow_type: 'investigation',
    approved_by_name: null,
    target: { kind: 'test', id: 'fpA1b2C3d4', label: 'checkout-retry', href: `/runs/${RUN_ID}?tab=tests` },
    outcome: 'awaiting_approval',
    outcome_text: "Waiting for a QA lead's approval.",
    approvable: true,
    approve_blocked_reason: null,
    result_link: null,
    rejection_reason_code: null,
    ...over,
  }
}

/** The three waiting rows of `inbox-agent-actions`: an approvable quarantine, a reassign blocked by its report, a release item. */
export function pendingActions(): Row[] {
  return [
    agentActionRow({ id: 'act-quarantine-1', created_at: isoAgo(0, 5) }),
    agentActionRow({
      id: 'act-reassign-1', created_at: isoAgo(0, 4), action_type: 'failure.reassign', canonical_type: 'failure.reassign',
      target_type: 'test_case', target_id: 'tc-cart-total', title: 'Reassign cart-total to the Checkout owner',
      rationale: 'A newly-failed test outside the flaky and infrastructure sets: its suite owner should triage it.',
      risk: 'medium', pipeline_run_id: 'pipe-inv-b2', proposing_review_state: 'pending_review', review_id: 'review-inv-b2',
      target: { kind: 'test_case', id: 'tc-cart-total', label: 'cart-total', href: `/runs/${RUN_ID}?tab=tests` },
      approvable: false, approve_blocked_reason: REVIEW_BLOCKED,
    }),
    agentActionRow({
      id: 'act-ack-1', created_at: isoAgo(0, 3), action_type: 'decision_report_action', canonical_type: 'decision.acknowledge',
      target_type: 'release_item', target_id: 'ra-2', title: 'Ship behind the payments flag', risk: 'medium',
      rationale: 'The gateway timeouts are infrastructure: release with the flag off.', side_effect: 'none',
      reversible: false, proposing_agent_id: 'agent.decision_report.v1', proposing_agent_label: 'Decision report',
      pipeline_run_id: 'pipe-deep-c3', review_id: 'review-deep-c3', pipeline_workflow_type: 'deep',
      target: { kind: 'release_item', id: 'ra-2', label: 'ra-2', href: null },
    }),
  ]
}

const QUARANTINED = { kind: 'quarantine', id: 'qr-checkout-retry', label: 'quarantined', href: '/flaky?tab=quarantined' }

/** The four decided rows of `inbox-agent-actions-decided`: executed (+ Undo), rejected, outcome unknown, undone. */
export function decidedActions(): Row[] {
  const decided = { approved_by: USER.id, approved_by_name: DECIDER, approvable: false }
  return [
    agentActionRow({
      ...decided, id: 'act-done-1', status: 'executed', approved_at: isoAgo(0, 2), execution_completed_at: isoAgo(0, 2),
      title: 'Quarantine login-retry', target: { kind: 'test', id: 'fpL0g1nR3t', label: 'login-retry', href: `/runs/${RUN_ID}?tab=tests` },
      outcome: 'executed', outcome_text: `Executed · approved by ${DECIDER} · quarantined.`, result_link: QUARANTINED,
    }),
    agentActionRow({
      ...decided, id: 'act-rej-1', status: 'rejected', approved_at: isoAgo(1, 0), title: 'Quarantine search-suggest',
      outcome: 'rejected', outcome_text: `Rejected by ${DECIDER} (unsupported_claim).`, rejection_reason_code: 'unsupported_claim',
    }),
    agentActionRow({
      ...decided, id: 'act-lost-1', status: 'failed', error_code: 'outcome_unknown', approved_at: isoAgo(1, 2),
      title: 'Quarantine cart-badge', target: { kind: 'test', id: 'fpC4rtB4dg', label: 'cart-badge', href: `/runs/${RUN_ID}?tab=tests` },
      outcome: 'outcome_unknown',
      outcome_text: 'The outcome is unknown: the run stopped while the action was executing. It is not retried — check cart-badge and act on its own page.',
    }),
    agentActionRow({
      ...decided, id: 'act-undone-1', status: 'rolled_back', approved_at: isoAgo(2, 0), title: 'Reassign refund-flow to the Payments owner',
      action_type: 'failure.reassign', canonical_type: 'failure.reassign', target_type: 'test_case',
      target: { kind: 'test_case', id: 'tc-refund-flow', label: 'refund-flow', href: `/runs/${RUN_ID}?tab=tests` },
      outcome: 'rolled_back', outcome_text: `Undone by ${DECIDER}.`,
    }),
  ]
}

const DECIDED = new Set(['approved', 'executing', 'executed', 'failed', 'rejected', 'rolled_back'])
const decidedAt = (r: Row) => String(r.execution_completed_at ?? r.approved_at ?? r.created_at)

/** An approved row runs on the next decided read, and is done on the one after (the list polls meanwhile). */
function advance(row: Row) {
  if (row.status === 'approved') {
    Object.assign(row, { status: 'executing', execution_started_at: isoAgo(0, 0) })
  } else if (row.status === 'executing') {
    const ack = row.side_effect === 'none'
    Object.assign(row, {
      status: 'executed',
      execution_completed_at: isoAgo(0, 0),
      outcome: ack ? 'acknowledged' : 'executed',
      outcome_text: ack ? `Acknowledged by ${DECIDER}.` : `Executed · approved by ${DECIDER} · quarantined.`,
      result_link: ack ? null : QUARANTINED,
    })
  }
}

const refuse = (status: number, code: string, message: string) => respond(status, { detail: { code, message } })

/** One decision on the stored rows: the row it leaves, or the server's refusal. */
function decide(rows: Map<string, Row>, id: string, body: { status?: string; reason_code?: string } | null) {
  const row = rows.get(id)
  if (!row) return refuse(404, 'action_not_found', 'Agent action not found.')
  const decided = { approved_by: USER.id, approved_by_name: DECIDER, approved_at: isoAgo(0, 0), approvable: false }
  if (body?.status === 'approved') {
    if (row.status !== 'pending_review') return refuse(409, 'action_not_pending', `This action is already ${row.status}.`)
    if (!row.approvable) return refuse(409, 'review_not_accepted', String(row.approve_blocked_reason ?? REVIEW_BLOCKED))
    Object.assign(row, decided, { status: 'approved', outcome: 'approved_queued', outcome_text: `Approved by ${DECIDER} · running…` })
  } else if (body?.status === 'rejected') {
    if (!REVIEW_REASON_CODES.includes(body.reason_code ?? '')) {
      return refuse(422, 'reason_code_required', `A rejection needs a reason_code: ${REVIEW_REASON_CODES.join(', ')}.`)
    }
    if (row.status !== 'pending_review') return refuse(409, 'action_not_pending', `This action is already ${row.status}.`)
    Object.assign(row, decided, {
      status: 'rejected', outcome: 'rejected', rejection_reason_code: body.reason_code,
      outcome_text: `Rejected by ${DECIDER} (${body.reason_code}).`,
    })
  } else if (body?.status === 'rolled_back') {
    if (row.status !== 'executed') return refuse(409, 'action_not_executed', 'Only an executed action can be undone.')
    if (!row.reversible) return refuse(409, 'action_not_reversible', 'This action cannot be undone.')
    Object.assign(row, { status: 'rolled_back', outcome: 'rolled_back', outcome_text: `Undone by ${DECIDER}.`, result_link: null })
  } else {
    return respond(422, { detail: 'status must be approved, rejected or rolled_back' })
  }
  return row
}

/** The queue's list and decision answers over `rows` (the list route's `view` / `test_run_id` / `limit`). */
export function agentActionHandlers(rows: Map<string, Row>, writes: InboxWrite[]): ApiHandlers {
  return [
    [AGENT_ACTIONS_PATH, ({ url }) => {
      const view = url.searchParams.get('view')
      const runId = url.searchParams.get('test_run_id')
      const limit = Number(url.searchParams.get('limit') ?? 50)
      let items = [...rows.values()]
      if (view === 'pending') {
        items = items.filter((r) => r.status === 'pending_review').sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
      } else if (view === 'decided') {
        items.forEach(advance)
        items = items.filter((r) => DECIDED.has(r.status)).sort((a, b) => decidedAt(b).localeCompare(decidedAt(a)))
      }
      if (runId) items = items.filter((r) => r.test_run_id === runId)
      const total = items.length
      items = items.slice(0, limit)
      return { items, count: items.length, total }
    }],
    [ACTION_PATH, (request: ApiRequest) => {
      const body = bodyOf(request) as { status?: string; reason_code?: string } | null
      writes.push({ line: `PATCH ${request.path}`, body })
      return decide(rows, decodeURIComponent(request.path.split('/').pop() ?? ''), body)
    }, 'PATCH'],
  ]
}

export type AgentActionsRole = 'qa_lead' | 'qa_engineer'

/** Inbox › Approvals with the agent-action queue in front of the shared inbox world (fresh state per call). */
export function agentActionsWorld({
  pending = pendingActions(),
  decided = [] as Row[],
  role = 'qa_lead' as AgentActionsRole,
}: { pending?: Row[]; decided?: Row[]; role?: AgentActionsRole } = {}) {
  const writes: InboxWrite[] = []
  const rows = new Map<string, Row>([...pending, ...decided].map((r) => [r.id, { ...r }]))
  return {
    handlers: [...agentActionHandlers(rows, writes), ...inboxHandlers(writes)] as ApiHandlers,
    writes,
    rows,
    user: role === 'qa_engineer' ? QA_ENGINEER : USER,
  }
}

// ── /settings/ai-agents ─────────────────────────────────────────────────────

export const INVESTIGATOR_CONFIG_PATH = `/api/v1/projects/${P}/agent-configs/investigator`

/** One recorded PUT of the investigator's document: its body and the `If-Match` it carried. */
export interface ConfigWrite {
  line: string
  ifMatch: string | undefined
  body: { mode?: string; extensions?: { investigator?: { act?: { allowed_action_types?: string[] } } } } | null
}

function configView(agentId: string, version: number, config: Record<string, unknown>) {
  return { agent_id: agentId, source: version ? 'project' : 'default', config_version: version, valid: true, errors: [], updated_at: null, updated_by: null, config }
}

/** The investigator's agent-config (GET / PUT with `config_version` + `If-Match`), the Fixer's, and the panel's list. */
export function aiAgentsWorld({ mode = 'shadow', ticks = [] as string[] }: { mode?: string; ticks?: string[] } = {}) {
  const writes: ConfigWrite[] = []
  let version = 3
  let config: Record<string, unknown> = {
    agent_id: 'investigator',
    enabled: true,
    mode,
    extensions: {
      fixer: null,
      investigator: {
        budgets: { max_runs_per_day: 10, max_llm_calls_per_run: 30, max_tokens_per_run: 60_000, max_seconds_per_run: 300 },
        shadow_runs_completed: 7,
        promotion_note: null,
        act: { allowed_action_types: ticks },
      },
    },
  }
  const fixer = {
    agent_id: 'fixer', enabled: false, mode: 'shadow',
    extensions: {
      investigator: null,
      fixer: {
        runner: { type: 'none', runner_image: null, command_template: null, workflow_ref: null },
        test_globs: ['tests/**'], schedule: 'off',
        budgets: { max_tests_per_run: 3, max_attempts_per_test: 2, validation_reruns: 5, max_concurrent_open_prs: 2 },
      },
    },
  }
  const handlers: ApiHandlers = [
    [INVESTIGATOR_CONFIG_PATH, () => configView('investigator', version, config)],
    [INVESTIGATOR_CONFIG_PATH, (request: ApiRequest) => {
      const ifMatch = request.route.request().headers()['if-match']
      const body = bodyOf(request) as ConfigWrite['body']
      writes.push({ line: `PUT ${request.path}`, ifMatch, body })
      if (ifMatch !== `"${version}"`) return respond(412, { detail: 'config_version_mismatch' })
      version += 1
      config = body as Record<string, unknown>
      return configView('investigator', version, config)
    }, 'PUT'],
    [`/api/v1/projects/${P}/agent-configs/fixer`, () => configView('fixer', 0, fixer)],
    [`/api/v1/projects/${P}/agent-configs`, () => ({ configs: [], tools: {} })],
    ...LAYOUT,
  ]
  return { handlers, writes, version: () => version }
}

// ── /runs/:id?tab=evidence ──────────────────────────────────────────────────

/** The decision report's three proposed actions; each `idempotency_key` IS its ledger row's key. */
export const DECISION_ACTIONS = [
  { key: 'decision:report-w27a-1:ra-1', title: 'Hold the release until the gateway recovers', status: 'pending_review', outcome: 'awaiting_approval', text: "Waiting for a QA lead's approval." },
  { key: 'decision:report-w27a-1:ra-2', title: 'Ship behind the payments flag', status: 'executed', outcome: 'acknowledged', text: `Acknowledged by ${DECIDER}.` },
  { key: 'decision:report-w27a-1:ra-3', title: 'Re-run the Payments suite', status: 'rejected', outcome: 'rejected', text: `Rejected by ${DECIDER} (stale_data).` },
]

/** The Run page with a published decision report (`runAnalysisWorld`) whose proposed actions have ledger rows. */
export function runDecisionWorld() {
  const base = runAnalysisWorld({ decisionReport: true })
  const proposed = DECISION_ACTIONS.map((a, i) => ({
    action_id: `ra-${i + 1}`, title: a.title, owner: 'release_owner', rationale: 'From the release conditions of the report.',
    evidence: [], risk: 'medium', required_permission: 'release_review', idempotency_key: a.key, status: 'proposed',
  }))
  const ledger = DECISION_ACTIONS.map((a, i) => agentActionRow({
    id: `act-decision-${i + 1}`, idempotency_key: a.key, status: a.status, outcome: a.outcome, outcome_text: a.text,
    action_type: 'decision_report_action', canonical_type: 'decision.acknowledge', side_effect: 'none', reversible: false,
    title: a.title, proposing_agent_id: 'agent.decision_report.v1', proposing_agent_label: 'Decision report',
  }))
  const handlers: ApiHandlers = [
    [/^\/api\/v1\/runs\/[^/]+\/intelligence$/, (request) => {
      const answer = delegate(base.handlers, request) as { structured_summary: Record<string, unknown> }
      const report = answer.structured_summary.decision_intelligence as Record<string, unknown>
      return { ...answer, structured_summary: { ...answer.structured_summary, decision_intelligence: { ...report, proposed_actions: proposed } } }
    }],
    [AGENT_ACTIONS_PATH, ({ url }) => {
      const items = url.searchParams.get('test_run_id') === RUN_ID ? ledger : []
      return { items, count: items.length, total: items.length }
    }],
    ...base.handlers,
  ]
  return { handlers, writes: base.writes }
}
