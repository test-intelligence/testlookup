/**
 * W15 AGT-01 / AGT-03 §2.1-6: the agent-action copy and rules — the chip tone
 * and label per outcome, the outcome line (server text first, this table
 * otherwise), why Approve is disabled, the confirm / undo questions, the
 * AI-report fact and the links built from ids only.
 */
import { describe, expect, it } from 'vitest'
import {
  APPROVE_BLOCKED,
  CHIP_LABEL,
  OUTCOME_COPY,
  approveBlockedReason,
  canApprove,
  canUndo,
  chipFor,
  confirmSentence,
  outcomeOf,
  outcomeSentence,
  proposerLine,
  REFUSAL_FALLBACK,
  refusalMessage,
  resultHref,
  reviewFact,
  riskWord,
  targetHref,
  undoSentence,
  type AgentActionLike,
} from './agentActionModel'

const row = (over: Partial<AgentActionLike> & Record<string, unknown> = {}): AgentActionLike => ({
  id: 'a1',
  status: 'pending_review',
  canonical_type: 'quarantine.apply',
  title: 'Quarantine checkout-retry',
  side_effect: 'internal',
  reversible: true,
  test_run_id: 'run-1',
  pipeline_run_id: 'pipe-1',
  ...over,
})

describe('chipFor — label and tone per outcome', () => {
  it.each([
    ['awaiting_approval', CHIP_LABEL.waiting, 'info'],
    ['approved_queued', CHIP_LABEL.running, 'info'],
    ['acknowledged', CHIP_LABEL.acknowledged, 'success'],
    ['executed', CHIP_LABEL.executed, 'success'],
    ['rolled_back', CHIP_LABEL.undone, 'muted'],
    ['rejected', CHIP_LABEL.rejected, 'muted'],
    ['superseded', CHIP_LABEL.superseded, 'muted'],
    ['not_executed_review', CHIP_LABEL.notExecuted, 'warning'],
    ['not_executed_policy', CHIP_LABEL.notExecuted, 'warning'],
    ['not_executed_refused', CHIP_LABEL.notExecuted, 'warning'],
    ['no_executor', CHIP_LABEL.notExecuted, 'warning'],
    ['outcome_unknown', CHIP_LABEL.unknown, 'warning'],
    ['failed', CHIP_LABEL.notExecuted, 'warning'],
  ])('%s → %s (%s)', (outcome, label, tone) => {
    expect(chipFor(row({ outcome }))).toEqual({ label, tone })
  })

  it('derives the outcome from the status when an older server sends none', () => {
    expect(outcomeOf(row({ status: 'executing' }))).toBe('approved_queued')
    expect(outcomeOf(row({ status: 'executed', side_effect: 'none' }))).toBe('acknowledged')
    expect(outcomeOf(row({ status: 'executed' }))).toBe('executed')
    expect(outcomeOf(row({ status: 'rejected', error_code: 'superseded' }))).toBe('superseded')
    expect(outcomeOf(row({ status: 'failed', error_code: 'policy_denied' }))).toBe('not_executed_policy')
    expect(outcomeOf(row({ status: 'failed', error_code: 'act_mode_required' }))).toBe('not_executed_policy')
    expect(outcomeOf(row({ status: 'failed', error_code: 'outcome_unknown' }))).toBe('outcome_unknown')
    expect(outcomeOf(row({ status: 'failed', error_code: 'quarantine_disabled' }))).toBe('not_executed_refused')
    expect(outcomeOf(row({ status: 'failed', error_code: 'weird_code' }))).toBe('failed')
  })
})

describe('outcomeSentence — the server text first, this table otherwise', () => {
  it("prefers the server's outcome_text verbatim", () => {
    expect(outcomeSentence(row({ status: 'executed', outcome_text: 'Executed · approved by Ana · quarantined.' })))
      .toBe('Executed · approved by Ana · quarantined.')
  })

  it.each([
    [{ status: 'failed', error_code: 'policy_denied' }, OUTCOME_COPY.notExecutedPolicy],
    [{ status: 'failed', error_code: 'act_mode_required' }, OUTCOME_COPY.notExecutedPolicy],
    [{ status: 'rejected', error_code: 'superseded' }, OUTCOME_COPY.superseded],
    [{ status: 'failed', error_code: 'review_not_accepted' }, OUTCOME_COPY.notExecutedReview],
    [{ status: 'pending_review' }, OUTCOME_COPY.awaitingApproval],
  ])('%o → its sentence', (over, sentence) => {
    expect(outcomeSentence(row(over))).toBe(sentence)
  })

  it('fills the executed / undone / rejected / unknown lines from the row', () => {
    const base = { approved_by_name: 'Ana Lead', result_link: { kind: 'quarantine', label: 'quarantined' } }
    expect(outcomeSentence(row({ ...base, status: 'executed' }))).toBe('Executed · approved by Ana Lead · quarantined.')
    expect(outcomeSentence(row({ ...base, status: 'rolled_back' }))).toBe('Undone by Ana Lead.')
    expect(outcomeSentence(row({ ...base, status: 'rejected', rejection_reason_code: 'unsupported_claim' })))
      .toBe('Rejected by Ana Lead (unsupported_claim).')
    expect(outcomeSentence(row({ status: 'failed', error_code: 'outcome_unknown', target: { label: 'checkout-retry' } })))
      .toBe('The outcome is unknown: the run stopped while the action was executing. It is not retried — check checkout-retry and act on its own page.')
    expect(outcomeSentence(row({ status: 'failed', error_code: 'quarantine_disabled' })))
      .toBe('Not executed: Quarantine is turned off for this project.')
  })
})

describe('Approve — offered only when the server says it would run', () => {
  it('is offered on a pending, registered, approvable row', () => {
    expect(canApprove(row({ approvable: true }))).toBe(true)
    expect(approveBlockedReason(row({ approvable: true }))).toBeNull()
  })

  it("is disabled with the server's sentence", () => {
    const blocked = row({ approvable: false, approve_blocked_reason: APPROVE_BLOCKED.actModeRequired })
    expect(canApprove(blocked)).toBe(false)
    expect(approveBlockedReason(blocked)).toBe(APPROVE_BLOCKED.actModeRequired)
  })

  it('an unregistered type has no executor; a pending report must be accepted first', () => {
    expect(canApprove(row({ canonical_type: null, approvable: true }))).toBe(false)
    expect(approveBlockedReason(row({ canonical_type: null }))).toBe(APPROVE_BLOCKED.noExecutor)
    expect(approveBlockedReason(row({ proposing_review_state: 'pending_review' }))).toBe(APPROVE_BLOCKED.reviewNotAccepted)
  })
})

describe('the confirm and undo questions', () => {
  it('names the title and what approving does', () => {
    expect(confirmSentence(row()).sentence).toBe(
      'Approve "Quarantine checkout-retry"? It runs within a minute and can be undone.',
    )
    expect(confirmSentence(row({ side_effect: 'none', title: 'Ship it' })).sentence).toBe(
      'Approve "Ship it"? It is recorded as acknowledged.',
    )
  })

  it('names what the undo restores, per type; Undo only on executed + reversible', () => {
    expect(undoSentence(row({ status: 'executed' })).sentence).toBe('Undo "Quarantine checkout-retry"? The quarantine is released.')
    expect(undoSentence(row({ canonical_type: 'failure.reassign', title: 'R' })).tail).toBe('The previous assignee is restored.')
    expect(undoSentence(row({ canonical_type: 'defect.promote', title: 'D' })).tail).toBe('The defect is closed as Invalid.')
    expect(canUndo(row({ status: 'executed' }))).toBe(true)
    expect(canUndo(row({ status: 'executed', reversible: false }))).toBe(false)
    expect(canUndo(row({ status: 'rolled_back' }))).toBe(false)
  })
})

describe('the review fact and the links', () => {
  it('states the proposing report and links only an awaiting one to AI reports', () => {
    expect(reviewFact(row({ proposing_review_state: 'accepted' }))).toEqual({ text: 'AI report: accepted', linkToReports: false })
    expect(reviewFact(row({ proposing_review_state: 'pending_review' }))).toEqual({ text: 'AI report: awaiting review', linkToReports: true })
    expect(reviewFact(row({ proposing_review_state: 'rejected' })).text).toBe('AI report: rejected')
    expect(reviewFact(row({ proposing_review_state: 'none' })).text).toBe('no AI report')
  })

  it('takes only an internal path from the server, else builds the run page from ids', () => {
    expect(targetHref(row({ target: { kind: 'test', href: '/flaky?tab=quarantined' } }))).toBe('/flaky?tab=quarantined')
    expect(targetHref(row({ target: { kind: 'test', href: 'javascript:alert(1)' } }))).toBe('/runs/run-1?tab=tests')
    expect(targetHref(row({ target: { kind: 'cluster', href: '//evil.example/x' } }))).toBe('/runs/run-1?tab=analysis')
    expect(targetHref(row({ target: { kind: 'release_item' } }))).toBe('/runs/run-1?tab=evidence')
  })

  it('builds the result link from its kind and ids', () => {
    expect(resultHref(row({ result_link: { kind: 'quarantine', href: 'javascript:x' } }))).toBe('/flaky?tab=quarantined')
    expect(resultHref(row({ result_link: { kind: 'defect', id: 'd 1' } }))).toBe('/defects?defect=d%201')
    expect(resultHref(row({ result_link: { kind: 'assignee', label: 'Bo' } }))).toBe('/runs/run-1?tab=tests')
    expect(resultHref(row({ result_link: null }))).toBeNull()
  })

  it("reads a refusal's sentence from the PATCH's {code, message} detail", () => {
    const err = (detail: unknown) => ({ response: { data: { detail } } })
    expect(refusalMessage(err({ code: 'review_not_accepted', message: APPROVE_BLOCKED.reviewNotAccepted })))
      .toBe(APPROVE_BLOCKED.reviewNotAccepted)
    expect(refusalMessage(err({ code: 'rollback_target_changed' })))
      .toBe('The target changed after the action ran: undo it on its own page.')
    expect(refusalMessage(err('action_not_found'))).toBe('action_not_found')
    expect(refusalMessage(new Error('network'))).toBe(REFUSAL_FALLBACK)
  })

  it('words the risk and the proposer line', () => {
    expect(riskWord('medium')).toBe('medium risk')
    expect(riskWord('unknown')).toBeNull()
    expect(proposerLine('Investigator', '128')).toBe('Investigator · run #128')
    expect(proposerLine(null, null)).toBe('Agent · open run')
  })
})
