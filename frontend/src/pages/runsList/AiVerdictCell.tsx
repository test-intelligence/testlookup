/**
 * One run's AI verdict in the Runs table (UX redesign P4, D2). The verdict
 * links to the run's Analysis tab, which replaced the row's separate "Intel"
 * link. A run with no verdict reads "—", with the reason as its title.
 *
 * Presentational (FAIL-04): the page asks ONE batch request for the runs on
 * screen (`useRunAiVerdicts`) and hands each cell its verdict. The data and
 * its rules: `aiVerdict.ts`.
 */
import { Link } from 'react-router-dom'
import Skeleton from '@/components/ui/Skeleton'
import DecisionChip from '@/components/ui/DecisionChip'
import { NO_VERDICT_TITLE, RECOMMENDATION_LABEL, type AiVerdict } from './aiVerdict'

const REVIEW_TITLE = {
  draft: 'AI-generated draft: awaiting human review',
  rejected: 'The AI report was rejected by a reviewer',
} as const

export default function AiVerdictCell({
  run,
  verdict,
  isLoading,
}: {
  run: { id: string }
  verdict: AiVerdict | undefined
  isLoading: boolean
}) {
  if (isLoading || !verdict) {
    return (
      <span data-ai-verdict="loading" aria-busy="true" className="inline-block align-middle">
        <Skeleton width={56} height={10} />
      </span>
    )
  }
  if (verdict.recommendation === null) {
    const title = verdict.title ?? NO_VERDICT_TITLE[verdict.reason]
    return (
      <span data-ai-verdict="none" data-ai-verdict-reason={verdict.reason} title={title} className="text-[var(--color-text-muted)]">
        —
      </span>
    )
  }
  const label = RECOMMENDATION_LABEL[verdict.recommendation]
  const review = verdict.review
  // The review state goes UNDER the pill, not beside it: the column stays as
  // narrow as its widest pill ("Conditional"), which is what lets the table
  // fit its card at a 1280 px window; a row is two lines tall anyway (the
  // Timing cell).
  return (
    <Link
      to={`/runs/${run.id}?tab=analysis`}
      data-ai-verdict={verdict.recommendation}
      title={review ? `${label}: ${REVIEW_TITLE[review]}. Open the run's analysis.` : `${label}. Open the run's analysis.`}
      className="inline-flex flex-col items-start gap-0.5 whitespace-nowrap hover:underline"
    >
      {/* W29a UIX-03: the one decision chip (its tint, border and legible ink). The cell keeps the
          table's 10.5 px (a later utility than the chip's text-xs, so it wins): the verdict column
          stays as narrow as before, which is what fits the table at 1280 px. */}
      <DecisionChip decision={verdict.recommendation} className="text-[10.5px]" />
      {review && (
        <span data-ai-verdict-review={review} className="pl-2 text-[10.5px] leading-none text-[var(--color-text-muted)]">
          {review}
        </span>
      )}
    </Link>
  )
}
