// W15 AGT-01/02/03: the agent-action queue. Mirrors the backend
// `schemas.AgentActionRead` / `AgentActionListResponse`; the few vocabularies
// the UI renders mirror `services/agent_action_types.py` (pinned by
// `backend/tests/test_agent_action_vocab_frontend_parity.py`).
//
// Enum-like fields are `string` (the server owns the vocabulary). Every text
// field (title, rationale, labels, outcome_text) is display-only: render it as
// React text, never as HTML, never as an href.

export { REVIEW_REASON_CODES, REVIEW_REASON_LABEL } from './review'
export type { ReviewReasonCode } from './review'

/** D16: the ONLY types Act may run, each opt-in per project. */
export const ACT_ACTION_TYPES = ['quarantine.apply', 'failure.reassign', 'defect.promote'] as const
export type ActActionType = (typeof ACT_ACTION_TYPES)[number]

export const ACT_ACTION_LABELS: Record<ActActionType, string> = {
  'quarantine.apply': 'Quarantine flaky offenders',
  'failure.reassign': 'Reassign failures to the suite owner',
  'defect.promote': 'Promote an internal defect',
}

/** GET `?view=`: pending = waiting for a decision; decided = approved onwards. */
export const QUEUE_VIEWS = ['pending', 'decided'] as const
export type QueueView = (typeof QUEUE_VIEWS)[number]

export interface AgentActionTarget {
  /** test | test_case | cluster | release_item */
  kind: string
  id?: string | null
  label?: string | null
  /** Built server-side from ids only. */
  href?: string | null
}

export interface AgentActionResultLink {
  /** quarantine | defect | assignee */
  kind: string
  id?: string | null
  label?: string | null
  href?: string | null
}

export interface AgentAction {
  // The twenty keys the route has always returned.
  id: string
  project_id: string
  test_run_id: string | null
  pipeline_run_id: string | null
  action_type: string
  target_type: string
  target_id: string | null
  status: string
  approval_required: boolean
  idempotency_key: string
  request_sha256: string
  request_payload: Record<string, unknown>
  approved_by: string | null
  approved_at: string | null
  execution_started_at: string | null
  execution_completed_at: string | null
  result_payload: Record<string, unknown> | null
  rollback_payload: Record<string, unknown> | null
  error_code: string | null
  created_at: string | null
  // W15 — every one optional so old fixtures and mocks still type-check.
  /** The catalogue type after aliases; null = unregistered. */
  canonical_type?: string | null
  title?: string | null
  rationale?: string | null
  /** low | medium | high | unknown */
  risk?: string | null
  /** none | internal | external */
  side_effect?: string | null
  reversible?: boolean
  proposing_agent_id?: string | null
  proposing_agent_label?: string | null
  /** accepted | pending_review | rejected | superseded | none */
  proposing_review_state?: string
  review_id?: string | null
  test_run_build_number?: string | null
  test_run_branch?: string | null
  pipeline_workflow_type?: string | null
  approved_by_name?: string | null
  target?: AgentActionTarget | null
  /** awaiting_approval | approved_queued | acknowledged | executed | rolled_back | rejected | superseded | not_executed_* | no_executor | outcome_unknown | failed | unknown */
  outcome?: string
  outcome_text?: string
  approvable?: boolean
  approve_blocked_reason?: string | null
  result_link?: AgentActionResultLink | null
  rejection_reason_code?: string | null
}

export interface AgentActionList {
  items: AgentAction[]
  /** len(items) — the page length. */
  count: number
  /** The filtered set's size — the number the UI shows (falls back to `count` on an old server). */
  total?: number
}

export interface AgentActionDecision {
  status: 'approved' | 'rejected' | 'rolled_back'
  /** Required on `rejected` (one of REVIEW_REASON_CODES); refused otherwise. */
  reason_code?: string
  /** ≤ 2000; redacted and stored, never returned. */
  notes?: string
}

/** An approved row the executor has not finished yet ("Running…"; the decided list polls). */
export function isAgentActionRunning(action: Pick<AgentAction, 'status'>): boolean {
  return action.status === 'approved' || action.status === 'executing'
}

export interface AgentActionListQuery {
  view?: QueueView
  action_type?: string
  pipeline_run_id?: string
  test_run_id?: string
  limit?: number
}
