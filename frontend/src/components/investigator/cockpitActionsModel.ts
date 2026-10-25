/**
 * The cockpit's Proposed actions (W15 AGT-02) — a pure model the component
 * and its tests share, so every sentence lives in ONE place.
 *
 * A verdict's `structured_actions` are typed proposals the Investigator built
 * from run data (never model text). Each one is matched to the investigation
 * detail's `proposals` (the ledger rows, newest first) on its action type +
 * target id, and its row shows that row's state. Shadow files nothing, so a
 * shadow action reads "Shadow mode — not proposed".
 *
 * Everything returned here is rendered as React TEXT; links are built from
 * ids only.
 */
import type { AgentMode, InvestigationProposal, StructuredAction } from '@/types/investigator'

export const PROPOSED_ACTIONS_HEADING = 'Proposed actions'
export const REVIEW_HREF = '/my-failures?tab=approvals&source=actions'
export const REVIEW_LABEL = 'Review'
export const DO_IT_MYSELF_LABEL = 'Do it myself'
export const SHADOW_NOT_PROPOSED = 'Shadow mode — not proposed'
export const NOT_PROPOSED = 'Not proposed'
export const NOT_PROPOSED_TITLE =
  'The investigation did not file this action: it was already done, or it could not be proposed safely.'
export const SUGGEST_MODE_LINE =
  'Suggest mode — these proposals wait in Inbox › Approvals; nothing runs until a person approves it.'
export const ACT_MODE_LINE = 'Act mode — an approved proposal runs, is audited, and can be undone.'

/** The chip's colour family: --ui-info / --ui-success / --ui-warning, or muted text. */
export type CockpitTone = 'info' | 'success' | 'warning' | 'muted'

export interface CockpitActionState {
  label: string
  tone: CockpitTone
  /** The server's outcome sentence (or the not-proposed reason), shown as the chip's title. */
  title?: string
  /** A ledger row waiting for a QA lead's decision. */
  pending: boolean
}

const OUTCOME_STATE: Record<string, { label: string; tone: CockpitTone }> = {
  awaiting_approval: { label: 'Waiting for approval', tone: 'info' },
  approved_queued: { label: 'Approved · running', tone: 'info' },
  acknowledged: { label: 'Acknowledged', tone: 'success' },
  executed: { label: 'Executed', tone: 'success' },
  rolled_back: { label: 'Undone', tone: 'muted' },
  rejected: { label: 'Rejected', tone: 'muted' },
  superseded: { label: 'Replaced by a newer proposal', tone: 'muted' },
  not_executed_review: { label: 'Not executed', tone: 'warning' },
  not_executed_policy: { label: 'Not executed', tone: 'warning' },
  not_executed_refused: { label: 'Not executed', tone: 'warning' },
  no_executor: { label: 'Not executed', tone: 'warning' },
  failed: { label: 'Not executed', tone: 'warning' },
  outcome_unknown: { label: 'Outcome unknown', tone: 'warning' },
}

/** The ledger statuses an old server answers without an `outcome`. */
const STATUS_OUTCOME: Record<string, string> = {
  pending_review: 'awaiting_approval',
  proposed: 'awaiting_approval',
  approved: 'approved_queued',
  executing: 'approved_queued',
  executed: 'executed',
  rolled_back: 'rolled_back',
  rejected: 'rejected',
  failed: 'failed',
}

function canonicalOf(proposal: InvestigationProposal): string {
  return proposal.canonical_type || proposal.action_type
}

/** The newest ledger row for this action (proposals arrive newest first), or null. */
export function matchProposal(
  action: StructuredAction,
  proposals: readonly InvestigationProposal[] | undefined,
): InvestigationProposal | null {
  for (const proposal of proposals ?? []) {
    if (proposal.target_id === action.target_id && canonicalOf(proposal) === action.action_type) return proposal
  }
  return null
}

function outcomeKey(proposal: InvestigationProposal): string {
  if (proposal.status === 'rejected' && proposal.error_code === 'superseded') return 'superseded'
  if (proposal.outcome && proposal.outcome !== 'unknown' && proposal.outcome in OUTCOME_STATE) return proposal.outcome
  return STATUS_OUTCOME[proposal.status] ?? 'unknown'
}

/** The row's state: from the matched ledger row, else what the mode explains. */
export function stateLabel(proposal: InvestigationProposal | null, mode: AgentMode | string): CockpitActionState {
  if (!proposal) {
    return mode === 'shadow'
      ? { label: SHADOW_NOT_PROPOSED, tone: 'muted', pending: false }
      : { label: NOT_PROPOSED, tone: 'muted', title: NOT_PROPOSED_TITLE, pending: false }
  }
  const key = outcomeKey(proposal)
  const known = OUTCOME_STATE[key]
  return {
    label: known?.label ?? proposal.status,
    tone: known?.tone ?? 'muted',
    title: proposal.outcome_text || undefined,
    pending: key === 'awaiting_approval',
  }
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

/** What the action acts on, as text: the test / suite / cluster it names. */
export function targetLabel(action: StructuredAction): string {
  const p = action.payload ?? {}
  if (action.action_type === 'quarantine.apply') {
    const suite = text(p.suite_name)
    return suite ? `test in ${suite}` : 'test'
  }
  if (action.action_type === 'failure.reassign') {
    const suite = text(p.suite_name)
    return suite ? `failure in ${suite}` : 'failure'
  }
  if (action.action_type === 'defect.promote') return `cluster ${text(p.cluster_label) ?? action.target_id}`
  return action.target_type
}

export interface QuarantineFormPrefill {
  test_fingerprint: string
  test_name: string
  suite_name: string | null
}

export type DoItMyself =
  | { kind: 'form'; prefill: QuarantineFormPrefill }
  | { kind: 'link'; label: string; href: string }

/** How a QA lead does the action by hand: the EXISTING in-page quarantine
 *  form, or a link to the page that already does the job. Never a modal. */
export function doItMyself(action: StructuredAction, runId: string): DoItMyself | null {
  const p = action.payload ?? {}
  const run = encodeURIComponent(runId)
  if (action.action_type === 'quarantine.apply') {
    const fingerprint = text(p.test_fingerprint) ?? action.target_id
    return {
      kind: 'form',
      prefill: { test_fingerprint: fingerprint, test_name: text(p.test_name) ?? fingerprint, suite_name: text(p.suite_name) },
    }
  }
  if (action.action_type === 'failure.reassign') return { kind: 'link', label: 'Open the failure', href: `/runs/${run}?tab=tests` }
  if (action.action_type === 'defect.promote') return { kind: 'link', label: 'Open the clusters', href: `/runs/${run}?tab=analysis` }
  return null
}

export interface CockpitActionRow {
  /** Stable per row (the in-page slot key): type + target. */
  key: string
  action: StructuredAction
  proposal: InvestigationProposal | null
  state: CockpitActionState
  target: string
  /** QA lead+ and a pending ledger row: the Inbox decides it. */
  canReview: boolean
  /** QA lead+ and a pending or shadow action. */
  doItMyself: DoItMyself | null
}

/** One row per structured action, in the verdict's order. `null` when the
 *  block must not render (no typed actions, or an old detail without `proposals`). */
export function cockpitActionRows(
  actions: readonly StructuredAction[] | undefined,
  proposals: readonly InvestigationProposal[] | undefined,
  { mode, runId, isQaLead }: { mode: AgentMode | string; runId: string; isQaLead: boolean },
): CockpitActionRow[] | null {
  if (!actions || actions.length === 0 || !Array.isArray(proposals)) return null
  return actions.map((action) => {
    const proposal = matchProposal(action, proposals)
    const state = stateLabel(proposal, mode)
    const shadowAction = mode === 'shadow' && proposal === null
    return {
      key: `${action.action_type}:${action.target_id}`,
      action,
      proposal,
      state,
      target: targetLabel(action),
      canReview: isQaLead && state.pending,
      doItMyself: isQaLead && (state.pending || shadowAction) ? doItMyself(action, runId) : null,
    }
  })
}
