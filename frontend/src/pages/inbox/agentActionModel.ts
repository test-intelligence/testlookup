/**
 * Inbox › Approvals › Agent actions (W15 AGT-01 / AGT-03): the plain-language
 * copy and the pure rules behind every agent-action row — the status chip, the
 * outcome line, why Approve is disabled, the confirm / undo questions and the
 * AI-report fact. The server writes `outcome_text` and `approve_blocked_reason`
 * (`services/agent_action_types.py`); the sentences here mirror its table so
 * an older server (no `outcome` / `outcome_text`) still reads honestly, and the
 * tests import ONE string from here.
 *
 * Every value read here is the server's TEXT (titles, labels, codes); nothing
 * becomes HTML, a style or an href except the internal paths built in
 * `targetHref` / `resultHref` from ids.
 */

/** The fields of an agent action this model reads (`types/agentActions.ts` AgentAction satisfies it). */
export interface AgentActionLike {
  id?: string
  status: string
  action_type?: string
  canonical_type?: string | null
  title?: string | null
  outcome?: string | null
  outcome_text?: string | null
  error_code?: string | null
  side_effect?: string | null
  reversible?: boolean | null
  approvable?: boolean | null
  approve_blocked_reason?: string | null
  proposing_review_state?: string | null
  pipeline_run_id?: string | null
  test_run_id?: string | null
  target_id?: string | null
  target?: { kind?: string | null; id?: string | null; label?: string | null; href?: string | null } | null
  result_link?: { kind?: string | null; id?: string | null; label?: string | null; href?: string | null } | null
}

// ── The status chip ────────────────────────────────────────────────────────
export type ChipTone = 'info' | 'success' | 'warning' | 'muted'
export interface ActionChip {
  label: string
  tone: ChipTone
}

export const CHIP_LABEL = {
  waiting: 'Waiting',
  running: 'Running…',
  acknowledged: 'Acknowledged',
  executed: 'Executed',
  undone: 'Undone',
  rejected: 'Rejected',
  superseded: 'Replaced',
  notExecuted: 'Not executed',
  unknown: 'Outcome unknown',
} as const

/** The token family per tone (§4.2): info waiting, success done, warning not executed, muted decided-away. */
export const CHIP_TONE_CLASS: Record<ChipTone, string> = {
  info: 'border-[var(--ui-info-bd)] bg-[var(--ui-info-bg)] text-[var(--ui-info-ink)]',
  success: 'border-[var(--ui-success-bd)] bg-[var(--ui-success-bg)] text-[var(--ui-success-ink)]',
  warning: 'border-[var(--ui-warning-bd)] bg-[var(--ui-warning-bg)] text-[var(--ui-warning-ink)]',
  muted: 'border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]',
}

const OUTCOME_CHIP: Record<string, ActionChip> = {
  awaiting_approval: { label: CHIP_LABEL.waiting, tone: 'info' },
  approved_queued: { label: CHIP_LABEL.running, tone: 'info' },
  acknowledged: { label: CHIP_LABEL.acknowledged, tone: 'success' },
  executed: { label: CHIP_LABEL.executed, tone: 'success' },
  rolled_back: { label: CHIP_LABEL.undone, tone: 'muted' },
  rejected: { label: CHIP_LABEL.rejected, tone: 'muted' },
  superseded: { label: CHIP_LABEL.superseded, tone: 'muted' },
  not_executed_review: { label: CHIP_LABEL.notExecuted, tone: 'warning' },
  not_executed_policy: { label: CHIP_LABEL.notExecuted, tone: 'warning' },
  not_executed_refused: { label: CHIP_LABEL.notExecuted, tone: 'warning' },
  no_executor: { label: CHIP_LABEL.notExecuted, tone: 'warning' },
  outcome_unknown: { label: CHIP_LABEL.unknown, tone: 'warning' },
  failed: { label: CHIP_LABEL.notExecuted, tone: 'warning' },
}

/**
 * The OUTCOME_CODES member for a row: the server's `outcome` when it sent a
 * known one, else derived from the status and error code (an older server).
 */
export function outcomeOf(action: AgentActionLike): string {
  if (action.outcome && action.outcome in OUTCOME_CHIP) return action.outcome
  const code = action.error_code ?? ''
  switch (action.status) {
    case 'pending_review':
    case 'proposed':
      return 'awaiting_approval'
    case 'approved':
    case 'executing':
      return 'approved_queued'
    case 'executed':
      return action.side_effect === 'none' ? 'acknowledged' : 'executed'
    case 'rolled_back':
      return 'rolled_back'
    case 'rejected':
      return code === 'superseded' ? 'superseded' : 'rejected'
    case 'failed':
      if (code === 'outcome_unknown') return 'outcome_unknown'
      if (code === 'review_not_accepted') return 'not_executed_review'
      if (code === 'policy_denied' || code === 'act_mode_required' || code === 'action_type_not_allowed') {
        return 'not_executed_policy'
      }
      if (code === 'action_executor_not_configured') return 'no_executor'
      return code in ACTION_ERROR_SENTENCES ? 'not_executed_refused' : 'failed'
    default:
      return 'unknown'
  }
}

/** The chip for a row (Inbox and the Decision panel); an unknown status reads as muted text of the status. */
export function chipFor(action: AgentActionLike): ActionChip {
  return OUTCOME_CHIP[outcomeOf(action)] ?? { label: action.status || 'Unknown', tone: 'muted' }
}

// ── The outcome line (OUTCOME_SENTENCES, mirrored) ─────────────────────────
/** The refusal sentences the outcome line names (ACTION_ERROR_SENTENCES, the ones a person meets). */
export const ACTION_ERROR_SENTENCES: Record<string, string> = {
  proposer_not_permitted: 'This agent may not propose this action type.',
  payload_tampered: 'The stored proposal no longer matches its hash.',
  payload_invalid: 'The stored proposal is not a valid proposal of its type.',
  approver_not_authorized: 'The approver is no longer a QA lead of this project.',
  target_not_in_project: 'The target does not belong to this project.',
  quarantine_disabled: 'Quarantine is turned off for this project.',
  target_already_quarantined: 'The test is already quarantined.',
  test_case_not_failing: 'The test is no longer failing.',
  assignee_no_longer_owner: 'The proposed assignee is no longer the suite owner.',
  already_assigned: 'The failure is already assigned to the suite owner.',
  cluster_not_found: 'The failure cluster no longer exists.',
  defect_already_exists: 'An open defect already covers this cluster.',
  offline_mode_blocked: 'Offline mode blocks actions outside TestLookup.',
  execution_error: 'The action failed while it ran.',
  rollback_target_changed: 'The target changed after the action ran: undo it on its own page.',
}

export const OUTCOME_COPY = {
  awaitingApproval: "Waiting for a QA lead's approval.",
  running: 'Approved by {name} · running…',
  acknowledged: 'Acknowledged by {name}.',
  executed: 'Executed · approved by {name} · {result}.',
  undone: 'Undone by {name}.',
  rejected: 'Rejected by {name} ({reason_code}).',
  superseded: 'Replaced by a newer proposal for the same target.',
  notExecutedReview: 'Not executed: the AI report was not accepted.',
  notExecutedPolicy: 'Not executed: this action type does not run in Act mode in this project.',
  noExecutor: 'Approved and recorded — this action type has no executor.',
  outcomeUnknown:
    'The outcome is unknown: the run stopped while the action was executing. It is not retried — check {target} and act on its own page.',
  failed: 'Not executed: the action failed ({error_code}).',
} as const

function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => values[key] ?? '')
}

/**
 * The row's outcome sentence: the server's `outcome_text` when it sent one
 * (it knows the decider's name and the result), else this table's copy with
 * what the row carries.
 */
export function outcomeSentence(
  action: AgentActionLike & { approved_by_name?: string | null; rejection_reason_code?: string | null },
): string {
  if (action.outcome_text && action.outcome_text.trim()) return action.outcome_text
  const values = {
    name: action.approved_by_name?.trim() || 'a QA lead',
    result: action.result_link?.label?.trim() || 'done',
    reason_code: action.rejection_reason_code?.trim() || 'no reason given',
    target: action.target?.label?.trim() || 'the target',
    error_code: action.error_code ?? 'unknown',
  }
  switch (outcomeOf(action)) {
    case 'awaiting_approval': return OUTCOME_COPY.awaitingApproval
    case 'approved_queued': return fill(OUTCOME_COPY.running, values)
    case 'acknowledged': return fill(OUTCOME_COPY.acknowledged, values)
    case 'executed': return fill(OUTCOME_COPY.executed, values)
    case 'rolled_back': return fill(OUTCOME_COPY.undone, values)
    case 'rejected': return fill(OUTCOME_COPY.rejected, values)
    case 'superseded': return OUTCOME_COPY.superseded
    case 'not_executed_review': return OUTCOME_COPY.notExecutedReview
    case 'not_executed_policy': return OUTCOME_COPY.notExecutedPolicy
    case 'no_executor': return OUTCOME_COPY.noExecutor
    case 'outcome_unknown': return fill(OUTCOME_COPY.outcomeUnknown, values)
    case 'not_executed_refused': return `Not executed: ${ACTION_ERROR_SENTENCES[action.error_code ?? '']}`
    default: return fill(OUTCOME_COPY.failed, values)
  }
}

// ── Why Approve is disabled ────────────────────────────────────────────────
export const APPROVE_BLOCKED = {
  reviewNotAccepted: 'Accept the AI report first: an approved action runs only on an accepted report.',
  actModeRequired:
    'This action type does not run in this project: a QA lead turns it on under Settings › AI agents › Investigator › Act, or do it yourself.',
  separationOfDuties: 'The person who requested this investigation cannot approve the changes it proposes.',
  noExecutor: 'This action type has no executor.',
  notPending: 'This action is already decided.',
  generic: 'This action cannot be approved yet.',
} as const

/** Approve is offered only on a pending, registered row the server marked approvable (never a click that 409s). */
export function canApprove(action: AgentActionLike): boolean {
  return (
    (action.status === 'pending_review' || action.status === 'proposed') &&
    Boolean(action.canonical_type) &&
    action.approvable === true
  )
}

/** The `title` of a disabled Approve: the server's sentence, else the honest reason this row shows. */
export function approveBlockedReason(action: AgentActionLike): string | null {
  if (canApprove(action)) return null
  if (action.approve_blocked_reason && action.approve_blocked_reason.trim()) return action.approve_blocked_reason
  if (action.status !== 'pending_review' && action.status !== 'proposed') return APPROVE_BLOCKED.notPending
  if (!action.canonical_type) return APPROVE_BLOCKED.noExecutor
  if (action.pipeline_run_id && action.proposing_review_state && action.proposing_review_state !== 'accepted') {
    return APPROVE_BLOCKED.reviewNotAccepted
  }
  return APPROVE_BLOCKED.generic
}

// ── The confirm and undo questions ─────────────────────────────────────────
/** A question in three parts: the component bolds `title`; `sentence` is the whole text (what the tests read). */
export interface Question {
  lead: string
  title: string
  tail: string
  sentence: string
}

function question(lead: string, title: string, tail: string): Question {
  return { lead, title, tail, sentence: `${lead} "${title}"? ${tail}` }
}

/** The row's display title (text): its own title, else its raw type. */
export function titleOf(action: AgentActionLike): string {
  return action.title?.trim() || action.canonical_type || action.action_type || 'Agent action'
}

export const CONFIRM_TAIL = {
  acknowledge: 'It is recorded as acknowledged.',
  effect: 'It runs within a minute and can be undone.',
} as const

export const UNDO_TAIL: Record<string, string> = {
  'quarantine.apply': 'The quarantine is released.',
  'failure.reassign': 'The previous assignee is restored.',
  'defect.promote': 'The defect is closed as Invalid.',
}

/** `Approve "{title}"? It is recorded as acknowledged. | It runs within a minute and can be undone.` */
export function confirmSentence(action: AgentActionLike): Question {
  const tail = action.side_effect === 'none' ? CONFIRM_TAIL.acknowledge : CONFIRM_TAIL.effect
  return question('Approve', titleOf(action), tail)
}

/** `Undo "{title}"? {what the undo does}` — only asked for an executed, reversible row. */
export function undoSentence(action: AgentActionLike): Question {
  return question('Undo', titleOf(action), UNDO_TAIL[action.canonical_type ?? ''] ?? 'Its effect is reversed.')
}

/** Undo is offered on an executed row of a reversible type only. */
export function canUndo(action: AgentActionLike): boolean {
  return action.status === 'executed' && action.reversible === true && Boolean(action.canonical_type)
}

// ── The AI-report fact and the row's links ─────────────────────────────────
export const REVIEW_FACT: Record<string, string> = {
  accepted: 'AI report: accepted',
  pending_review: 'AI report: awaiting review',
  rejected: 'AI report: rejected',
  superseded: 'AI report: superseded',
  none: 'no AI report',
}

/** The proposing run's review, as one fact; `linkToReports` when a person can act on it under AI reports. */
export function reviewFact(action: AgentActionLike): { text: string; linkToReports: boolean } {
  const state = action.proposing_review_state || 'none'
  return { text: REVIEW_FACT[state] ?? REVIEW_FACT.none, linkToReports: state === 'pending_review' }
}

export const REPORTS_QUEUE_HREF = '/my-failures?tab=approvals&source=reports'
export const ACTIONS_QUEUE_HREF = '/my-failures?tab=approvals&source=actions'

/** An internal path only: one leading slash, no scheme, no backslash, no whitespace. */
function internalPath(href: string | null | undefined): string | null {
  return typeof href === 'string' && /^\/(?![/\\])[^\s\\]*$/.test(href) ? href : null
}

function runHref(runId: string | null | undefined, tab?: string): string | null {
  if (!runId) return null
  return `/runs/${encodeURIComponent(runId)}${tab ? `?tab=${tab}` : ''}`
}

/** Where the row's target is shown: the server's internal path, else the run's page built from ids. */
export function targetHref(action: AgentActionLike): string | null {
  const fromServer = internalPath(action.target?.href)
  if (fromServer) return fromServer
  const kind = action.target?.kind
  if (kind === 'cluster') return runHref(action.test_run_id, 'analysis')
  if (kind === 'test' || kind === 'test_case') return runHref(action.test_run_id, 'tests')
  return runHref(action.test_run_id, 'evidence')
}

/** Where an executed action's effect is seen (§2.3): built from the link's kind and ids. */
export function resultHref(action: AgentActionLike): string | null {
  const link = action.result_link
  if (!link) return null
  if (link.kind === 'quarantine') return '/flaky?tab=quarantined'
  if (link.kind === 'defect' && link.id) return `/defects?defect=${encodeURIComponent(link.id)}`
  if (link.kind === 'assignee') return runHref(action.test_run_id, 'tests')
  return internalPath(link.href)
}

/** The risk word (`low risk`), or null for an unknown / missing risk. */
export function riskWord(risk: string | null | undefined): string | null {
  return risk === 'low' || risk === 'medium' || risk === 'high' ? `${risk} risk` : null
}

/** `Investigator · run #128` (the proposer's label, else its id). */
export function proposerLine(proposer: string | null | undefined, buildNumber: string | null | undefined): string {
  const who = proposer?.trim() || 'Agent'
  return buildNumber ? `${who} · run #${buildNumber}` : `${who} · open run`
}

// ── The section's own lines ────────────────────────────────────────────────
export const SECTION_COPY = {
  emptyWaiting: 'No agent actions are waiting.',
  shadowNote: 'The Investigator is in Shadow mode, so it proposes nothing. Settings › AI agents.',
  loadError: 'Could not load the agent actions.',
  recentlyDecided: 'Recently decided',
  rejectTitle: 'Reject agent action',
  rejectReason: 'Reason',
  rejectNotes: 'Notes (optional)',
  settingsLink: 'Open AI agents settings',
} as const

/** The longest note a rejection carries (the server stores it redacted, never returns it). */
export const REJECT_NOTES_MAX = 2000

export const REFUSAL_FALLBACK = 'The decision was not saved. Reload the queue and try again.'

/**
 * The sentence a refused decision shows in its row (the PATCH answers
 * `detail: {code, message}`; the request suppresses the global toast, so this
 * is the only place it is said). Text only.
 */
export function refusalMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  if (detail && typeof detail === 'object') {
    const { message, code } = detail as { message?: unknown; code?: unknown }
    if (typeof message === 'string' && message.trim()) return message
    if (typeof code === 'string' && ACTION_ERROR_SENTENCES[code]) return ACTION_ERROR_SENTENCES[code]
  }
  if (typeof detail === 'string' && detail.trim()) return ACTION_ERROR_SENTENCES[detail] ?? detail
  return REFUSAL_FALLBACK
}
