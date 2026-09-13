import { clsx } from 'clsx'
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import ScopedLink from '@/components/ui/ScopedLink'
import type { ReviewEnvelopeFields, ReviewState } from '@/types/review'

const TONE: Record<ReviewState, { box: string; icon: string; Icon: LucideIcon; title: string }> = {
  pending_review: {
    box: 'border-[var(--status-broken)]/40 bg-[var(--status-broken)]/10',
    icon: 'text-[var(--status-broken)]',
    Icon: AlertTriangle,
    title: 'Draft: awaiting human review',
  },
  accepted: {
    box: 'border-[var(--status-passed)]/40 bg-[var(--status-passed)]/10',
    icon: 'text-[var(--status-passed)]',
    Icon: CheckCircle2,
    title: 'Reviewed and accepted',
  },
  rejected: {
    box: 'border-[var(--status-failed)]/40 bg-[var(--status-failed)]/10',
    icon: 'text-[var(--status-failed)]',
    Icon: XCircle,
    title: 'Rejected by a reviewer',
  },
  superseded: {
    box: 'border-[var(--color-border)] bg-[var(--color-bg-secondary)]',
    icon: 'text-[var(--color-text-muted)]',
    Icon: Info,
    title: 'Superseded by a newer report',
  },
}

/**
 * The human-review status of an AI report (E8.5, architecture section 8.3).
 *
 * Driven by the review envelope a report response carries (E8.3). It renders
 * nothing for deterministic content (`not_applicable`) and nothing for a
 * payload without a review block (a cached response or an older API): there is
 * no true statement to make, and a banner must never imply a review happened.
 */
export default function ReviewBanner({
  envelope,
  className,
}: {
  envelope: ReviewEnvelopeFields | null | undefined
  className?: string
}) {
  const review = envelope?.review
  if (!review || review.state === 'not_applicable') return null
  const tone = TONE[review.state]
  if (!tone) return null
  const reviewedAt = review.reviewed_at ? new Date(review.reviewed_at).toLocaleString() : null

  return (
    <div
      role="status"
      data-testid="review-banner"
      data-review-state={review.state}
      className={clsx('flex items-start gap-2 rounded-md border px-3 py-2 text-sm', tone.box, className)}
    >
      <tone.Icon className={clsx('h-4 w-4 mt-0.5 shrink-0', tone.icon)} aria-hidden />
      <div className="min-w-0 space-y-0.5">
        <p className="font-medium text-[var(--color-text)]">
          {tone.title}
          {reviewedAt && review.state !== 'pending_review' && (
            <span className="ml-1 font-normal text-[var(--color-text-muted)]">· {reviewedAt}</span>
          )}
        </p>
        <p className="text-xs text-[var(--color-text-muted)]">{review.message}</p>
        {review.state === 'pending_review' && envelope?.ai_disclaimer && (
          <p className="text-xs text-[var(--color-text-muted)]">{envelope.ai_disclaimer}</p>
        )}
      </div>
      {review.state === 'pending_review' && (
        <ScopedLink
          to="/reviews"
          containerClassName="ml-auto shrink-0"
          className="text-xs font-medium text-[var(--color-accent)] hover:underline"
        >
          Open review queue
        </ScopedLink>
      )}
    </div>
  )
}
