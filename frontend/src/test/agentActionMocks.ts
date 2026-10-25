/**
 * Stubs for `@/hooks/useAgentActions` (W15), for the page vitests. One set-up
 * line per file:
 *
 *   vi.mock('@/hooks/useAgentActions', async () => (await import('@/test/agentActionMocks')).agentActionHookMocks())
 *
 * The three reads are `vi.fn`s answering the IDLE SWR shape (no data, no
 * error, not loading, a `mutate`), so a page renders without a request
 * (`src/test/networkGuard.ts` fails a test that makes one).
 * `useDecideAgentAction` answers a `vi.fn` decide function (resolving to the
 * action) — it is called, not destructured. Data per hook:
 * `agentActionHookMocks({ usePendingAgentActions: { data: PENDING_ACTIONS_FIXTURE } })`,
 * or `vi.mocked(usePendingAgentActions).mockReturnValue(...)` in a case.
 *
 * Deliberately imports nothing from `@/hooks/useAgentActions`: this module is
 * loaded INSIDE that module's mock factory (that module exports hooks only).
 */
import { vi, type Mock } from 'vitest'

import type { AgentAction, AgentActionList } from '@/types/agentActions'

/** The reads of `@/hooks/useAgentActions`, in the module's order. */
export const AGENT_ACTION_READ_HOOK_NAMES = [
  'usePendingAgentActions',
  'useDecidedAgentActions',
  'useRunAgentActions',
] as const
export type AgentActionReadHookName = (typeof AGENT_ACTION_READ_HOOK_NAMES)[number]
export type AgentActionHookName = AgentActionReadHookName | 'useDecideAgentAction'

export interface AgentActionSwrStub {
  data: AgentActionList | undefined
  error: unknown
  isLoading: boolean
  mutate: Mock
}

export function idleAgentActionSwr(overrides: Partial<AgentActionSwrStub> = {}): AgentActionSwrStub {
  return { data: undefined, error: undefined, isLoading: false, mutate: vi.fn(), ...overrides }
}

export interface AgentActionHookOverrides extends Partial<Record<AgentActionReadHookName, Partial<AgentActionSwrStub>>> {
  /** The decide function `useDecideAgentAction()` answers. */
  decide?: Mock
}

/** Every `useAgentActions` hook as a `vi.fn` (reads: the idle SWR answer merged with the override). */
export function agentActionHookMocks(overrides: AgentActionHookOverrides = {}): Record<AgentActionHookName, Mock> {
  const mocks = {} as Record<AgentActionHookName, Mock>
  for (const name of AGENT_ACTION_READ_HOOK_NAMES) {
    const override = overrides[name] ?? {}
    mocks[name] = vi.fn(() => idleAgentActionSwr(override))
  }
  const decide = overrides.decide ?? vi.fn(async (actionId: string) => ({ id: actionId }))
  mocks.useDecideAgentAction = vi.fn(() => decide)
  return mocks
}

const PROJECT = 'proj-1'
const RUN = '0b6f1c9e-2a4d-4c8e-9f1a-5d3b7e2c4a10'
const PIPELINE = '7c2e9a41-5b3d-4f6e-8a1c-2d4b6e8f0a12'

/** One ledger row; every W15 field present (fixtures override what a case needs). */
export function agentActionFixture(overrides: Partial<AgentAction> = {}): AgentAction {
  return {
    id: 'act-q1',
    project_id: PROJECT,
    test_run_id: RUN,
    pipeline_run_id: PIPELINE,
    action_type: 'quarantine.apply',
    target_type: 'test',
    target_id: 'fpA1b2C3d4',
    status: 'pending_review',
    approval_required: true,
    idempotency_key: 'inv:act-q1',
    request_sha256: 'a'.repeat(64),
    request_payload: {},
    approved_by: null,
    approved_at: null,
    execution_started_at: null,
    execution_completed_at: null,
    result_payload: null,
    rollback_payload: null,
    error_code: null,
    created_at: '2026-10-25T09:00:00+00:00',
    canonical_type: 'quarantine.apply',
    title: 'Quarantine checkout-retry',
    rationale: 'Failing in this run and already in the known-flaky set.',
    risk: 'low',
    side_effect: 'internal',
    reversible: true,
    proposing_agent_id: 'investigator',
    proposing_agent_label: 'Investigator',
    proposing_review_state: 'accepted',
    review_id: 'rev-1',
    test_run_build_number: '412',
    test_run_branch: 'main',
    pipeline_workflow_type: 'investigation',
    approved_by_name: null,
    target: { kind: 'test', id: 'fpA1b2C3d4', label: 'checkout-retry', href: `/runs/${RUN}?tab=tests` },
    outcome: 'awaiting_approval',
    outcome_text: "Waiting for a QA lead's approval.",
    approvable: true,
    approve_blocked_reason: null,
    result_link: null,
    rejection_reason_code: null,
    ...overrides,
  }
}

function list(items: AgentAction[]): AgentActionList {
  return { items, count: items.length, total: items.length }
}

/** Waiting: a quarantine on an accepted report, a reassign on a pending report, a decision item. */
export const PENDING_ACTIONS_FIXTURE: AgentActionList = list([
  agentActionFixture(),
  agentActionFixture({
    id: 'act-r1',
    action_type: 'failure.reassign',
    canonical_type: 'failure.reassign',
    target_type: 'test_case',
    target_id: 'tc-a1',
    idempotency_key: 'inv:act-r1',
    title: 'Reassign payment-refund to the payments owner',
    rationale: 'A newly-failed test outside the flaky and infrastructure sets: its suite owner should triage it.',
    risk: 'medium',
    proposing_review_state: 'pending_review',
    target: { kind: 'test_case', id: 'tc-a1', label: 'payment-refund', href: `/runs/${RUN}?tab=tests` },
    approvable: false,
    approve_blocked_reason: 'Accept the AI report first: an approved action runs only on an accepted report.',
  }),
  agentActionFixture({
    id: 'act-d1',
    action_type: 'decision_report_action',
    canonical_type: 'decision.acknowledge',
    target_type: 'human_review',
    target_id: 'release-condition-1',
    idempotency_key: 'dr:release-condition-1',
    title: 'Confirm the payments smoke suite before release',
    rationale: 'Two release conditions are still open.',
    risk: 'unknown',
    side_effect: 'none',
    reversible: false,
    proposing_agent_id: 'agent.decision_report.v1',
    proposing_agent_label: 'Decision report',
    pipeline_workflow_type: 'deep',
    target: { kind: 'release_item', id: 'release-condition-1', label: 'release-condition-1', href: `/runs/${RUN}?tab=evidence` },
  }),
])

/** Recently decided: an executed quarantine, a rejection, an unknown outcome, an undone action. */
export const DECIDED_ACTIONS_FIXTURE: AgentActionList = list([
  agentActionFixture({
    id: 'act-q2',
    idempotency_key: 'inv:act-q2',
    status: 'executed',
    approved_by: 'user-lead',
    approved_by_name: 'Lena Lead',
    approved_at: '2026-10-25T09:05:00+00:00',
    execution_completed_at: '2026-10-25T09:05:30+00:00',
    outcome: 'executed',
    outcome_text: 'Executed · approved by Lena Lead · quarantined.',
    approvable: false,
    result_link: { kind: 'quarantine', id: 'qr-1', label: 'quarantined', href: '/flaky?tab=quarantined' },
  }),
  agentActionFixture({
    id: 'act-r2',
    idempotency_key: 'inv:act-r2',
    action_type: 'failure.reassign',
    canonical_type: 'failure.reassign',
    status: 'rejected',
    approved_by_name: 'Lena Lead',
    outcome: 'rejected',
    outcome_text: 'Rejected by Lena Lead (unsupported_claim).',
    approvable: false,
    rejection_reason_code: 'unsupported_claim',
  }),
  agentActionFixture({
    id: 'act-q3',
    idempotency_key: 'inv:act-q3',
    status: 'failed',
    error_code: 'outcome_unknown',
    approved_by_name: 'Lena Lead',
    outcome: 'outcome_unknown',
    outcome_text:
      'The outcome is unknown: the run stopped while the action was executing. It is not retried — check checkout-retry and act on its own page.',
    approvable: false,
  }),
  agentActionFixture({
    id: 'act-q4',
    idempotency_key: 'inv:act-q4',
    status: 'rolled_back',
    approved_by_name: 'Lena Lead',
    outcome: 'rolled_back',
    outcome_text: 'Undone by Lena Lead.',
    approvable: false,
  }),
])
