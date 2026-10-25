/**
 * Inbox › Approvals › Agent actions (W15 AGT-01 / AGT-03, D16): what an agent
 * proposed in one project, waiting for a QA lead's decision, and what was
 * recently decided.
 *
 *  - **Waiting**: one row per pending action — its title, target, proposer and
 *    run, risk, rationale and the proposing AI report's state. **Approve** is
 *    offered only when the server says the action would run (`approvable`);
 *    otherwise it is disabled with the reason in its title (never a click that
 *    409s). Approve asks in the row (`InlineConfirm`); **Reject** opens an
 *    in-page form in the row's slot (a closed reason code + optional notes).
 *  - **Recently decided**: the outcome of each decision; executing rows poll
 *    (`useDecidedAgentActions`) until none runs; an executed, reversible row
 *    links its result and offers **Undo** (asked in the row). An unknown
 *    outcome is said as such, with the target's link and no retry control.
 *
 * QA leads only (the list is QA_LEAD on the server): for anyone else nothing
 * is requested and nothing renders. Titles, rationales, labels and server
 * sentences are React text; links are internal paths built from ids. A
 * refused decision shows the server's sentence in its row (the request
 * suppresses the global toast). No modal, no portal, no `confirm(`.
 */
import { Fragment, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Bot } from 'lucide-react'
import { useSWRConfig } from 'swr'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { useInlineSlot, type InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useDecidedAgentActions, useDecideAgentAction, usePendingAgentActions } from '@/hooks/useAgentActions'
import { usePermissions } from '@/hooks/usePermissions'
import type { AgentAction, AgentActionDecision } from '@/types/agentActions'
import type { AgentPolicyListResponse } from '@/types/investigator'
import { REVIEW_REASON_CODES, REVIEW_REASON_LABEL } from '@/types/review'
import {
  CHIP_TONE_CLASS,
  REJECT_NOTES_MAX,
  REPORTS_QUEUE_HREF,
  SECTION_COPY,
  approveBlockedReason,
  canApprove,
  canUndo,
  chipFor,
  confirmSentence,
  outcomeOf,
  outcomeSentence,
  proposerLine,
  refusalMessage,
  resultHref,
  reviewFact,
  riskWord,
  targetHref,
  titleOf,
  undoSentence,
  type AgentActionLike,
  type Question,
} from './agentActionModel'

const LINK = 'text-[var(--color-accent)] hover:underline'

/** The status chip (Inbox and the Decision panel read the same tone table). */
export function ActionStatusChip({ action, title, className = '' }: { action: AgentActionLike; title?: string; className?: string }) {
  const chip = chipFor(action)
  return (
    <span
      data-agent-action-chip={chip.tone}
      title={title}
      className={`inline-flex flex-shrink-0 items-center rounded-full border px-1.5 py-px text-2xs font-semibold ${CHIP_TONE_CLASS[chip.tone]} ${className}`}
    >
      {chip.label}
    </span>
  )
}

/** A question whose title is bold: `Approve "<strong>title</strong>"? tail` (text only). */
function QuestionText({ q }: { q: Question }) {
  return (
    <>
      {q.lead} &quot;<strong>{q.title}</strong>&quot;? {q.tail}
    </>
  )
}

/** A refused decision's sentence, in its row. */
function Refusal({ message }: { message: string }) {
  return (
    <p role="alert" data-agent-action-refusal="" className="m-0 text-xs text-[var(--ui-warning-ink)]">
      {message}
    </p>
  )
}

/** `Investigator · run #128` linked to the run, the target and the AI-report fact. */
function RowFacts({ action }: { action: AgentAction }) {
  const target = action.target?.label?.trim() || action.target_id || null
  const targetLink = targetHref(action)
  const fact = reviewFact(action)
  const proposer = proposerLine(action.proposing_agent_label ?? action.proposing_agent_id, action.test_run_build_number)
  const runLink = action.test_run_id ? `/runs/${encodeURIComponent(action.test_run_id)}` : null
  const parts: ReactNode[] = []
  if (target) {
    parts.push(
      targetLink ? (
        <Link key="target" to={targetLink} className={`max-w-[40ch] truncate ${LINK}`} title={target}>{target}</Link>
      ) : (
        <span key="target" className="max-w-[40ch] truncate" title={target}>{target}</span>
      ),
    )
  }
  parts.push(runLink ? <Link key="run" to={runLink} className={LINK}>{proposer}</Link> : <span key="run">{proposer}</span>)
  parts.push(
    fact.linkToReports ? (
      <Link key="review" to={REPORTS_QUEUE_HREF} className={LINK} data-agent-action-review="">{fact.text}</Link>
    ) : (
      <span key="review" data-agent-action-review="">{fact.text}</span>
    ),
  )
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-[var(--color-text-muted)]">
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 && <span aria-hidden>·</span>}
          {part}
        </Fragment>
      ))}
    </div>
  )
}

/** Reject's in-page form: a reason from the reviews' closed list (required) and optional notes. */
function RejectForm({
  action,
  slot,
  busy,
  refusal,
  onCancel,
  onReject,
}: {
  action: AgentAction
  slot: InlineSlot
  busy: boolean
  refusal: string | null
  onCancel: () => void
  onReject: (reasonCode: string, notes: string) => void
}) {
  const [reason, setReason] = useState('')
  const [notes, setNotes] = useState('')
  return (
    <InlineFormSection
      title={SECTION_COPY.rejectTitle}
      headingLevel={3}
      description={<>&quot;{titleOf(action)}&quot; leaves the queue and is not run.</>}
      slot={slot}
      canClose={!busy}
      busy={busy}
      onClose={onCancel}
      testId="agent-action-reject-form"
      footer={
        <>
          <button type="button" className="btn-secondary text-xs" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            data-testid="agent-action-reject-confirm"
            className="btn-primary inline-flex items-center gap-1.5 text-xs"
            disabled={busy || !reason}
            onClick={() => onReject(reason, notes)}
          >
            {busy && <LoadingSpinner size="sm" />}
            Reject
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <label className="block text-xs text-[var(--color-text-secondary)]">
          {SECTION_COPY.rejectReason}
          <select
            aria-label={SECTION_COPY.rejectReason}
            required
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="input mt-1 block w-[200px] py-1.5"
          >
            <option value="">Choose a reason…</option>
            {REVIEW_REASON_CODES.map((code) => (
              <option key={code} value={code}>{REVIEW_REASON_LABEL[code]}</option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-[var(--color-text-secondary)]">
          {SECTION_COPY.rejectNotes}
          <textarea
            aria-label={SECTION_COPY.rejectNotes}
            maxLength={REJECT_NOTES_MAX}
            rows={2}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="input mt-1 block py-1.5"
          />
        </label>
        {refusal && <Refusal message={refusal} />}
      </div>
    </InlineFormSection>
  )
}

type Open = { id: string; kind: 'approve' | 'reject' | 'undo' }

/** The Investigator's policy, ONLY when the settings page already cached it (no request from here). */
function useCachedInvestigatorMode(projectId: string): string | null {
  const { cache } = useSWRConfig()
  const cached = cache.get(`/projects/${projectId}/agent-configs/investigator`)?.data as AgentPolicyListResponse | undefined
  return cached?.policies?.find((p) => p.agent_id === 'investigator')?.mode ?? null
}

export default function AgentActionApprovals({ projectId }: { projectId: string }) {
  const { isQaLead } = usePermissions()
  const pending = usePendingAgentActions(projectId, isQaLead)
  const decided = useDecidedAgentActions(projectId, isQaLead)
  const decide = useDecideAgentAction(projectId)
  const investigatorMode = useCachedInvestigatorMode(projectId)
  const slot = useInlineSlot()
  const [open, setOpen] = useState<Open | null>(null)
  const [busy, setBusy] = useState<Open | null>(null)
  const [refusal, setRefusal] = useState<{ id: string; message: string } | null>(null)
  // A decided row leaves the waiting list at once; the refetch then agrees.
  const [left, setLeft] = useState<ReadonlySet<string>>(() => new Set())

  if (!isQaLead) return null

  const waiting = (pending.data?.items ?? []).filter((a) => !left.has(a.id))
  const recent = (decided.data?.items ?? []).slice(0, 20)
  const refusalFor = (id: string) => (refusal?.id === id ? refusal.message : null)

  const openHere = (next: Open) => {
    if (open?.id === next.id && open.kind === next.kind) return
    slot.guard(() => {
      setRefusal(null)
      setOpen(next)
    })
  }
  const close = () => {
    setOpen(null)
    setRefusal(null)
  }

  async function send(action: AgentAction, kind: Open['kind'], body: AgentActionDecision) {
    setBusy({ id: action.id, kind })
    setRefusal(null)
    try {
      await decide(action.id, body)
      if (kind !== 'undo') setLeft((current) => new Set(current).add(action.id))
      // A late answer closes only its own question.
      setOpen((current) => (current?.id === action.id ? null : current))
    } catch (error) {
      setRefusal({ id: action.id, message: refusalMessage(error) })
    } finally {
      setBusy(null)
    }
  }

  const reject = (action: AgentAction, reasonCode: string, notes: string) =>
    send(action, 'reject', { status: 'rejected', reason_code: reasonCode, ...(notes.trim() ? { notes: notes.trim() } : {}) })

  return (
    <div className="space-y-4" data-agent-actions="">
      {Boolean(pending.error) && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border border-[var(--ui-warning-bd)] bg-[var(--ui-warning-bg)] p-3 text-sm text-[var(--ui-warning-ink)]"
        >
          <span>{SECTION_COPY.loadError}</span>
          <button type="button" className="btn-secondary flex-shrink-0 text-xs" onClick={() => void pending.mutate()}>
            Retry
          </button>
        </div>
      )}

      {pending.isLoading && waiting.length === 0 ? (
        <div className="flex justify-center py-8"><LoadingSpinner size="lg" /></div>
      ) : waiting.length === 0 && !pending.error ? (
        <EmptyState
          icon={<Bot className="h-10 w-10" />}
          title={SECTION_COPY.emptyWaiting}
          description={investigatorMode === 'shadow' ? SECTION_COPY.shadowNote : undefined}
        />
      ) : waiting.length > 0 ? (
        <ul className="divide-y divide-[var(--color-border)] rounded-md border border-[var(--color-border)]" aria-label="Agent actions waiting">
          {waiting.map((action) => {
            const here = open?.id === action.id ? open.kind : null
            return (
              <Fragment key={action.id}>
                <WaitingRow
                  action={action}
                  busy={busy?.id === action.id && busy.kind === 'approve'}
                  locked={busy !== null}
                  onApprove={() => openHere({ id: action.id, kind: 'approve' })}
                  onReject={() => openHere({ id: action.id, kind: 'reject' })}
                />
                {here === 'approve' && (
                  <li data-in-page-row="" className="space-y-2 px-3 pb-3">
                    <InlineConfirm
                      testId="agent-action-approve-confirm"
                      message={<QuestionText q={confirmSentence(action)} />}
                      confirmLabel="Approve"
                      busy={busy?.id === action.id}
                      onCancel={close}
                      onConfirm={() => void send(action, 'approve', { status: 'approved' })}
                    />
                    {refusalFor(action.id) && <Refusal message={refusalFor(action.id) as string} />}
                  </li>
                )}
                {here === 'reject' && (
                  <li data-in-page-row="" className="px-3 pb-3">
                    <RejectForm
                      action={action}
                      slot={slot}
                      busy={busy?.id === action.id}
                      refusal={refusalFor(action.id)}
                      onCancel={close}
                      onReject={(reasonCode, notes) => void reject(action, reasonCode, notes)}
                    />
                  </li>
                )}
              </Fragment>
            )
          })}
        </ul>
      ) : null}

      {recent.length > 0 && (
        <div className="space-y-1.5" data-agent-actions-decided="">
          <h3 className="m-0 text-xs font-semibold text-[var(--color-text-muted)]">{SECTION_COPY.recentlyDecided}</h3>
          <ul className="divide-y divide-[var(--color-border)] rounded-md border border-[var(--color-border)]" aria-label={SECTION_COPY.recentlyDecided}>
            {recent.map((action) => (
              <Fragment key={action.id}>
                <DecidedRow
                  action={action}
                  locked={busy !== null}
                  onUndo={() => openHere({ id: action.id, kind: 'undo' })}
                />
                {open?.id === action.id && open.kind === 'undo' && (
                  <li data-in-page-row="" className="space-y-2 px-3 pb-3">
                    <InlineConfirm
                      testId="agent-action-undo-confirm"
                      message={<QuestionText q={undoSentence(action)} />}
                      confirmLabel="Undo"
                      busy={busy?.id === action.id}
                      onCancel={close}
                      onConfirm={() => void send(action, 'undo', { status: 'rolled_back' })}
                    />
                    {refusalFor(action.id) && <Refusal message={refusalFor(action.id) as string} />}
                  </li>
                )}
              </Fragment>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/** One pending action: what, where, who proposed it and why; Approve / Reject. */
function WaitingRow({
  action,
  busy,
  locked,
  onApprove,
  onReject,
}: {
  action: AgentAction
  busy: boolean
  locked: boolean
  onApprove: () => void
  onReject: () => void
}) {
  const approvable = canApprove(action)
  const blocked = approveBlockedReason(action)
  const risk = riskWord(action.risk)
  const title = titleOf(action)
  return (
    <li data-testid="agent-action-row" data-agent-action-id={action.id} className="flex items-start gap-4 px-3 py-3">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 truncate text-sm font-medium text-[var(--color-text)]" title={title}>
            {title}
          </span>
          {risk && <span className="flex-shrink-0 text-2xs text-[var(--color-text-muted)]">{risk}</span>}
        </div>
        <RowFacts action={action} />
        {action.rationale?.trim() && (
          <p className="m-0 line-clamp-2 max-w-[80ch] text-xs text-[var(--color-text-secondary)]">{action.rationale}</p>
        )}
      </div>
      <div className="flex flex-shrink-0 items-center gap-1.5">
        <button
          type="button"
          data-testid="agent-action-approve"
          className="btn-primary text-xs"
          disabled={!approvable || locked}
          title={blocked ?? undefined}
          onClick={onApprove}
        >
          {busy ? 'Approving…' : 'Approve'}
        </button>
        <button
          type="button"
          data-testid="agent-action-reject"
          className="btn-secondary text-xs"
          disabled={locked}
          onClick={onReject}
        >
          Reject
        </button>
      </div>
    </li>
  )
}

/** One decided action: chip · outcome (up to two lines) · result / target link · Undo. */
function DecidedRow({ action, locked, onUndo }: { action: AgentAction; locked: boolean; onUndo: () => void }) {
  const sentence = outcomeSentence(action)
  const result = resultHref(action)
  const unknown = outcomeOf(action) === 'outcome_unknown'
  const target = unknown ? targetHref(action) : null
  return (
    <li
      data-testid="agent-action-decided-row"
      data-agent-action-id={action.id}
      className="flex min-w-0 items-center gap-3 px-3 py-2 text-xs"
    >
      {/* One width for every chip, so the outcome lines start in one column (8.5rem: "Outcome unknown"
          measured ~131 px on CI's DejaVu, past the 120 px of 7.5rem). */}
      <ActionStatusChip action={action} className="min-w-[8.5rem] justify-center" />
      {/* Two lines, not one: on CI's DejaVu the outcome_unknown sentence lost its "check the target"
          ending to an ellipsis — the one line of that row a person must read. */}
      <span className="min-w-0 flex-1 line-clamp-2 text-[var(--color-text-secondary)]" title={sentence}>
        <span className="font-medium text-[var(--color-text)]">{titleOf(action)}</span> — {sentence}
      </span>
      {result && action.status === 'executed' && (
        <Link to={result} className={`flex-shrink-0 ${LINK}`} data-agent-action-result="">
          {action.result_link?.label?.trim() || 'Open result'}
        </Link>
      )}
      {target && (
        <Link to={target} className={`flex-shrink-0 ${LINK}`}>
          {action.target?.label?.trim() || 'Open target'}
        </Link>
      )}
      {canUndo(action) && (
        <button
          type="button"
          data-testid="agent-action-undo"
          className="btn-secondary flex-shrink-0 text-xs"
          disabled={locked}
          onClick={onUndo}
        >
          Undo
        </button>
      )}
    </li>
  )
}
