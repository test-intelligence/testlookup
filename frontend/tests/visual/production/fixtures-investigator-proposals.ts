/**
 * W15 G-C hermetic world (`docs/deferred-scope-2026-10-13/work/W15-contract.md` §2.2, §4.2): `/deep-investigate`
 * on the shared `DEEP_INVESTIGATION` answers, with ONE completed investigation of the focused run whose verdict
 * carries the Investigator's typed actions (`structured_actions`) and whose detail carries the ledger rows it
 * proposed (`proposals`, newest first):
 *
 *  - `investigationWorld({ mode: 'suggest' })` — three quarantine actions: two waiting for a QA lead's approval,
 *    one executed (Act was turned on and a lead approved it);
 *  - `investigationWorld({ mode: 'shadow' })` — the same actions, nothing proposed (`proposals: []`);
 *  - `proposals` overrides the ledger rows.
 *
 * The answers are PREPENDED to `DEEP_INVESTIGATION` (first match wins): the project's investigation list holds the
 * one investigation, its detail answers the verdict. Every write is left unmocked (fail closed). Fresh objects per
 * call. Not a spec file; `fixtures.ts` is G-A's and is not edited here.
 */
import type { ApiHandlers } from '../../lib/production-pages'
import { DEEP_INVESTIGATION, isoAgo, PROJECT_ID, RUN_ID } from './fixtures'

export const INVESTIGATION_ID = 'c1000000-0000-4000-8000-0000000000c1'

/** Fingerprints are mixed-case hex led by letters (never a phone or card number to a redactor). */
export const OFFENDERS = [
  { fingerprint: 'fpA1b2C3d4', test_name: 'checkout-retry', suite_name: 'checkout' },
  { fingerprint: 'fpB5e6F7a8', test_name: 'payment-refund-sync', suite_name: 'payments' },
  { fingerprint: 'fpC9d0E1f2', test_name: 'cart-session-expiry', suite_name: 'checkout' },
] as const

export const REVIEW_HREF = '/my-failures?tab=approvals&source=actions'

export type InvestigationMode = 'shadow' | 'suggest' | 'act'

function structuredActions(mode: InvestigationMode) {
  return OFFENDERS.map((o) => ({
    action_type: 'quarantine.apply',
    target_type: 'test',
    target_id: o.fingerprint,
    title: `Quarantine ${o.test_name}`,
    payload: {
      proposing_agent_id: 'investigator',
      proposing_agent_mode: mode,
      investigation_id: INVESTIGATION_ID,
      test_run_id: RUN_ID,
      test_fingerprint: o.fingerprint,
      test_name: o.test_name,
      suite_name: o.suite_name,
      title: `Quarantine ${o.test_name}`,
      rationale: 'Failing in this run and already in the known-flaky set.',
      risk: 'low',
      evidence: [{ type: 'flaky_set', id: o.fingerprint }],
    },
  }))
}

function proposal(index: number, over: Record<string, unknown> = {}) {
  const o = OFFENDERS[index]
  return {
    id: `a1000000-0000-4000-8000-00000000000${index + 1}`,
    action_type: 'quarantine.apply',
    canonical_type: 'quarantine.apply',
    target_type: 'test',
    target_id: o.fingerprint,
    status: 'pending_review',
    error_code: null,
    outcome: 'awaiting_approval',
    outcome_text: "Waiting for a QA lead's approval.",
    idempotency_key: `inv:${INVESTIGATION_ID}:quarantine.apply:k${index}`,
    title: `Quarantine ${o.test_name}`,
    created_at: isoAgo(0, 1),
    ...over,
  }
}

/** Two waiting rows and one executed, newest first. */
export function suggestProposals() {
  return [
    proposal(0),
    proposal(1),
    proposal(2, {
      status: 'executed',
      outcome: 'executed',
      outcome_text: 'Executed · approved by QA Lead Two · quarantined.',
    }),
  ]
}

const HYPOTHESES = [
  ['infra', 'Infrastructure', 'invalidated', 30, 'Few broken tests; no infrastructure signature.'],
  ['commit', 'Commit-caused', 'inconclusive', 45, 'Commit differs but the onset is partial.'],
  ['environment', 'Environment', 'invalidated', 60, 'No environment drift vs baseline.'],
  ['known_flaky', 'Known-flaky', 'validated', 86, 'Known flaky tests explain 100% of the failing set (3/3).'],
  ['regression', 'Regression', 'invalidated', 60, 'Every newly-failed test is explained by known-flaky signals.'],
] as const

function detail(mode: InvestigationMode, proposals: unknown[]) {
  return {
    id: INVESTIGATION_ID,
    run_id: RUN_ID,
    project_id: PROJECT_ID,
    status: 'completed',
    mode,
    triggered_by: 'manual',
    started_at: isoAgo(0, 2),
    completed_at: isoAgo(0, 2),
    cancelled_by: null,
    budget: { max_llm_calls: 30, max_tokens: 60000, max_seconds: 300 },
    spend: { llm_calls: 0, tokens: 0, cost_usd: 0, seconds: 4.2 },
    hypotheses: HYPOTHESES.map(([id, title, status, confidence, summary]) => ({
      id, title, status, confidence, confidence_basis: 'heuristic_estimate', summary, evidence: [],
      started_at: isoAgo(0, 2), completed_at: isoAgo(0, 2),
    })),
    verdict: {
      primary_cause: 'known_flaky',
      narrative: 'Primary cause: known_flaky — Known flaky tests explain 100% of the failing set (3/3).',
      confidence: 86,
      recommended_actions: [
        'Propose quarantine for the recurring flaky offenders (Flaky Quarantine workflow).',
        "Review the offenders' flip history in the Flaky Coach before treating this run as a regression.",
      ],
      structured_actions: structuredActions(mode),
    },
    prompt_versions: {},
    model: null,
    proposals,
  }
}

function summary(mode: InvestigationMode) {
  return {
    id: INVESTIGATION_ID,
    run_id: RUN_ID,
    run_build_number: 812,
    status: 'completed',
    mode,
    triggered_by: 'manual',
    primary_cause: 'known_flaky',
    confidence: 86,
    started_at: isoAgo(0, 2),
    completed_at: isoAgo(0, 2),
  }
}

/** `/deep-investigate` with one completed investigation of `RUN_ID` (module doc). */
export function investigationWorld(
  { mode = 'suggest', proposals }: { mode?: InvestigationMode; proposals?: unknown[] } = {},
): { handlers: ApiHandlers } {
  const rows = proposals ?? (mode === 'shadow' ? [] : suggestProposals())
  return {
    handlers: [
      [/^\/api\/v1\/projects\/[^/]+\/investigations$/, () => ({ items: [summary(mode)], total: 1 })],
      [`/api/v1/investigations/${INVESTIGATION_ID}`, () => detail(mode, rows)],
      ...DEEP_INVESTIGATION,
    ],
  }
}
