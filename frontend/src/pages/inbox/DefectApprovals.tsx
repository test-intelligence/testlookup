/**
 * Inbox › Approvals › Defects (W06 DEF-06): the promoted defects awaiting a QA
 * lead's approval in one project, with why each one waits. **Approve** makes
 * the defect effective and, when a Jira project key was given at promotion,
 * files the ticket on that defect (offline it says so); **Reject** needs a
 * reason and closes the defect as Invalid, so it stops counting toward the
 * release gate.
 *
 * QA leads only: the list is QA_LEAD-only on the server, so for anyone else
 * nothing is requested and nothing renders (a QA engineer who opens
 * `?source=defects` by URL sees no queue and no 403 toast).
 *
 * Titles, components, teams, reasons and Jira keys are rendered as text.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Bug, Clock } from 'lucide-react'
import toast from 'react-hot-toast'
import EmptyState from '@/components/ui/EmptyState'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import TransitionReasonDialog from '@/components/testManagement/TransitionReasonDialog'
import { usePendingDefects } from '@/hooks/usePendingDefects'
import { usePermissions } from '@/hooks/usePermissions'
import { defectPromotionService } from '@/services/defectPromotionService'
import type { DefectApprovalResponse, PendingDefect } from '@/types/defect-promotion'

const JIRA_REASON_LABELS: Record<string, string> = {
  offline_mode: 'offline mode',
  disabled: 'Jira integration disabled',
  not_configured: 'Jira not configured',
}

const SEVERITY_TONE: Record<string, string> = {
  CRITICAL: 'text-[var(--status-failed)] border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)]',
  HIGH: 'text-[var(--status-broken)] border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)]',
  MEDIUM: 'text-[var(--status-broken)] border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)]',
  LOW: 'text-[var(--status-passed)] border-[var(--status-passed-bd)] bg-[var(--status-passed-bg)]',
}
const NEUTRAL_TONE = 'text-[var(--color-text-muted)] border-[var(--color-border)]'

const blank = (value: string | null | undefined) => (value && value.trim() ? value : '—')

function ageLabel(createdAt: string | null): string | null {
  if (!createdAt) return null
  const created = new Date(createdAt).getTime()
  if (Number.isNaN(created)) return null
  const days = Math.max(0, Math.floor((Date.now() - created) / 86_400_000))
  return `created ${days}d ago`
}

/** The filed / linked issue's key: the ticket's `key`, else the last segment of its browse URL. */
function issueKey(response: DefectApprovalResponse): string | null {
  const fromTicket = response.jira_ticket?.key
  if (typeof fromTicket === 'string' && fromTicket) return fromTicket
  const match = response.jira_url ? /\/browse\/([^/?#]+)/.exec(response.jira_url) : null
  return match ? decodeURIComponent(match[1]) : null
}

/** The approval toast, per what the Jira filing did. */
function announceApproval(response: DefectApprovalResponse) {
  const key = issueKey(response) ?? 'issue'
  switch (response.jira_status) {
    case 'filed':
      toast.success(`Approved — Jira ${key} filed`)
      return
    case 'linked':
      toast.success(`Approved — linked to the open Jira issue ${key}`)
      return
    case 'not_filed':
      toast.success(
        `Approved — Jira not filed (${JIRA_REASON_LABELS[response.jira_reason ?? ''] ?? response.jira_reason ?? 'Jira unavailable'})`,
      )
      return
    case 'failed':
    case 'unknown':
      toast.error(`Approved — Jira filing failed: ${response.jira_error ?? 'no detail from the server'}`)
      return
    default:
      toast.success('Approved')
  }
}

export default function DefectApprovals({ projectId }: { projectId: string }) {
  const { isQaLead } = usePermissions()
  const { items, isLoading, error, mutate } = usePendingDefects(projectId, isQaLead)
  // A decided defect leaves the list at once; the refetch then agrees.
  const [decided, setDecided] = useState<ReadonlySet<string>>(() => new Set())
  const [busy, setBusy] = useState<{ id: string; action: 'approve' | 'reject' } | null>(null)
  const [rejecting, setRejecting] = useState<PendingDefect | null>(null)

  if (!isQaLead) return null

  const rows = items.filter((d) => !decided.has(d.defect_id))
  const markDecided = (id: string) => setDecided((current) => new Set(current).add(id))

  async function approve(defect: PendingDefect) {
    setBusy({ id: defect.defect_id, action: 'approve' })
    try {
      const response = await defectPromotionService.reviewDefect(defect.defect_id, { action: 'approve' })
      markDecided(defect.defect_id)
      announceApproval(response)
      void mutate()
    } catch {
      // The shared Axios interceptor has already shown the server's message.
    } finally {
      setBusy(null)
    }
  }

  async function reject(defect: PendingDefect, reason: string) {
    setBusy({ id: defect.defect_id, action: 'reject' })
    try {
      await defectPromotionService.reviewDefect(defect.defect_id, { action: 'reject', reason })
      markDecided(defect.defect_id)
      setRejecting(null)
      toast.success('Rejected — closed as Invalid')
      void mutate()
    } catch {
      // The shared Axios interceptor has already shown the server's message.
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-3">
      {Boolean(error) && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-lg border border-[var(--status-failed-bd)] bg-[var(--status-failed-bg)] p-3 text-sm text-[var(--status-failed)]"
        >
          <span>The defects awaiting approval could not be loaded.</span>
          <button type="button" className="btn-secondary flex-shrink-0 text-xs" onClick={() => void mutate()}>
            Retry
          </button>
        </div>
      )}

      {isLoading && rows.length === 0 ? (
        <div className="flex justify-center py-8"><LoadingSpinner size="lg" /></div>
      ) : rows.length === 0 && !error ? (
        <EmptyState
          icon={<Bug className="h-10 w-10" />}
          title="No defects are waiting for approval."
          description="Defects promoted from a failure cluster that need a QA lead's approval appear here."
        />
      ) : rows.length > 0 ? (
        <ul className="divide-y divide-[var(--color-border)] rounded-md border border-[var(--color-border)]" aria-label="Defects awaiting approval">
          {rows.map((defect) => {
            const severity = (defect.severity ?? '').toUpperCase()
            const age = ageLabel(defect.created_at)
            const rowBusy = busy?.id === defect.defect_id
            return (
              <li key={defect.defect_id} data-testid="defect-approval-row" className="flex items-start gap-4 px-3 py-3">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-[var(--color-text)]" title={defect.title ?? undefined}>
                      {defect.title?.trim() || 'Untitled defect'}
                    </span>
                    {severity && (
                      <span
                        className={`inline-flex flex-shrink-0 items-center rounded-full border px-1.5 py-0.5 text-[10.5px] font-semibold ${SEVERITY_TONE[severity] ?? NEUTRAL_TONE}`}
                        data-defect-severity=""
                      >
                        {severity}
                      </span>
                    )}
                  </div>
                  <div className="truncate text-[11.5px] text-[var(--color-text-muted)]" data-defect-owner="">
                    {blank(defect.component)} · {blank(defect.owner_team)}
                  </div>
                  {defect.policy_reasons.length > 0 && (
                    <div className="text-[11.5px] text-[var(--color-text-secondary)]">
                      <span>Waiting because:</span>
                      <ul className="ml-4 list-disc">
                        {defect.policy_reasons.map((reason, i) => (
                          <li key={i}>{reason}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-[var(--color-text-muted)]">
                    <span data-defect-jira-project="">
                      {defect.jira_project_key
                        ? `Jira project ${defect.jira_project_key}`
                        : 'No Jira project: approving keeps it in TestLookup'}
                    </span>
                    {age && (
                      <span className="inline-flex items-center gap-1">
                        <Clock className="h-3 w-3" aria-hidden />
                        {age}
                      </span>
                    )}
                    {defect.run_id && (
                      <Link
                        to={`/runs/${encodeURIComponent(defect.run_id)}?tab=analysis`}
                        className="text-[var(--color-accent)] hover:underline"
                      >
                        Open run
                      </Link>
                    )}
                  </div>
                </div>
                <div className="flex flex-shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    data-testid="defect-approve"
                    className="btn-primary text-xs"
                    disabled={busy !== null}
                    onClick={() => void approve(defect)}
                  >
                    {rowBusy && busy?.action === 'approve' ? 'Approving…' : 'Approve'}
                  </button>
                  <button
                    type="button"
                    data-testid="defect-reject"
                    className="btn-secondary text-xs"
                    disabled={busy !== null}
                    onClick={() => setRejecting(defect)}
                  >
                    Reject
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      ) : null}

      {rejecting && (
        <TransitionReasonDialog
          title="Reject this defect?"
          description="It is closed as Invalid and stops counting toward the release gate."
          fieldLabel="Reason (recorded in the activity log)"
          placeholder="Why this is not a defect"
          confirmLabel="Reject"
          busy={busy?.id === rejecting.defect_id}
          onCancel={() => setRejecting(null)}
          onConfirm={(reason) => reject(rejecting, reason)}
        />
      )}
    </div>
  )
}
