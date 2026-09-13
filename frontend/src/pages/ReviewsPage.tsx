import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { clsx } from 'clsx'
import { Check, ClipboardCheck, X } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import EmptyState from '@/components/ui/EmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import Pagination from '@/components/ui/Pagination'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { usePermissions } from '@/hooks/usePermissions'
import { useProjectReviews } from '@/hooks/useReviews'
import { reviewService } from '@/services/reviewService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import {
  REVIEW_REASON_CODES,
  REVIEW_REASON_LABEL,
  REVIEW_STATE_LABEL,
  type Review,
  type ReviewReasonCode,
  type ReviewStateFilter,
} from '@/types/review'

const PAGE_SIZE = 25

const TABS: { key: ReviewStateFilter; label: string }[] = [
  { key: 'pending_review', label: 'Pending' },
  { key: 'accepted', label: 'Accepted' },
  { key: 'rejected', label: 'Rejected' },
  { key: 'superseded', label: 'Superseded' },
  { key: 'all', label: 'All' },
]

const STATE_TONE: Record<Review['state'], string> = {
  pending_review: 'text-[var(--status-broken)]',
  accepted: 'text-[var(--status-passed)]',
  rejected: 'text-[var(--status-failed)]',
  superseded: 'text-[var(--color-text-muted)]',
}

const formatWhen = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—')

/**
 * Review Queue — the human review gate (architecture E8.5, section 8.3).
 *
 * Every AI-generated report is a draft until a person accepts it. QA leads
 * accept (the pipeline run becomes `passed`) or reject with a reason code (it
 * becomes `failed`). Everyone else sees the queue read-only. The server
 * enforces the rest: API keys and synthetic accounts are refused, and nobody
 * settles a review of a run they triggered (separation of duties).
 */
export default function ReviewsPage() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const { canAccessManagement: canSettle } = usePermissions()
  const [tab, setTab] = useState<ReviewStateFilter>('pending_review')
  const [page, setPage] = useState(1)
  const { reviews, isLoading, error, isError, refresh } = useProjectReviews(tab)

  const pages = Math.max(1, Math.ceil(reviews.length / PAGE_SIZE))
  const currentPage = Math.min(page, pages)
  const rows = useMemo(
    () => reviews.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE),
    [reviews, currentPage],
  )

  const header = (
    <PageHeader
      title="Review Queue"
      subtitle="AI-generated reports are drafts until a person accepts them. Accepting marks the pipeline run passed; rejecting marks it failed."
    />
  )

  if (isAllProjects) {
    return (
      <div className="space-y-4">
        {header}
        <ProjectRequiredEmptyState
          icon={<ClipboardCheck className="h-10 w-10" />}
          description="Reviews belong to one project. Pick the project whose AI reports you want to review."
        />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {header}

      <div className="flex items-center gap-1 border-b border-[var(--color-border)]" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => {
              setTab(t.key)
              setPage(1)
            }}
            className={clsx(
              'px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors',
              tab === t.key
                ? 'border-[var(--color-accent)] text-[var(--color-text)]'
                : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {!canSettle && (
        <p className="text-xs text-[var(--color-text-muted)]" data-testid="review-readonly-note">
          Only QA leads can accept or reject AI reports. You can see the queue.
        </p>
      )}

      {isLoading && <LoadingSpinner size="lg" />}
      {isError && <DataUnavailable error={error} onRetry={() => void refresh()} />}
      {!isLoading && !isError && reviews.length === 0 && (
        <EmptyState
          icon={<ClipboardCheck className="h-10 w-10" />}
          title="Nothing here"
          description={
            tab === 'pending_review'
              ? 'No AI reports are awaiting review. A report joins this queue when its AI pipeline finishes.'
              : 'No reviews in this state yet.'
          }
        />
      )}
      {!isLoading && !isError && reviews.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-[var(--color-border)]">
          <table className="w-full text-sm">
            <thead className="bg-[var(--color-bg-secondary)] text-xs text-[var(--color-text-muted)]">
              <tr>
                <th className="text-left px-3 py-2">Report</th>
                <th className="text-left px-3 py-2">Run</th>
                <th className="text-left px-3 py-2">State</th>
                <th className="text-left px-3 py-2">Requested</th>
                <th className="text-left px-3 py-2">Decision</th>
                <th className="text-right px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((review) => (
                <ReviewRow key={review.id} review={review} canSettle={canSettle} onSettled={() => refresh()} />
              ))}
            </tbody>
          </table>
          <Pagination page={currentPage} pages={pages} total={reviews.length} onChange={setPage} />
        </div>
      )}

      {reviews[0]?.ai_disclaimer && (
        <p className="text-xs text-[var(--color-text-muted)]">{reviews[0].ai_disclaimer}</p>
      )}
    </div>
  )
}

function ReviewRow({
  review,
  canSettle,
  onSettled,
}: {
  review: Review
  canSettle: boolean
  onSettled: () => Promise<unknown>
}) {
  const [mode, setMode] = useState<'accept' | 'reject' | null>(null)
  const [notes, setNotes] = useState('')
  const [reason, setReason] = useState<ReviewReasonCode | ''>('')
  const [busy, setBusy] = useState(false)
  const pending = review.state === 'pending_review'

  function close() {
    setMode(null)
    setNotes('')
    setReason('')
  }

  async function submit() {
    if (busy || !mode) return
    if (mode === 'reject' && !reason) return
    setBusy(true)
    const trimmed = notes.trim() || undefined
    try {
      if (mode === 'accept') await reviewService.accept(review.id, trimmed)
      else await reviewService.reject(review.id, reason as ReviewReasonCode, trimmed)
      toast.success(mode === 'accept' ? 'Report accepted.' : 'Report rejected.')
      close()
      await onSettled()
    } catch {
      // The shared API interceptor already shows the server's reason (already
      // settled, separation of duties, ...). Refresh in case someone else
      // settled it first.
      void onSettled()
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <tr className="border-t border-[var(--color-border)]" data-testid="review-row" data-review-id={review.id}>
        <td className="px-3 py-2">
          <div className="text-[var(--color-text)] capitalize">{review.workflow_type ?? review.kind} report</div>
          <div className="text-[10px] font-mono text-[var(--color-text-muted)]">{review.subject_type.replace(/_/g, ' ')}</div>
        </td>
        <td className="px-3 py-2 text-xs">
          {review.test_run_id ? (
            <Link
              to={`/runs/${encodeURIComponent(review.test_run_id)}/intelligence`}
              className="text-[var(--color-accent)] hover:underline"
            >
              Open report
            </Link>
          ) : (
            <span className="text-[var(--color-text-muted)]">—</span>
          )}
        </td>
        <td className={clsx('px-3 py-2 text-xs font-medium whitespace-nowrap', STATE_TONE[review.state])}>
          {REVIEW_STATE_LABEL[review.state]}
        </td>
        <td className="px-3 py-2 text-xs text-[var(--color-text-muted)] whitespace-nowrap">{formatWhen(review.created_at)}</td>
        <td className="px-3 py-2 text-xs text-[var(--color-text-muted)]">
          {review.reviewed_at ? (
            <div className="space-y-0.5">
              <div className="whitespace-nowrap">{formatWhen(review.reviewed_at)}</div>
              {review.reason_code && (
                <div>{REVIEW_REASON_LABEL[review.reason_code as ReviewReasonCode] ?? review.reason_code}</div>
              )}
              {review.notes && <div className="truncate max-w-[260px]" title={review.notes}>{review.notes}</div>}
            </div>
          ) : (
            '—'
          )}
        </td>
        <td className="px-3 py-2 text-right whitespace-nowrap">
          {canSettle && pending && mode === null && (
            <span className="inline-flex gap-1">
              <button
                type="button"
                data-testid="review-accept"
                onClick={() => setMode('accept')}
                className="btn-ghost inline-flex items-center gap-1 text-xs text-[var(--status-passed)]"
              >
                <Check className="h-3.5 w-3.5" /> Accept
              </button>
              <button
                type="button"
                data-testid="review-reject"
                onClick={() => setMode('reject')}
                className="btn-ghost inline-flex items-center gap-1 text-xs text-[var(--status-failed)]"
              >
                <X className="h-3.5 w-3.5" /> Reject
              </button>
            </span>
          )}
        </td>
      </tr>
      {mode !== null && (
        <tr data-testid="review-action-form">
          <td colSpan={6} className="px-3 pb-3">
            <div className="flex flex-wrap items-end gap-2 rounded-md bg-[var(--color-bg-secondary)] p-3">
              {mode === 'reject' && (
                <label className="flex flex-col gap-1 text-xs text-[var(--color-text-muted)]">
                  Reason
                  <select
                    data-testid="review-reason"
                    value={reason}
                    onChange={(e) => setReason(e.target.value as ReviewReasonCode | '')}
                    className="input text-sm"
                  >
                    <option value="">Choose a reason</option>
                    {REVIEW_REASON_CODES.map((code) => (
                      <option key={code} value={code}>
                        {REVIEW_REASON_LABEL[code]}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="flex min-w-[220px] flex-1 flex-col gap-1 text-xs text-[var(--color-text-muted)]">
                Notes (optional)
                <input
                  data-testid="review-notes"
                  value={notes}
                  maxLength={4000}
                  onChange={(e) => setNotes(e.target.value)}
                  className="input text-sm"
                />
              </label>
              <button
                type="button"
                data-testid="review-confirm"
                disabled={busy || (mode === 'reject' && !reason)}
                onClick={() => void submit()}
                className="btn-primary text-xs disabled:opacity-40"
              >
                {mode === 'accept' ? 'Confirm accept' : 'Confirm reject'}
              </button>
              <button type="button" onClick={close} className="btn-ghost text-xs">
                Cancel
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
