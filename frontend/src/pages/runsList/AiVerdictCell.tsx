/**
 * One run's AI verdict in the Runs table (UX redesign P4, D2). The verdict
 * links to the run's Analysis tab, which replaced the row's separate "Intel"
 * link. A run with no verdict reads "—", with the reason as its title.
 * The data and its rules: `aiVerdict.ts`.
 */
import { Link } from 'react-router-dom'
import Skeleton from '@/components/ui/Skeleton'
import {
  NO_VERDICT_TITLE,
  RECOMMENDATION_LABEL,
  useRunAiVerdict,
  type AiRecommendation,
} from './aiVerdict'

const HUE: Record<AiRecommendation, string> = {
  GO: 'var(--status-passed)',
  CONDITIONAL_GO: 'var(--status-broken)',
  NO_GO: 'var(--status-failed)',
}

const REVIEW_TITLE = {
  draft: 'AI-generated draft: awaiting human review',
  rejected: 'The AI report was rejected by a reviewer',
} as const

export default function AiVerdictCell({ run }: { run: { id: string; status?: string | null } }) {
  const { verdict, isLoading } = useRunAiVerdict(run)
  if (isLoading || !verdict) {
    return (
      <span data-ai-verdict="loading" aria-busy="true" className="inline-block align-middle">
        <Skeleton width={56} height={10} />
      </span>
    )
  }
  if (verdict.recommendation === null) {
    const title = NO_VERDICT_TITLE[verdict.reason]
    return (
      <span data-ai-verdict="none" data-ai-verdict-reason={verdict.reason} title={title} className="text-[var(--color-text-muted)]">
        —
      </span>
    )
  }
  const label = RECOMMENDATION_LABEL[verdict.recommendation]
  const hue = HUE[verdict.recommendation]
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
      <span
        className="inline-flex items-center px-2 py-0.5 rounded-full text-[10.5px] font-semibold"
        style={{
          background: `color-mix(in srgb, ${hue} 15%, transparent)`,
          color: hue,
        }}
      >
        {label}
      </span>
      {review && (
        <span data-ai-verdict-review={review} className="pl-2 text-[10.5px] leading-none text-[var(--color-text-muted)]">
          {review}
        </span>
      )}
    </Link>
  )
}
