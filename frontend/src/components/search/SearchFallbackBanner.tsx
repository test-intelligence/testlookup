/**
 * W12 SRCH-01 — why a Hybrid / Semantic search answered with keyword rows.
 *
 * Keyed off the server's `fallback_reason`, never off a count: an outage
 * (`semantic_unavailable`, warning ink) and a semantic query that matched
 * nothing (`no_semantic_match`) or had no words to compare (`no_text`, info
 * ink) are different facts with different sentences. The sentence is the
 * server's `fallback_message`, shown verbatim as text. No reason, no banner.
 */
import { clsx } from 'clsx'

const WARNING = 'border-[var(--ui-warning-bd)] bg-[var(--ui-warning-bg)] text-[var(--ui-warning-ink)]'
const INFO = 'border-[var(--ui-info-bd)] bg-[var(--ui-info-bg)] text-[var(--ui-info-ink)]'

export default function SearchFallbackBanner({
  reason, message,
}: {
  reason?: string | null
  message?: string | null
}) {
  if (!reason || !message) return null
  return (
    <div
      role="status"
      data-search-fallback={reason}
      className={clsx('mb-3 rounded-md border px-3 py-2 text-xs', reason === 'semantic_unavailable' ? WARNING : INFO)}
    >
      {message}
    </div>
  )
}
